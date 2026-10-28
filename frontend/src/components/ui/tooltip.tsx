import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = T.Provider;

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  if (!content) return <>{children}</>;
  return (
    <T.Root delayDuration={150}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content side={side} sideOffset={6} className="z-[60] max-w-xs rounded-md bg-brand-navy px-2.5 py-1.5 text-[12px] leading-snug text-white shadow-lg">
          {content}
          <T.Arrow className="fill-brand-navy" />
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
