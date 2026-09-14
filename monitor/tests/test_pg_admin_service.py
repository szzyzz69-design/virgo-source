from contextlib import contextmanager

from pg.admin_service import (
    AccountCreate,
    AccountUpdate,
    ContactCreate,
    ContactUpdate,
    PgAdminService,
    ProductCreate,
    ProductUpdate,
    RegionCreate,
    SimCardUpdate,
    TABLES,
)
from pg.admin_ui import _account_option_labels, _region_option_labels, _table_columns


class RecordingCursor:
    def __init__(self, database):
        self._database = database

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=()):
        self._database.statements.append(" ".join(statement.split()))
        self._database.params.append(params)
        return self

    def fetchall(self):
        return self._database.rows


class RecordingConnection:
    def __init__(self, database):
        self._database = database

    def cursor(self, **_kwargs):
        return RecordingCursor(self._database)

    def execute(self, statement, params=()):
        return self.cursor().execute(statement, params)


class RecordingDatabase:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []
        self.params = []

    @contextmanager
    def transaction(self):
        yield RecordingConnection(self)


def test_list_rows_uses_allowed_columns_and_hides_password_hash():
    database = RecordingDatabase(rows=[{"id": "acc_1", "username": "alice", "use_sims_id": "sim_1,sim_2"}])
    service = PgAdminService(database)

    rows = service.list_rows("accounts")

    assert rows == [{"id": "acc_1", "username": "alice", "use_sims_id": "sim_1,sim_2"}]
    assert "FROM accounts" in database.statements[0]
    assert "password_hash" not in database.statements[0]
    assert "account_sim_cards" in database.statements[0]
    assert database.params[0] == (200,)


def test_list_rows_rejects_unknown_table_name():
    service = PgAdminService(RecordingDatabase())

    try:
        service.list_rows("messages")
    except ValueError as error:
        assert str(error) == "Unsupported admin table: messages"
    else:
        raise AssertionError("unknown table name was accepted")


def test_regions_are_available_as_admin_table_without_editable_columns():
    assert TABLES["regions"].columns == ("id", "created_at", "updated_at")

    columns = _table_columns("regions")

    assert columns[0]["name"] == "id"
    assert columns[0]["label"] == "地区"


def test_create_region_stores_trimmed_region_name_with_timestamps():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 123456)

    service.create_region(RegionCreate(id=" North "))

    assert database.statements[0] == (
        "INSERT INTO regions (id, created_at, updated_at) VALUES (%s, %s, %s)"
    )
    assert database.params[0] == ("North", 123456, 123456)


def test_delete_region_removes_by_name_without_touching_existing_area_values():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.delete_region("North")

    assert database.statements[0] == "DELETE FROM regions WHERE id = %s"
    assert database.params[0] == ("North",)


def test_list_region_options_returns_region_names_for_area_dropdowns():
    database = RecordingDatabase(rows=[{"id": "North"}, {"id": "South"}])
    service = PgAdminService(database)

    options = service.list_region_options()

    assert options == [{"id": "North"}, {"id": "South"}]
    assert database.statements[0] == (
        "SELECT id FROM regions ORDER BY id ASC"
    )


def test_region_option_labels_keep_area_value_and_include_current_legacy_value():
    labels = _region_option_labels(
        [{"id": "North"}, {"id": "South"}],
        current_value="Legacy",
    )

    assert labels == {
        "Legacy": "Legacy",
        "North": "North",
        "South": "South",
    }


def test_create_product_sets_update_time():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 123456)

    service.create_product(
        ProductCreate(
            id="prod_1",
            menu="remind support",
            update_by="acc_1",
            areas="CN",
        )
    )

    assert "INSERT INTO products" in database.statements[0]
    assert database.params[0] == (
        "prod_1",
        "remind support",
        123456,
        "acc_1",
        "CN",
    )


def test_update_product_keeps_id_and_refreshes_update_time():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 234567)

    service.update_product(
        "prod_1",
        ProductUpdate(menu="new reminder", update_by=None, areas="US"),
    )

    statement = database.statements[0]
    assert "UPDATE products" in statement
    assert "id =" not in statement.partition("SET")[2].partition("WHERE")[0]
    assert database.params[0] == ("new reminder", 234567, None, "US", "prod_1")


def test_delete_product_removes_by_id():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.delete_product("prod_1")

    assert database.statements[0] == "DELETE FROM products WHERE id = %s"
    assert database.params[0] == ("prod_1",)


def test_create_contact_sets_normalized_phone_and_timestamps():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 567890)

    service.create_contact(
        ContactCreate(
            id="contact_1",
            display_name="Alice",
            phone_number=" +8613800000000 ",
            normalized_phone_number="",
            avatar_url="https://example.test/avatar.png",
            remark="VIP",
            status="NORMAL",
            source="MANUAL",
            areas="CN",
        )
    )

    assert "INSERT INTO contacts" in database.statements[0]
    assert database.params[0] == (
        "contact_1",
        "Alice",
        "+8613800000000",
        "+8613800000000",
        "https://example.test/avatar.png",
        "VIP",
        "NORMAL",
        "MANUAL",
        567890,
        567890,
        "CN",
    )


def test_update_contact_changes_allowed_fields_and_refreshes_updated_at():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 678901)

    service.update_contact(
        "contact_1",
        ContactUpdate(
            display_name="Alice B",
            phone_number="+8613900000000",
            normalized_phone_number="+8613900000000",
            avatar_url="",
            remark="new remark",
            status="BLOCKED",
            source="IMPORTED",
            areas="US",
        ),
    )

    statement = database.statements[0]
    changed_columns = statement.partition("SET")[2].partition("WHERE")[0]
    updated_names = {
        assignment.strip().split(" = ", 1)[0]
        for assignment in changed_columns.split(",")
    }
    assert "UPDATE contacts" in statement
    assert "id" not in updated_names
    assert "created_at" not in updated_names
    assert "last_contact_at" not in updated_names
    assert database.params[0] == (
        "Alice B",
        "+8613900000000",
        "+8613900000000",
        None,
        "new remark",
        "BLOCKED",
        "IMPORTED",
        "US",
        678901,
        "contact_1",
    )


def test_archive_contact_marks_archived_without_deleting_history():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 789012)

    service.archive_contact("contact_1")

    assert database.statements[0] == (
        "UPDATE contacts SET status = 'ARCHIVED', updated_at = %s WHERE id = %s"
    )
    assert database.params[0] == (789012, "contact_1")
    assert all("DELETE" not in statement for statement in database.statements)


def test_list_contact_rows_excludes_archived_contacts():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.list_rows("contacts")

    assert "FROM contacts" in database.statements[0]
    assert "status <> 'ARCHIVED'" in database.statements[0]


def test_list_device_rows_excludes_unregistered_devices():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.list_rows("devices")

    assert "FROM devices" in database.statements[0]
    assert "unregistered_at IS NULL" in database.statements[0]


def test_list_sim_card_rows_excludes_unregistered_sim_cards():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.list_rows("sim_cards")

    assert "FROM sim_cards" in database.statements[0]
    assert "unregistered_at IS NULL" in database.statements[0]


def test_list_account_options_returns_product_update_by_choices():
    database = RecordingDatabase(
        rows=[
            {"id": "acc_1", "username": "alice"},
            {"id": "acc_2", "username": "bob"},
        ]
    )
    service = PgAdminService(database)

    options = service.list_account_options()

    assert options == [
        {"id": "acc_1", "username": "alice"},
        {"id": "acc_2", "username": "bob"},
    ]
    assert database.statements[0] == (
        "SELECT id, username FROM accounts ORDER BY username ASC, id ASC"
    )


def test_account_option_labels_show_username_and_keep_id_value():
    labels = _account_option_labels(
        [
            {"id": "acc_1", "username": "alice"},
            {"id": "acc_2", "username": ""},
        ]
    )

    assert labels == {
        "acc_1": "alice / acc_1",
        "acc_2": "acc_2",
    }


def test_unregister_device_disables_device_and_owned_sim_cards_without_deleting():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 456789)

    service.unregister_device("dev_1")

    assert database.statements == [
        (
            "UPDATE devices SET enabled = FALSE, status = 'disabled', "
            "unregistered_at = %s, updated_at = %s WHERE id = %s"
        ),
        (
            "UPDATE sim_cards SET enabled = FALSE, status = 'disabled', "
            "unregistered_at = %s, updated_at = %s WHERE device_id = %s"
        ),
    ]
    assert database.params == [
        (456789, 456789, "dev_1"),
        (456789, 456789, "dev_1"),
    ]
    assert all("DELETE" not in statement for statement in database.statements)


def test_create_account_hashes_password():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.create_account(
        AccountCreate(
            id="acc_1",
            username="alice",
            password="secret",
            areas="CN",
            use_sims_ids=("sim_1", "sim_2"),
            status="ACTIVE",
        )
    )

    assert "INSERT INTO accounts" in database.statements[0]
    params = database.params[0]
    assert params[0] == "acc_1"
    assert params[1] == "alice"
    assert params[2].startswith("pbkdf2_sha256$")
    assert params[2] != "secret"
    assert params[3:] == ("CN", "sim_1", "ACTIVE")
    assert database.statements[1] == (
        "INSERT INTO account_sim_cards (account_id, sim_card_id) "
        "VALUES (%s, %s)"
    )
    assert database.params[1] == ("acc_1", "sim_1")
    assert database.params[2] == ("acc_1", "sim_2")


def test_update_account_without_password_does_not_touch_password_hash():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.update_account(
        "acc_1",
        AccountUpdate(
            username="alice",
            password="",
            areas="CN",
            use_sims_ids=("sim_2", "sim_3"),
            status="DISABLED",
        ),
    )

    statement = database.statements[0]
    assert "UPDATE accounts" in statement
    assert "password_hash" not in statement
    assert database.params[0] == ("alice", "CN", "sim_2", "DISABLED", "acc_1")
    assert database.statements[1] == "DELETE FROM account_sim_cards WHERE account_id = %s"
    assert database.params[1] == ("acc_1",)
    assert database.params[2] == ("acc_1", "sim_2")
    assert database.params[3] == ("acc_1", "sim_3")


def test_update_account_with_password_updates_password_hash():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.update_account(
        "acc_1",
        AccountUpdate(
            username="alice",
            password="new-secret",
            areas="CN",
            use_sims_ids=("sim_1",),
            status="ACTIVE",
        ),
    )

    statement = database.statements[0]
    assert "password_hash = %s" in statement
    params = database.params[0]
    assert params[0] == "alice"
    assert params[1].startswith("pbkdf2_sha256$")
    assert params[1] != "new-secret"
    assert params[2:] == ("CN", "sim_1", "ACTIVE", "acc_1")
    assert database.statements[1] == "DELETE FROM account_sim_cards WHERE account_id = %s"
    assert database.params[1] == ("acc_1",)
    assert database.params[2] == ("acc_1", "sim_1")


def test_update_account_accepts_comma_separated_sim_ids_for_compatibility():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.update_account(
        "acc_1",
        AccountUpdate(
            username="alice",
            password="",
            areas="CN",
            use_sims_ids="sim_1, sim_2",
            status="ACTIVE",
        ),
    )

    assert database.params[0] == ("alice", "CN", "sim_1", "ACTIVE", "acc_1")
    assert database.params[2] == ("acc_1", "sim_1")
    assert database.params[3] == ("acc_1", "sim_2")


def test_delete_account_removes_by_id():
    database = RecordingDatabase()
    service = PgAdminService(database)

    service.delete_account("acc_1")

    assert database.statements[0] == "DELETE FROM accounts WHERE id = %s"
    assert database.params[0] == ("acc_1",)


def test_list_sim_card_options_returns_phone_labels_source_data():
    database = RecordingDatabase(
        rows=[
            {
                "id": "sim_1",
                "phone_number": "+8613800000000",
                "device_id": "dev_1",
                "sim_number": 1,
                "enabled": True,
                "status": "active",
                "unregistered_at": None,
            }
        ]
    )
    service = PgAdminService(database)

    options = service.list_sim_card_options()

    assert options == [
        {
            "id": "sim_1",
            "phone_number": "+8613800000000",
            "device_id": "dev_1",
            "sim_number": 1,
            "enabled": True,
            "status": "active",
            "unregistered_at": None,
        }
    ]
    assert database.statements[0] == (
        "SELECT id, phone_number, device_id, sim_number, enabled, status, unregistered_at "
        "FROM sim_cards "
        "ORDER BY phone_number ASC NULLS LAST, device_id ASC, sim_number ASC"
    )


def test_update_sim_card_changes_only_allowed_fields_and_refreshes_updated_at():
    database = RecordingDatabase()
    service = PgAdminService(database, now_ms=lambda: 345678)

    service.update_sim_card(
        "sim_1",
        SimCardUpdate(
            sim_type="ESIM",
            subscription_id=42,
            phone_number="+8613800000000",
            carrier_name="Carrier",
            iccid_hash="hash",
            esim_profile_name="Work",
            esim_group_id="group",
            enabled=False,
            status="disabled",
            areas="CN",
        ),
    )

    statement = database.statements[0]
    changed_columns = statement.partition("SET")[2].partition("WHERE")[0]
    updated_names = {
        assignment.strip().split(" = ", 1)[0]
        for assignment in changed_columns.split(",")
    }
    assert "UPDATE sim_cards" in statement
    assert "id" not in updated_names
    assert "device_id" not in updated_names
    assert "slot_index" not in updated_names
    assert "sim_number" not in updated_names
    assert "updated_at = %s" in changed_columns
    assert database.params[0] == (
        "ESIM",
        42,
        "+8613800000000",
        "Carrier",
        "hash",
        "Work",
        "group",
        False,
        "disabled",
        "CN",
        345678,
        "sim_1",
    )
