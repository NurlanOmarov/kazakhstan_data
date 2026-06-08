import { useState } from "react";
import { Icon } from "./Icon";
import { useAuth } from "./auth";
import { ApiError } from "./api";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [otpStage, setOtpStage] = useState(false);
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const needOtp = await login(username, password, otpStage ? otp : undefined, remember);
      if (needOtp) setOtpStage(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="login-logo" aria-hidden="true"><Icon name="lock" size={32} /></div>
        <h1>Вход в систему</h1>
        <p className="muted">Доступ только для авторизованных сотрудников</p>
        <label className="field">
          <span>Логин</span>
          <input
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={otpStage}
          />
        </label>
        {otpStage && (
          <label className="field">
            <span>Код из приложения (2FA)</span>
            <input
              autoFocus
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="6 цифр"
              maxLength={6}
            />
          </label>
        )}
        <label className="remember-row">
          <input type="checkbox" checked={remember}
            onChange={(e) => setRemember(e.target.checked)} />
          <span>Запомнить меня на этом устройстве (30 дней)</span>
        </label>
        {error && <p className="login-error"><Icon name="alert" size={16} /> {error}</p>}
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Вход…" : otpStage ? "Подтвердить" : "Войти"}
        </button>
      </form>
    </div>
  );
}
