import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "./Icon";

type Kind = "ok" | "err";
interface Toast {
  id: number;
  msg: string;
  kind: Kind;
}

type Push = (msg: string, kind?: Kind) => void;
const ToastCtx = createContext<Push>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const push = useCallback<Push>((msg, kind = "ok") => {
    const id = ++idRef.current;
    setItems((x) => [...x, { id, msg, kind }]);
    window.setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3500);
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite" role="status">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <Icon name={t.kind === "err" ? "alert" : "check"} size={16} />{" "}
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  return useContext(ToastCtx);
}
