import { useState } from "react";
import { toast } from "sonner";
import { ApiError, post } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

export function ChangePasswordDialog({ open, onOpenChange, forced }: { open: boolean; onOpenChange: (o: boolean) => void; forced?: boolean }) {
  const { refresh } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (next !== confirm) return setError("The new passwords don't match");
    setBusy(true);
    setError(null);
    try {
      await post("/auth/change-password", { current_password: current, new_password: next });
      toast.success("Password changed");
      refresh();
      onOpenChange(false);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (e) {
      const err = e as ApiError;
      const details = (err.details?.password as string[] | undefined)?.join("; ");
      setError(details ? `${err.message}: ${details}` : err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => (!forced || !o ? onOpenChange(o) : undefined)} title="Change password"
      description={forced ? "Your administrator requires a new password before you continue." : "At least 12 characters with three of: lowercase, uppercase, digit, symbol."}
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)} disabled={forced}>Cancel</Button><Button variant="primary" onClick={submit} disabled={busy || !current || !next}>Change password</Button></>}>
      <div className="space-y-3">
        <Field label="Current password"><Input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
        <Field label="New password"><Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
        <Field label="Confirm new password"><Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
        {error && <p role="alert" className="text-[13px] text-crit">{error}</p>}
      </div>
    </Dialog>
  );
}
