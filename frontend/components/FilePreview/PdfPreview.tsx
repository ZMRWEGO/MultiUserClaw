'use client';

import React, { useEffect, useState } from 'react';
import { fetchWorkspaceBlobUrl } from '@/lib/api';
import { Loader2 } from 'lucide-react';

interface PdfPreviewProps {
  path: string;
  reloadKey?: number;
}

export function PdfPreview({ path, reloadKey }: PdfPreviewProps) {
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
      className="flex-1 w-full h-full border-0"
      title={`PDF preview: ${path}`}
    />
  );
}
