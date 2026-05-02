# 排查记录：Gateway 返回 503 Service Unavailable

## 现象

- 直接访问 nanobot web（`curl http://127.0.0.1:18080/api/status`）→ **200 OK**
- 通过 Platform Gateway（`8080`）访问 `/api/nanobot/status`（带有效 JWT）→ **503 Service Unavailable**，返回空体
- 不带 token 访问 Gateway → 401（说明 Gateway 本身在运行，路由匹配正常）

## 排查过程

### 1. 确认 nanobot web 是否启动

```bash
lsof -i :18080
curl http://127.0.0.1:18080/api/ping
```

结果：PID 正常，端口监听，直接 curl 返回 200。

### 2. 确认 Gateway 配置

检查 `PLATFORM_DEV_NANOBOT_URL` 环境变量是否被正确读取：

```bash
ps eww <gateway_pid> | grep DEV_NANOBOT
```

环境变量存在，但 `platform/app/config.py` 中 `model_config = {"env_prefix": "PLATFORM_"}` 写法在 **pydantic-settings v2** 中不兼容，导致配置未生效。

**修复**：改为 `SettingsConfigDict(env_prefix="PLATFORM_")`。

### 3. 确认请求是否到达 proxy.py

在 `platform/app/routes/proxy.py` 中添加日志：

- `_container_url` 返回的 URL
- `target_url`
- httpx 响应状态码

日志输出：

```
[_container_url] local dev mode -> http://127.0.0.1:18080
[proxy_http] target_url=http://127.0.0.1:18080/api/status
[proxy_http] resp status=503 ct=
```

这说明：
1. Gateway 转发目标是对的
2. **nanobot web（18080）对 Gateway 发过来的请求返回了 503**
3. 但 curl 直接访问 18080 是正常的

### 4. 隔离变量：curl vs httpx

写了一个测试脚本 `platform/tests/test_nanobot_status_503.py`，用不同客户端请求同一地址：

```python
import httpx
resp = httpx.get('http://127.0.0.1:18080/api/status')
print(resp.status_code)  # -> 503
print(resp.headers)       # -> {'connection': 'close', 'proxy-connection': 'close', 'content-length': '0'}
```

关键发现：
- `curl` → 200
- `httpx` → 503，且响应头中有 **`proxy-connection: close`**

### 5. 验证代理假设

```python
import httpx
# 默认
httpx.get('http://127.0.0.1:18080/api/status')  # -> 503

# 显式禁用代理（mounts 方式）
with httpx.Client(mounts={'http://': None, 'https://': None}) as client:
    client.get('http://127.0.0.1:18080/api/status')  # -> 200
```

**结论**：httpx 自动走了**系统代理**（macOS 上的 Clash/Surge/Shadowsocks 等），代理服务器无法正确转发 `127.0.0.1` 的本地请求，返回了 503。curl 默认不走系统代理，所以正常。

## 根因

**系统代理软件（如 Clash、Surge、Shadowsocks 等）拦截了 httpx 发出的本地请求。**

- Platform Gateway 使用 `httpx.AsyncClient` 转发请求到 nanobot web
- httpx 自动读取系统代理配置，将 `127.0.0.1:18080` 的请求发给代理
- 代理服务器对本地回环地址处理异常，返回 503
- curl 默认不读取系统代理，直连成功

## 解决方案

### 推荐：关闭系统代理（或添加绕过规则）

在系统代理工具中添加 **绕过规则**，排除 `127.0.0.1`、`localhost` 和本地开发端口：

```
127.0.0.1
localhost
192.168.*
10.*
172.*
```

或者临时关闭系统代理，测试确认：

```bash
# macOS 关闭系统代理（终端级别）
unset http_proxy https_proxy all_proxy
```

### 备选：代码中强制禁用代理（不推荐）

如果必须在代码层面处理，可以给 `httpx.AsyncClient` 加 `mounts`：

```python
async with httpx.AsyncClient(
    timeout=120.0,
    mounts={"http://": None, "https://": None}
) as client:
    ...
```

但这种方式会强制所有环境都直连，可能破坏需要代理的部署场景。建议优先在系统代理层面解决。

## 验证方法

```bash
# 1. 确认当前代理环境变量
env | grep -i proxy

# 2. 用 curl 测试（不走代理）
curl -s -w "\nHTTP %{http_code}\n" http://127.0.0.1:18080/api/status

# 3. 用 httpx 测试（可能走代理）
python -c "import httpx; print(httpx.get('http://127.0.0.1:18080/api/status').status_code)"

# 4. 用 httpx 禁用代理测试
python -c "
import httpx
with httpx.Client(mounts={'http://': None, 'https://': None}) as c:
    print(c.get('http://127.0.0.1:18080/api/status').status_code)
"
```

如果步骤 3 返回 503，步骤 4 返回 200，即可确认是系统代理导致。

## 相关代码文件

- `platform/app/routes/proxy.py` — Gateway 反向代理逻辑
- `platform/app/config.py` — pydantic-settings 配置加载（已修复 model_config 写法）
- `nanobot/channels/web.py` — nanobot web 的 uvicorn 启动配置
