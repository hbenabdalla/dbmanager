import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorState, SkeletonRows } from "./states";

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[];
  data: T[] | undefined;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  empty?: ReactNode;
  pageSize?: number;
  onRowClick?: (row: T) => void;
  initialSort?: SortingState;
  dense?: boolean;
  rowClassName?: (row: T) => string | undefined;
  /** Server-side pagination */
  manual?: { page: number; pageSize: number; total: number; onPage: (p: number) => void };
  stickyHeader?: boolean;
}

export function DataTable<T>({
  columns, data, loading, error, onRetry, empty, pageSize = 50, onRowClick, initialSort = [], dense, rowClassName, manual,
  stickyHeader = true,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const table = useReactTable({
    data: data ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: manual ? undefined : getSortedRowModel(),
    getPaginationRowModel: manual ? undefined : getPaginationRowModel(),
    manualSorting: !!manual,
    manualPagination: !!manual,
    initialState: { pagination: { pageSize } },
  });

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (loading && !data) return <SkeletonRows />;
  if (!loading && data && data.length === 0) return <>{empty ?? <EmptyState title="Nothing to show" />}</>;

  const pageCount = manual ? Math.max(1, Math.ceil(manual.total / manual.pageSize)) : table.getPageCount();
  const pageIndex = manual ? manual.page - 1 : table.getState().pagination.pageIndex;
  const total = manual ? manual.total : (data?.length ?? 0);

  return (
    <div>
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead className={cn(stickyHeader && "sticky top-0 z-10")}>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-border bg-surface-2">
                {hg.headers.map((h) => {
                  const canSort = h.column.getCanSort() && !manual;
                  const dir = h.column.getIsSorted();
                  const align = (h.column.columnDef.meta as { align?: string } | undefined)?.align;
                  return (
                    <th key={h.id} scope="col"
                      className={cn("whitespace-nowrap px-3 py-2 text-[12px] font-medium text-muted", align === "right" && "text-right")}
                      style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}>
                      {h.isPlaceholder ? null : canSort ? (
                        <button className={cn("inline-flex items-center gap-1 hover:text-text", align === "right" && "flex-row-reverse")} onClick={h.column.getToggleSortingHandler()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {dir === "asc" ? <ArrowUp className="size-3" /> : dir === "desc" ? <ArrowDown className="size-3" /> : null}
                        </button>
                      ) : (
                        flexRender(h.column.columnDef.header, h.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                className={cn("border-b border-border/70 last:border-0", onRowClick && "cursor-pointer hover:bg-surface-2", rowClassName?.(row.original))}>
                {row.getVisibleCells().map((cell) => {
                  const align = (cell.column.columnDef.meta as { align?: string } | undefined)?.align;
                  return (
                    <td key={cell.id} className={cn("px-3 align-middle text-[13px]", dense ? "py-1.5" : "py-2.5", align === "right" && "text-right tabular")}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pageCount > 1 && (
        <div className="flex items-center justify-between border-t border-border px-3 py-2 text-[12.5px] text-muted">
          <span className="tabular">{total.toLocaleString()} rows, page {pageIndex + 1} of {pageCount}</span>
          <div className="flex gap-1">
            <button aria-label="Previous page" className="rounded p-1 hover:bg-surface-3 disabled:opacity-40" disabled={pageIndex === 0}
              onClick={() => (manual ? manual.onPage(manual.page - 1) : table.previousPage())}>
              <ChevronLeft className="size-4" />
            </button>
            <button aria-label="Next page" className="rounded p-1 hover:bg-surface-3 disabled:opacity-40" disabled={pageIndex + 1 >= pageCount}
              onClick={() => (manual ? manual.onPage(manual.page + 1) : table.nextPage())}>
              <ChevronRight className="size-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
