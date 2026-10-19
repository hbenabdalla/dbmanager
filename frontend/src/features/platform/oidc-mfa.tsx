import { useState } from "react";
import { Field, Input } from "@/components/ui/input";

/** RFC 8176 methods accepted as a second factor when no list is configured (app.services.oidc.MFA_AMR_VALUES). */
export const DEFAULT_AMR = ["mfa", "otp", "hwk", "swk", "sc", "fpt", "face", "iris", "retina", "vbm", "pin"];

const list = (v: unknown): string => (Array.isArray(v) ? v.join(", ") : typeof v === "string" ? v : "");
const parse = (s: string): string[] => s.split(",").map((x) => x.trim()).filter(Boolean);

/**
 * OIDC multi-factor settings stored in the provider configuration: with ``require_mfa`` the ID token must
 * carry an accepted ``amr`` value or one of the required ``acr`` values, otherwise sign-in is refused.
 */
/** A comma-separated list input that keeps what is typed (including a trailing comma) and reports the parsed list. */
function ListInput({ value, onChange, ...rest }: { value: unknown; onChange: (v: string[]) => void; id?: string; disabled?: boolean; placeholder?: string; "aria-describedby"?: string }) {
  const [text, setText] = useState(() => list(value));
  return <Input {...rest} value={text} onChange={(e) => { setText(e.target.value); onChange(parse(e.target.value)); }} />;
}

export function OidcMfaFields({ cfg, onChange }: { cfg: Record<string, unknown>; onChange: (patch: Record<string, unknown>) => void }) {
  const on = Boolean(cfg.require_mfa);
  return (
    <fieldset className="space-y-3 rounded-md border border-border p-3 sm:col-span-2">
      <legend className="px-1 text-[12.5px] font-semibold">Multi-factor authentication</legend>
      <label className="flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={on} onChange={(e) => onChange({ require_mfa: e.target.checked })} />
        Require multi-factor authentication (checked in the ID token's amr / acr claims)
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Request acr_values" hint="Sent with the sign-in request, e.g. an MFA policy of your provider">
          <Input value={String(cfg.acr_values ?? "")} onChange={(e) => onChange({ acr_values: e.target.value || undefined })} />
        </Field>
        <Field label="Accepted acr values" hint="Comma separated; any of them satisfies the requirement">
          <ListInput value={cfg.required_acr} disabled={!on} onChange={(v) => onChange({ required_acr: v })} />
        </Field>
        <Field label="Accepted amr values" hint="Comma separated; empty uses the RFC 8176 multi-factor methods" className="sm:col-span-2">
          <ListInput value={cfg.mfa_amr_values} disabled={!on} placeholder={DEFAULT_AMR.join(", ")} onChange={(v) => onChange({ mfa_amr_values: v })} />
        </Field>
      </div>
    </fieldset>
  );
}
