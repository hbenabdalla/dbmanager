import { PageHeader } from "@/components/page";
import { AuditTable } from "../shared/audit-table";

export function AuditPage() {
  return (
    <div>
      <PageHeader title="Audit logs" description="Every administrative action, sign-in, denied request and query execution. Secrets are never written to the audit log." crumbs={[{ label: "Audit logs" }]} />
      <AuditTable />
    </div>
  );
}
