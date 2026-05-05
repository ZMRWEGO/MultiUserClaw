'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { searchWorkspaceFiles, type WorkspaceFile } from '@/lib/api';

const QUERY_MAX_LEN = 100;
const DEBOUNCE_MS = 150;
const RESULT_LIMIT = 20;

export interface MentionState {
  active: boolean;
  /** Index of the `@` character in the textarea value, or -1 when inactive. */
  mentionStart: number;
  /** Substring after `@` up to the current caret. */
  query: string;
}

const INACTIVE: MentionState = { active: false, mentionStart: -1, query: '' };

export interface MentionCommit {
  /** New textarea value after replacing `@<query>` with `@<path> ` (note trailing space). */
  value: string;
  /** Caret position to set after commit. */
  cursor: number;
  /** The selected file. */
  file: WorkspaceFile;
}

export interface UseMentionPickerOptions {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  /** Current textarea value (controlled). */
  value: string;
  /** Called once user picks a file from the popup. */
  onCommit: (commit: MentionCommit) => void;
}

export interface UseMentionPicker {
  state: MentionState;
  items: WorkspaceFile[];
  pickIndex: number;
  loading: boolean;
  setPickIndex: (i: number) => void;
  /** Wire to `<textarea onChange>`. Pass new value + caret position. */
  onValueChange: (value: string, cursor: number) => void;
  /** Wire to `<textarea onSelect/onClick/onKeyUp>`. Re-evaluate state on caret movement. */
  onSelectionChange: (cursor: number) => void;
  /** Wire to `<textarea onKeyDown>`. Returns `true` if the event was consumed. */
  handleKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => boolean;
  /** Programmatic pick of the i-th item; emits `onCommit` and closes the popup. */
  pick: (idx: number) => void;
  /** Force-close. */
  close: () => void;
}

/**
 * Detect whether the caret is inside an `@...` mention token.
 * The `@` must be at a token boundary (preceded by whitespace, newline, or string start).
 * Email-like inputs (`me@example.com`) do NOT trigger.
 * Returns INACTIVE if no valid mention token contains the caret.
 */
export function detectMention(value: string, cursor: number): MentionState {
  if (cursor < 0 || cursor > value.length) return INACTIVE;

  // Walk backwards from caret looking for the latest '@'.
  let i = cursor - 1;
  while (i >= 0) {
    const ch = value[i];
    if (ch === '@') break;
    // Token-internal characters: anything that's not whitespace/newline.
    if (ch === ' ' || ch === '\n' || ch === '\t' || ch === '\r') {
      return INACTIVE;
    }
    i -= 1;
  }
  if (i < 0 || value[i] !== '@') return INACTIVE;

  // `@` must be at a token boundary: preceded by whitespace or be at string start.
  if (i > 0) {
    const prev = value[i - 1];
    if (prev !== ' ' && prev !== '\n' && prev !== '\t' && prev !== '\r') {
      return INACTIVE;
    }
  }

  const query = value.slice(i + 1, cursor);
  if (query.length > QUERY_MAX_LEN) return INACTIVE;
  return { active: true, mentionStart: i, query };
}

export function useMentionPicker(opts: UseMentionPickerOptions): UseMentionPicker {
  const { textareaRef, value, onCommit } = opts;

  const [state, setState] = useState<MentionState>(INACTIVE);
  const [items, setItems] = useState<WorkspaceFile[]>([]);
  const [pickIndex, setPickIndex] = useState(0);
  const [loading, setLoading] = useState(false);

  // Keep latest ref to avoid stale closures inside async fetches.
  const stateRef = useRef(state);
  stateRef.current = state;

  // After Esc, remember the dismissed token so we don't immediately re-open
  // when the cursor stays inside the same @-token (onSelect fires after key events).
  const dismissedRef = useRef<{ start: number; query: string } | null>(null);

  const close = useCallback(() => {
    setState((cur) => {
      if (cur.active) {
        dismissedRef.current = { start: cur.mentionStart, query: cur.query };
      }
      return INACTIVE;
    });
    setItems([]);
    setPickIndex(0);
    setLoading(false);
  }, []);

  const onValueChange = useCallback((newValue: string, cursor: number) => {
    const next = detectMention(newValue, cursor);
    // If the user typed/modified text while dismissed, drop the dismissed flag.
    const d = dismissedRef.current;
    if (d && (!next.active || next.mentionStart !== d.start || next.query !== d.query)) {
      dismissedRef.current = null;
    }
    if (next.active && d && next.mentionStart === d.start && next.query === d.query) {
      // Still inside the dismissed token; stay closed.
      return;
    }
    setState(next);
    if (!next.active) {
      setItems([]);
      setPickIndex(0);
    }
  }, []);

  const onSelectionChange = useCallback(
    (cursor: number) => {
      const next = detectMention(value, cursor);
      const d = dismissedRef.current;
      if (next.active && d && next.mentionStart === d.start && next.query === d.query) {
        return; // Stay closed: cursor still inside the dismissed @-token.
      }
      // Different state → drop dismissed marker
      if (d && (!next.active || next.mentionStart !== d.start || next.query !== d.query)) {
        dismissedRef.current = null;
      }
      setState(next);
      if (!next.active) {
        setItems([]);
        setPickIndex(0);
      }
    },
    [value],
  );

  // Debounced fetch of candidates whenever query changes while active.
  useEffect(() => {
    if (!state.active) return;
    let cancelled = false;
    const handle = setTimeout(() => {
      setLoading(true);
      searchWorkspaceFiles(state.query, RESULT_LIMIT)
        .then((res) => {
          if (cancelled) return;
          // Guard against stale results (user closed picker mid-flight).
          if (!stateRef.current.active) return;
          setItems(res.items);
          setPickIndex(0);
        })
        .catch(() => {
          if (cancelled) return;
          setItems([]);
          setPickIndex(0);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [state.active, state.query]);

  const pick = useCallback(
    (idx: number) => {
      const cur = stateRef.current;
      const item = items[idx];
      if (!item || !cur.active || cur.mentionStart < 0) return;
      const ta = textareaRef.current;
      const cursor = ta?.selectionStart ?? value.length;
      const before = value.slice(0, cur.mentionStart);
      const after = value.slice(cursor);
      const inserted = `@${item.path} `;
      const newValue = before + inserted + after;
      const newCursor = (before + inserted).length;
      onCommit({ value: newValue, cursor: newCursor, file: item });
      close();
    },
    [items, value, textareaRef, onCommit, close],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>): boolean => {
      const cur = stateRef.current;
      if (!cur.active) return false;

      // Esc always closes when picker is active (even before results load).
      if (e.key === 'Escape') {
        e.preventDefault();
        close();
        return true;
      }

      // Navigation/commit only when we actually have items to navigate.
      if (items.length === 0) return false;

      // Composing: don't intercept Enter/Tab; let the IME finish.
      const composing = e.nativeEvent.isComposing;

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setPickIndex((i) => (i <= 0 ? items.length - 1 : i - 1));
        return true;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setPickIndex((i) => (i >= items.length - 1 ? 0 : i + 1));
        return true;
      }
      if (!composing && (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey))) {
        e.preventDefault();
        pick(pickIndex);
        return true;
      }
      return false;
    },
    [items, pickIndex, pick, close],
  );

  return {
    state,
    items,
    pickIndex,
    loading,
    setPickIndex,
    onValueChange,
    onSelectionChange,
    handleKeyDown,
    pick,
    close,
  };
}
