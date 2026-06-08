import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";
import { revealField } from "./api";

interface MaskCtx {
  masking: boolean;
  setMasking: (b: boolean) => void;
  isRevealed: (rowid: number | string | null | undefined, col: string) => boolean;
  reveal: (rowid: number | string | null | undefined, col: string) => void;
}

const Ctx = createContext<MaskCtx>(null as unknown as MaskCtx);
const LS_KEY = "kz_masking";

/**
 * Маскирование ПДн (ИИН, телефоны). По умолчанию включено — снижает риск
 * массового визуального сбора. Раскрытие конкретной ячейки фиксируется в аудите.
 */
export function MaskProvider({ children }: { children: ReactNode }) {
  const [masking, setMaskingState] = useState<boolean>(
    () => localStorage.getItem(LS_KEY) !== "0",
  );
  // Раскрытые ячейки этой сессии: ключ `${rowid}:${col}`.
  const [revealed, setRevealed] = useState<Set<string>>(new Set());

  const setMasking = useCallback((b: boolean) => {
    setMaskingState(b);
    localStorage.setItem(LS_KEY, b ? "1" : "0");
  }, []);

  const isRevealed = useCallback(
    (rowid: number | string | null | undefined, col: string) =>
      revealed.has(`${rowid}:${col}`),
    [revealed],
  );

  const reveal = useCallback(
    (rowid: number | string | null | undefined, col: string) => {
      const key = `${rowid}:${col}`;
      setRevealed((s) => {
        if (s.has(key)) return s;
        const next = new Set(s);
        next.add(key);
        return next;
      });
      // Фиксируем раскрытие в аудите (без ожидания ответа).
      if (rowid != null && rowid !== "") void revealField(Number(rowid), col);
    },
    [],
  );

  return (
    <Ctx.Provider value={{ masking, setMasking, isRevealed, reveal }}>
      {children}
    </Ctx.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useMask() {
  return useContext(Ctx);
}
