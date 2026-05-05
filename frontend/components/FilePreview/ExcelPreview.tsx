'use client';

import React from 'react';
import { Download, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { fetchWorkspaceArrayBuffer, fetchWorkspaceBlobUrl } from '@/lib/api';

interface ExcelPreviewProps {
  path: string;
  size: number;
  reloadKey?: number;
}

interface ParsedSheet {
  name: string;
  rows: (string | number | null)[][];
  cols: number;
}

/** Hard cap on cells to render — beyond this we ask the user to download. */
const MAX_CELLS = 1_000_000;

/** Default cell size when react-window is used. */
const COL_WIDTH = 120;
const ROW_HEIGHT = 28;

export function ExcelPreview({ path, size, reloadKey }: ExcelPreviewProps) {
  const [sheets, setSheets] = React.useState<ParsedSheet[] | null>(null);
  const [activeIdx, setActiveIdx] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);
  const [tooBig, setTooBig] = React.useState(false);
  const [GridComp, setGridComp] = React.useState<any>(null);

  React.useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setError(null);
    setTooBig(false);

    (async () => {
      try {
        const [xlsxModule, reactWindow] = await Promise.all([
          import('xlsx'),
          import('react-window'),
        ]);
        if (cancelled) return;
        const XLSX: any = (xlsxModule as any).default ?? xlsxModule;
        setGridComp(() => (reactWindow as any).Grid);

        const buf = await fetchWorkspaceArrayBuffer(path);
        if (cancelled) return;
        const wb = XLSX.read(buf, { type: 'array' });

        const parsed: ParsedSheet[] = [];
        let totalCells = 0;
        for (const name of wb.SheetNames) {
          const ws = wb.Sheets[name];
          // sheet_to_json with header:1 returns array-of-arrays
          const rows: (string | number | null)[][] = XLSX.utils.sheet_to_json(ws, {
            header: 1,
            defval: null,
            raw: true,
          });
          const cols = rows.reduce((max, r) => Math.max(max, r.length), 0);
          totalCells += rows.length * cols;
          parsed.push({ name, rows, cols });
          if (totalCells > MAX_CELLS) {
            setTooBig(true);
            return;
          }
        }
        if (!cancelled) {
          setSheets(parsed);
          setActiveIdx(0);
        }
      } catch (err: any) {
        if (!cancelled) setError(err?.message || 'xlsx 解析失败');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [path, reloadKey]);

  const handleDownload = async () => {
    try {
      const url = await fetchWorkspaceBlobUrl(path);
      const filename = path.split('/').pop() || 'file.xlsx';
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

  if (tooBig) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
        <p className="text-sm mb-2">表格太大（cells &gt; 100w）</p>
        <p className="text-xs text-muted-foreground mb-4">建议下载到本地查看</p>
        <Button onClick={handleDownload} size="sm" className="gap-2">
          <Download className="w-4 h-4" />
          下载 xlsx
        </Button>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (!sheets) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }

  const sheet = sheets[activeIdx];

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-muted/30 flex-shrink-0">
        <span className="text-xs text-muted-foreground">
          {sheets.length} sheet · {(size / 1024).toFixed(1)} KB
        </span>
        <Button onClick={handleDownload} size="sm" variant="ghost" className="h-7 gap-1">
          <Download className="w-3.5 h-3.5" />
          <span className="text-xs">下载</span>
        </Button>
      </div>
      <SheetView sheet={sheet} GridComp={GridComp} />
      {sheets.length > 1 && (
        <div className="flex border-t border-border bg-muted/20 overflow-x-auto">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              onClick={() => setActiveIdx(i)}
              className={`px-3 py-1.5 text-xs whitespace-nowrap border-r border-border hover:bg-muted ${
                i === activeIdx
                  ? 'bg-background font-semibold'
                  : 'text-muted-foreground'
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface CellProps {
  rows: (string | number | null)[][];
}

function Cell({
  rows,
  columnIndex,
  rowIndex,
  style,
}: CellProps & {
  columnIndex: number;
  rowIndex: number;
  style: React.CSSProperties;
}) {
  const cell = rows[rowIndex]?.[columnIndex];
  const isHeader = rowIndex === 0;
  const text = cell == null ? '' : String(cell);
  return (
    <div
      style={style}
      className={`border-r border-b border-border/60 px-2 py-1 text-xs truncate ${
        isHeader ? 'bg-muted/40 font-semibold' : ''
      }`}
      title={text}
    >
      {text}
    </div>
  );
}

function SheetView({ sheet, GridComp }: { sheet: ParsedSheet; GridComp: any }) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const rafIdRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      // Batch updates using requestAnimationFrame to avoid render conflicts
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
      }
      rafIdRef.current = requestAnimationFrame(() => {
        for (const e of entries) {
          setSize({ w: e.contentRect.width, h: e.contentRect.height });
        }
        rafIdRef.current = null;
      });
    });
    ro.observe(containerRef.current);
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
      }
      ro.disconnect();
    };
  }, []);

  // Determine which rendering mode to use
  // Use simple table for files up to 300 rows or 100 columns for better compatibility
  const isSmallSheet = sheet.rows.length <= 300 && sheet.cols <= 100;
  const isLargeSheet = !isSmallSheet && GridComp;
  const headerHeight = ROW_HEIGHT;
  const gridHeight = Math.max(0, size.h - headerHeight);

  // Memoize the cell renderer to avoid recreating on every render
  const CellRenderer = React.useCallback(
    ({ columnIndex, rowIndex, style, data }: any) => {
      // Guard against undefined data - use sheet.rows as fallback
      const rows = Array.isArray(data) && data.length > 0 ? data : sheet.rows;
      const cell = rows?.[rowIndex + 1]?.[columnIndex];
      const text = cell == null ? '' : String(cell);
      return (
        <div
          style={style}
          className="border-r border-b border-border/60 px-2 py-1 text-xs truncate"
          title={text}
        >
          {text}
        </div>
      );
    },
    [sheet.rows]
  );

  // Small sheets: simple table with sticky header
  if (isSmallSheet) {
    return (
      <div ref={containerRef} className="flex-1 overflow-auto min-w-0">
        <table className="border-collapse text-xs w-full" data-testid="excel-preview-table">
          {sheet.rows.length > 0 && (
            <thead>
              <tr className="bg-muted/40 font-semibold">
                {Array.from({ length: sheet.cols }).map((_, ci) => {
                  const cell = sheet.rows[0][ci];
                  return (
                    <th
                      key={ci}
                      className="sticky top-0 border border-border/60 px-2 py-1 text-left font-semibold whitespace-pre-wrap bg-muted/40 z-10"
                      style={{ minWidth: 80, maxWidth: 300 }}
                    >
                      {cell == null ? '' : String(cell)}
                    </th>
                  );
                })}
              </tr>
            </thead>
          )}
          <tbody>
            {sheet.rows.slice(1).map((row, ri) => (
              <tr key={ri + 1}>
                {Array.from({ length: sheet.cols }).map((_, ci) => {
                  const cell = row[ci];
                  return (
                    <td
                      key={ci}
                      className="border border-border/60 px-2 py-1 align-top whitespace-pre-wrap break-words"
                      style={{ minWidth: 80, maxWidth: 300 }}
                    >
                      {cell == null ? '' : String(cell)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (!isLargeSheet) {
    return null;
  }

  // Large sheets: react-window virtualization with fixed header row
  // Ensure itemData is always a valid array to avoid Object.values(undefined) errors
  const safeItemData = Array.isArray(sheet?.rows) ? sheet.rows : [];

  return (
    <div ref={containerRef} className="flex-1 overflow-hidden min-w-0 flex flex-col">
      {/* Fixed header row */}
      {size.w > 0 && (
        <div className="flex border-b border-border bg-muted/40 shrink-0 overflow-hidden" style={{ width: size.w, height: headerHeight }}>
          {Array.from({ length: sheet.cols }).map((_, ci) => {
            const cell = sheet.rows[0]?.[ci];
            return (
              <div
                key={ci}
                className="border-r border-border/60 px-2 py-1 text-xs font-semibold truncate flex-shrink-0"
                style={{ width: COL_WIDTH, height: headerHeight, lineHeight: `${ROW_HEIGHT - 8}px` }}
                title={cell == null ? '' : String(cell)}
              >
                {cell == null ? '' : String(cell)}
              </div>
            );
          })}
        </div>
      )}
      {size.w > 0 && gridHeight > 0 && (
        <GridComp
          columnCount={sheet.cols}
          columnWidth={COL_WIDTH}
          rowCount={Math.max(0, sheet.rows.length - 1)}
          rowHeight={ROW_HEIGHT}
          height={gridHeight}
          width={size.w}
          itemData={safeItemData}
        >
          {CellRenderer}
        </GridComp>
      )}
    </div>
  );
}
