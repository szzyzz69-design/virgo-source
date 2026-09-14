from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import tomllib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from app.security import hash_password, hash_sha256


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_AGENT_USERNAME = "agent_test"
DEFAULT_AGENT_PASSWORD = "agent_test_123"
DEFAULT_AREA = "test"
DEFAULT_DEVICE_ID = "dev_agent_inbox_seed"
DEFAULT_DEVICE_TOKEN = "seed-mobile-device-token"
DEFAULT_SIM_ID = "sim_agent_inbox_seed"
DEFAULT_ACCOUNT_ID = "acct_agent_inbox_seed"
DEFAULT_SENDER = "+8613800009527"
DEFAULT_RECIPIENT = "+8613900000000"
DEFAULT_TEXT = "hello from mobile inbox seed"


@dataclass(frozen=True, slots=True)
class InboxSeedConfig:
    base_url: str
    database_url: str
    registration_token: str | None = None
    agent_username: str = DEFAULT_AGENT_USERNAME
    agent_password: str = DEFAULT_AGENT_PASSWORD
    area: str = DEFAULT_AREA
    sender: str = DEFAULT_SENDER
    recipient: str = DEFAULT_RECIPIENT
    text: str = DEFAULT_TEXT
    sim_number: int = 1
    subscription_id: int = 3


@dataclass(frozen=True, slots=True)
class SeededRoute:
    account_id: str
    device_id: str
    device_token: str
    sim_id: str


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("base URL is required")
    return normalized


def build_inbox_payload(
    config: InboxSeedConfig,
    *,
    message_id: str | None = None,
    received_at: str | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id or f"seed-inbox-{uuid4().hex}",
        "type": "SMS",
        "sender": config.sender,
        "recipient": config.recipient,
        "simNumber": config.sim_number,
        "subscriptionId": config.subscription_id,
        "receivedAt": received_at or utc_now_iso(),
        "textMessage": {"text": config.text},
        "dataMessage": None,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_registration_token(path: Path) -> str | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    token = data.get("private_registration_token")
    return token if isinstance(token, str) and token.strip() else None


def get_config_value(
    key: str,
    dotenv_values: dict[str, str],
    fallback: str | None = None,
) -> str | None:
    return os.getenv(key) or dotenv_values.get(key) or fallback


def build_config(args: argparse.Namespace) -> InboxSeedConfig:
    dotenv_values = read_dotenv(Path(args.env_file))
    database_url = args.database_url or get_config_value("DATABASE_URL", dotenv_values)
    if not database_url:
        raise RuntimeError("DATABASE_URL is required. Set it in .env or pass --database-url.")

    config_file = Path(
        args.config_file
        or get_config_value("VIRGO_CONFIG_FILE", dotenv_values, "config.toml")
        or "config.toml"
    )
    registration_token = args.registration_token or load_registration_token(config_file)

    return InboxSeedConfig(
        base_url=normalize_base_url(
            args.base_url
            or get_config_value("API_BASE_URL", dotenv_values, DEFAULT_BASE_URL)
            or DEFAULT_BASE_URL
        ),
        database_url=database_url,
        registration_token=registration_token,
        agent_username=args.agent_username,
        agent_password=args.agent_password,
        area=args.area,
        sender=args.sender,
        recipient=args.recipient,
        text=args.text,
        sim_number=args.sim_number,
        subscription_id=args.subscription_id,
    )


def ensure_seed_route(
    config: InboxSeedConfig,
    *,
    account_id: str = DEFAULT_ACCOUNT_ID,
    device_id: str = DEFAULT_DEVICE_ID,
    device_token: str = DEFAULT_DEVICE_TOKEN,
    sim_id: str = DEFAULT_SIM_ID,
) -> SeededRoute:
    now = time.time_ns() // 1_000_000
    password_hash = hash_password(config.agent_password)
    token_hash = hash_sha256(device_token)

    with psycopg.connect(config.database_url) as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO devices(
                    id, name, push_token, token_hash, login, password_hash,
                    enabled, status, last_seen_at, registered, created_at, updated_at
                )
                VALUES(%s, %s, %s, %s, %s, NULL, TRUE, 'online', %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    name = EXCLUDED.name,
                    push_token = EXCLUDED.push_token,
                    token_hash = EXCLUDED.token_hash,
                    enabled = TRUE,
                    status = 'online',
                    last_seen_at = EXCLUDED.last_seen_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    device_id,
                    "agent inbox seed phone",
                    "agent-inbox-seed",
                    token_hash,
                    "agent-inbox-seed-device",
                    now,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO sim_cards(
                    id, device_id, sim_type, slot_index, sim_number,
                    phone_number, carrier_name, enabled, status, last_used_at,
                    created_at, updated_at, areas
                )
                VALUES(%s, %s, 'PHYSICAL', 0, %s, %s, %s, TRUE, 'active', %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    sim_type = EXCLUDED.sim_type,
                    slot_index = EXCLUDED.slot_index,
                    sim_number = EXCLUDED.sim_number,
                    phone_number = EXCLUDED.phone_number,
                    carrier_name = EXCLUDED.carrier_name,
                    enabled = TRUE,
                    status = 'active',
                    last_used_at = EXCLUDED.last_used_at,
                    updated_at = EXCLUDED.updated_at,
                    areas = EXCLUDED.areas
                """,
                (
                    sim_id,
                    device_id,
                    config.sim_number,
                    config.recipient,
                    "Seed Carrier",
                    now,
                    now,
                    now,
                    config.area,
                ),
            )
            row = connection.execute(
                """
                INSERT INTO accounts(id, username, password_hash, areas, use_sims_id, status)
                VALUES(%s, %s, %s, %s, %s, 'ACTIVE')
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    areas = EXCLUDED.areas,
                    use_sims_id = EXCLUDED.use_sims_id,
                    status = 'ACTIVE'
                RETURNING id
                """,
                (
                    account_id,
                    config.agent_username,
                    password_hash,
                    config.area,
                    sim_id,
                ),
            ).fetchone()
            actual_account_id = row[0]
            connection.execute(
                """
                INSERT INTO account_sim_cards(account_id, sim_card_id)
                VALUES(%s, %s)
                ON CONFLICT(account_id, sim_card_id) DO NOTHING
                """,
                (actual_account_id, sim_id),
            )

    return SeededRoute(
        account_id=actual_account_id,
        device_id=device_id,
        device_token=device_token,
        sim_id=sim_id,
    )


def post_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {url} failed with HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"POST {url} failed: {error.reason}") from error
    return json.loads(body) if body else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a bound agent route and trigger POST /mobile/v1/inbox.",
    )
    parser.add_argument("--base-url", help=f"API base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--database-url", help="PostgreSQL DSN. Default: DATABASE_URL.")
    parser.add_argument("--env-file", default=".env", help="Dotenv file to read.")
    parser.add_argument("--config-file", help="Virgo config TOML path.")
    parser.add_argument("--registration-token", help=argparse.SUPPRESS)
    parser.add_argument("--agent-username", default=DEFAULT_AGENT_USERNAME)
    parser.add_argument("--agent-password", default=DEFAULT_AGENT_PASSWORD)
    parser.add_argument("--area", default=DEFAULT_AREA)
    parser.add_argument("--sender", default=DEFAULT_SENDER)
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--sim-number", type=int, default=1)
    parser.add_argument("--subscription-id", type=int, default=3)
    parser.add_argument("--message-id", help="Optional inbox idempotency key.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_config(args)
    route = ensure_seed_route(config)
    payload = build_inbox_payload(config, message_id=args.message_id)
    response = post_json(
        f"{config.base_url}/mobile/v1/inbox",
        route.device_token,
        payload,
    )

    print("Triggered POST /mobile/v1/inbox")
    print(f"Agent username: {config.agent_username}")
    print(f"Agent password: {config.agent_password}")
    print(f"Agent account id: {route.account_id}")
    print(f"Device id: {route.device_id}")
    print(f"SIM card id: {route.sim_id}")
    print(f"Inbox id: {payload['id']}")
    print(f"Conversation id: {response.get('conversationId')}")
    print(f"Message id: {response.get('id')}")
    print("Keep the agent client SSE connection open before running this script to see the live event.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
