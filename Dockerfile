# --- Этап 1: сборка фронтенда ---
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Этап 2: бэкенд ---
FROM python:3.12-slim AS backend
WORKDIR /app

# Непривилегированный пользователь
RUN useradd -m -u 10001 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py search.py auth.py address.py init_users.py build_aux.py ./
COPY --from=frontend /fe/dist ./frontend/dist

# БД (kazakhstan_data.db, users.db) монтируются как volume, в образ не кладём.
USER appuser
EXPOSE 8000

# 2 воркера uvicorn под gunicorn (сервер ограничен по RAM ~4 ГБ).
# Access-log без query-string (%(U)s — путь без параметров): ПДн не утекают в логи.
CMD ["gunicorn", "app:app", "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000", "--timeout", "60", \
     "--forwarded-allow-ips", "*", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--access-logformat", "%(h)s \"%(m)s %(U)s\" %(s)s %(b)s %(D)s"]
