import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Database, FolderTree, Table2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { get } from "@/api/client";
import type { DatabaseInfo, ObjectDetail, ObjectInfo, SchemaInfo, ServerDetail } from "@/api/types";
import { CodeBlock, KeyValue } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { bytes, compactNum } from "@/lib/format";
import { cn } from "@/lib/utils";

const TYPE_LABEL: Record<string, string> = {
  table: "Tables", view: "Views", materialized_view: "Materialized views", index: "Indexes", sequence: "Sequences",
  function: "Functions", procedure: "Procedures", trigger: "Triggers", constraint: "Constraints", foreign_table: "Foreign tables", synonym: "Synonyms",
};

/** Database → schema → object browser with a detail pane. */
export function ObjectBrowser({ server, initialDatabase }: { server: ServerDetail; initialDatabase?: string }) {
  const dbs = useQuery({ queryKey: ["dbs", server.id], queryFn: () => get<DatabaseInfo[]>(`/servers/${server.id}/databases`) });
  const userDbs = useMemo(() => (dbs.data ?? []).filter((d) => !d.is_template && d.accessible), [dbs.data]);
  const [database, setDatabase] = useState<string | undefined>(initialDatabase);
  const [schema, setSchema] = useState<string>();
  const [selected, setSelected] = useState<ObjectInfo>();
  const [filter, setFilter] = useState("");
  const [showSystem, setShowSystem] = useState(false);

  useEffect(() => {
    if (!database && userDbs.length) setDatabase(userDbs.find((d) => d.name === server.default_database)?.name ?? userDbs.find((d) => !d.is_system)?.name ?? userDbs[0].name);
  }, [userDbs, database, server.default_database]);

  const schemas = useQuery({
    queryKey: ["schemas", server.id, database], enabled: !!database,
    queryFn: () => get<SchemaInfo[]>(`/servers/${server.id}/databases/${encodeURIComponent(database!)}/schemas`),
  });
  useEffect(() => {
    if (schemas.data && (!schema || !schemas.data.some((s) => s.name === schema))) {
      setSchema(schemas.data.find((s) => !s.is_system)?.name ?? schemas.data[0]?.name);
      setSelected(undefined);
    }
  }, [schemas.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const objects = useQuery({
    queryKey: ["objects", server.id, database, schema], enabled: !!database && !!schema,
    queryFn: () => get<ObjectInfo[]>(`/servers/${server.id}/databases/${encodeURIComponent(database!)}/schemas/${encodeURIComponent(schema!)}/objects`),
  });
  const grouped = useMemo(() => {
    const m = new Map<string, ObjectInfo[]>();
    for (const o of objects.data ?? []) {
      if (filter && !o.name.toLowerCase().includes(filter.toLowerCase())) continue;
      m.set(o.type, [...(m.get(o.type) ?? []), o]);
    }
    return [...m.entries()].sort((a, b) => Object.keys(TYPE_LABEL).indexOf(a[0]) - Object.keys(TYPE_LABEL).indexOf(b[0]));
  }, [objects.data, filter]);

  if (dbs.error) return <ErrorState error={dbs.error} />;

  return (
    <Card className="grid min-h-[560px] overflow-hidden lg:grid-cols-[300px_1fr]">
      <div className="flex min-h-0 flex-col border-b border-border lg:border-b-0 lg:border-r">
        <div className="space-y-2 border-b border-border p-3">
          <div className="flex items-center gap-2"><Database className="size-4 text-muted" />
            <Select aria-label="Database" value={database ?? ""} onChange={(e) => { setDatabase(e.target.value); setSchema(undefined); setSelected(undefined); }}>
              {userDbs.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
            </Select>
          </div>
          <div className="flex items-center gap-2"><FolderTree className="size-4 text-muted" />
            <Select aria-label="Schema" value={schema ?? ""} onChange={(e) => { setSchema(e.target.value); setSelected(undefined); }}>
              {(schemas.data ?? []).filter((s) => showSystem || !s.is_system).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </Select>
          </div>
          <label className="flex items-center gap-2 text-[12px] text-muted"><input type="checkbox" checked={showSystem} onChange={(e) => setShowSystem(e.target.checked)} /> Show system schemas</label>
          <Input aria-label="Filter objects" placeholder="Filter objects" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-2 lg:max-h-[640px]">
          {objects.isLoading || schemas.isLoading ? <SkeletonRows rows={8} /> : objects.error ? <ErrorState error={objects.error} /> : grouped.length === 0 ? (
            <EmptyState title="No objects" className="py-8" />
          ) : grouped.map(([type, list]) => (
            <details key={type} open={["table", "view"].includes(type) || grouped.length <= 3} className="mb-1">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-2 py-1 text-[12.5px] font-medium text-text-2 hover:bg-surface-2 [&::-webkit-details-marker]:hidden">
                <ChevronRight className="size-3.5 transition-transform [details[open]_&]:rotate-90" />{TYPE_LABEL[type] ?? type}<span className="ml-auto text-2xs text-muted tabular">{list.length}</span>
              </summary>
              <ul className="ml-4">
                {list.map((o) => (
                  <li key={`${o.type}:${o.name}:${o.parent ?? ""}`}>
                    <button onClick={() => setSelected(o)} className={cn("flex w-full items-center gap-1.5 truncate rounded px-2 py-1 text-left text-[12.5px] hover:bg-surface-2",
                      selected?.name === o.name && selected.type === o.type && "bg-accent-soft text-accent")}>
                      <span className="truncate">{o.name}</span>{o.parent && <span className="truncate text-2xs text-muted">on {o.parent}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      </div>
      <div className="min-w-0">
        {selected && database && schema ? <ObjectDetailPane server={server} database={database} schema={schema} object={selected} /> : (
          <EmptyState icon={<Table2 />} title="Select an object" className="h-full">Choose a table, view or routine to see its columns, indexes, statistics and definition.</EmptyState>
        )}
      </div>
    </Card>
  );
}

function ObjectDetailPane({ server, database, schema, object }: { server: ServerDetail; database: string; schema: string; object: ObjectInfo }) {
  const detailable = ["table", "view", "materialized_view", "index", "sequence", "function", "procedure", "foreign_table"].includes(object.type);
  const q = useQuery({
    queryKey: ["object", server.id, database, schema, object.type, object.name], enabled: detailable,
    queryFn: () => get<ObjectDetail>(`/servers/${server.id}/databases/${encodeURIComponent(database)}/schemas/${encodeURIComponent(schema)}/objects/${encodeURIComponent(object.name)}`, { type: object.type }),
  });
  const d = q.data;
  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-semibold">{schema}.{object.name}</h3>
        <Badge tone="outline">{object.type.replaceAll("_", " ")}</Badge>
        {object.size_bytes !== null && <span className="text-[12.5px] text-muted">{bytes(object.size_bytes)}</span>}
        {object.row_estimate !== null && <span className="text-[12.5px] text-muted">~{compactNum(object.row_estimate)} rows</span>}
      </div>
      {!detailable ? (
        <div className="space-y-3">
          <KeyValue items={[["On", object.parent], ...Object.entries(object.extra).filter(([, v]) => v !== null && v !== "").map(([k, v]) => [k, String(v)] as [string, string])]} />
          {typeof object.extra.definition === "string" && <CodeBlock code={object.extra.definition} />}
        </div>
      ) : q.isLoading ? <SkeletonRows /> : q.error ? <ErrorState error={q.error} /> : d && (
        <Tabs defaultValue={d.columns.length ? "columns" : "definition"}>
          <TabsList>
            {d.columns.length > 0 && <TabsTrigger value="columns">Columns ({d.columns.length})</TabsTrigger>}
            {d.indexes.length > 0 && <TabsTrigger value="indexes">Indexes ({d.indexes.length})</TabsTrigger>}
            {d.constraints.length > 0 && <TabsTrigger value="constraints">Constraints ({d.constraints.length})</TabsTrigger>}
            {d.triggers.length > 0 && <TabsTrigger value="triggers">Triggers</TabsTrigger>}
            {Object.keys(d.statistics).length > 0 && <TabsTrigger value="stats">Statistics</TabsTrigger>}
            {d.definition && <TabsTrigger value="definition">Definition</TabsTrigger>}
          </TabsList>
          <TabsContent value="columns" className="pt-3">
            <table className="w-full text-[13px]">
              <thead><tr className="border-b border-border text-left text-[12px] text-muted"><th className="py-1.5 pr-3 font-medium">#</th><th className="pr-3 font-medium">Column</th><th className="pr-3 font-medium">Type</th><th className="pr-3 font-medium">Nullable</th><th className="font-medium">Default</th></tr></thead>
              <tbody>{d.columns.map((c) => (
                <tr key={c.name} className="border-b border-border/60"><td className="py-1.5 pr-3 text-muted tabular">{c.position}</td><td className="pr-3 font-medium">{c.name}</td><td className="pr-3 font-mono text-[12px]">{c.data_type}</td><td className="pr-3">{c.nullable ? "yes" : "no"}</td><td className="font-mono text-[12px] text-muted">{c.default ?? ""}</td></tr>
              ))}</tbody>
            </table>
          </TabsContent>
          <TabsContent value="indexes" className="space-y-2 pt-3">
            {d.indexes.map((i) => (
              <div key={String(i.name)} className="rounded-md border border-border p-2.5">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-[13px]"><span className="font-medium">{String(i.name)}</span>
                  {Boolean(i.is_primary) && <Badge tone="accent">primary</Badge>}{Boolean(i.is_unique) && !i.is_primary && <Badge>unique</Badge>}
                  {i.idx_scan !== undefined && i.idx_scan !== null && <span className="text-[12px] text-muted">{compactNum(Number(i.idx_scan))} scans</span>}
                  {i.size_bytes !== undefined && <span className="text-[12px] text-muted">{bytes(Number(i.size_bytes))}</span>}
                </div>
                <code className="block break-all font-mono text-[12px] text-text-2">{String(i.definition)}</code>
              </div>
            ))}
          </TabsContent>
          <TabsContent value="constraints" className="space-y-1.5 pt-3">
            {d.constraints.map((c) => <div key={String(c.name)} className="text-[13px]"><span className="font-medium">{String(c.name)}</span> <Badge tone="outline">{String(c.kind)}</Badge> <code className="font-mono text-[12px] text-text-2">{String(c.definition)}</code></div>)}
          </TabsContent>
          <TabsContent value="triggers" className="space-y-2 pt-3">{d.triggers.map((t) => <CodeBlock key={String(t.name)} code={String(t.definition)} />)}</TabsContent>
          <TabsContent value="stats" className="pt-3"><KeyValue items={Object.entries(d.statistics).map(([k, v]) => [k.replaceAll("_", " "), k.endsWith("bytes") ? bytes(Number(v)) : v === null ? "—" : String(v)])} /></TabsContent>
          <TabsContent value="definition" className="pt-3">
            {d.definition_note && <p className="mb-2 text-[12.5px] text-muted">{d.definition_note}</p>}
            <CodeBlock code={d.definition ?? ""} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

export function SchemasTable({ server }: { server: ServerDetail }) {
  const dbs = useQuery({ queryKey: ["dbs", server.id], queryFn: () => get<DatabaseInfo[]>(`/servers/${server.id}/databases`) });
  const [database, setDatabase] = useState<string>();
  const db = database ?? dbs.data?.find((d) => d.name === server.default_database)?.name ?? dbs.data?.find((d) => !d.is_system && d.accessible)?.name;
  const schemas = useQuery({ queryKey: ["schemas", server.id, db], enabled: !!db, queryFn: () => get<SchemaInfo[]>(`/servers/${server.id}/databases/${encodeURIComponent(db!)}/schemas`) });
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-border p-3">
        <Database className="size-4 text-muted" />
        <Select aria-label="Database" className="w-64" value={db ?? ""} onChange={(e) => setDatabase(e.target.value)}>
          {(dbs.data ?? []).filter((d) => d.accessible && !d.is_template).map((d) => <option key={d.name}>{d.name}</option>)}
        </Select>
      </div>
      {schemas.isLoading ? <SkeletonRows /> : schemas.error ? <ErrorState error={schemas.error} /> : (
        <table className="w-full text-[13px]">
          <thead><tr className="border-b border-border bg-surface-2 text-left text-[12px] text-muted">{["Schema", "Owner", "Tables", "Views", "Indexes", "Sequences", "Routines", "Size"].map((h, i) => <th key={h} className={cn("px-3 py-2 font-medium", i > 1 && "text-right")}>{h}</th>)}</tr></thead>
          <tbody>{schemas.data?.map((s) => (
            <tr key={s.name} className="border-b border-border/60">
              <td className="px-3 py-2 font-medium">{s.name} {s.is_system && <Badge>system</Badge>}</td><td className="px-3">{s.owner}</td>
              {["tables", "views", "indexes", "sequences", "routines"].map((k) => <td key={k} className="px-3 text-right tabular">{s.object_counts[k] ?? 0}</td>)}
              <td className="px-3 text-right tabular">{bytes(s.size_bytes)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}
