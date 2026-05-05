'use client';

import React from 'react';

interface TextPreviewProps {
  content: string;
  language?: string;
  truncated?: boolean;
}

export function TextPreview({ content, language, truncated }: TextPreviewProps) {
  return (
    <div className="flex flex-col h-full">
      {truncated && (
        <div className="px-3 py-1.5 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 text-xs border-b border-border">
          文件超过 5MB，仅显示前 5MB
        </div>
      )}
      <div className="flex-1 overflow-auto bg-muted/20">
        <pre className="p-3 text-xs font-mono whitespace-pre-wrap break-words leading-relaxed">
          <code data-language={language || 'plain'}>{content}</code>
        </pre>
      </div>
    </div>
  );
}
