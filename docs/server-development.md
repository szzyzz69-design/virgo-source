# Virgo

Virgo is a FastAPI-based SMS gateway service. It manages Android SMS devices, SIM cards, outbound message dispatch, mobile polling, delivery status updates, inbound SMS records, and a small PostgreSQL admin UI.

The project is designed around a business API for creating SMS tasks and a mobile API for registered devices to receive and report work.

## Features

- Device registration and token-based device authentication.
- Outbound SMS creation with idempotency support.
- Online device dispatch through Server-Sent Events.
- Mobile fallback polling for pending messages.
- Delivery status updates from mobile devices.
- Inbound SMS submission and conversation tracking.
- PostgreSQL schema initialization through Docker Compose.
- NiceGUI-powered database admin page at `/admin/db`.

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- PostgreSQL 17
- psycopg 3
- NiceGUI
- pytest

## Project Layout

```text
app/
  api/          FastAPI routers for business and mobile APIs
  schemas/      Pydantic request and response models
  services/     Application services and database-backed workflows
  application.py
  config.py
pg/
  init/         PostgreSQL initialization SQL
  admin_ui.py   NiceGUI database admin UI
  admin_service.py
tests/          Unit and integration tests
docker/         Container files
docs/           Design notes and API testing guide
main.py         ASGI application entrypoint
```

## Configuration

Create a local config file from the example:

```powershell
Copy-Item config.example.toml config.toml
```

`config.toml` is intentionally ignored by git. Replace the example secrets before using anything beyond local development:

```toml
private_registration_token = "replace-with-a-long-random-registration-secret"
business_api_token = "replace-with-a-long-random-business-secret"
device_online_window_seconds = 300
```

The app also requires environment variables:

```powershell
$env:DATABASE_URL = "postgresql://admin:admin@127.0.0.1:5433/virgo_pg"
$env:VIRGO_CONFIG_FILE = "config.toml"
```

See `.env.example` for the same values in dotenv-style format.

## Quick Start

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Start PostgreSQL:

```powershell
docker compose up -d postgres
```

Start the API server:

```powershell
uvicorn main:app --reload
```

Useful local URLs:

- API docs: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- Database admin UI: <http://127.0.0.1:8000/admin/db>

## API Overview

All protected endpoints use `Authorization: Bearer <token>`.

| Method | Path | Auth token | Purpose |
| --- | --- | --- | --- |
| `POST` | `/mobile/v1/device` | `private_registration_token` | Register a mobile SMS device and its SIM cards. |
| `PATCH` | `/mobile/v1/device` | Device token | Update device metadata, push token, or SIM card state. |
| `GET` | `/mobile/v1/events` | Device token | Open an SSE stream for real-time outbound message notifications. |
| `POST` | `/business/v1/messages` | `business_api_token` | Create an outbound SMS task. Requires `Idempotency-Key`. |
| `GET` | `/mobile/v1/message?order=fifo` | Device token | Pull pending outbound messages. `order` can be `fifo` or `lifo`. |
| `PATCH` | `/mobile/v1/message` | Device token | Report delivery state for one or more messages. |
| `POST` | `/mobile/v1/inbox` | Device token | Submit inbound SMS records from a mobile device. |

## Testing

Run the test suite with:

```powershell
pytest
```

The NiceGUI admin UI is disabled during pytest runs so API and service tests can run without starting the UI runtime.

## Database

The root `docker-compose.yml` starts PostgreSQL on local port `5433` and mounts `pg/init` as the initialization directory. The schema covers devices, SIM cards, contacts, conversations, outbound messages, message status history, inbound messages, products, and accounts.

If you need to reset local data, stop the database and remove the `virgo_pg_data` Docker volume.

## Security Notes

- Do not commit `config.toml` or real production secrets.
- Use long random values for both configured tokens.
- Treat device tokens returned by registration as credentials.
- Rotate tokens if a local development config was shared accidentally.
