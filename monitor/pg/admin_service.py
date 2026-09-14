from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Any, Sequence

from psycopg.rows import dict_row

from app.database import Database
from app.security import hash_password


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


@dataclass(frozen=True, slots=True)
class TableDefinition:
    name: str
    columns: tuple[str, ...]
    order_by: str


@dataclass(frozen=True, slots=True)
class ProductCreate:
    id: str
    menu: str | None = None
    update_by: str | None = None
    areas: str | None = None


@dataclass(frozen=True, slots=True)
class ProductUpdate:
    menu: str | None = None
    update_by: str | None = None
    areas: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceUpdate:
    name: str | None = None


@dataclass(frozen=True, slots=True)
class RegionCreate:
    id: str


@dataclass(frozen=True, slots=True)
class ContactCreate:
    id: str
    display_name: str | None = None
    phone_number: str = ""
    normalized_phone_number: str | None = None
    avatar_url: str | None = None
    remark: str | None = None
    status: str = "NORMAL"
    source: str = "MANUAL"
    areas: str | None = None


@dataclass(frozen=True, slots=True)
class ContactUpdate:
    display_name: str | None = None
    phone_number: str = ""
    normalized_phone_number: str | None = None
    avatar_url: str | None = None
    remark: str | None = None
    status: str = "NORMAL"
    source: str = "MANUAL"
    areas: str | None = None


@dataclass(frozen=True, slots=True)
class AccountCreate:
    id: str
    username: str
    password: str
    areas: str | None = None
    use_sims_ids: Sequence[str] | str = ()
    status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class AccountUpdate:
    username: str
    password: str | None = None
    areas: str | None = None
    use_sims_ids: Sequence[str] | str = ()
    status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class SimCardUpdate:
    sim_type: str = "PHYSICAL"
    phone_number: str | None = None
    carrier_name: str | None = None
    esim_profile_name: str | None = None
    enabled: bool = True
    areas: str | None = None
    display_updated_at: int | None = None


TABLES: dict[str, TableDefinition] = {
    "devices": TableDefinition(
        name="devices",
        columns=(
            "id",
            "name",
            "enabled",
            "status",
            "last_seen_at",
            "unregistered_at",
            "registered",
            "created_at",
            "updated_at",
        ),
        order_by="updated_at DESC",
    ),
    "sim_cards": TableDefinition(
        name="sim_cards",
        columns=(
            "enabled", "phone_number", "sim_type", "areas",
            "esim_profile_name", "display_updated_at", "carrier_name",
            "device_id", "id",
        ),
        order_by="display_updated_at DESC NULLS LAST, id ASC",
    ),
    "contacts": TableDefinition(
        name="contacts",
        columns=(
            "id",
            "display_name",
            "phone_number",
            "normalized_phone_number",
            "avatar_url",
            "remark",
            "status",
            "source",
            "last_contact_at",
            "created_at",
            "updated_at",
            "areas",
        ),
        order_by="updated_at DESC",
    ),
    "products": TableDefinition(
        name="products",
        columns=("id", "menu", "update_time", "update_by", "areas"),
        order_by="update_time DESC",
    ),
    "accounts": TableDefinition(
        name="accounts",
        columns=("id", "username", "areas", "use_sims_id", "status"),
        order_by="username ASC",
    ),
    "regions": TableDefinition(
        name="regions",
        columns=("id", "created_at", "updated_at"),
        order_by="id ASC",
    ),
}


class PgAdminService:
    def __init__(
        self,
        database: Database,
        now_ms: Callable[[], int] = _now_ms,
        password_hasher: Callable[[str], str] = hash_password,
    ):
        self._database = database
        self._now_ms = now_ms
        self._password_hasher = password_hasher

    def list_rows(self, table_name: str, limit: int = 200) -> list[dict[str, Any]]:
        table = TABLES.get(table_name)
        if table is None:
            raise ValueError(f"Unsupported admin table: {table_name}")
        safe_limit = self._coerce_limit(limit)
        if table_name == "accounts":
            return self._list_accounts(safe_limit)
        if table_name == "contacts":
            return self._list_contacts(safe_limit)
        if table_name == "devices":
            return self._list_devices(safe_limit)
        if table_name == "sim_cards":
            return self._list_sim_cards(safe_limit)
        columns = ", ".join(table.columns)
        statement = (
            f"SELECT {columns} FROM {table.name} "
            f"ORDER BY {table.order_by} LIMIT %s"
        )
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(statement, (safe_limit,))
                return [dict(row) for row in cursor.fetchall()]

    def list_sim_card_options(self) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT id, phone_number, device_id, sim_number,
                           enabled, status, unregistered_at
                    FROM sim_cards
                    WHERE enabled = TRUE
                      AND status = 'active'
                      AND unregistered_at IS NULL
                    ORDER BY phone_number ASC NULLS LAST, device_id ASC, sim_number ASC
                    """
                )
                return [dict(row) for row in cursor.fetchall()]

    def list_account_options(self) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT id, username
                    FROM accounts
                    ORDER BY username ASC, id ASC
                    """
                )
                return [dict(row) for row in cursor.fetchall()]

    def list_region_options(self) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM regions
                    ORDER BY id ASC
                    """
                )
                return [dict(row) for row in cursor.fetchall()]

    def create_region(self, data: RegionCreate) -> None:
        now = self._now_ms()
        self._execute(
            """
            INSERT INTO regions (id, created_at, updated_at)
            VALUES (%s, %s, %s)
            """,
            (data.id.strip(), now, now),
        )

    def delete_region(self, region_id: str) -> None:
        self._execute("DELETE FROM regions WHERE id = %s", (region_id,))

    def create_product(self, data: ProductCreate) -> None:
        self._execute(
            """
            INSERT INTO products (id, menu, update_time, update_by, areas)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                data.id.strip(),
                data.menu,
                self._now_ms(),
                _blank_to_none(data.update_by),
                _blank_to_none(data.areas),
            ),
        )

    def update_product(self, product_id: str, data: ProductUpdate) -> None:
        self._execute(
            """
            UPDATE products
            SET menu = %s, update_time = %s, update_by = %s, areas = %s
            WHERE id = %s
            """,
            (
                data.menu,
                self._now_ms(),
                _blank_to_none(data.update_by),
                _blank_to_none(data.areas),
                product_id,
            ),
        )

    def delete_product(self, product_id: str) -> None:
        self._execute("DELETE FROM products WHERE id = %s", (product_id,))

    def create_contact(self, data: ContactCreate) -> None:
        now = self._now_ms()
        phone_number = data.phone_number.strip()
        normalized_phone_number = (
            _blank_to_none(data.normalized_phone_number) or phone_number
        )
        self._execute(
            """
            INSERT INTO contacts (
                id, display_name, phone_number, normalized_phone_number,
                avatar_url, remark, status, source, created_at, updated_at, areas
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data.id.strip(),
                _blank_to_none(data.display_name),
                phone_number,
                normalized_phone_number,
                _blank_to_none(data.avatar_url),
                _blank_to_none(data.remark),
                data.status,
                data.source,
                now,
                now,
                _blank_to_none(data.areas),
            ),
        )

    def update_contact(self, contact_id: str, data: ContactUpdate) -> None:
        phone_number = data.phone_number.strip()
        normalized_phone_number = (
            _blank_to_none(data.normalized_phone_number) or phone_number
        )
        self._execute(
            """
            UPDATE contacts
            SET display_name = %s, phone_number = %s,
                normalized_phone_number = %s, avatar_url = %s, remark = %s,
                status = %s, source = %s, areas = %s, updated_at = %s
            WHERE id = %s
            """,
            (
                _blank_to_none(data.display_name),
                phone_number,
                normalized_phone_number,
                _blank_to_none(data.avatar_url),
                _blank_to_none(data.remark),
                data.status,
                data.source,
                _blank_to_none(data.areas),
                self._now_ms(),
                contact_id,
            ),
        )

    def archive_contact(self, contact_id: str) -> None:
        self._execute(
            """
            UPDATE contacts
            SET status = 'ARCHIVED', updated_at = %s
            WHERE id = %s
            """,
            (self._now_ms(), contact_id),
        )

    def unregister_device(self, device_id: str) -> None:
        unregistered_at = self._now_ms()
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE devices
                SET enabled = FALSE, status = 'disabled',
                    unregistered_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (unregistered_at, unregistered_at, device_id),
            )

            connection.execute(
                """
                UPDATE sim_cards
                SET enabled = FALSE, status = 'disabled',
                    unregistered_at = %s, updated_at = %s
                WHERE device_id = %s
                """,
                (unregistered_at, unregistered_at, device_id),
            )

    def update_device(self, device_id: str, data: DeviceUpdate) -> None:
        self._execute(
            "UPDATE devices SET name = %s, updated_at = %s WHERE id = %s",
            (_blank_to_none(data.name), self._now_ms(), device_id),
        )

    def create_account(self, data: AccountCreate) -> None:
        account_id = data.id.strip()
        sim_ids = self._coerce_sim_ids(data.use_sims_ids)
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO accounts (
                    id, username, password_hash, areas, use_sims_id, status
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    account_id,
                    data.username.strip(),
                    self._password_hasher(data.password),
                    _blank_to_none(data.areas),
                    self._first_sim_id(sim_ids),
                    data.status,
                ),
            )
            self._insert_account_sim_cards(connection, account_id, sim_ids)

    def update_account(self, account_id: str, data: AccountUpdate) -> None:
        password = _blank_to_none(data.password)
        sim_ids = self._coerce_sim_ids(data.use_sims_ids)
        if password is None:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE accounts
                    SET username = %s, areas = %s, use_sims_id = %s, status = %s
                    WHERE id = %s
                    """,
                    (
                        data.username.strip(),
                        _blank_to_none(data.areas),
                        self._first_sim_id(sim_ids),
                        data.status,
                        account_id,
                    ),
                )
                self._replace_account_sim_cards(connection, account_id, sim_ids)
            return

        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE accounts
                SET username = %s, password_hash = %s,
                    areas = %s, use_sims_id = %s, status = %s
                WHERE id = %s
                """,
                (
                    data.username.strip(),
                    self._password_hasher(password),
                    _blank_to_none(data.areas),
                    self._first_sim_id(sim_ids),
                    data.status,
                    account_id,
                ),
            )
            self._replace_account_sim_cards(connection, account_id, sim_ids)

    def delete_account(self, account_id: str) -> None:
        self._execute("DELETE FROM accounts WHERE id = %s", (account_id,))

    def update_sim_card(self, sim_card_id: str, data: SimCardUpdate) -> None:
        status = "active" if data.enabled else "disabled"
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE sim_cards
                SET sim_type = %s, phone_number = %s, carrier_name = %s,
                    esim_profile_name = %s, enabled = %s, status = %s,
                    areas = %s, display_updated_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (data.sim_type, _blank_to_none(data.phone_number),
                 _blank_to_none(data.carrier_name), _blank_to_none(data.esim_profile_name),
                 data.enabled, status, _blank_to_none(data.areas),
                 data.display_updated_at, self._now_ms(), sim_card_id),
            )
            if not data.enabled:
                connection.execute("DELETE FROM account_sim_cards WHERE sim_card_id = %s", (sim_card_id,))
                connection.execute("UPDATE accounts SET use_sims_id = NULL WHERE use_sims_id = %s", (sim_card_id,))

    def _execute(self, statement: str, params: tuple[Any, ...]) -> None:
        with self._database.transaction() as connection:
            connection.execute(statement, params)

    def _list_accounts(self, limit: int) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT a.id, a.username, a.areas,
                           COALESCE(
                               string_agg(ascs.sim_card_id, ',' ORDER BY ascs.sim_card_id),
                               a.use_sims_id
                           ) AS use_sims_id,
                           a.status
                    FROM accounts a
                    LEFT JOIN account_sim_cards ascs ON ascs.account_id = a.id
                    GROUP BY a.id, a.username, a.areas, a.use_sims_id, a.status
                    ORDER BY a.username ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]

    def _list_contacts(self, limit: int) -> list[dict[str, Any]]:
        table = TABLES["contacts"]
        columns = ", ".join(table.columns)
        statement = (
            f"SELECT {columns} FROM contacts "
            f"WHERE status <> 'ARCHIVED' "
            f"ORDER BY {table.order_by} LIMIT %s"
        )
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(statement, (limit,))
                return [dict(row) for row in cursor.fetchall()]

    def _list_devices(self, limit: int) -> list[dict[str, Any]]:
        table = TABLES["devices"]
        columns = ", ".join(table.columns)
        statement = (
            f"SELECT {columns} FROM devices "
            f"WHERE unregistered_at IS NULL "
            f"ORDER BY {table.order_by} LIMIT %s"
        )
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(statement, (limit,))
                return [dict(row) for row in cursor.fetchall()]

    def _list_sim_cards(self, limit: int) -> list[dict[str, Any]]:
        table = TABLES["sim_cards"]
        columns = ", ".join(table.columns)
        columns = columns.replace(
            "enabled,",
            "enabled AS enabled_raw, CASE WHEN enabled THEN '已启用' ELSE '未启用' END AS enabled,",
            1,
        )
        statement = (
            f"SELECT {columns} FROM sim_cards "
            f"WHERE unregistered_at IS NULL "
            f"ORDER BY {table.order_by} LIMIT %s"
        )
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(statement, (limit,))
                return [dict(row) for row in cursor.fetchall()]

    def _replace_account_sim_cards(
        self,
        connection: Any,
        account_id: str,
        sim_ids: tuple[str, ...],
    ) -> None:
        connection.execute(
            "DELETE FROM account_sim_cards WHERE account_id = %s",
            (account_id,),
        )
        self._insert_account_sim_cards(connection, account_id, sim_ids)

    def _insert_account_sim_cards(
        self,
        connection: Any,
        account_id: str,
        sim_ids: tuple[str, ...],
    ) -> None:
        for sim_id in sim_ids:
            allowed = connection.execute(
                """SELECT 1 FROM sim_cards
                   WHERE id = %s AND enabled = TRUE AND status = 'active'
                     AND unregistered_at IS NULL""",
                (sim_id,),
            ).fetchone()
            if allowed is None:
                raise ValueError(f"SIM card is not enabled: {sim_id}")
            connection.execute(
                """
                INSERT INTO account_sim_cards (account_id, sim_card_id)
                VALUES (%s, %s)
                """,
                (account_id, sim_id),
            )

    def _coerce_sim_ids(self, values: Sequence[str] | str) -> tuple[str, ...]:
        raw_values = values.split(",") if isinstance(values, str) else values
        sim_ids: list[str] = []
        seen: set[str] = set()
        for value in raw_values:
            sim_id = _blank_to_none(value)
            if sim_id is None or sim_id in seen:
                continue
            seen.add(sim_id)
            sim_ids.append(sim_id)
        return tuple(sim_ids)

    def _first_sim_id(self, sim_ids: tuple[str, ...]) -> str | None:
        return sim_ids[0] if sim_ids else None

    def _coerce_limit(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            return 200
        if limit <= 0:
            return 200
        return min(limit, 500)
