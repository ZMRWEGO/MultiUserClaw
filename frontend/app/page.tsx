'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ImperativePanelHandle } from 'react-resizable-panels';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Send,
  Plus,
  Trash2,
  Loader2,
  MessageSquare,
  User,
  Bot,
  Paperclip,
  X,
  PanelRightClose,
  PanelRightOpen,
  PanelLeftClose,
  PanelLeftOpen,
  Wifi,
  WifiOff,
  Maximize2,
  FileText,
  File as FileIcon,
  Image as ImageIcon,
  FileType2,
} from 'lucide-react';
import { useChatStore } from '@/lib/store';
import {
  listSessions,
  getSession,
  deleteSession,
  sendMessage,
  wsManager,
  listCommands,
  getStatus,
  uploadFile,
  getFileUrl,
  getAccessToken,
  type WorkspaceItem,
  type WorkspaceFile,
  type PreviewKind,
} from '@/lib/api';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable';
import { FileTree } from '@/components/FileTree';
import { FilePreview } from '@/components/FilePreview';
import { useFileEvents } from '@/hooks/useFileEvents';
import { useMentionPicker } from '@/hooks/useMentionPicker';
import type { ChatMessage, SlashCommand, FileAttachment } from '@/types';

export default function ChatPage() {
  const {
    sessionId,
    messages,
    isLoading,
    isThinking,
    sessions,
    setSessionId,
    setMessages,
    addMessage,
    setIsLoading,
    setSessions,
    clearMessages,
    setWsStatus,
    setIsThinking,
    setNanobotReady,
  } = useChatStore();

  const [input, setInput] = useState('');
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [showCommandPicker, setShowCommandPicker] = useState(false);
  const [pickerIndex, setPickerIndex] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingFiles, setPendingFiles] = useState<
    { file: File; id?: string; progress: number; error?: string }[]
  >([]);

  // Workspace file preview state
  const [selectedFile, setSelectedFile] = useState<WorkspaceItem | null>(null);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [previewDeleted, setPreviewDeleted] = useState(false);
  const [popupFile, setPopupFile] = useState<string | null>(null);
  const [scrollToPath, setScrollToPath] = useState<string | null>(null);
  const fileEvents = useFileEvents(true);

  // Mention picker (`@` triggers workspace file search)
  const mention = useMentionPicker({
    textareaRef,
    value: input,
    onCommit: ({ value, cursor, file }) => {
      setInput(value);
      // Set caret after React commits the new value.
      requestAnimationFrame(() => {
        const ta = textareaRef.current;
        if (ta) {
          ta.setSelectionRange(cursor, cursor);
          ta.focus();
        }
      });
      // Switch right-side preview to the picked file.
      const item: WorkspaceItem = {
        name: file.name,
        path: file.path,
        type: 'file',
        size: file.size,
        modified: file.modified,
        content_type: file.content_type,
      };
      setSelectedFile(item);
      setPreviewDeleted(false);
      // Tell FileTree to expand parent dirs and scroll into view.
      setScrollToPath(file.path);
    },
  });

  // Sidebar collapse state (client-only to avoid SSR mismatch)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarReady, setSidebarReady] = useState(false);
  const leftPanelRef = useRef<ImperativePanelHandle>(null);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);

  useEffect(() => {
    const saved = localStorage.getItem('sidebarCollapsed');
    if (saved === 'true') setSidebarCollapsed(true);
    setSidebarReady(true);
  }, []);

  useEffect(() => {
    if (!sidebarReady) return;
    if (sidebarCollapsed) {
      leftPanelRef.current?.collapse();
    } else {
      leftPanelRef.current?.expand();
    }
  }, [sidebarReady, sidebarCollapsed]);

  useEffect(() => {
    if (sidebarReady) {
      localStorage.setItem('sidebarCollapsed', String(sidebarCollapsed));
    }
  }, [sidebarCollapsed, sidebarReady]);

  const toggleSidebar = useCallback(() => {
    if (sidebarCollapsed) {
      leftPanelRef.current?.expand();
    } else {
      leftPanelRef.current?.collapse();
    }
  }, [sidebarCollapsed]);

  const toggleWorkspace = useCallback(() => {
    if (workspaceCollapsed) {
      rightPanelRef.current?.expand();
    } else {
      rightPanelRef.current?.collapse();
    }
  }, [workspaceCollapsed]);

  // When a file event hits the currently-previewed file, bump reload key (and detect delete).
  const previewReloadKey = useMemo(() => {
    if (!selectedFile) return 0;
    return fileEvents.lastEventForPath(selectedFile.path) ?? 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileEvents.events, selectedFile]);

  useEffect(() => {
    if (!selectedFile) {
      setPreviewDeleted(false);
      return;
    }
    // Look at the last event for this path to decide delete vs modify
    const recent = [...fileEvents.events]
      .reverse()
      .find((e) => e.path === selectedFile.path);
    if (recent && recent.event === 'deleted') {
      setPreviewDeleted(true);
    } else if (recent && recent.event === 'created') {
      // re-created — clear deleted flag
      setPreviewDeleted(false);
    } else if (recent && recent.event === 'modified') {
      setPreviewDeleted(false);
    }
  }, [fileEvents.events, selectedFile]);

  // Filtered commands shown in the picker
  const filteredCommands = useMemo(() => {
    if (!input.startsWith('/') || input.includes(' ')) return [];
    const filter = input.slice(1).toLowerCase();
    return commands.filter(
      (c) => c.name.startsWith(filter) || (filter === '' ? true : c.name.includes(filter))
    );
  }, [input, commands]);

  // Load commands on mount
  useEffect(() => {
    listCommands().then(setCommands).catch(() => { });
  }, []);

  // Show/reset picker when filtered list changes
  useEffect(() => {
    setShowCommandPicker(filteredCommands.length > 0);
    setPickerIndex(0);
  }, [filteredCommands]);

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, []);

  // Connect WebSocket when sessionId changes
  useEffect(() => {
    const wsSessionId = sessionId.startsWith('web:') ? sessionId.slice(4) : sessionId;
    wsManager.connect(wsSessionId);
    loadSessionMessages(sessionId);
  }, [sessionId]);

  // Register WebSocket handlers
  useEffect(() => {
    const unsubStatus = wsManager.onStatusChange(async (status) => {
      setWsStatus(status);
      if (status === 'connected') {
        loadSessionMessages(useChatStore.getState().sessionId);
        // Check if the nanobot user backend is actually running
        try {
          await getStatus();
          setNanobotReady(true);
        } catch {
          setNanobotReady(false);
        }
      } else {
        setNanobotReady(null);
      }
    });

    const unsubMessage = wsManager.onMessage((data) => {
      if (data.type === 'status' && data.status === 'thinking') {
        setIsThinking(true);
      } else if (data.type === 'message' && data.role === 'assistant') {
        setIsThinking(false);
        setIsLoading(false);
        addMessage({
          role: 'assistant',
          content: data.content || '',
          timestamp: new Date().toISOString(),
          attachments: data.attachments,
        });
        loadSessions();
      }
    });

    return () => {
      unsubStatus();
      unsubMessage();
    };
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  // Scroll picker selected item into view
  useEffect(() => {
    if (!showCommandPicker || !pickerRef.current) return;
    const item = pickerRef.current.children[pickerIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: 'nearest' });
  }, [pickerIndex, showCommandPicker]);

  const loadSessions = async () => {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch {
      // Backend may not be running yet
    }
  };

  const loadSessionMessages = async (key: string) => {
    try {
      const detail = await getSession(key);
      setMessages(detail.messages);
    } catch {
      setMessages([]);
    }
  };

  const selectCommand = useCallback((cmd: SlashCommand) => {
    // If command takes arguments, leave a trailing space for the user to type
    setInput(cmd.argument_hint ? `/${cmd.name} ` : `/${cmd.name}`);
    setShowCommandPicker(false);
    textareaRef.current?.focus();
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if ((!text && pendingFiles.length === 0) || isLoading) return;

    const readyFiles = pendingFiles.filter((p) => p.id && !p.error);
    const attachments: FileAttachment[] = readyFiles.map((p) => ({
      file_id: p.id!,
      name: p.file.name,
      content_type: p.file.type || 'application/octet-stream',
      size: p.file.size,
    }));

    setInput('');
    setPendingFiles([]);
    setShowCommandPicker(false);

    const msgContent = text || '(attached files)';
    const userMsg: ChatMessage = {
      role: 'user',
      content: msgContent,
      timestamp: new Date().toISOString(),
      attachments: attachments.length > 0 ? attachments : undefined,
    };
    addMessage(userMsg);
    setIsLoading(true);
    setIsThinking(false);

    if (wsManager.getStatus() === 'connected') {
      const wsPayload: Record<string, unknown> = { type: 'message', content: msgContent };
      if (attachments.length > 0) {
        wsPayload.attachments = attachments;
      }
      wsManager.sendRaw(wsPayload);
    } else {
      try {
        await sendMessage(msgContent, sessionId, attachments.length > 0 ? attachments : undefined);
      } catch {
        setIsLoading(false);
        addMessage({
          role: 'assistant',
          content: 'Failed to send. Check if the backend is running.',
          timestamp: new Date().toISOString(),
        });
      }
    }
  }, [input, isLoading, sessionId, pendingFiles]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Mention picker takes priority — when active it consumes nav/select/Esc keys.
    if (mention.handleKeyDown(e)) return;
    if (showCommandPicker && filteredCommands.length > 0) {
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setPickerIndex((i) => (i <= 0 ? filteredCommands.length - 1 : i - 1));
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setPickerIndex((i) => (i >= filteredCommands.length - 1 ? 0 : i + 1));
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing)) {
        e.preventDefault();
        selectCommand(filteredCommands[pickerIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowCommandPicker(false);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      if (!files.length) return;
      e.target.value = '';

      for (const file of files) {
        if (file.size > 50 * 1024 * 1024) {
          setPendingFiles((prev) => [
            ...prev,
            { file, progress: 0, error: 'File too large (max 50MB)' },
          ]);
          continue;
        }

        setPendingFiles((prev) => [...prev, { file, progress: 0 }]);

        try {
          const result = await uploadFile(file, sessionId, (pct) => {
            setPendingFiles((prev) =>
              prev.map((p) => (p.file === file ? { ...p, progress: pct } : p))
            );
          });
          setPendingFiles((prev) =>
            prev.map((p) =>
              p.file === file ? { ...p, id: result.file_id, progress: 100 } : p
            )
          );
        } catch (err: any) {
          setPendingFiles((prev) =>
            prev.map((p) =>
              p.file === file ? { ...p, error: err.message || 'Upload failed' } : p
            )
          );
        }
      }
    },
    [sessionId]
  );

  const removePendingFile = useCallback((file: File) => {
    setPendingFiles((prev) => prev.filter((p) => p.file !== file));
  }, []);

  const handleNewSession = () => {
    const id = `web:${Date.now()}`;
    setSessionId(id);
    clearMessages();
  };

  const handleDeleteSession = async (key: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteSession(key);
      if (key === sessionId) {
        setSessionId('web:default');
        clearMessages();
      }
      loadSessions();
    } catch {
      // ignore
    }
  };

  const handleSelectSession = (key: string) => {
    setSessionId(key);
  };

  const formatSessionName = (key: string) => {
    if (key.startsWith('web:')) {
      const id = key.slice(4);
      if (id === 'default') return '默认';
      const n = Number(id);
      if (!isNaN(n)) {
        return new Date(n).toLocaleDateString('zh-CN', {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        });
      }
      return id;
    }
    return key;
  };

  return (
    <>
      <div className="h-[calc(100vh-3.5rem)] overflow-hidden">
        <ResizablePanelGroup
          direction="horizontal"
          className="h-full"
        >
          {/* Left sidebar */}
          <ResizablePanel
            ref={leftPanelRef}
            collapsible
            collapsedSize={4}
            minSize={4}
            maxSize={25}
            defaultSize={15}
            onCollapse={() => setSidebarCollapsed(true)}
            onExpand={() => setSidebarCollapsed(false)}
            className="bg-card border-r border-border min-h-0"
          >
        <div className="flex h-full flex-col">
          {/* Header */}
          <div className="flex items-center gap-2 px-2 py-1.5 border-b border-border">
            <button
              type="button"
              onClick={toggleSidebar}
              className="p-1.5 rounded-md hover:bg-muted text-muted-foreground shrink-0"
              title={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
              data-testid="toggle-sidebar"
            >
              {sidebarCollapsed ? (
                <PanelRightOpen className="w-4 h-4" />
              ) : (
                <PanelLeftClose className="w-4 h-4" />
              )}
            </button>
            {!sidebarCollapsed && (
              <Button
                onClick={handleNewSession}
                variant="outline"
                className="w-full justify-start gap-2"
                size="sm"
              >
                <Plus className="w-4 h-4" />
                新对话
              </Button>
            )}
          </div>
          {!sidebarCollapsed && (
            <>
              <ScrollArea className="flex-1 min-h-0">
                <div className="p-2 space-y-1">
                  {sessions.length === 0 && (
                    <p className="text-xs text-muted-foreground px-2 py-4 text-center">
                      暂无对话记录
                    </p>
                  )}
                  {sessions.map((s) => (
                    <div
                      key={s.key}
                      onClick={() => handleSelectSession(s.key)}
                      className={`group flex items-center justify-between px-2 py-1.5 rounded-md cursor-pointer text-sm ${
                        s.key === sessionId
                          ? 'bg-accent text-accent-foreground'
                          : 'text-muted-foreground hover:bg-accent/50'
                      }`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        <MessageSquare className="w-3.5 h-3.5 flex-shrink-0" />
                        <span className="truncate">{formatSessionName(s.key)}</span>
                      </div>
                      <button
                        onClick={(e) => handleDeleteSession(s.key, e)}
                        className="opacity-0 group-hover:opacity-100 p-0.5 hover:text-destructive transition-opacity"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </>
          )}
        </div>
      </ResizablePanel>

      <ResizableHandle />

      {/* Main chat area */}
      <ResizablePanel minSize={30} defaultSize={55} className="min-w-0 min-h-0">
        <div className="flex h-full flex-col min-w-0">
          {/* Toggle workspace button (top-right) */}
          <div className="flex items-center justify-end px-2 py-1 border-b border-border bg-card">
            <button
              type="button"
              onClick={toggleWorkspace}
              className="p-1.5 rounded-md hover:bg-muted text-muted-foreground"
              title={workspaceCollapsed ? '展开工作区' : '折叠工作区'}
              data-testid="toggle-workspace"
            >
              {workspaceCollapsed ? (
                <PanelRightOpen className="w-4 h-4" />
              ) : (
                <PanelRightClose className="w-4 h-4" />
              )}
            </button>
          </div>

          {/* Messages */}
          <ScrollArea className="flex-1 min-h-0 px-4">
            <div className="max-w-3xl mx-auto py-4 space-y-4">
              {messages.length === 0 && !isThinking && (
                <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
                  <Bot className="w-12 h-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">nanobot</p>
                  <p className="text-sm">发送消息开始对话</p>
                </div>
              )}

              {messages.map((msg, i) => (
                <MessageBubble key={i} message={msg} />
              ))}

              {/* Thinking indicator */}
              {(isThinking ||
                (isLoading &&
                  messages.length > 0 &&
                  messages[messages.length - 1]?.role === 'user')) && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Bot className="w-5 h-5" />
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span className="text-sm">思考中...</span>
                  </div>
                )}

              <div ref={messagesEndRef} />
            </div>
          </ScrollArea>

          {/* Input area */}
          <div className="border-t border-border p-4">
            <div className="max-w-3xl mx-auto">
              {/* Pending file attachments */}
              {pendingFiles.length > 0 && (
                <div className="mb-2 space-y-1">
                  {pendingFiles.map((pf, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 px-3 py-1.5 bg-muted rounded-md text-sm"
                    >
                      <span className="truncate flex-1">
                        {pf.file.name}{' '}
                        <span className="text-muted-foreground">
                          ({(pf.file.size / 1024).toFixed(0)}KB)
                        </span>
                      </span>
                      {pf.error ? (
                        <span className="text-destructive text-xs">{pf.error}</span>
                      ) : pf.progress < 100 ? (
                        <div className="w-20 h-1.5 bg-muted-foreground/20 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary rounded-full transition-all"
                            style={{ width: `${pf.progress}%` }}
                          />
                        </div>
                      ) : (
                        <span className="text-green-500 text-xs">ready</span>
                      )}
                      <button
                        onClick={() => removePendingFile(pf.file)}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="relative flex gap-2">
                {/* Slash command picker */}
                {showCommandPicker && filteredCommands.length > 0 && (
                  <div
                    ref={pickerRef}
                    className="absolute bottom-full left-0 right-10 mb-2 bg-popover border border-border rounded-lg shadow-lg overflow-y-auto max-h-60 z-50"
                  >
                    {filteredCommands.map((cmd, i) => (
                      <button
                        key={cmd.name}
                        className={`w-full text-left px-3 py-2 flex items-center gap-2 text-sm transition-colors ${
                          i === pickerIndex
                            ? 'bg-accent text-accent-foreground'
                            : 'hover:bg-accent/50 text-foreground'
                        }`}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          selectCommand(cmd);
                        }}
                        onMouseEnter={() => setPickerIndex(i)}
                      >
                        <span className="font-mono font-semibold text-primary shrink-0">
                          /{cmd.name}
                        </span>
                        {cmd.argument_hint && (
                          <span className="text-muted-foreground text-xs shrink-0">
                            {cmd.argument_hint}
                          </span>
                        )}
                        <span className="text-muted-foreground text-xs truncate ml-auto">
                          {cmd.description}
                        </span>
                        {cmd.plugin_name !== 'builtin' && (
                          <span className={`text-xs px-1 rounded shrink-0 ${
                            cmd.plugin_name === 'skill'
                              ? 'bg-blue-500/10 text-blue-500'
                              : 'bg-muted'
                          }`}>
                            {cmd.plugin_name}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                )}

                {/* @-mention workspace file picker */}
                {mention.state.active && !showCommandPicker && (
                  <div
                    className="absolute bottom-full left-0 right-10 mb-2 bg-popover border border-border rounded-lg shadow-lg overflow-y-auto max-h-60 z-50"
                    data-testid="mention-picker"
                  >
                    {mention.loading && mention.items.length === 0 && (
                      <div className="px-3 py-2 text-sm text-muted-foreground flex items-center gap-2">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        搜索中…
                      </div>
                    )}
                    {!mention.loading && mention.items.length === 0 && (
                      <div className="px-3 py-2 text-sm text-muted-foreground">
                        未找到匹配的文件
                      </div>
                    )}
                    {mention.items.map((item, i) => (
                      <button
                        key={item.path}
                        className={`w-full text-left px-3 py-2 flex items-center gap-2 text-sm transition-colors ${
                          i === mention.pickIndex
                            ? 'bg-accent text-accent-foreground'
                            : 'hover:bg-accent/50 text-foreground'
                        }`}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          mention.pick(i);
                        }}
                        onMouseEnter={() => mention.setPickIndex(i)}
                        data-testid="mention-item"
                        data-path={item.path}
                      >
                        <FileIconForKind kind={item.preview_kind} />
                        <span className="font-mono text-foreground truncate shrink-0">
                          {item.name}
                        </span>
                        <span className="text-muted-foreground text-xs truncate ml-auto">
                          {item.path}
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={handleFileSelect}
                />

                <Button
                  onClick={() => fileInputRef.current?.click()}
                  variant="ghost"
                  size="icon"
                  className="h-10 w-10 flex-shrink-0"
                  title="Attach file"
                >
                  <Paperclip className="w-4 h-4" />
                </Button>

                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    mention.onValueChange(e.target.value, e.target.selectionStart);
                  }}
                  onSelect={(e) => {
                    mention.onSelectionChange((e.target as HTMLTextAreaElement).selectionStart);
                  }}
                  onKeyDown={handleKeyDown}
                  placeholder="输入消息或 / 呼出命令…（Enter 发送，Shift+Enter 换行）"
                  rows={1}
                  className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  style={{ minHeight: '40px', maxHeight: '200px' }}
                  onInput={(e) => {
                    const target = e.target as HTMLTextAreaElement;
                    target.style.height = 'auto';
                    target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
                  }}
                />
                <Button
                  onClick={handleSend}
                  disabled={
                    (!input.trim() && pendingFiles.filter((p) => p.id && !p.error).length === 0) ||
                    isLoading
                  }
                  size="icon"
                  className="h-10 w-10 flex-shrink-0"
                >
                  <Send className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>
      </ResizablePanel>

      <ResizableHandle />

      {/* Right panel: workspace */}
      <ResizablePanel
        ref={rightPanelRef}
        collapsible
        collapsedSize={0}
        minSize={5}
        maxSize={50}
        defaultSize={30}
        onCollapse={() => setWorkspaceCollapsed(true)}
        onExpand={() => setWorkspaceCollapsed(false)}
        className="bg-card border-l border-border min-h-0"
      >
        <div className="flex h-full flex-col">
          {/* Inner horizontal panels: FileTree | FilePreview */}
          <ResizablePanelGroup direction="horizontal" className="flex-1 min-h-0">
            <ResizablePanel minSize={20} defaultSize={35} className="min-w-0 min-h-0 flex flex-col">
              <FileTree
                selectedPath={selectedFile?.path ?? null}
                onSelectFile={(item) => {
                  setSelectedFile(item);
                  setPreviewDeleted(false);
                }}
                events={fileEvents.events}
                refreshKey={fileEvents.snapshotKey}
                scrollToPath={scrollToPath}
              />
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel minSize={20} defaultSize={65} className="min-w-0 min-h-0 flex flex-col">
              {selectedFile && (
                <div className="flex items-center justify-between px-2 py-1.5 border-b border-border bg-muted/20 flex-shrink-0">
                  <span className="text-xs text-muted-foreground truncate pr-2">
                    {selectedFile.name}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPopupFile(selectedFile.path)}
                    className="p-1 rounded-md hover:bg-muted text-muted-foreground shrink-0"
                    title="弹出预览"
                    data-testid="popup-preview"
                  >
                    <Maximize2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
              <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
                <FilePreview
                  path={selectedFile?.path ?? null}
                  reloadKey={previewReloadKey}
                  deleted={previewDeleted}
                />
              </div>
            </ResizablePanel>
          </ResizablePanelGroup>

          {/* WS status bar */}
          <div
            className="flex-shrink-0 flex items-center gap-1 px-2 py-1 border-t border-border bg-muted/20 text-xs text-muted-foreground"
            data-testid="ws-status"
            data-status={fileEvents.status}
          >
            {fileEvents.status === 'connected' ? (
              <>
                <Wifi className="w-3 h-3 text-green-500" />
                <span>实时同步已连接</span>
              </>
            ) : fileEvents.status === 'connecting' ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                <span>连接中…</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3 h-3 text-amber-500" />
                <span>未连接（重连中）</span>
              </>
            )}
          </div>
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
    </div>

    {/* Popup preview dialog */}
    <Dialog open={!!popupFile} onOpenChange={(open) => !open && setPopupFile(null)}>
      <DialogContent className="max-w-[90vw] w-[90vw] h-[90vh] max-h-[90vh] p-0 flex flex-col overflow-hidden" data-testid="preview-dialog">
        <DialogHeader className="px-4 py-3 border-b border-border flex-shrink-0">
          <DialogTitle className="text-sm font-medium truncate">{popupFile}</DialogTitle>
        </DialogHeader>
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <FilePreview
            path={popupFile}
            reloadKey={previewReloadKey}
          />
        </div>
      </DialogContent>
    </Dialog>
  </>);
}

function FileIconForKind({ kind }: { kind: PreviewKind }) {
  if (kind === 'image') {
    return <ImageIcon className="w-3.5 h-3.5 flex-shrink-0 text-muted-foreground" />;
  }
  if (kind === 'pdf' || kind === 'docx' || kind === 'xlsx') {
    return <FileType2 className="w-3.5 h-3.5 flex-shrink-0 text-muted-foreground" />;
  }
  if (kind === 'markdown' || kind === 'json' || kind === 'html' || kind === 'text') {
    return <FileText className="w-3.5 h-3.5 flex-shrink-0 text-muted-foreground" />;
  }
  return <FileIcon className="w-3.5 h-3.5 flex-shrink-0 text-muted-foreground" />;
}

function AuthImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [blobUrl, setBlobUrl] = React.useState<string | null>(null);

  React.useEffect(() => {
    const token = getAccessToken();
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    let revoke: string | null = null;
    fetch(src, { headers })
      .then((res) => res.blob())
      .then((blob) => {
        revoke = URL.createObjectURL(blob);
        setBlobUrl(revoke);
      })
      .catch(() => { });

    return () => {
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [src]);

  if (!blobUrl) return <div className="w-32 h-32 bg-muted animate-pulse rounded" />;
  return <img src={blobUrl} alt={alt} className={className} />;
}

function MessageBubble({
  message,
  isStreaming,
}: {
  message: ChatMessage;
  isStreaming?: boolean;
}) {
  const isUser = message.role === 'user';

  const extractText = (content: any) => {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content.map(c => c.text || '').join('\n');
    }
    return String(content || '');
  };
  const textContent = extractText(message.content);

  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : ''}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Bot className="w-4 h-4 text-primary" />
        </div>
      )}
      <div
        className={`rounded-lg px-4 py-2 max-w-[80%] ${isUser ? 'bg-primary text-primary-foreground' : 'bg-card border border-border'
          }`}
      >
        {/* Attachments */}
        {message.attachments && message.attachments.length > 0 && (
          <div className="mb-2 space-y-2">
            {message.attachments.map((att) => {
              const fileUrl = getFileUrl(att.file_id);
              if (att.content_type.startsWith('image/')) {
                return (
                  <a key={att.file_id} href={fileUrl} target="_blank" rel="noopener noreferrer">
                    <AuthImage
                      src={fileUrl}
                      alt={att.name}
                      className="max-w-xs max-h-60 rounded border border-border/50 cursor-pointer hover:opacity-90"
                    />
                  </a>
                );
              }
              return (
                <a
                  key={att.file_id}
                  href={fileUrl}
                  download={att.name}
                  className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm ${isUser
                      ? 'bg-primary-foreground/10 hover:bg-primary-foreground/20'
                      : 'bg-muted hover:bg-muted/80'
                    }`}
                >
                  <Paperclip className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="truncate">{att.name}</span>
                  {att.size && (
                     <span className="text-xs opacity-70 flex-shrink-0">
                       {att.size > 1024 * 1024
                         ? `${(att.size / 1024 / 1024).toFixed(1)}MB`
                         : `${(att.size / 1024).toFixed(0)}KB`}
                     </span>
                  )}
                </a>
              );
            })}
          </div>
        )}

        {/* Text content */}
        {isUser ? (
          <p className="text-sm whitespace-pre-wrap">{textContent}</p>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                table: ({ children, ...props }) => (
                  <div className="my-3 overflow-x-auto rounded-lg border border-border">
                    <table className="w-full border-collapse text-sm" {...props}>
                      {children}
                    </table>
                  </div>
                ),
                thead: ({ children, ...props }) => (
                  <thead className="bg-muted/60" {...props}>
                    {children}
                  </thead>
                ),
                th: ({ children, ...props }) => (
                  <th className="px-3 py-2 text-left font-semibold text-foreground border-b border-border" {...props}>
                    {children}
                  </th>
                ),
                td: ({ children, ...props }) => (
                  <td className="px-3 py-2 border-b border-border/50" {...props}>
                    {children}
                  </td>
                ),
                tr: ({ children, ...props }) => (
                  <tr className="hover:bg-muted/30 transition-colors" {...props}>
                    {children}
                  </tr>
                ),
              }}
            >
              {textContent}
            </ReactMarkdown>
            {isStreaming && (
              <span className="inline-block w-1.5 h-4 bg-foreground/60 animate-pulse ml-0.5 align-middle" />
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center flex-shrink-0 mt-0.5">
          <User className="w-4 h-4" />
        </div>
      )}
    </div>
  );
}
