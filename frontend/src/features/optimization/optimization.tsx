import { PageHeader } from "@/components/page";
import { FindingsList } from "../shared/findings";

const TEXT = {
  performance: ["Performance recommendations", "Connection pressure, locking, replication, maintenance and capacity findings. Each one shows the measurements that triggered it."],
  query: ["Query recommendations", "Expensive statements, index opportunities, unused and duplicate indexes, and stale statistics."],
  configuration: ["Configuration recommendations", "Memory, checkpoint and engine settings that the evidence suggests reviewing. Nothing is changed automatically."],
} as const;

export function RecommendationsPage({ group }: { group: keyof typeof TEXT }) {
  const [title, description] = TEXT[group];
  return (
    <div>
      <PageHeader title={title} description={description} crumbs={[{ label: "Optimization" }, { label: title.split(" ")[0] }]} />
      <FindingsList key={group} kind="recommendations" group={group} />
    </div>
  );
}
