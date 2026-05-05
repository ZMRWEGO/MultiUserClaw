'use client';

import { useEffect, useRef, useState } from 'react';
import { getAccessToken } from '@/lib/api';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';

export type FileEventType = 'created' | 'modified' | 'deleted' | 'moved';

export interface FileEvent {
  type: 'file_event';
  event: FileEventType;
  path: string;
  old_path?: string;
  is_directory: boolean;
  size?: number;
  modified?: string;
}

export type FileWsStatus = 'disconnected' | 'connecting' | 'connected';

interface UseFileEventsResult {
  status: FileWsStatus;
  events: FileEvent[];
  /** Last event timestamp (ms since epoch) for a given workspace path, or null */
  lastEventForPath: (path: string) => number | null;
  /** Bump-on-snapshot counter — useful for triggering tree refresh on reconnect */
  snapshotKey: number;
}

const MAX_EVENTS = 200;

function getWsBase(): string {
  const url = new URL(API_URL);
  const protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${url.host}`;
}

/**
 * Subscribe to /api/nanobot/ws/files and stream file events.
 * Reconnects with exponential backoff (1s → 30s).
 */
export function useFileEvents(enabled: boolean = true): UseFileEventsResult {
  const [status, setStatus] = useState<FileWsStatus>('disconnected');
  const [events, setEvents] = useState<FileEvent[]>([]);
  const [snapshotKey, setSnapshotKey] = useState(0);
  const lastEventTsRef = useRef<Map<string, number>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef(1000);
  const intentionalCloseRef = useRef(false);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!enabled) return;
    intentionalCloseRef.current = false;

    const cleanup = () => {
      if (pingTimerRef.current) {
        clearInterval(pingTimerRef.current);
        pingTimerRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        if (
          wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING
        ) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
    };

    const connect = () => {
      cleanup();
      const token = getAccessToken() || '';
      const url = `${getWsBase()}/api/nanobot/ws/files?token=${encodeURIComponent(token)}`;
      setStatus('connecting');

      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        // Browser refused (e.g. invalid URL) — schedule reconnect
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectDelayRef.current = 1000;
        setStatus('connected');
        // Periodic ping
        pingTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === 'pong') return;
          if (data.type === 'snapshot') {
            setSnapshotKey((k) => k + 1);
            return;
          }
          if (data.type === 'file_event') {
            const ev = data as FileEvent;
            lastEventTsRef.current.set(ev.path, Date.now());
            if (ev.old_path) {
              lastEventTsRef.current.set(ev.old_path, Date.now());
            }
            setEvents((prev) => {
              const next = [...prev, ev];
              if (next.length > MAX_EVENTS) {
                return next.slice(next.length - MAX_EVENTS);
              }
              return next;
            });
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        if (pingTimerRef.current) {
          clearInterval(pingTimerRef.current);
          pingTimerRef.current = null;
        }
        if (!intentionalCloseRef.current) {
          setStatus('disconnected');
          scheduleReconnect();
        }
      };

      ws.onerror = () => {
        // onclose will fire after onerror
      };
    };

    const scheduleReconnect = () => {
      if (reconnectTimerRef.current) return;
      const delay = reconnectDelayRef.current;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, delay);
      reconnectDelayRef.current = Math.min(delay * 2, 30000);
    };

    connect();

    return () => {
      intentionalCloseRef.current = true;
      cleanup();
      setStatus('disconnected');
    };
  }, [enabled]);

  const lastEventForPath = (path: string): number | null => {
    return lastEventTsRef.current.get(path) ?? null;
  };

  return { status, events, lastEventForPath, snapshotKey };
}
