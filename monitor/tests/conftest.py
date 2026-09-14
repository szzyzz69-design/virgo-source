import os
from dataclasses import dataclass, field

import psycopg
import pytest


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://admin:admin@127.0.0.1:5433/virgo_pg",
)


@dataclass(slots=True)
class TestDatabaseContext:
    dsn: str
    device_ids: list[str] = field(default_factory=list)
    push_tokens: list[str] = field(default_factory=list)
    message_keys: list[str] = field(default_factory=list)
    phone_numbers: list[str] = field(default_factory=list)

    def track(self, device_id: str) -> str:
        self.device_ids.append(device_id)
        return device_id

    def track_push_token(self, push_token: str) -> str:
        self.push_tokens.append(push_token)
        return push_token

    def track_message_key(self, idempotency_key: str) -> str:
        self.message_keys.append(idempotency_key)
        return idempotency_key

    def track_phone(self, phone_number: str) -> str:
        self.phone_numbers.append(phone_number)
        return phone_number


@pytest.fixture
def clean_database():
    context = TestDatabaseContext(TEST_DATABASE_URL)
    yield context
    if (
        context.device_ids
        or context.push_tokens
        or context.message_keys
        or context.phone_numbers
    ):
        with psycopg.connect(context.dsn) as connection:
            if context.message_keys:
                connection.execute(
                    "DELETE FROM messages WHERE idempotency_key = ANY(%s::varchar[])",
                    (context.message_keys,),
                )
            if context.phone_numbers:
                connection.execute(
                    "DELETE FROM conversations WHERE external_phone_number = ANY(%s::varchar[])",
                    (context.phone_numbers,),
                )
                connection.execute(
                    "DELETE FROM contacts WHERE normalized_phone_number = ANY(%s::varchar[])",
                    (context.phone_numbers,),
                )
            if context.device_ids:
                connection.execute(
                    "DELETE FROM devices WHERE id = ANY(%s::varchar[])",
                    (context.device_ids,),
                )
            if context.push_tokens:
                connection.execute(
                    "DELETE FROM devices WHERE push_token = ANY(%s::varchar[])",
                    (context.push_tokens,),
                )
