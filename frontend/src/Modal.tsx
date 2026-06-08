import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Icon } from "./Icon";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}

/** Доступная модалка: role=dialog, закрытие по Escape и клику вне, возврат фокуса. */
export function Modal({ title, onClose, children, wide }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.activeElement as HTMLElement | null;
    ref.current
      ?.querySelector<HTMLElement>("input, button, select, textarea, [tabindex]")
      ?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="modal-back" onClick={onClose}>
      <div
        className={`modal${wide ? " wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <b>{title}</b>
          <button type="button" className="icon-btn" aria-label="Закрыть" onClick={onClose}>
            <Icon name="x" size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
