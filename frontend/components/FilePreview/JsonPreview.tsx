'use client';

import React from 'react';

interface JsonPreviewProps {
  content: string;
}

export function JsonPreview({ content }: JsonPreviewProps) {
  return (
    <div className="flex-1 overflow-auto bg-muted/20">
      <pre className="p-3 text-xs font-mono whitespace-pre-wrap break-words leading-relaxed">
        <code>{content}</code>
      </pre>
    </div>
  );
}
