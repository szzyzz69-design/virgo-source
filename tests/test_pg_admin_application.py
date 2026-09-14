from datetime import datetime, timezone

from app.application import create_app
from app.config import Settings
from pg.admin_ui import (
    FIELD_LABELS,
    TABLE_LABELS,
    _parse_sim_card_ids,
    _format_account_sim_display,
    _format_table_rows_for_display,
    _format_date_input,
    _sim_card_option_labels,
    _patch_nicegui_process_pool_setup,
)


class RecordingDatabase:
    def __init__(self, dsn):
        self.dsn = dsn


def test_create_app_mounts_pg_admin_ui_with_configured_database(monkeypatch):
    mounted = {}

    def fake_mount_admin_ui(app, database, sms_checks=None):
        mounted["app"] = app
        mounted["database"] = database
        mounted["sms_checks"] = sms_checks

    monkeypatch.setattr("app.application.Database", RecordingDatabase)
    monkeypatch.setattr(
        "app.application.mount_admin_ui",
        fake_mount_admin_ui,
        raising=False,
    )

    app = create_app(Settings("postgresql://configured/database", "registration-token"))

    assert mounted["app"] is app
    assert isinstance(mounted["database"], RecordingDatabase)
    assert mounted["database"].dsn == "postgresql://configured/database"


def test_nicegui_process_pool_setup_degrades_on_permission_error():
    class FakeNiceGuiRun:
        process_pool = object()

        @staticmethod
        def setup():
            raise PermissionError("named pipe denied")

    _patch_nicegui_process_pool_setup(FakeNiceGuiRun)

    FakeNiceGuiRun.setup()

    assert FakeNiceGuiRun.process_pool is None


def test_table_rows_format_unix_millisecond_time_fields():
    rows = [
        {
            "id": "dev_1",
            "created_at": 0,
            "updated_at": 1000,
            "last_seen_at": None,
            "unregistered_at": 2000,
            "name": "phone",
        }
    ]

    formatted = _format_table_rows_for_display(rows, timezone.utc)

    assert formatted == [
        {
            "id": "dev_1",
            "created_at": "1970-01-01 00:00:00",
            "updated_at": "1970-01-01 00:00:01",
            "last_seen_at": None,
            "unregistered_at": "1970-01-01 00:00:02",
            "name": "phone",
        }
    ]
    assert rows[0]["created_at"] == 0


def test_sim_display_date_keeps_raw_value_for_edit_dialog():
    timestamp = int(datetime(2026, 9, 1).timestamp() * 1000)
    rows = [
        {"id": "sim_with_date", "display_updated_at": timestamp},
        {"id": "sim_without_date", "display_updated_at": None},
    ]

    formatted = _format_table_rows_for_display(rows)

    assert formatted[0]["display_updated_at"] == "2026-09-01"
    assert formatted[0]["_display_updated_at_raw"] == timestamp
    assert _format_date_input(formatted[0]["_display_updated_at_raw"]) == "2026-09-01"
    assert formatted[1]["display_updated_at"] is None
    assert formatted[1]["_display_updated_at_raw"] is None
    assert _format_date_input(formatted[1]["_display_updated_at_raw"]) == ""


def test_sim_card_option_labels_prefer_phone_number():
    options = [
        {
            "id": "sim_1",
            "phone_number": "+8613800000000",
            "device_id": "dev_1",
            "sim_number": 1,
            "enabled": True,
            "status": "active",
            "unregistered_at": None,
        },
        {
            "id": "sim_2",
            "phone_number": None,
            "device_id": "dev_2",
            "sim_number": 2,
            "enabled": True,
            "status": "active",
            "unregistered_at": None,
        },
    ]

    labels = _sim_card_option_labels(options)

    assert labels == {
        "sim_1": "+8613800000000",
        "sim_2": "sim_2 / dev_2 / SIM 2",
    }


def test_sim_card_option_labels_hide_deleted_sim_cards_by_default():
    options = [
        {
            "id": "sim_1",
            "phone_number": "+8613800000000",
            "device_id": "dev_1",
            "sim_number": 1,
            "enabled": False,
            "status": "disabled",
            "unregistered_at": 123456,
        },
        {
            "id": "sim_2",
            "phone_number": "<unsafe>",
            "device_id": "dev_2",
            "sim_number": 2,
            "enabled": True,
            "status": "active",
            "unregistered_at": None,
        },
    ]

    labels = _sim_card_option_labels(options)

    assert "sim_1" not in labels
    assert labels["sim_2"] == "&lt;unsafe&gt;"


def test_sim_card_option_labels_mark_current_deleted_sim_cards_red():
    options = [
        {
            "id": "sim_1",
            "phone_number": "+8613800000000",
            "device_id": "dev_1",
            "sim_number": 1,
            "enabled": False,
            "status": "disabled",
            "unregistered_at": 123456,
        }
    ]

    labels = _sim_card_option_labels(options, include_deleted_ids={"sim_1"})

    assert labels["sim_1"] == (
        '<span class="text-red-600 font-medium">'
        "+8613800000000 (deleted)"
        "</span>"
    )


def test_account_sim_display_hides_deleted_sims_and_keeps_raw_ids_for_editing():
    row = {"id": "acc_1", "use_sims_id": "sim_1,sim_2"}
    labels = {
        "sim_1": "+8613800000000",
    }

    formatted = _format_account_sim_display(row, labels)

    assert formatted["_use_sims_id_raw"] == "sim_1,sim_2"
    assert formatted["use_sims_id"] == "+8613800000000"


def test_parse_sim_card_ids_accepts_comma_string_and_sequence():
    assert _parse_sim_card_ids(" sim_1, sim_2 ,, ") == ["sim_1", "sim_2"]
    assert _parse_sim_card_ids(["sim_2", "", "sim_3"]) == ["sim_2", "sim_3"]
    assert _parse_sim_card_ids(None) == []


def test_admin_ui_uses_menu_wording_for_products_table():
    assert TABLE_LABELS["products"] == "menu"
    assert "商品" not in TABLE_LABELS.values()


def test_admin_ui_labels_esim_profile_name_as_customer_remark():
    assert FIELD_LABELS["esim_profile_name"] == "客服备注"


def test_admin_ui_labels_product_account_as_owner():
    assert FIELD_LABELS["update_by"] == "所属账号"
