# PostgreSQL Docker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and run a PostgreSQL Docker Compose service that creates the `virgo_pg` database with `admin` credentials and initializes all tables from the three existing SQL files.

**Architecture:** A single official PostgreSQL container exposes container port 5432 on host port 5433, persists database files in a named Docker volume, mounts the existing PostgreSQL configuration, and mounts `pg/init` into the image's standard initialization directory. The official entrypoint executes the numbered SQL files only when the data volume is empty.

**Tech Stack:** Docker Engine 29, Docker Compose 5, PostgreSQL 17 Alpine, SQL

---

The workspace does not currently contain a valid Git repository, so this plan omits commit steps that cannot succeed.

### Task 1: Define the PostgreSQL Compose service

**Files:**
- Create: `docker-compose.yml`
- Read: `pg/postgresql.conf`
- Read: `pg/init/001_device.sql`
- Read: `pg/init/002_conversation.sql`
- Read: `pg/init/003_other.sql`

- [ ] **Step 1: Confirm the Compose definition does not exist yet**

Run:

```powershell
docker compose config
```

Expected: FAIL because the project has no `docker-compose.yml` or `compose.yml`.

- [ ] **Step 2: Create the Compose definition**

Create `docker-compose.yml` with exactly:

```yaml
services:
  postgres:
    image: postgres:17-alpine
    container_name: virgo-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: virgo_pg
      POSTGRES_USER: admin
      POSTGRES_PASSWORD: admin
    ports:
      - "5433:5432"
    volumes:
      - virgo_pg_data:/var/lib/postgresql/data
      - ./pg/init:/docker-entrypoint-initdb.d:ro
      - ./pg/postgresql.conf:/etc/postgresql/postgresql.conf:ro
    command:
      - postgres
      - -c
      - config_file=/etc/postgresql/postgresql.conf
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U admin -d virgo_pg"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s

volumes:
  virgo_pg_data:
```

- [ ] **Step 3: Validate the resolved Compose configuration**

Run:

```powershell
docker compose config
```

Expected: exit code 0; the resolved output contains service `postgres`, database `virgo_pg`, user `admin`, port mapping `5433:5432`, and volume `virgo_pg_data`.

### Task 2: Initialize and verify the database

**Files:**
- Verify: `docker-compose.yml`
- Execute: `pg/init/001_device.sql`
- Execute: `pg/init/002_conversation.sql`
- Execute: `pg/init/003_other.sql`

- [ ] **Step 1: Start PostgreSQL and wait for health**

Run:

```powershell
docker compose up -d --wait
```

Expected: exit code 0 and container `virgo-postgres` reports healthy. If Docker must download `postgres:17-alpine`, wait for the pull to finish.

- [ ] **Step 2: Verify all expected tables over an authenticated TCP connection**

Run:

```powershell
docker compose exec -T -e PGPASSWORD=admin postgres psql -h 127.0.0.1 -U admin -d virgo_pg -v ON_ERROR_STOP=1 -Atc "SELECT string_agg(tablename, ',' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'public';"
```

Expected exactly:

```text
accounts,contacts,conversations,devices,message_recipients,message_state_history,messages,products,sim_cards
```

- [ ] **Step 3: Verify the database identity and effective user**

Run:

```powershell
docker compose exec -T -e PGPASSWORD=admin postgres psql -h 127.0.0.1 -U admin -d virgo_pg -v ON_ERROR_STOP=1 -Atc "SELECT current_database() || ':' || current_user;"
```

Expected exactly:

```text
virgo_pg:admin
```

- [ ] **Step 4: Check initialization logs for SQL errors**

Run:

```powershell
docker compose logs postgres
```

Expected: log entries reference `001_device.sql`, `002_conversation.sql`, and `003_other.sql` in that order, followed by a ready-to-accept-connections message; no `ERROR` or initialization failure appears.

- [ ] **Step 5: Leave the verified service running**

Run:

```powershell
docker compose ps
```

Expected: `virgo-postgres` remains running and healthy on host port `5433`.
