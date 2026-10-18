import { AlertTriangle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";

/**
 * Explicit confirmation for risky actions. When ``expected`` is set, the user must type it
 * (e.g. the server name) before the action is enabled.
 */
export function ConfirmDialog({ open, onOpenChange, title, children, expected, expectedLabel, confirmLabel = "Confirm", danger, askReason, reasonRequired, busy, onConfirm }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; children?: ReactNode; expected?: string;
  expectedLabel?: string; confirmLabel?: string; danger?: boolean; askReason?: boolean; reasonRequired?: boolean; busy?: boolean;
  onConfirm: (typed: string, reason: string) => void;
}) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) {
      setTyped("");
      setReason("");
    }
  }, [open]);
  const ok = (!expected || typed === expected) && (!reasonRequired || reason.trim().length > 0);
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} disabled={!ok || busy} onClick={() => onConfirm(expected ? typed : "", reason)}>
            {busy ? "Working…" : confirmLabel}
          </Button>
        </>
      }>
      <div className="space-y-4">
        {danger && (
          <div className="flex gap-2.5 rounded-md border border-crit/30 bg-crit-soft px-3 py-2.5 text-[13px] text-crit">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>This action can't be undone automatically. It will be recorded in the audit log.</div>
          </div>
        )}
        {children}
        {expected && (
          <Field label={expectedLabel ?? `Type ${expected} to confirm`}>
            <Input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={expected} aria-label="Confirmation text" />
          </Field>
        )}
        {askReason && (
          <Field label={reasonRequired ? "Change reason (required)" : "Change reason (optional)"} hint="Stored with the audit record, e.g. a change ticket number.">
            <Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} className="font-sans" />
          </Field>
        )}
      </div>
    </Dialog>
  );
}
