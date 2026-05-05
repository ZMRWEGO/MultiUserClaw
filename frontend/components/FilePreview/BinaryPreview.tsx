'use client';

import React from 'react';
import { Download, FileWarning } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  fetchWorkspaceBlobUrl,
  type PreviewBinaryReason,
} from '@/lib/api';

interface BinaryPreviewProps {
  path: string;
  size: number;
  reason: PreviewBinaryReason;
  modified: string;
}

const REASON_LABEL: Record<PreviewBinaryReason, string> = {
  office_legacy: '此格式（旧版 Office / RTF / OpenDocument）暂不支持在线预览',
  archive: '压缩包不支持预览',
  unknown: '此文件类型不支持预览',
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export function BinaryPreview({ path, size, reason, modified }: BinaryPreviewProps) {
  const handleDownload = async () => {
    try {
      const blobUrl = await fetchWorkspaceBlobUrl(path);
      const filename = path.split('/').pop() || 'file';
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Defer revoke a tick so browser starts the download
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (err) {
      console.error('download failed', err);
    }
  };

  const filename = path.split('/').pop() || path;

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
      <FileWarning className="w-10 h-10 text-muted-foreground mb-3" />
      <h3 className="text-sm font-semibold mb-1 break-all max-w-full">{filename}</h3>
      <p className="text-xs text-muted-foreground mb-3 max-w-xs">
        {REASON_LABEL[reason]}
      </p>
      <div className="text-xs text-muted-foreground mb-4">
        <div>大小：{formatSize(size)}</div>
        <div>修改时间：{new Date(modified).toLocaleString('zh-CN')}</div>
      </div>
      <Button onClick={handleDownload} size="sm" className="gap-2">
        <Download className="w-4 h-4" />
        下载文件
      </Button>
    </div>
  );
}
