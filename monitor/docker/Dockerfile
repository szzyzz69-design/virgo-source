# syntax=docker/dockerfile:1

FROM node:22-alpine AS supervisor-build
WORKDIR /build
COPY supervisor/package.json supervisor/pnpm-lock.yaml supervisor/pnpm-workspace.yaml supervisor/vite.config.js supervisor/index.html ./
COPY supervisor/src ./src
RUN corepack enable && pnpm install --frozen-lockfile && pnpm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY --from=supervisor-build /build/dist ./supervisor/dist
COPY pg ./pg
COPY main.py ./
COPY config.example.toml ./

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
