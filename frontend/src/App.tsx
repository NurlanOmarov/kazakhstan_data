import { useState } from "react";
import { Icon } from "./Icon";
import { AuthProvider, useAuth } from "./auth";
import { ToastProvider } from "./toast";
import { MaskProvider, useMask } from "./mask";
import { Login } from "./Login";
import { SearchPage } from "./SearchPage";
import { Admin } from "./Admin";
import { Profile } from "./Profile";
import "./App.css";

type View = "search" | "profile" | "admin";

function Shell() {
  const { user, loading, logout } = useAuth();
  const mask = useMask();
  const [view, setView] = useState<View>("search");

  if (loading) return <div className="empty">Загрузка…</div>;
  if (!user) return <Login />;

  return (
    <div className="app">
      <header className="topbar">
        <span className="logo" aria-hidden="true"><Icon name="search" size={22} /></span>
        <h1>Поиск по базе</h1>
        <nav className="nav">
          <button className={view === "search" ? "active" : ""} onClick={() => setView("search")}>
            Поиск
          </button>
          <button className={view === "profile" ? "active" : ""} onClick={() => setView("profile")}>
            Профиль
          </button>
          {user.role === "admin" && (
            <button className={view === "admin" ? "active" : ""} onClick={() => setView("admin")}>
              Админка
            </button>
          )}
        </nav>
        <span className="user">
          <button className="mask-toggle" title="Маскировать ИИН и телефоны в выдаче"
            onClick={() => mask.setMasking(!mask.masking)}>
            {mask.masking
              ? <><Icon name="eye-off" size={16} /> ПДн скрыты</>
              : <><Icon name="eye" size={16} /> ПДн видны</>}
          </button>
          <span className="muted">{user.username}</span>
          <button className="link-btn" onClick={logout}>Выйти</button>
        </span>
      </header>
      <main>
        {view === "search" && <SearchPage />}
        {view === "profile" && <Profile />}
        {view === "admin" && user.role === "admin" && <Admin />}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <MaskProvider>
          <Shell />
        </MaskProvider>
      </AuthProvider>
    </ToastProvider>
  );
}
