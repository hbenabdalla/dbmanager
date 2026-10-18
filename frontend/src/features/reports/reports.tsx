import { useQuery } from "@tanstack/react-query";
import { Download, FileSpreadsheet } from "lucide-react";
import { toast } from "sonner";
import { download, get } from "@/api/client";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SkeletonRows } from "@/components/ui/states";

interface Report { key: string; title: string; description: string; formats: string[]; planned_formats: string[]; available: boolean }

export function ReportsPage() {
  const q = useQuery({ queryKey: ["reports"], queryFn: () => get<Report[]>("/reports") });
  const run = async (r: Report) => {
    try {
      await download(`/reports/${r.key}.csv`, `${r.key}.csv`);
      toast.success(`${r.title} downloaded`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  return (
    <div>
      <PageHeader title="Reports" description="Reports include only the servers your role can see. Every download is audited." crumbs={[{ label: "Reports" }]} />
      {q.isLoading ? <SkeletonRows /> : (
        <Card className="divide-y divide-border">
          {q.data?.map((r) => (
            <div key={r.key} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <FileSpreadsheet className="size-5 text-muted" />
              <div className="min-w-0 flex-1"><div className="font-medium">{r.title}</div><div className="text-[12.5px] text-muted">{r.description}</div></div>
              <span className="flex gap-1">{r.planned_formats.map((f) => <Badge key={f} tone="outline">{f.toUpperCase()} later</Badge>)}</span>
              <Button size="sm" disabled={!r.available} onClick={() => run(r)}><Download /> CSV</Button>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
