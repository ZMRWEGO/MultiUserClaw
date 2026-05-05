'use client';

import React, { useEffect, useRef, useState } from 'react';
import { FileText, Loader2 } from 'lucide-react';
import { getWorkspacePreview, type PreviewResult } from '@/lib/api';
import { TextPreview } from './TextPreview';
import { MarkdownPreview } from './MarkdownPreview';
import { JsonPreview } from './JsonPreview';
import { ImagePreview } from './ImagePreview';
import { PdfPreview } from './PdfPreview';
import { HtmlPreview } from './HtmlPreview';
import { BinaryPreview } from './BinaryPreview';

// Lazy-loaded heavy previews (Phase 3)
const WordPreview = React.lazy(() =>
  import('./WordPreview').then((m) => ({ default: m.WordPreview }))
);
const ExcelPreview = React.lazy(() =>
  import('./ExcelPreview').then((m) => ({ default: m.ExcelPreview }))
);

export interface FilePreviewProps {
  /** Workspace-relative path of the file to preview, or null when nothing selected */
  path: string | null;
  /** Bumped to force a re-fetch (e.g. when WS reports the file changed) */
  reloadKey?: number;
  /** True when the previewed file has been deleted by an external event */
  deleted?: boolean;
}

export function FilePreview({ path, reloadKey = 0, deleted }: FilePreviewProps) {
  const [data, setData] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const reqIdRef = useRef(0);

  useEffect(() => {
    if (!path) {
      setData(null);
      setError(null);
      return;
    }
    if (deleted) {
      setData(null);
      setError(null);
      return;
    }
    const myReq = ++reqIdRef.current;
    setLoading(true);
    setError(null);
    getWorkspacePreview(path)
      .then((result) => {
        if (myReq !== reqIdRef.current) return;
        setData(result);
        setLoading(false);
      })
      .catch((err: Error) => {
        if (myReq !== reqIdRef.current) return;
        setError(err.message || '预览加载失败');
        setData(null);
        setLoading(false);
      });
  }, [path, reloadKey, deleted]);

  if (deleted) {
    return (
      <div className="h-full flex-1 flex flex-col items-center justify-center p-6 text-center text-muted-foreground">
        <FileText className="w-10 h-10 mb-3 opacity-50" />
        <p className="text-sm">文件已被删除</p>
      </div>
    );
  }

  if (!path) {
    return (
      <div className="h-full flex-1 flex flex-col items-center justify-center p-6 text-center text-muted-foreground">
        <FileText className="w-10 h-10 mb-3 opacity-50" />
        <p className="text-sm">点击左侧文件查看预览</p>
      </div>
    );
  }

  if (loading || !data) {
    if (error) {
      return (
        <div className="h-full flex-1 flex items-center justify-center text-sm text-destructive p-4">
          {error}
        </div>
      );
    }
    return (
      <div className="h-full flex-1 flex items-center justify-center text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }

  const previewWrap = (children: React.ReactNode) => (
    <div className="h-full min-w-0 overflow-hidden flex-1 flex flex-col">{children}</div>
  );

  switch (data.kind) {
    case 'text':
      return previewWrap(
        <TextPreview
          content={data.content}
          language={data.language}
          truncated={data.truncated}
        />
      );
    case 'markdown':
      return previewWrap(
        <MarkdownPreview content={data.content} truncated={data.truncated} />
      );
    case 'json':
      return previewWrap(
        <JsonPreview content={data.content} />
      );
    case 'image':
      return previewWrap(
        <ImagePreview
          path={path}
          contentType={data.content_type}
          reloadKey={reloadKey}
        />
      );
    case 'pdf':
      return previewWrap(
        <PdfPreview path={path} reloadKey={reloadKey} />
      );
    case 'html':
      return previewWrap(
        <HtmlPreview path={path} reloadKey={reloadKey} />
      );
    case 'docx':
      return previewWrap(
        <React.Suspense
          fallback={
            <div className="h-full flex-1 flex items-center justify-center text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          }
        >
          <WordPreview path={path} size={data.size} reloadKey={reloadKey} />
        </React.Suspense>
      );
    case 'xlsx':
      return previewWrap(
        <React.Suspense
          fallback={
            <div className="h-full flex-1 flex items-center justify-center text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          }
        >
          <ExcelPreview path={path} size={data.size} reloadKey={reloadKey} />
        </React.Suspense>
      );
    case 'binary':
      return previewWrap(
        <BinaryPreview
          path={path}
          size={data.size}
          reason={data.reason}
          modified={data.modified}
        />
      );
    default:
      // exhaustiveness — should not happen
      return previewWrap(
        <div className="h-full flex-1 flex items-center justify-center text-sm text-destructive">
          未知预览类型
        </div>
      );
  }
}
