import { useCallback, useEffect, useState } from "react";
import {
  adminListUsers, adminCreateUser, adminUpdateUser, adminResetPassword,
  adminDeleteUser, adminAudit, adminStats,
} from "./api";
import type { AdminUser, AuditEvent, Stats } from "./api";
import { Modal } from "./Modal";
import { useToast } from "./toast";
import { formatDateTime } from "./format";
import { Icon } from "./Icon";

export function Admin() {
  const [tab, setTab] = useState<"stats" | "users" | "audit">("stats");
  return (
    <div className="admin">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "stats"}
          className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>
          Статистика
        </button>
        <button role="tab" aria-selected={tab === "users"}
          className={tab === "users" ? "active" : ""} onClick={() => setTab("users")}>
          Пользователи
        </button>
        <button role="tab" aria-selected={tab === "audit"}
          className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")}>
          Журнал действий
        </button>
      </div>
      {tab === "stats" && <Dashboard />}
      {tab === "users" && <Users />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

/** Разбор детали аномалии в человекочитаемое объяснение. */
function explainAnomaly(detail: string): { action: string; text: string } {
  let reason = detail;
  try {
    const obj = JSON.parse(detail);
    reason = obj?.reason ?? detail;
  } catch { /* оставляем как есть */ }
  const m = /^(\w+):\s*больше\s*(\d+)/.exec(String(reason));
  if (m) {
    const action = m[1] === "export" ? "экспорт" : m[1] === "search" ? "поиск" : m[1];
    return {
      action,
      text: `Превышен часовой порог: ${action} — более ${m[2]} за час. ` +
        `Возможен массовый сбор данных или скомпрометированная учётка.`,
    };
  }
  return { action: "—", text: String(reason) };
}

function Dashboard() {
  const toast = useToast();
  const [stats, setStats] = useState<Stats | null>(null);
  const [days, setDays] = useState(7);
  const reload = useCallback(
    () => adminStats(days).then(setStats).catch(() => setStats(null)),
    [days],
  );
  useEffect(() => { reload(); }, [reload]);

  const blockUser = async (uid: number | null, username: string) => {
    if (!uid) { toast("Не удалось определить пользователя", "err"); return; }
    try {
      await adminUpdateUser(uid, { is_active: 0 });
      toast(`Пользователь ${username} заблокирован`);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (!stats) return <div className="empty">Загрузка статистики…</div>;

  const a = stats.by_action;
  const maxDay = Math.max(1, ...stats.by_day.map((d) => d.searches + d.exports));

  return (
    <div className="dash">
      <div className="dash-period">
        <span className="muted">Период:</span>
        {[7, 30, 90].map((d) => (
          <button key={d} className={`chip${days === d ? " active" : ""}`} onClick={() => setDays(d)}>
            {d} дн.
          </button>
        ))}
      </div>

      <div className="stat-cards">
        <StatCard label="Поисков" value={a.search ?? 0} />
        <StatCard label="Экспортов" value={a.export ?? 0} />
        <StatCard label="Входов" value={a.login_success ?? 0} />
        <StatCard label="Ошибок входа" value={stats.failed_logins} warn={stats.failed_logins > 0} />
        <StatCard label="Раскрытий ПДн" value={a.reveal ?? 0} />
        <StatCard label="Аномалий" value={stats.anomalies.length} warn={stats.anomalies.length > 0} />
      </div>

      <div className="dash-grid">
        <section className="dash-block">
          <h3>Активность по дням</h3>
          <div className="bars">
            {stats.by_day.length === 0 && <p className="muted">Нет данных</p>}
            {stats.by_day.map((d) => (
              <div className="bar-col" key={d.day} title={`${d.day}: ${d.searches} поисков, ${d.exports} экспортов`}>
                <div className="bar-stack">
                  <div className="bar search" style={{ height: `${(d.searches / maxDay) * 100}%` }} />
                  <div className="bar export" style={{ height: `${(d.exports / maxDay) * 100}%` }} />
                </div>
                <span className="bar-label">{d.day.slice(5)}</span>
              </div>
            ))}
          </div>
          <div className="legend">
            <span><i className="dot search" /> поиск</span>
            <span><i className="dot export" /> экспорт</span>
          </div>
        </section>

        <section className="dash-block">
          <h3>Топ пользователей</h3>
          <table className="admin-table">
            <thead><tr><th>Пользователь</th><th>Поиски</th><th>Экспорты</th><th>Всего</th></tr></thead>
            <tbody>
              {stats.top_users.length === 0 && (
                <tr><td colSpan={4} className="muted" style={{ textAlign: "center" }}>Нет данных</td></tr>
              )}
              {stats.top_users.map((u) => (
                <tr key={u.username}>
                  <td>{u.username}</td><td>{u.searches}</td><td>{u.exports}</td><td><b>{u.total}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      {stats.anomalies.length > 0 && (
        <section className="dash-block warn-block">
          <h3><Icon name="alert" size={17} /> Аномалии активности</h3>
          <p className="muted anomaly-hint">
            Срабатывают при всплеске активности одного пользователя сверх часового порога.
            Проверьте журнал действий этого пользователя; при подозрении — заблокируйте учётку.
          </p>
          <div className="anomaly-timeline">
            {stats.anomalies.map((an, i) => {
              const ex = explainAnomaly(an.detail);
              return (
                <div className="anomaly-item" key={i}>
                  <div className="anomaly-dot" aria-hidden="true" />
                  <div className="anomaly-body">
                    <div className="anomaly-top">
                      <span className="anomaly-user">{an.username || "—"}</span>
                      <span className={`tag tag-${ex.action === "экспорт" ? "export" : "search"}`}>{ex.action}</span>
                      <span className="muted">{formatDateTime(an.ts)}</span>
                    </div>
                    <div className="anomaly-text">{ex.text}</div>
                  </div>
                  <button className="btn-danger sm" onClick={() => blockUser(an.user_id, an.username)}>
                    Заблокировать
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

function StatCard({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className={`stat-card${warn ? " warn" : ""}`}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

function Users() {
  const toast = useToast();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [pwUser, setPwUser] = useState<AdminUser | null>(null);
  const [delUser, setDelUser] = useState<AdminUser | null>(null);

  const load = () => adminListUsers().then(setUsers).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const block = async (u: AdminUser) => {
    try {
      await adminUpdateUser(u.id, { is_active: u.is_active ? 0 : 1 });
      toast(u.is_active ? `${u.username} заблокирован` : `${u.username} разблокирован`);
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const doDelete = async () => {
    if (!delUser) return;
    try {
      await adminDeleteUser(delUser.id);
      toast(`Пользователь ${delUser.username} удалён`);
      setDelUser(null);
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div>
      {err && <p className="meta err"><Icon name="alert" size={16} /> {err}</p>}
      <div className="admin-actions">
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ Создать пользователя</button>
      </div>
      {showCreate && <CreateUser onClose={() => setShowCreate(false)}
        onCreated={() => { setShowCreate(false); load(); }} />}

      {pwUser && <ResetPassword user={pwUser} onClose={() => setPwUser(null)} />}

      {delUser && (
        <Modal title="Удалить пользователя" onClose={() => setDelUser(null)}>
          <div className="modal-body">
            <p>Удалить пользователя <b>{delUser.username}</b>? Действие необратимо.</p>
          </div>
          <div className="actions">
            <button className="btn-danger" onClick={doDelete}>Удалить</button>
            <button className="btn-ghost" onClick={() => setDelUser(null)}>Отмена</button>
          </div>
        </Modal>
      )}

      <div className="table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Логин</th><th>ФИО</th><th>Роль</th><th>Статус</th><th>2FA</th>
              <th>Посл. вход</th><th>IP</th><th>Входов</th><th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "blocked"}>
                <td>{u.username}</td>
                <td>{u.full_name}</td>
                <td>{u.role === "admin" ? "Админ" : "Пользователь"}</td>
                <td>
                  <span className={`status-dot ${u.is_active ? "ok" : "off"}`} aria-hidden="true" />
                  {u.is_active ? "активен" : "заблокирован"}
                </td>
                <td>{u.totp_enabled ? <Icon name="check" size={15} /> : "—"}</td>
                <td>{u.last_login_at ? formatDateTime(u.last_login_at) : "—"}</td>
                <td>{u.last_login_ip || "—"}</td>
                <td>{u.login_count}</td>
                <td className="row-actions">
                  <button className="link-btn" onClick={() => block(u)}>
                    {u.is_active ? "Блок" : "Разблок"}
                  </button>
                  <button className="link-btn" onClick={() => setPwUser(u)}>Пароль</button>
                  <button className="link-btn danger del" onClick={() => setDelUser(u)}>Удалить</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ResetPassword({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const toast = useToast();
  const [pw, setPw] = useState("");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pw.length < 8) { setErr("Минимум 8 символов"); return; }
    setBusy(true);
    setErr(null);
    try {
      await adminResetPassword(user.id, pw);
      toast(`Пароль для ${user.username} изменён`);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Сброс пароля: ${user.username}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body">
          <label className="field full">
            <span>Новый пароль (мин. 8 символов)</span>
            <div className="pw-wrap">
              <input
                type={show ? "text" : "password"}
                value={pw}
                autoComplete="new-password"
                onChange={(e) => setPw(e.target.value)}
                required
              />
              <button type="button" className="pw-toggle" onClick={() => setShow((s) => !s)}
                aria-label={show ? "Скрыть пароль" : "Показать пароль"}>
                {show ? "Скрыть" : "Показать"}
              </button>
            </div>
          </label>
          {err && <p className="login-error"><Icon name="alert" size={16} /> {err}</p>}
        </div>
        <div className="actions">
          <button className="btn-primary" type="submit" disabled={busy}>
            {busy ? "Сохранение…" : "Сохранить"}
          </button>
          <button className="btn-ghost" type="button" onClick={onClose}>Отмена</button>
        </div>
      </form>
    </Modal>
  );
}

function CreateUser({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({
    username: "", password: "", role: "user", full_name: "", note: "",
    expires_at: "", daily_search_limit: "0", daily_export_limit: "0",
  });
  const [err, setErr] = useState<string | null>(null);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (f.password.length < 8) { setErr("Пароль — минимум 8 символов"); return; }
    setErr(null);
    setBusy(true);
    try {
      await adminCreateUser(f);
      toast(`Пользователь ${f.username} создан`);
      onCreated();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Новый пользователь" onClose={onClose} wide>
      <form onSubmit={submit}>
        <div className="modal-body grid">
          <label className="field"><span>Логин *</span>
            <input value={f.username} onChange={(e) => set("username", e.target.value)} required /></label>
          <label className="field"><span>Пароль * (мин. 8)</span>
            <div className="pw-wrap">
              <input type={show ? "text" : "password"} value={f.password} autoComplete="new-password"
                onChange={(e) => set("password", e.target.value)} required />
              <button type="button" className="pw-toggle" onClick={() => setShow((s) => !s)}
                aria-label={show ? "Скрыть пароль" : "Показать пароль"}>
                {show ? "Скрыть" : "Показать"}
              </button>
            </div></label>
          <label className="field"><span>ФИО</span>
            <input value={f.full_name} onChange={(e) => set("full_name", e.target.value)} /></label>
          <label className="field"><span>Роль</span>
            <select value={f.role} onChange={(e) => set("role", e.target.value)}>
              <option value="user">Пользователь</option>
              <option value="admin">Админ</option>
            </select></label>
          <label className="field"><span>Действует до (ГГГГ-ММ-ДД, опц.)</span>
            <input value={f.expires_at} onChange={(e) => set("expires_at", e.target.value)} placeholder="2027-01-01" /></label>
          <label className="field"><span>Лимит поисков/день (0=по умолч.)</span>
            <input type="number" value={f.daily_search_limit} onChange={(e) => set("daily_search_limit", e.target.value)} /></label>
          <label className="field"><span>Лимит экспортов/день</span>
            <input type="number" value={f.daily_export_limit} onChange={(e) => set("daily_export_limit", e.target.value)} /></label>
          <label className="field full"><span>Заметка</span>
            <input value={f.note} onChange={(e) => set("note", e.target.value)} /></label>
        </div>
        {err && <p className="login-error"><Icon name="alert" size={16} /> {err}</p>}
        <div className="actions">
          <button className="btn-primary" type="submit" disabled={busy}>
            {busy ? "Создание…" : "Создать"}
          </button>
          <button className="btn-ghost" type="button" onClick={onClose}>Отмена</button>
        </div>
      </form>
    </Modal>
  );
}

function Audit() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  useEffect(() => { adminAudit().then(setEvents).catch(() => setEvents([])); }, []);
  return (
    <div className="table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>IP</th><th>Найдено</th><th>Детали</th></tr>
        </thead>
        <tbody>
          {events.length === 0 && (
            <tr><td colSpan={6} className="muted" style={{ textAlign: "center" }}>Событий пока нет</td></tr>
          )}
          {events.map((e) => (
            <tr key={e.id}>
              <td>{formatDateTime(e.ts)}</td>
              <td>{e.username ?? "—"}</td>
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

const ACTION_RU: Record<string, string> = {
  login_success: "вход", login_fail: "ошибка входа", logout: "выход",
  search: "поиск", export: "экспорт", neighbors: "соседи", connections: "связи",
  reveal: "раскрытие ПДн", anomaly: "аномалия", "2fa_enable": "вкл. 2FA",
  "2fa_disable": "откл. 2FA",
  admin_create_user: "создал юзера", admin_update_user: "изменил юзера",
  admin_reset_password: "сброс пароля", admin_delete_user: "удалил юзера",
};
