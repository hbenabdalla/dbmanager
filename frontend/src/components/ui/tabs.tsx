import * as T from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = T.Root;
export const TabsContent = T.Content;

export function TabsList({ className, ...p }: T.TabsListProps) {
  return <T.List className={cn("scroll-thin flex gap-0.5 overflow-x-auto border-b border-border", className)} {...p} />;
}

export function TabsTrigger({ className, ...p }: T.TabsTriggerProps) {
  return (
    <T.Trigger
      className={cn(
        "relative -mb-px whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-[13px] font-medium text-muted transition-colors hover:text-text data-[state=active]:border-accent data-[state=active]:text-text disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
      {...p}
    />
  );
}
