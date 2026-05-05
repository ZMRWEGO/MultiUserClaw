'use client';

import React from 'react';
import { Download, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { fetchWorkspaceBlobUrl } from '@/lib/api';

interface WordPreviewProps {
  path: string;
  size: number;
  reloadKey?: number;
}

/**
 * Renders a .docx file via the `docx-preview` JS library.
 * docx-preview parses the file in-browser and renders to HTML — no server-side conversion.
 */
export function WordPreview({ path, size, reloadKey }: WordPreviewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        // Dynamic import keeps docx-preview out of the main bundle
        const { renderAsync } = await import('docx-preview');
        const url = await fetchWorkspaceBlobUrl(path);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        try {
          const blob = await fetch(url).then((r) => r.blob());
          if (!containerRef.current) return;
          containerRef.current.innerHTML = '';
          await renderAsync(blob, containerRef.current, undefined, {
            className: 'docx-viewer',
            inWrapper: true,
            ignoreWidth: false,
            breakPages: true,
          });
          if (!cancelled) setLoading(false);
        } finally {
          URL.revokeObjectURL(url);
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || 'docx 渲染失败');
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [path, reloadKey]);

  const handleDownload = async () => {
    try {
      const url = await fetchWorkspaceBlobUrl(path);
      const filename = path.split('/').pop() || 'file.docx';
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      // ignore
    }
  };

  return (
    <div className="flex-1 flex flex-col">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-muted/30">
        <span className="text-xs text-muted-foreground">
          .docx · {(size / 1024).toFixed(1)} KB
        </span>
        <Button onClick={handleDownload} size="sm" variant="ghost" className="h-7 gap-1">
          <Download className="w-3.5 h-3.5" />
          <span className="text-xs">下载</span>
        </Button>
      </div>
      {loading && !error && (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      )}
      {error && (
        <div className="flex-1 flex items-center justify-center p-4 text-sm text-destructive">
          {error}
        </div>
      )}
      <div
        ref={containerRef}
        className="flex-1 overflow-auto p-4 bg-white text-black docx-viewer-host min-w-0 max-w-full"
        style={{ display: loading || error ? 'none' : undefined }}
      />
    </div>
  );
}
