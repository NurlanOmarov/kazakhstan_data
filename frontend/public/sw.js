// Service worker для установки PWA и офлайн-запуска оболочки.
// ВАЖНО: персональные данные (ответы /api/) НИКОГДА не кешируются.
const CACHE = "kz-shell-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // Данные и аутентификация — только сеть, без кеша.
  if (url.pathname.startsWith("/api/")) return;

  // Статика приложения (хешированные бандлы, иконки) — cache-first.
  if (url.pathname.startsWith("/assets/") ||
      /\.(png|svg|css|js|webmanifest|woff2?)$/.test(url.pathname)) {
    e.respondWith(
      caches.open(CACHE).then((c) =>
        c.match(e.request).then((hit) =>
          hit || fetch(e.request).then((resp) => {
            if (resp.ok) c.put(e.request, resp.clone());
            return resp;
          }),
        ),
      ),
    );
    return;
  }

  // Навигация — network-first, офлайн-фолбэк на закешированную оболочку.
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request)
        .then((resp) => {
          caches.open(CACHE).then((c) => c.put("/", resp.clone()));
          return resp;
        })
        .catch(() => caches.match("/")),
    );
  }
});
