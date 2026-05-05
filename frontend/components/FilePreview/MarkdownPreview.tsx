'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MarkdownPreviewProps {
  content: string;
  truncated?: boolean;
}

export function MarkdownPreview({ content, truncated }: MarkdownPreviewProps) {
  return (
    <div className="flex flex-col h-full">
      {truncated && (
        <div className="px-3 py-1.5 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 text-xs border-b border-border">
          文件超过 5MB，仅显示前 5MB
        </div>
      )}
      <div className="flex-1 overflow-auto p-3">
        <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              table: ({ children, ...props }) => (
                <div className="my-3 overflow-x-auto rounded-lg border border-border">
                  <table className="w-full border-collapse text-xs" {...props}>
                    {children}
                  </table>
                </div>
              ),
              th: ({ children, ...props }) => (
                <th
                  className="px-2 py-1.5 text-left font-semibold border-b border-border bg-muted/40"
                  {...props}
                >
                  {children}
                </th>
              ),
              td: ({ children, ...props }) => (
                <td className="px-2 py-1.5 border-b border-border/50" {...props}>
                  {children}
                </td>
              ),
              code: ({ className, children, ...props }) => {
                const isBlock = className?.includes('language-');
                if (isBlock) {
                  return (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                }
                return (
                  <code
                    className="px-1 py-0.5 rounded bg-muted text-xs font-mono"
                    {...props}
                  >
                    {children}
                  </code>
                );
              },
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
