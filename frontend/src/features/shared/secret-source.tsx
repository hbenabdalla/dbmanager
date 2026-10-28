import { useQuery } from "@tanstack/react-query";
import { get } from "@/api/client";
import { Field, Input, Select } from "@/components/ui/input";
import { useOptionalAuth } from "@/lib/auth";

export type SecretProvider = "local" | "env" | "file" | "vault";

/** GET /platform/secret-providers */
export interface SecretProviders {
  local: { available: boolean };
  env: { available: boolean };
  file: { available: boolean; allowed_dirs?: string[] };
  vault: {
    available: boolean; configured?: boolean; reachable?: boolean; initialized?: boolean | null; sealed?: boolean | null;
    version?: string | null; auth?: string; error?: string; kv_mount?: string;
  };
}

export const PROVIDER_LABEL: Record<SecretProvider, string> = {
  local: "Encrypted in KBM DBManager",
  env: "Environment variable",
  file: "Mounted secret file",
  vault: "HashiCorp Vault (KV v2)",
};

/** Mirrors app.services.vault.REF: ``[mount:]path#key``. */
const VAULT_REF = /^(?:([A-Za-z0-9_\-/]{1,64}):)?([A-Za-z0-9_\-./]{1,256})#([A-Za-z0-9_\-.]{1,128})$/;
export const VAULT_REF_HINT = "path/to/secret#key or mount:path/to/secret#key, e.g. databases/pg-prod#password";

/** Mirrors app.services.secrets._RESERVED_ENV_PREFIXES. */
const RESERVED_ENV_PREFIXES = ["DBM_", "POSTGRES_", "PG", "AWS_", "AZURE_", "GOOGLE_"];

/** Client-side check of a secret reference; the backend validates again. Returns an error or null. */
export function secretRefError(provider: SecretProvider, ref: string): string | null {
  const v = ref.trim();
  if (provider === "local") return null;
  if (!v) return provider === "env" ? "Enter the variable name" : provider === "file" ? "Enter the file path" : "Enter the Vault reference";
  if (provider === "env") {
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(v)) return "Letters, digits and underscores; must not start with a digit";
    const reserved = RESERVED_ENV_PREFIXES.find((x) => v.toUpperCase().startsWith(x));
    if (reserved) return `Variables starting with ${reserved} belong to the platform and cannot hold database secrets`;
  }
  if (provider === "file" && !v.startsWith("/")) return "Use an absolute path";
  if (provider === "vault") {
    const m = VAULT_REF.exec(v);
    if (!m || m[2].includes("..") || m[2].startsWith("/")) return `Vault references look like ${VAULT_REF_HINT}`;
  }
  return null;
}

/** Secret providers of this installation (needs credentials.manage somewhere; null otherwise). */
export function useSecretProviders() {
  const auth = useOptionalAuth();
  return useQuery({
    queryKey: ["secret-providers"],
    queryFn: () => get<SecretProviders>("/platform/secret-providers"),
    enabled: !!auth?.can("credentials.manage"),
    retry: false,
    staleTime: 60_000,
  });
}

/** One-line state of the Vault provider. */
export function vaultStatus(v: SecretProviders["vault"] | undefined): { tone: "ok" | "warn" | "crit" | "neutral"; text: string } {
  if (!v) return { tone: "neutral", text: "Status unknown" };
  if (!v.configured && !v.available) return { tone: "neutral", text: "Not configured (DBM_VAULT_ADDR is empty)" };
  if (v.reachable === false) return { tone: "crit", text: `Configured but unreachable${v.error ? ` (${v.error})` : ""}` };
  if (v.sealed) return { tone: "crit", text: "Reachable but sealed" };
  if (v.initialized === false) return { tone: "warn", text: "Reachable but not initialized" };
  return { tone: "ok", text: `Reachable${v.version ? `, Vault ${v.version}` : ""}${v.auth ? `, ${v.auth} auth` : ""}${v.kv_mount ? `, default mount "${v.kv_mount}"` : ""}` };
}

const TONE_CLASS = { ok: "text-ok", warn: "text-warn", crit: "text-crit", neutral: "text-muted" } as const;

export function VaultStatusLine({ vault }: { vault: SecretProviders["vault"] | undefined }) {
  const s = vaultStatus(vault);
  return <span className={`text-[12px] ${TONE_CLASS[s.tone]}`} data-testid="vault-status">Vault: {s.text}</span>;
}

/**
 * Secret source selector + password or reference input. ``provider``/``secretRef``/``password``
 * are controlled by the caller. Vault is offered only when the installation configured it (or
 * when the credential already uses it).
 */
export function SecretSourceFields({ provider, onProvider, secretRef, onSecretRef, password, onPassword, passwordLabel = "Password", passwordHint }: {
  provider: SecretProvider; onProvider: (p: SecretProvider) => void; secretRef: string; onSecretRef: (v: string) => void;
  password: string; onPassword: (v: string) => void; passwordLabel?: string; passwordHint?: string;
}) {
  const providers = useSecretProviders();
  const vault = providers.data?.vault;
  const vaultOffered = !!vault?.available || provider === "vault";
  const refErr = secretRefError(provider, secretRef);
  const dirs = providers.data?.file.allowed_dirs;
  const hint = provider === "vault"
    ? `${VAULT_REF_HINT}. Read at connection time, never stored.`
    : provider === "file"
      ? `Must be under ${dirs?.length ? dirs.join(", ") : "/run/secrets or another allowed directory"}`
      : "Read by the backend at connection time";
  return (
    <>
      <Field label="Secret source">
        <Select value={provider} onChange={(e) => onProvider(e.target.value as SecretProvider)} aria-label="Secret source">
          <option value="local">{PROVIDER_LABEL.local}</option>
          <option value="env">{PROVIDER_LABEL.env}</option>
          <option value="file">{PROVIDER_LABEL.file}</option>
          {vaultOffered && <option value="vault">{PROVIDER_LABEL.vault}</option>}
        </Select>
      </Field>
      {provider === "local" ? (
        <Field label={passwordLabel} hint={passwordHint}>
          <Input type="password" autoComplete="new-password" value={password} onChange={(e) => onPassword(e.target.value)} />
        </Field>
      ) : (
        <Field label={provider === "env" ? "Variable name" : provider === "file" ? "File path" : "Vault reference"} hint={hint}
          error={secretRef.trim() ? refErr ?? undefined : undefined}>
          <Input value={secretRef} onChange={(e) => onSecretRef(e.target.value)} autoComplete="off" spellCheck={false}
            placeholder={provider === "env" ? "SECRET_PG_ORDERS" : provider === "file" ? "/run/secrets/pg-orders" : "databases/pg-prod#password"} />
        </Field>
      )}
      {(provider === "vault" || vault?.available) && providers.data && (
        <div className="sm:col-span-2"><VaultStatusLine vault={vault} /></div>
      )}
    </>
  );
}
