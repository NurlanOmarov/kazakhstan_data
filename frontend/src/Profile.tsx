import { useEffect, useState } from "react";
import {
  listBookmarks, deleteBookmark, myActivity, twofaSetup, twofaEnable, twofaDisable,
} from "./api";
import type { Bookmark, AuditEvent } from "./api";
import { ResidentCard } from "./ResultsTable";
import { Modal } from "./Modal";
import { useToast } from "./toast";
import { useAuth } from "./auth";
import { useMask } from "./mask";
import { formatDateTime } from "./format";
import { Icon } from "./Icon";

export function Profile() {
  const [tab, setTab] = useState<"bookmarks" | "activity" | "security">("bookmarks");
  return (
    <div className="admin">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "bookmarks"}
          className={tab === "bookmarks" ? "active" : ""} onClick={() => setTab("bookmarks")}>
          <Icon name="star" size={15} /> Закладки
        </button>
        <button role="tab" aria-selected={tab === "activity"}
          className={tab === "activity" ? "active" : ""} onClick={() => setTab("activity")}>
          Моя активность
        </button>
        <button role="tab" aria-selected={tab === "security"}
          className={tab === "security" ? "active" : ""} onClick={() => setTab("security")}>
          Безопасность
        </button>
      </div>
      {tab === "bookmarks" && <Bookmarks />}
      {tab === "activity" && <Activity />}
      {tab === "security" && <Security />}
    </div>
  );
}

function Bookmarks() {
  const toast = useToast();
  const [items, setItems] = useState<Bookmark[]>([]);
  const [fields, setFields] = useState<string[]>([]);
  const [open, setOpen] = useState<Bookmark | null>(null);

  const load = () =>
    listBookmarks().then((r) => { setItems(r.bookmarks); setFields(r.fields); }).catch(() => {});
  useEffect(() => { load(); }, []);

  const remove = async (b: Bookmark, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteBookmark(b.id);
      toast("Удалено из закладок");
      load();
    } catch {
      toast("Не удалось удалить", "err");
    }
  };

  if (!items.length)
    return <div className="empty"><span className="ico"><Icon name="star" size={40} /></span>
      <p><b>Закладок пока нет</b></p>
      <p className="muted">Сохраняйте найденные карточки кнопкой «В закладки»</p></div>;

  return (
    <>
      <div className="bm-grid">
        {items.map((b) => (
          <button className="bm-card" key={b.id} onClick={() => setOpen(b)}>
            <span className="bm-name">{b.label}</span>
            <span className="muted">{formatDateTime(b.created_at)}</span>
            <span className="bm-del" title="Удалить" onClick={(e) => remove(b, e)}><Icon name="x" size={15} /></span>
          </button>
        ))}
      </div>
      {open && (
        <Modal title={open.label} onClose={() => setOpen(null)}>
          <div className="modal-body detail-body">
            <ResidentCard row={open.data} fields={fields} tokens={[]} onNeighbors={() => {}} />
          </div>
        </Modal>
      )}
    </>
  );
}

function Activity() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  useEffect(() => { myActivity().then(setEvents).catch(() => setEvents([])); }, []);
  return (
    <div className="table-wrap">
      <p className="muted" style={{ margin: "0 0 8px" }}>
        Полный журнал ваших действий — для прозрачности и самоконтроля.
      </p>
      <table className="admin-table">
        <thead>
          <tr><th>Время</th><th>Действие</th><th>IP</th><th>Найдено</th><th>Детали</th></tr>
        </thead>
        <tbody>
          {events.length === 0 && (
            <tr><td colSpan={5} className="muted" style={{ textAlign: "center" }}>Событий пока нет</td></tr>
          )}
          {events.map((e) => (
            <tr key={e.id}>
              <td>{formatDateTime(e.ts)}</td>
              <td>{ACTION_RU[e.action] ?? e.action}</td>
              <td>{e.ip}</td>
              <td>{e.result_count ?? ""}</td>
              <td className="addr">{e.detail ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Security() {
  const { user, refresh } = useAuth();
  const mask = useMask();
  const toast = useToast();
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [otp, setOtp] = useState("");
  const [disabling, setDisabling] = useState(false);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);

  const startSetup = async () => {
    try {
      setSetup(await twofaSetup());
    } catch {
      toast("Не удалось начать настройку", "err");
    }
  };

  const enable = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await twofaEnable(otp);
      toast("Двухфакторная аутентификация включена");
      setSetup(null); setOtp("");
      await refresh();
    } catch (err) {
      toast((err as Error).message || "Неверный код", "err");
    } finally {
      setBusy(false);
    }
  };

  const disable = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await twofaDisable(pw, otp);
      toast("2FA отключена");
      setDisabling(false); setPw(""); setOtp("");
      await refresh();
    } catch (err) {
      toast((err as Error).message || "Ошибка", "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="security">
      <section className="sec-block">
        <h3>Маскирование персональных данных</h3>
        <p className="muted">
          Скрывает ИИН и телефоны в выдаче. Полное значение раскрывается по клику —
          каждое раскрытие фиксируется в журнале.
        </p>
        <label className="switch-row">
          <input type="checkbox" checked={mask.masking}
            onChange={(e) => mask.setMasking(e.target.checked)} />
          <span>{mask.masking ? "Включено" : "Выключено"}</span>
        </label>
      </section>

      <section className="sec-block">
        <h3>Двухфакторная аутентификация (2FA)</h3>
        {user?.totp_enabled ? (
          <>
            <p className="ok-text"><Icon name="check" size={16} /> 2FA включена — при входе запрашивается код.</p>
            {!disabling ? (
              <button className="btn-ghost" onClick={() => setDisabling(true)}>Отключить 2FA</button>
            ) : (
              <form onSubmit={disable} className="sec-form">
                <label className="field"><span>Текущий пароль</span>
                  <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} required /></label>
                <label className="field"><span>Код из приложения</span>
                  <input value={otp} inputMode="numeric" maxLength={6}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} required /></label>
                <div className="actions">
                  <button className="btn-danger" type="submit" disabled={busy}>Отключить</button>
                  <button className="btn-ghost" type="button" onClick={() => setDisabling(false)}>Отмена</button>
                </div>
              </form>
            )}
          </>
        ) : !setup ? (
          <>
            <p className="muted">
              Защитите вход одноразовым кодом из приложения (Google Authenticator,
              1Password, и т.п.). Особенно рекомендуется администраторам.
            </p>
            <button className="btn-primary" onClick={startSetup}>Настроить 2FA</button>
          </>
        ) : (
          <form onSubmit={enable} className="sec-form">
            <p>1. Добавьте ключ в приложение-аутентификатор (ручной ввод):</p>
            <code className="totp-secret">{setup.secret}</code>
            <p className="muted" style={{ wordBreak: "break-all", fontSize: ".8rem" }}>{setup.uri}</p>
            <label className="field"><span>2. Введите код из приложения для подтверждения</span>
              <input autoFocus value={otp} inputMode="numeric" maxLength={6}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} required /></label>
            <div className="actions">
              <button className="btn-primary" type="submit" disabled={busy}>Включить</button>
              <button className="btn-ghost" type="button" onClick={() => { setSetup(null); setOtp(""); }}>Отмена</button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}

const ACTION_RU: Record<string, string> = {
  login_success: "вход", login_fail: "ошибка входа", logout: "выход",
  search: "поиск", export: "экспорт", neighbors: "соседи", connections: "связи",
  reveal: "раскрытие поля", "2fa_enable": "включил 2FA", "2fa_disable": "отключил 2FA",
  anomaly: "аномалия активности",
};
