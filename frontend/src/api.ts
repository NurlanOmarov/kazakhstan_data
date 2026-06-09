export interface Resident {
  [key: string]: string | number | null;
}

export interface SearchResponse {
  results: Resident[];
  total: number;
  mode: "exact" | "fuzzy" | "none";
  error: string | null;
  fields: string[];
  count: number;
  page: number;
  page_size: number;
}

export interface SearchParams {
  fio?: string;
  surname?: string;
  name?: string;
  patronymic?: string;
  dob?: string;
  inn?: string;
  phone?: string;
  address?: string;
  city?: string;
  district?: string;
  street?: string;
  house?: string;
  gender?: string;
  age_min?: string;
  age_max?: string;
  citizenship?: string;
  nationality?: string;
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_dir?: string;
}

export interface User {
  username: string;
  role: "user" | "admin";
  full_name?: string;
  totp_enabled?: boolean;
}

export interface AdminUser {
  id: number;
  username: string;
  role: string;
  full_name: string;
  note: string;
  is_active: number;
  created_at: string;
  created_by: string;
  expires_at: string;
  daily_search_limit: number;
  daily_export_limit: number;
  last_login_at: string;
  last_login_ip: string;
  login_count: number;
  failed_attempts: number;
  locked_until: string;
  totp_enabled: number;
}

export interface AuditEvent {
  id: number;
  ts: string;
  username: string | null;
  action: string;
  ip: string;
  detail: string | null;
  result_count: number | null;
}

const JSON_OPTS: RequestInit = { credentials: "include" };

async function handle(res: Response) {
  if (res.status === 401) throw new ApiError("Не авторизован", 401);
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) msg = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(msg, res.status);
  }
  return res.json();
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v).trim() !== "")
      u.set(k, String(v).trim());
  });
  return u.toString();
}

// ---------- Auth ----------
export type LoginResult = User | { otp_required: true };

export async function apiLogin(
  username: string, password: string, otp?: string, remember?: boolean,
): Promise<LoginResult> {
  const body = new URLSearchParams({ username, password });
  if (otp) body.set("otp", otp);
  if (remember) body.set("remember", "1");
  return handle(
    await fetch("/api/login", { method: "POST", body, credentials: "include" }),
  );
}

export async function apiLogout(): Promise<void> {
  await fetch("/api/logout", { method: "POST", credentials: "include" });
}

export async function apiMe(): Promise<User> {
  return handle(await fetch("/api/me", JSON_OPTS));
}

// ---------- Search ----------
export async function searchResidents(
  params: SearchParams,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const res = await fetch(`/api/search?${qs(params as Record<string, unknown>)}`, {
    ...JSON_OPTS,
    signal,
  });
  return handle(res);
}

export async function fetchSuggest(field: string, q: string): Promise<string[]> {
  if (!q.trim()) return [];
  const res = await fetch(`/api/suggest?${qs({ field, q })}`, JSON_OPTS);
  if (!res.ok) return [];
  const body = await res.json();
  return body.results ?? [];
}

export async function fetchNeighbors(
  rowid: number,
): Promise<{ results: Resident[]; total: number; fields: string[] }> {
  return handle(await fetch(`/api/neighbors?${qs({ rowid })}`, JSON_OPTS));
}

export function exportUrl(params: SearchParams): string {
  return `/api/export?${qs(params as Record<string, unknown>)}`;
}

/** Обратный поиск по телефону: все владельцы номера по всей базе. */
export async function phoneLookup(
  phone: string, page = 1, signal?: AbortSignal,
): Promise<SearchResponse> {
  const res = await fetch(
    `/api/phone_lookup?${qs({ phone, page, page_size: 50 })}`,
    { ...JSON_OPTS, signal },
  );
  return handle(res);
}

/** Экспорт выбранных строк (bulk) в Excel — скачивает файл в браузере. */
export async function exportRows(rowids: number[]): Promise<void> {
  const res = await fetch("/api/export_rows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rowids }),
    credentials: "include",
  });
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) msg = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(msg, res.status);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "export-selected.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------- Admin ----------
export async function adminListUsers(): Promise<AdminUser[]> {
  const body = await handle(await fetch("/api/admin/users", JSON_OPTS));
  return body.users;
}

export async function adminCreateUser(form: Record<string, string>): Promise<void> {
  const body = new URLSearchParams(form);
  await handle(
    await fetch("/api/admin/users", { method: "POST", body, credentials: "include" }),
  );
}

export async function adminUpdateUser(
  id: number,
  changes: Record<string, unknown>,
): Promise<void> {
  await handle(
    await fetch(`/api/admin/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
      credentials: "include",
    }),
  );
}

export async function adminResetPassword(id: number, password: string): Promise<void> {
  const body = new URLSearchParams({ password });
  await handle(
    await fetch(`/api/admin/users/${id}/password`, {
      method: "POST",
      body,
      credentials: "include",
    }),
  );
}

export async function adminDeleteUser(id: number): Promise<void> {
  await handle(
    await fetch(`/api/admin/users/${id}`, { method: "DELETE", credentials: "include" }),
  );
}

export async function adminAudit(userId?: number): Promise<AuditEvent[]> {
  const body = await handle(
    await fetch(`/api/admin/audit?${qs({ user_id: userId, limit: 300 })}`, JSON_OPTS),
  );
  return body.events;
}

// ---------- Связи (родственники + по телефону) ----------
export interface Connections {
  relatives: Resident[];
  relatives_total: number;
  phone: Resident[];
  phone_total: number;
  phones: string[];
  fields: string[];
  error: string | null;
}

export async function fetchConnections(rowid: number): Promise<Connections> {
  return handle(await fetch(`/api/connections?${qs({ rowid })}`, JSON_OPTS));
}

// ---------- Раскрытие замаскированного поля (аудит) ----------
export async function revealField(rowid: number, field: string): Promise<void> {
  const body = new URLSearchParams({ rowid: String(rowid), field });
  await fetch("/api/reveal", { method: "POST", body, credentials: "include" });
}

// ---------- Личный кабинет ----------
export interface HistoryItem {
  ts: string;
  params: SearchParams;
  result_count: number | null;
}

export async function myHistory(): Promise<HistoryItem[]> {
  const body = await handle(await fetch("/api/my/history", JSON_OPTS));
  return body.history ?? [];
}

export async function myActivity(): Promise<AuditEvent[]> {
  const body = await handle(await fetch("/api/my/activity", JSON_OPTS));
  return body.events;
}

// ---------- Закладки ----------
export interface Bookmark {
  id: number;
  rowid_ref: number;
  label: string;
  note: string;
  data: Resident;
  created_at: string;
}

export async function listBookmarks(): Promise<{ bookmarks: Bookmark[]; fields: string[] }> {
  return handle(await fetch("/api/bookmarks", JSON_OPTS));
}

export async function addBookmark(
  rowid: number, label: string, data: Resident, note = "",
): Promise<void> {
  await handle(
    await fetch("/api/bookmarks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rowid, label, data, note }),
      credentials: "include",
    }),
  );
}

export async function deleteBookmark(id: number): Promise<void> {
  await handle(
    await fetch(`/api/bookmarks/${id}`, { method: "DELETE", credentials: "include" }),
  );
}

// ---------- 2FA ----------
export async function twofaSetup(): Promise<{ secret: string; uri: string }> {
  return handle(await fetch("/api/2fa/setup", { method: "POST", credentials: "include" }));
}

export async function twofaEnable(otp: string): Promise<void> {
  const body = new URLSearchParams({ otp });
  await handle(await fetch("/api/2fa/enable", { method: "POST", body, credentials: "include" }));
}

export async function twofaDisable(password: string, otp: string): Promise<void> {
  const body = new URLSearchParams({ password, otp });
  await handle(await fetch("/api/2fa/disable", { method: "POST", body, credentials: "include" }));
}

// ---------- Админ-статистика ----------
export interface Stats {
  days: number;
  by_action: Record<string, number>;
  top_users: { username: string; searches: number; exports: number; total: number }[];
  by_day: { day: string; searches: number; exports: number; logins: number }[];
  failed_logins: number;
  anomalies: { ts: string; user_id: number | null; username: string; detail: string }[];
}

export async function adminStats(days = 7): Promise<Stats> {
  return handle(await fetch(`/api/admin/stats?${qs({ days })}`, JSON_OPTS));
}
