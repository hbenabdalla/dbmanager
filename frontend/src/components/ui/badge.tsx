import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const badge = cva("inline-flex items-center gap-1 rounded-[5px] px-1.5 py-px text-2xs font-medium leading-[18px] whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-surface-3 text-text-2",
      accent: "bg-accent-soft text-accent",
      ok: "bg-ok-soft text-ok",
      warn: "bg-warn-soft text-warn",
      crit: "bg-crit-soft text-crit",
      down: "bg-down-soft text-down",
      info: "bg-info-soft text-info",
      outline: "border border-border text-text-2",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, ...p }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) {
  return <span className={cn(badge({ tone }), className)} {...p} />;
}
