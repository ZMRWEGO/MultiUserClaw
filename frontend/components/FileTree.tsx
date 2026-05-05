'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FileText,
  File as FileIcon,
  Loader2,
  RefreshCw,
  FileCode,
  FileJson,
  FileImage,
  FileVideo,
  FileAudio,
  FileArchive,
  FileSpreadsheet,
  FileType,
  Table,
  Terminal,
  Braces,
  Download,
} from 'lucide-react';
import { toast } from 'sonner';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  archiveWorkspacePath,
  browseWorkspace,
  downloadWorkspacePath,
  type WorkspaceItem,
} from '@/lib/api';
import type { FileEvent } from '@/hooks/useFileEvents';

interface FileTreeProps {
  /** Currently selected file path, or null */
  selectedPath: string | null;
  /** Called when a file (not directory) is selected */
  onSelectFile: (item: WorkspaceItem) => void;
  /** Optional incoming file events for incremental updates */
  events?: FileEvent[];
  /** Bumped externally to force a full reload (e.g. WS reconnect) */
  refreshKey?: number;
  /** When set, expand parent dirs along this path and scroll the node into view. */
  scrollToPath?: string | null;
}

interface NodeState {
  /** Children, undefined = not loaded */
  children?: WorkspaceItem[];
  loading?: boolean;
  expanded?: boolean;
  error?: string;
}

/** Map from directory path -> NodeState. Root directory uses the empty string "". */
type Tree = Record<string, NodeState>;

export function FileTree({
  selectedPath,
  onSelectFile,
  events,
  refreshKey,
  scrollToPath,
}: FileTreeProps) {
  const [tree, setTree] = useState<Tree>({ '': {} });
  /** Path of a directory currently being archived; used to show a spinner. */
  const [archivingPath, setArchivingPath] = useState<string | null>(null);

  const downloadDirectory = useCallback((dirPath: string) => {
    if (archivingPath) return; // already compressing something else
    setArchivingPath(dirPath);
    const toastId = `archive:${dirPath}`;
    toast.loading('正在压缩 …', { id: toastId });
    archiveWorkspacePath(dirPath, {
      onSuccess: () => {
        toast.success('压缩完成，已开始下载', { id: toastId });
      },
      onError: (msg) => {
        toast.error(`压缩失败：${msg}`, { id: toastId });
      },
    }).finally(() => {
      setArchivingPath(null);
    });
  }, [archivingPath]);

  const loadDir = useCallback(async (path: string) => {
    setTree((prev) => ({ ...prev, [path]: { ...prev[path], loading: true, error: undefined } }));
    try {
      const result = await browseWorkspace(path);
      setTree((prev) => ({
        ...prev,
        [path]: { ...prev[path], children: result.items, loading: false },
      }));
    } catch (err: any) {
      setTree((prev) => ({
        ...prev,
        [path]: {
          ...prev[path],
          loading: false,
          error: err?.message || 'load failed',
        },
      }));
    }
  }, []);

  // Initial load of root
  useEffect(() => {
    loadDir('');
  }, [loadDir]);

  // External refresh: re-load root + reload all currently expanded directories
  useEffect(() => {
    if (refreshKey === undefined) return;
    setTree((prev) => {
      const expanded = Object.entries(prev)
        .filter(([_, s]) => s.expanded || _ === '')
        .map(([k]) => k);
      // Trigger loads outside setState
      Promise.resolve().then(() => {
        for (const p of expanded) loadDir(p);
      });
      return prev;
    });
  }, [refreshKey, loadDir]);

  // Apply incoming file events
  const lastSeenRef = React.useRef(0);
  // Latest tree ref for use inside async effects (scrollToPath).
  const treeRef = React.useRef(tree);
  React.useEffect(() => {
    treeRef.current = tree;
  }, [tree]);
  useEffect(() => {
    if (!events || events.length === 0) return;
    const fresh = events.slice(lastSeenRef.current);
    lastSeenRef.current = events.length;
    if (fresh.length === 0) return;

    setTree((prev) => {
      const next = { ...prev };
      for (const ev of fresh) {
        const parent = ev.path.includes('/')
          ? ev.path.slice(0, ev.path.lastIndexOf('/'))
          : '';
        const parentState = next[parent];
        // Only patch if parent's children list is currently loaded
        if (!parentState || !parentState.children) {
          continue;
        }

        if (ev.event === 'created') {
          if (parentState.children.some((c) => c.path === ev.path)) continue;
          const newItem: WorkspaceItem = {
            name: ev.path.split('/').pop() || ev.path,
            path: ev.path,
            type: ev.is_directory ? 'directory' : 'file',
            size: ev.size ?? null,
            modified: ev.modified || new Date().toISOString(),
          };
          // Insert maintaining sort: dirs first, then by name asc
          const merged = [...parentState.children, newItem].sort((a, b) => {
            if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
            return a.name.localeCompare(b.name);
          });
          next[parent] = { ...parentState, children: merged };
        } else if (ev.event === 'deleted') {
          next[parent] = {
            ...parentState,
            children: parentState.children.filter((c) => c.path !== ev.path),
          };
          // If a directory was deleted, drop its cached state too
          if (ev.is_directory && next[ev.path]) {
            delete next[ev.path];
          }
        } else if (ev.event === 'modified') {
          next[parent] = {
            ...parentState,
            children: parentState.children.map((c) =>
              c.path === ev.path
                ? { ...c, size: ev.size ?? c.size, modified: ev.modified ?? c.modified }
                : c
            ),
          };
        } else if (ev.event === 'moved' && ev.old_path) {
          const oldParent = ev.old_path.includes('/')
            ? ev.old_path.slice(0, ev.old_path.lastIndexOf('/'))
            : '';
          // Remove old
          if (next[oldParent]?.children) {
            next[oldParent] = {
              ...next[oldParent],
              children: next[oldParent].children!.filter((c) => c.path !== ev.old_path),
            };
          }
          // Insert new (only if its parent is loaded)
          const newParentState = next[parent];
          if (newParentState?.children) {
            const newItem: WorkspaceItem = {
              name: ev.path.split('/').pop() || ev.path,
              path: ev.path,
              type: ev.is_directory ? 'directory' : 'file',
              size: ev.size ?? null,
              modified: ev.modified || new Date().toISOString(),
            };
            const merged = [...newParentState.children, newItem].sort((a, b) => {
              if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
              return a.name.localeCompare(b.name);
            });
            next[parent] = { ...newParentState, children: merged };
          }
        }
      }
      return next;
    });
  }, [events]);

  const toggleDir = useCallback(
    (path: string) => {
      setTree((prev) => {
        const cur = prev[path] || {};
        const expanded = !cur.expanded;
        const next = { ...prev, [path]: { ...cur, expanded } };
        // Lazy-load on first expand
        if (expanded && cur.children === undefined) {
          Promise.resolve().then(() => loadDir(path));
        }
        return next;
      });
    },
    [loadDir]
  );

  // Auto-expand parent chain + scroll into view when scrollToPath is set.
  useEffect(() => {
    if (!scrollToPath) return;
    let cancelled = false;
    (async () => {
      const parts = scrollToPath.split('/').slice(0, -1); // parent dirs only
      let cur = '';
      for (const seg of parts) {
        const next = cur ? `${cur}/${seg}` : seg;
        if (cancelled) return;
        if (treeRef.current[next]?.children === undefined) {
          try {
            await loadDir(next);
          } catch {
            // Swallow — partial expansion is acceptable
            return;
          }
        }
        if (cancelled) return;
        setTree((prev) => {
          const cur2 = prev[next] ?? {};
          if (cur2.expanded) return prev;
          return { ...prev, [next]: { ...cur2, expanded: true } };
        });
        cur = next;
      }
      // Wait for the next frame so the DOM reflects the expanded state.
      if (cancelled) return;
      requestAnimationFrame(() => {
        if (cancelled) return;
        const safe = typeof CSS !== 'undefined' && (CSS as any).escape
          ? CSS.escape(scrollToPath)
          : scrollToPath.replace(/"/g, '\\"');
        const el = document.querySelector(`[data-path="${safe}"]`);
        el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [scrollToPath, loadDir]);

  const renderItems = (parentPath: string, depth: number): React.ReactNode => {
    const state = tree[parentPath];
    if (!state) return null;
    if (state.loading && !state.children) {
      return (
        <div
          className="flex items-center gap-2 text-xs text-muted-foreground py-1"
          style={{ paddingLeft: depth * 12 + 8 }}
        >
          <Loader2 className="w-3 h-3 animate-spin" />
          加载中…
        </div>
      );
    }
    if (state.error) {
      return (
        <div
          className="text-xs text-destructive py-1"
          style={{ paddingLeft: depth * 12 + 8 }}
        >
          加载失败：{state.error}
        </div>
      );
    }
    if (!state.children || state.children.length === 0) {
      if (depth === 0) {
        return (
          <div className="text-xs text-muted-foreground py-1 px-2">工作区为空</div>
        );
      }
      return null;
    }

    return state.children.map((item) => {
      const isDir = item.type === 'directory';
      const childState = tree[item.path];
      const expanded = !!childState?.expanded;
      const isSelected = !isDir && item.path === selectedPath;

      return (
        <React.Fragment key={item.path}>
          <div
            className={`group flex items-center gap-1 px-2 py-1 text-xs hover:bg-muted/60 ${
              isSelected ? 'bg-accent text-accent-foreground' : ''
            }`}
            style={{ paddingLeft: depth * 12 + 8 }}
            data-testid={isDir ? 'dir-item' : 'file-item'}
            data-path={item.path}
          >
            <button
              type="button"
              onClick={() => (isDir ? toggleDir(item.path) : onSelectFile(item))}
              className="flex-1 flex items-center gap-1 min-w-0 text-left"
            >
              {isDir ? (
                expanded ? (
                  <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />
                )
              ) : (
                <span className="w-3.5 flex-shrink-0" />
              )}
              {isDir ? (
                <Folder className="w-3.5 h-3.5 flex-shrink-0 text-amber-500" />
              ) : (
                (() => {
                  const { Icon, color } = getFileIconInfo(item.name);
                  return <Icon className={`w-3.5 h-3.5 flex-shrink-0 ${color}`} />;
                })()
              )}
              <span className="truncate">{item.name}</span>
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (isDir) {
                  downloadDirectory(item.path);
                } else {
                  downloadWorkspacePath(item.path).catch(() => {});
                }
              }}
              disabled={isDir && archivingPath === item.path}
              className="opacity-0 group-hover:opacity-100 p-0.5 hover:text-foreground text-muted-foreground transition-opacity shrink-0 disabled:cursor-progress"
              title={isDir ? '下载压缩包' : '下载'}
              data-testid={isDir ? 'download-dir' : 'download-file'}
            >
              {isDir && archivingPath === item.path ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Download className="w-3 h-3" />
              )}
            </button>
          </div>
          {isDir && expanded && renderItems(item.path, depth + 1)}
        </React.Fragment>
      );
    });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-border bg-muted/20">
        <span className="text-xs text-muted-foreground font-medium">工作区</span>
        <button
          type="button"
          onClick={() => loadDir('')}
          className="p-1 hover:text-foreground text-muted-foreground"
          title="刷新"
          data-testid="refresh-tree"
        >
          <RefreshCw className="w-3 h-3" />
        </button>
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="py-1">{renderItems('', 0)}</div>
      </ScrollArea>
    </div>
  );
}

function getFileIconInfo(name: string): { Icon: React.ComponentType<{ className?: string }>; color: string } {
  const ext = name.toLowerCase().match(/\.[^./]+$/)?.[0] ?? '';

  // Images
  if (['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.ico', '.svg', '.avif', '.tiff'].includes(ext)) {
    return { Icon: FileImage, color: 'text-sky-500' };
  }
  // Videos
  if (['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v', '.mpg', '.mpeg'].includes(ext)) {
    return { Icon: FileVideo, color: 'text-purple-500' };
  }
  // Audio
  if (['.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma', '.opus', '.aiff'].includes(ext)) {
    return { Icon: FileAudio, color: 'text-pink-500' };
  }
  // Archives
  if (['.zip', '.tar', '.gz', '.tgz', '.bz2', '.rar', '.7z', '.xz', '.lz4', '.zst'].includes(ext)) {
    return { Icon: FileArchive, color: 'text-amber-600' };
  }
  // Spreadsheets
  if (['.xls', '.xlsx', '.ods', '.numbers'].includes(ext)) {
    return { Icon: FileSpreadsheet, color: 'text-green-600' };
  }
  // CSV
  if (ext === '.csv') {
    return { Icon: Table, color: 'text-green-500' };
  }
  // JSON
  if (ext === '.json' || ext === '.jsonl') {
    return { Icon: FileJson, color: 'text-yellow-500' };
  }
  // YAML / TOML / INI / Config
  if (['.yaml', '.yml'].includes(ext)) {
    return { Icon: Braces, color: 'text-red-400' };
  }
  if (['.xml', '.xsd', '.xsl'].includes(ext)) {
    return { Icon: Braces, color: 'text-orange-500' };
  }
  if (['.toml', '.ini', '.conf', '.cfg', '.env'].includes(ext)) {
    return { Icon: FileText, color: 'text-slate-400' };
  }
  // Python
  if (['.py', '.pyw', '.pyi', '.ipynb'].includes(ext)) {
    return { Icon: FileCode, color: 'text-yellow-500' };
  }
  // JavaScript
  if (['.js', '.jsx', '.mjs', '.cjs'].includes(ext)) {
    return { Icon: FileCode, color: 'text-yellow-400' };
  }
  // TypeScript
  if (['.ts', '.tsx', '.mts', '.cts'].includes(ext)) {
    return { Icon: FileCode, color: 'text-blue-500' };
  }
  // Go
  if (ext === '.go') {
    return { Icon: FileCode, color: 'text-cyan-500' };
  }
  // Rust
  if (['.rs', '.rlib'].includes(ext)) {
    return { Icon: FileCode, color: 'text-orange-500' };
  }
  // Java
  if (['.java', '.kt', '.scala', '.groovy'].includes(ext)) {
    return { Icon: FileCode, color: 'text-red-400' };
  }
  // C / C++
  if (['.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.hh'].includes(ext)) {
    return { Icon: FileCode, color: 'text-slate-500' };
  }
  // HTML
  if (['.html', '.htm', '.xhtml'].includes(ext)) {
    return { Icon: FileCode, color: 'text-orange-500' };
  }
  // CSS
  if (['.css', '.scss', '.sass', '.less', '.styl'].includes(ext)) {
    return { Icon: FileCode, color: 'text-blue-400' };
  }
  // Shell
  if (['.sh', '.bash', '.zsh', '.fish', '.ps1'].includes(ext)) {
    return { Icon: Terminal, color: 'text-green-500' };
  }
  // SQL
  if (['.sql', '.sqlite', '.sqlite3'].includes(ext)) {
    return { Icon: FileCode, color: 'text-slate-400' };
  }
  // PHP
  if (['.php', '.phtml', '.php3', '.php4', '.php5'].includes(ext)) {
    return { Icon: FileCode, color: 'text-indigo-400' };
  }
  // Ruby
  if (['.rb', '.erb', '.gemspec'].includes(ext)) {
    return { Icon: FileCode, color: 'text-red-500' };
  }
  // Markdown
  if (['.md', '.markdown', '.mdx', '.rst'].includes(ext)) {
    return { Icon: FileText, color: 'text-sky-500' };
  }
  // PDF
  if (ext === '.pdf') {
    return { Icon: FileText, color: 'text-red-500' };
  }
  // Word
  if (['.doc', '.docx', '.odt', '.rtf'].includes(ext)) {
    return { Icon: FileText, color: 'text-blue-600' };
  }
  // PowerPoint
  if (['.ppt', '.pptx', '.odp', '.key'].includes(ext)) {
    return { Icon: FileText, color: 'text-orange-600' };
  }
  // Fonts
  if (['.ttf', '.otf', '.woff', '.woff2', '.eot'].includes(ext)) {
    return { Icon: FileType, color: 'text-purple-400' };
  }
  // Plain text / log
  if (['.txt', '.log', '.diff', '.patch'].includes(ext)) {
    return { Icon: FileText, color: 'text-slate-400' };
  }
  // Binary / executable
  if (['.exe', '.dll', '.so', '.dylib', '.dmg', '.deb', '.rpm', '.pkg', '.msi', '.appimage'].includes(ext)) {
    return { Icon: FileIcon, color: 'text-slate-500' };
  }

  // Default
  return { Icon: FileIcon, color: 'text-muted-foreground' };
}
