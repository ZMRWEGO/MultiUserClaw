# Warm Container 缓存池技术方案

## 1. 背景与目标

### 问题
多租户架构下，每个用户对应一个独立 Docker 容器。当前容器一旦创建便长期运行（`restart_policy: unless-stopped`），即使用户数小时、数天不再访问，资源（CPU、内存）仍被持续占用。对于大量注册用户，这会导致严重的资源空置浪费。

### 目标
实现"Warm Container 缓存池"机制：
- **同用户热复用**：用户断开后容器保留一段时间（warm 状态），短期内再次访问可直接复用，无需重新 `create_container`。
- **全局水位线控制**：同时存在的容器总数（running + warm）有软上限，超出时按 LRU 淘汰 warm 容器，释放资源。
- **数据安全**：销毁容器时只删除 Docker 容器和 DB 记录，用户数据保留在 Docker volume 中，下次重建时自动挂载。

---

## 2. 术语定义

| 术语 | 含义 |
|------|------|
| **Running** | 容器正在服务用户，接收请求/WebSocket |
| **Warm** | 用户已断开，容器仍存活，等待复用 |
| **Destroyed** | 容器已停止并删除，DB 记录已清除，volume 数据保留 |
| **水位线（Watermark）** | 同时存在的 running + warm 容器数量上限 |
| **LRU 淘汰** | 按 `last_active_at` 最早优先淘汰 warm 容器 |

---

## 3. 状态机

```
                    用户请求
                       │
                       ▼
              ┌─────────────────┐
              │ ensure_running() │
              └─────────────────┘
                       │
      ┌────────────────┼────────────────┐
      ▼                ▼                ▼
  无记录           状态=warm          状态=running
      │                │                │
      ▼                ▼                ▼
 create_container()  改状态为          直接返回
  （新建）           running           （正在服务）
      │                │
      └────────┬───────┘
               ▼
          状态=running
               │
      用户断开 / WebSocket 关闭
               │
               ▼
          状态=warm
               │
      ┌────────┴────────┐
      ▼                 ▼
 30min 内用户再来    30min 超时 / 水位线超了
      │                 │
      ▼                 ▼
 状态=running      destroy_container()
 （热复用）         （释放 CPU + 内存）
```

---

## 4. 前置修复：`last_active_at` 更新不完整

当前代码中，`Container.last_active_at` 仅在 `app/llm_proxy/service.py` 中更新（LLM 调用时写一次）。这导致：
- WebSocket 聊天消息转发 → **不更新**
- 查看历史会话、文件下载等 HTTP 请求 → **不更新**
- 用户聊得很火热，但 nanobot 没触发 LLM → `last_active_at` 停留在创建时间

**后果**：后台任务依赖 `last_active_at` 判断空闲，如果更新不全，所有 running 容器会在 30 分钟后被误判为不活跃，导致误回收。

**修复方案**：
- `app/routes/proxy.py::proxy_http` — 每次 HTTP 请求转发成功后，异步更新 `last_active_at`
- `app/routes/proxy.py::proxy_websocket` — WebSocket 消息收发时更新 `last_active_at`（在 relay 协程中）
- `app/llm_proxy/service.py` — 保留现有更新逻辑（LLM 调用也是活跃信号）

---

## 5. 核心流程

### 5.1 用户请求到达（HTTP / WebSocket）

**入口**：`app/routes/proxy.py`

1. 调用 `_container_url(db, user)`
2. 内部调用 `ensure_running(db, user.id)`
3. `ensure_running` 查询 DB：
   - **无记录** → `create_container()` → 新建容器，状态 `running`
   - **状态 = `running`** → 直接返回现有容器
   - **状态 = `warm`** → 改状态为 `running`，`await db.commit()`，返回容器（热复用）
   - **状态 = `paused` / `archived`** → 按现有逻辑处理（unpause 或重建）
4. 请求转发前，**更新 `Container.last_active_at = now()`**

### 5.2 用户断开（WebSocket Close）

**入口**：`app/routes/proxy.py::proxy_websocket`

当 WebSocket 连接断开（`client_to_upstream` 或 `upstream_to_client` 协程结束）：

1. 捕获 `WebSocketDisconnect` 或 `websockets.ConnectionClosed`
2. 关闭 WebSocket
3. **调用 `warm_container(db, user.id)`**
   - 查询该用户的 Container 记录
   - 若状态为 `running`，改为 `warm`
   - 更新 `last_active_at = now()`
   - `await db.commit()`

> **HTTP 请求不触发 warm**：HTTP 是无状态的，一次请求结束不代表用户"离开"。只有 WebSocket 断开才表示会话结束。如果用户只发 HTTP（如下载文件），容器保持 `running` 状态，由后台任务根据 `last_active_at` 超时后回收。

### 5.3 后台回收任务（Idle Watcher）

**模块**：`app/container/idle_watcher.py`

**启动**：在 `app/main.py` 的 FastAPI `lifespan` startup 阶段，通过 `asyncio.create_task()` 启动后台协程。

**循环周期**：每 5 分钟执行一次扫描。

**执行逻辑**：

```python
async def _tick():
    now = datetime.now(timezone.utc)
    idle_deadline = now - timedelta(minutes=settings.container_idle_pause_minutes)

    async with async_session() as db:
        # 规则1：warm 超时的容器 → destroy
        stale_warm = await db.execute(
            select(Container).where(
                Container.status == "warm",
                Container.last_active_at < idle_deadline,
            )
        )
        for c in stale_warm.scalars():
            await destroy_container(db, c.user_id)

        # 规则2：总容器数超过水位线 → LRU 淘汰 warm 容器
        total = await db.scalar(
            select(func.count(Container.id)).where(
                Container.status.in_(["running", "warm"])
            )
        )
        over = total - settings.container_warm_pool_size
        if over > 0:
            lru_warm = await db.execute(
                select(Container)
                .where(Container.status == "warm")
                .order_by(Container.last_active_at.asc())
                .limit(over)
            )
            for c in lru_warm.scalars():
                await destroy_container(db, c.user_id)
```

**优雅关闭**：`lifespan` shutdown 阶段取消后台任务，等待当前 tick 完成。

---

## 6. 数据模型变更

### 6.1 `Container` 表（`app/db/models.py`）

现有字段已满足需求，`status` 枚举值扩展即可：

```python
class Container(Base):
    ...
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="creating"
    )
    # 状态枚举扩展为：creating | running | warm | paused | archived
    ...
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
```

> `last_active_at` 和 `created_at` 已存在，无需新增字段。

### 6.2 配置项（`app/config.py`）

```python
# 空闲销毁阈值（分钟），warm 超过此时长未复用即销毁
container_idle_pause_minutes: int = 30

# Warm 池水位线：running + warm 容器总数上限
container_warm_pool_size: int = 20
```

> `container_idle_pause_minutes` 已存在，复用其语义（从"pause 阈值"改为"warm 销毁阈值"）。
> `container_warm_pool_size` 为新增配置项。

---

## 7. 模块改动清单

| # | 文件 | 改动内容 |
|---|------|---------|
| 1 | `app/db/models.py` | `Container.status` 注释更新，增加 `"warm"` 状态说明 |
| 2 | `app/config.py` | 新增 `container_warm_pool_size: int = 20` |
| 3 | `app/container/manager.py` | 1. `ensure_running()` 增加对 `warm` 状态的处理（改 `running` 复用）<br>2. 新增 `warm_container(db, user_id)` 函数：running → warm |
| 4 | `app/routes/proxy.py` | **（前置修复）** 补全 `last_active_at` 更新：<br>1. `proxy_http()` 每次请求后更新 `last_active_at`<br>2. `proxy_websocket()` 消息收发时更新 `last_active_at`<br>3. WebSocket 断开时调用 `warm_container()` |
| 5 | `app/container/idle_watcher.py` | **新增**：后台定时任务模块，执行超时销毁 + 水位线 LRU 淘汰 |
| 6 | `app/main.py` | `lifespan` 中启动 idle watcher 协程，shutdown 时取消 |

---

## 8. 时序示例

### 场景1：正常热复用

```
T+0min   用户A打开聊天页 → WebSocket 连接 → create_container() → 状态 running
T+5min   用户A发消息     → ensure_running() 发现 running → 直接复用 → 更新 last_active_at
T+10min  用户A关闭页面   → WebSocket 断开 → warm_container() → 状态 warm
T+15min  用户A重新打开   → ensure_running() 发现 warm → 改 running → 秒级恢复
```

### 场景2：超时销毁后重建

```
T+0min   用户B打开聊天页 → create_container() → 状态 running
T+5min   用户B关闭页面   → 状态 warm，last_active_at = T+5min
T+35min  idle_watcher 扫描 → T+5min + 30min < T+35min → destroy_container()
T+40min  用户B重新打开   → ensure_running() 无记录 → create_container() → 挂载原有 volume
```

### 场景3：水位线 LRU 淘汰

```
当前：18 running + 3 warm = 21 个容器，水位线 = 20

idle_watcher 扫描：
  - 3 个 warm 均未超时
  - total=21 > 20，需要淘汰 1 个
  - 按 last_active_at 排序，淘汰最久未活跃的 warm 容器
  - 执行 destroy_container()，total 降为 20
```

---

## 9. 边界情况处理

| 场景 | 处理策略 |
|------|---------|
| **warm 容器被外部 Docker 删除** | `warm_container()` 或 `ensure_running()` 调用 Docker API 时捕获 `NotFound`，删除 DB 记录，按无记录处理 |
| **用户频繁刷新页面** | 每次刷新 = WebSocket 断开 + 重连。如果刷新间隔 < 30min，容器始终 warm → running → warm，不会销毁 |
| **水位线全满且全是 running** | 新用户仍然 `create_container()`，总数暂时超过水位线。下次扫描时无 warm 可淘汰，保持现状（软上限） |
| **HTTP 请求时容器已是 warm** | 理论上不应发生（HTTP 不触发 warm），但如果发生，`ensure_running()` 会将 warm 改回 running |
| **并发请求竞争同一容器** | `ensure_running()` 中 `warm → running` 是单次 DB update，SQLAlchemy 事务保证一致性。多个请求同时到达时，只有一个成功改状态，其他请求在事务冲突后重试 |

---

## 10. 收益与代价

### 收益
- **降低资源空置**：用户离开后 30min 内容器保留（热复用），超过后自动释放 CPU + 内存
- **快速恢复**：同用户短期复用无需重新 `create_container`（秒级 vs 秒级~数秒级）
- **全局可控**：水位线防止容器总数无限膨胀，LRU 保证活跃用户体验

### 代价
- **代码复杂度**：新增 warm 状态、后台任务、状态转换逻辑
- **轻微延迟**：WebSocket 断开时多一次 DB update（warm_container）
- **不是真正的"池化"**：新用户首次创建仍需 `docker run`，无法像进程池那样亚秒级分配
