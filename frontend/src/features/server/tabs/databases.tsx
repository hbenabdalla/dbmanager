import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { get } from "@/api/client";
import type { DatabaseInfo, ServerDetail } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { bytes, pct } from "@/lib/format";

export function DatabasesTab({ server, onOpen }: { server: ServerDetail; onOpen?: (db: string) => void }) {
  const q = useQuery({ queryKey: ["dbs", server.id], queryFn: () => get<DatabaseInfo[]>(`/servers/${server.id}/databases`) });
  const columns: ColumnDef<DatabaseInfo, unknown>[] = [
    { header: "Database", accessorKey: "name", cell: ({ row: { original: d } }) => (
      <span className="flex items-center gap-2"><span className="font-medium">{d.name}</span>{d.is_system && <Badge>system</Badge>}{!d.accessible && <Badge tone="warn">no access</Badge>}</span>
    ) },
    { header: "Owner", accessorKey: "owner" },
    { header: "Size", accessorKey: "size_bytes", meta: { align: "right" }, cell: ({ getValue }) => bytes(getValue() as number) },
    { header: "Sessions", accessorFn: (d) => d.extra.sessions as number, meta: { align: "right" } },
    { header: "Cache hit", accessorFn: (d) => d.extra.cache_hit_ratio as number, meta: { align: "right" }, cell: ({ getValue }) => pct(getValue() as number) },
    { header: "Encoding", accessorKey: "encoding" },
    { header: "Tablespace", accessorKey: "tablespace" },
  ];
  return (
    <Card>
      <DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} onRetry={() => q.refetch()}
        initialSort={[{ id: "size_bytes", desc: true }]} onRowClick={onOpen ? (d) => onOpen(d.name) : undefined} />
    </Card>
  );
}
