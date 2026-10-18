import * as M from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;

export function MenuContent({ children, align = "end" }: { children: ReactNode; align?: "start" | "end" }) {
  return (
    <M.Portal>
      <M.Content align={align} sideOffset={6} className="z-50 min-w-[200px] rounded-lg border border-border bg-surface p-1 shadow-[0_16px_40px_-16px_rgba(5,11,20,0.35)]">
        {children}
      </M.Content>
    </M.Portal>
  );
}

export function MenuItem({ className, ...p }: M.DropdownMenuItemProps) {
  return <M.Item className={cn("flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-surface-3 [&_svg]:size-4 [&_svg]:text-muted", className)} {...p} />;
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <M.Label className="px-2 py-1.5 text-[12px] text-muted">{children}</M.Label>;
}

export const MenuSeparator = () => <M.Separator className="my-1 h-px bg-border" />;
