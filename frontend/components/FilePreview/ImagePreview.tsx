'use client';

import React, { useEffect, useState } from 'react';
import { fetchWorkspaceBlobUrl } from '@/lib/api';
import { Loader2 } from 'lucide-react';

interface ImagePreviewProps {
  /** Workspace-relative path (used to fetch with auth) */
  path: string;
  contentType: string;
  /** Cache buster — changes when reloadKey changes upstream */
  reloadKey?: number;
}

export function ImagePreview({ path, reloadKey }: ImagePreviewProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState(false);

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
    <div className="flex-1 overflow-auto bg-muted/20 flex items-center justify-center p-4">
      <img
        src={blobUrl}
        alt={path}
        className={`max-w-full ${zoomed ? '' : 'max-h-full object-contain'} cursor-pointer`}
        onClick={() => setZoomed((z) => !z)}
      />
    </div>
  );
}
