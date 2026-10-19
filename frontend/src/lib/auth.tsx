import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, get, post, setUnauthorizedHandler } from "@/api/client";
import type { Me, MfaChallenge } from "@/api/types";

interface AuthCtx {
  me: Me | undefined;
  loading: boolean;
  /** Permission held anywhere (navigation hint only — the backend enforces). */
  can: (perm: string) => boolean;
  canPlatform: (perm: string) => boolean;
  /** Resolves with the signed-in user, or with an MFA challenge when a second factor is needed. */
  login: (username: string, password: string) => Promise<Me | MfaChallenge>;
  loginMfa: (mfaToken: string, code: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: () => get<Me>("/auth/me"),
    retry: (count, err) => !(err instanceof ApiError && err.status === 401) && count < 2,
    staleTime: 60_000,
  });

  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(["me"], undefined);
      if (!window.location.pathname.startsWith("/login")) {
        navigate(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
      }
    });
  }, [qc, navigate]);

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await post<Me | MfaChallenge>("/auth/login", { username, password });
      if ("mfa_required" in res && res.mfa_required) return res;
      qc.clear();
      qc.setQueryData(["me"], res);
      return res;
    },
    [qc],
  );
  const loginMfa = useCallback(
    async (mfaToken: string, code: string) => {
      const me = await post<Me>("/auth/login/mfa", { mfa_token: mfaToken, code });
      qc.clear();
      qc.setQueryData(["me"], me);
      return me;
    },
    [qc],
  );
  const logout = useCallback(async () => {
    try {
      await post("/auth/logout");
    } finally {
      qc.clear();
      navigate("/login");
    }
  }, [qc, navigate]);

  const perms = new Set(data?.permissions ?? []);
  const platform = new Set(data?.platform_permissions ?? []);
  const value: AuthCtx = {
    me: data,
    loading: isLoading,
    can: (p) => perms.has(p),
    canPlatform: (p) => platform.has(p),
    login,
    loginMfa,
    logout,
    refresh: () => qc.invalidateQueries({ queryKey: ["me"] }),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
