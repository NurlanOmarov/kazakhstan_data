# Kazakhstan Data — Подключение к серверу

## Параметры сервера
- **Host:** `69.197.178.118`
- **User:** `administrator`
- **Port:** `4822`
- **Auth:** Только SSH-ключи (пароль отключён)
- **ОС:** Ubuntu 22.04 LTS

## Подключение
```bash
ssh -p 4822 administrator@69.197.178.118
```

## Структура на сервере
- **Проект:** `/home/administrator/kazakhstan_data`
- **Сжатый том БД (Btrfs+zstd):** `./data` (loop-образ `data.btrfs.img`, ~12 ГБ → ~3 ГБ физически)
- **Центральный Nginx (других проектов):** `/home/administrator/labai/nginx/nginx.prod.conf` — **НЕ трогаем**
- **Документация сервера:** `/home/administrator/SERVER_GUIDE.md`

## Доступ к сервису
- **Домена нет** — доступ по IP через собственный nginx проекта (self-signed TLS).
- **URL:** `https://69.197.178.118:8443`
- Браузер один раз предупредит о самоподписанном сертификате — это нормально для доступа по IP.

## Docker контейнеры
| Контейнер | Описание |
|-----------|----------|
| `kazakhstan_data-app-1` | FastAPI + gunicorn (2 воркера), внутренний порт 8000 |
| `kazakhstan_data-nginx-1` | Nginx + self-signed TLS, внешний порт **8443** → 443 |

> Собственный nginx, изолирован от центрального `labai-nginx-1`. Другие проекты не затрагиваются.

## Занятые порты на сервере (для справки)
80, 443 (labai-nginx), 8000 (transfer_backend), 8085 (transfer_frontend),
3000/3001/3002/3005/3007/3010 (прочие проекты), 4822 (SSH).
**Наш порт: 8443** (свободен).

## Деплой
- **Автоматический:** Push в `main` → GitHub Actions → SSH на сервер → `git pull` + `docker compose up -d --build`.
- **Ручной:**
```bash
ssh -p 4822 administrator@69.197.178.118
cd /home/administrator/kazakhstan_data
git pull origin main
docker compose up -d --build
```

## База данных
- `kazakhstan_data.db` (~12 ГБ, read-only) и `users.db` (rw, учётки+аудит) лежат на **сжатом Btrfs-томе** `./data`.
- Том монтируется через loop-образ `data.btrfs.img` с `compress-force=zstd:6`.
- Автомонтирование при загрузке прописано в `/etc/fstab`.
- БД переносится один раз (статична). `users.db` бэкапить регулярно.

## GitHub Actions секреты
| Секрет | Значение / Описание |
|--------|---------------------|
| `SERVER_HOST` | `69.197.178.118` |
| `SERVER_USER` | `administrator` |
| `SSH_PORT` | `4822` |
| `SSH_PRIVATE_KEY` | Приватный ключ деплоя (ed25519, `github-actions-kazakhstan-data`) |
| `KZ_JWT_SECRET` | Секрет подписи JWT (64 hex) |
| `KZ_ADMIN_PASSWORD` | Пароль первичного админа |

## Git на сервере (server → GitHub)
Сервер тянет репозиторий через выделенный deploy key + алиас в `~/.ssh/config`:
```
Host github.com-kazakhstan-data
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_kazakhstan_data
    IdentitiesOnly yes
```
Remote: `git@github.com-kazakhstan-data:NurlanOmarov/kazakhstan_data.git`
Публичный ключ добавлен в **Deploy keys** репозитория на GitHub.

## Логи
```bash
cd /home/administrator/kazakhstan_data
docker compose logs -f app      # приложение + аудит входов
docker compose logs -f nginx    # nginx
docker stats --no-stream
```

## Автозапуск
- `restart: unless-stopped` у обоих контейнеров.
- Btrfs-том монтируется через `/etc/fstab` при старте ОС.

---
*Создано 08.06.2026*
