'use client';

import React, { useEffect, useState } from 'react';
import { fetchWorkspaceBlobUrl } from '@/lib/api';
import { Loader2 } from 'lucide-react';

interface HtmlPreviewProps {
  path: string;
  reloadKey?: number;
}

/**
 * Renders HTML inside a maximally-restricted sandbox iframe.
 * sandbox="" disables scripts, forms, popups, top-level navigation, and same-origin —
 * mitigating XSS/token-theft from arbitrary user files.
 */
export function HtmlPreview({ path, reloadKey }: HtmlPreviewProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    let cancelled = false;
    setBlobUrl(null);
    setError(null);

    fetchWorkspaceBlobUrl(path)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        revoke = url;
        setBlobUrl(url);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [path, reloadKey]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!blobUrl) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }
  return (
    <iframe
      src={blobUrl}
      sandbox=""
      className="flex-1 w-full h-full border-0 bg-white"
      title={`HTML preview: ${path}`}
    />
  );
}
