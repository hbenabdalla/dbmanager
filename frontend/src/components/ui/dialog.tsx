import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Dialog({ open, onOpenChange, title, description, children, footer, width = "max-w-lg" }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: ReactNode; description?: ReactNode; children: ReactNode;
  footer?: ReactNode; width?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-brand-navy/55 backdrop-blur-[1px]" />
        <D.Content
          className={cn("fixed left-1/2 top-[8vh] z-50 flex max-h-[84vh] w-[calc(100vw-2rem)] -translate-x-1/2 flex-col rounded-[var(--radius-panel)] border border-border bg-surface shadow-[0_24px_64px_-24px_rgba(5,11,20,0.45)]", width)}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <D.Title className="text-[15px] font-semibold">{title}</D.Title>
              {description ? <D.Description className="mt-1 text-[12.5px] text-muted">{description}</D.Description> : <D.Description className="sr-only">{String(title)}</D.Description>}
            </div>
            <D.Close className="rounded p-1 text-muted hover:bg-surface-3 hover:text-text" aria-label="Close">
              <X className="size-4" />
            </D.Close>
          </div>
          <div className="scroll-thin overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-border bg-surface-2 px-5 py-3 rounded-b-[var(--radius-panel)]">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
