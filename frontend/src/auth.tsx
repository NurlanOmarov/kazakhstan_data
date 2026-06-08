import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiMe, apiLogin, apiLogout } from "./api";
import type { User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  /** Возвращает true, если требуется код 2FA (вход ещё не выполнен). */
  login: (u: string, p: string, otp?: string, remember?: boolean) => Promise<boolean>;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = async (u: string, p: string, otp?: string, remember?: boolean) => {
    const res = await apiLogin(u, p, otp, remember);
    if ("otp_required" in res) return true;
    setUser(res);
    return false;
  };

  const refresh = async () => {
    try {
      setUser(await apiMe());
    } catch {
      setUser(null);
    }
  };

  const logout = async () => {
    await apiLogout();
    setUser(null);
  };

  return (
    <Ctx.Provider value={{ user, loading, login, refresh, logout }}>
      {children}
    </Ctx.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  return useContext(Ctx);
}
