from datetime import datetime, time as datetime_time, tzinfo
from html import escape
import os
from typing import Any

from fastapi import FastAPI

from app.database import Database
from pg.admin_service import (
    TABLES,
    AccountCreate,
    AccountUpdate,
    ContactCreate,
    ContactUpdate,
    DeviceUpdate,
    PgAdminService,
    ProductCreate,
    ProductUpdate,
    RegionCreate,
    SimCardUpdate,
)


TABLE_LABELS = {
    "devices": "设备",
    "sim_cards": "SIM 卡",
    "contacts": "联系人",
    "products": "menu",
    "accounts": "账号",
    "regions": "地区",
}

FIELD_LABELS = {
    "id": "ID",
    "name": "名称",
    "manufacturer": "厂商",
    "model": "型号",
    "android_version": "Android",
    "app_version": "App 版本",
    "enabled": "启用",
    "status": "状态",
    "last_seen_at": "最近心跳",
    "unregistered_at": "注销时间",
    "registered": "注册时间",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "display_updated_at": "更新时间",
    "device_id": "设备 ID",
    "sim_type": "SIM 类型",
    "slot_index": "卡槽",
    "sim_number": "SIM 编号",
    "subscription_id": "订阅 ID",
    "phone_number": "手机号",
    "carrier_name": "运营商",
    "iccid_hash": "ICCID Hash",
    "esim_profile_name": "客服备注",
    "esim_group_id": "eSIM 分组",
    "last_used_at": "最近使用",
    "areas": "地区",
    "display_name": "显示名",
    "normalized_phone_number": "标准号码",
    "avatar_url": "头像",
    "remark": "备注",
    "source": "来源",
    "last_contact_at": "最近联系",
    "menu": "客服提醒",
    "update_time": "更新时间",
    "update_by": "所属账号",
    "username": "用户名",
    "use_sims_id": "使用 SIM",
}

TIME_FIELDS = {
    "created_at",
    "updated_at",
    "last_seen_at",
    "registered",
    "last_used_at",
    "last_contact_at",
    "unregistered_at",
    "update_time",
    "display_updated_at",
}


def mount_admin_ui(app: FastAPI, database: Database, sms_checks=None) -> None:
    if _is_pytest_running():
        app.state.pg_admin_ui_available = False
        app.state.pg_admin_ui_skip_reason = "disabled while pytest is running"
        return

    try:
        from nicegui import run as nicegui_run
        from nicegui import ui
    except Exception as error:
        app.state.pg_admin_ui_available = False
        app.state.pg_admin_ui_error = repr(error)
        return

    _patch_nicegui_process_pool_setup(nicegui_run)
    service = PgAdminService(database)
    _register_admin_page(ui, service, sms_checks)
    ui.run_with(app, mount_path="/")
    app.state.pg_admin_ui_available = True


def _is_pytest_running() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def _patch_nicegui_process_pool_setup(nicegui_run: Any) -> None:
    if getattr(nicegui_run, "_virgo_process_pool_setup_patched", False):
        return

    original_setup = nicegui_run.setup

    def tolerant_setup() -> None:
        try:
            original_setup()
        except (OSError, PermissionError):
            nicegui_run.process_pool = None

    nicegui_run.setup = tolerant_setup
    nicegui_run._virgo_process_pool_setup_patched = True


def _register_admin_page(ui: Any, service: PgAdminService, sms_checks=None) -> None:
    @ui.page("/admin/db")
    def admin_page() -> None:
        _build_admin_page(ui, service, sms_checks)


def _build_admin_page(ui: Any, service: PgAdminService, sms_checks=None) -> None:
    ui.colors(primary="#2563eb", secondary="#0f766e", accent="#7c3aed")
    ui.page_title("数据库管理")

    with ui.header().classes("items-center gap-3"):
        ui.label("数据库管理").classes("text-lg font-medium")
        ui.space()
        ui.label("Virgo").classes("text-sm opacity-70")

    with ui.column().classes("w-full p-4 gap-4"):
        with ui.tabs().classes("w-full") as tabs:
            tab_refs = {
                table_name: ui.tab(label)
                for table_name, label in TABLE_LABELS.items()
            }
            if sms_checks:
                check_tab = ui.tab('短信检测')

        with ui.tab_panels(tabs, value=tab_refs["devices"]).classes("w-full"):
            for table_name in TABLE_LABELS:
                with ui.tab_panel(tab_refs[table_name]).classes("w-full"):
                    _build_table_panel(ui, service, table_name)
            if sms_checks:
                with ui.tab_panel(check_tab).classes('w-full'):
                    from pg.sms_check_ui import build_sms_check_panel
                    build_sms_check_panel(ui, sms_checks)


def _build_table_panel(ui: Any, service: PgAdminService, table_name: str) -> None:
    contact_phone_search = None
    if table_name == "contacts":
        with ui.row().classes("w-full items-center gap-2"):
            contact_phone_search = (
                ui.input(
                    "手机号搜索",
                    placeholder="输入完整手机号或后四位",
                )
                .props("outlined dense clearable")
                .classes("w-72")
            )
            ui.button(
                "搜索",
                icon="search",
                on_click=lambda: refresh(),
            )
            ui.button(
                "清除",
                icon="close",
                on_click=lambda: clear_contact_search(),
            ).props("flat")

    sim_card_labels = (
        _sim_card_option_labels(service.list_sim_card_options())
        if table_name == "accounts"
        else None
    )
    rows = _format_table_rows_for_display(
        service.list_rows(
            table_name,
            phone_query=(
                contact_phone_search.value
                if contact_phone_search is not None
                else None
            ),
        ),
        sim_card_labels=sim_card_labels,
    )
    table = ui.table(
        columns=_table_columns(table_name),
        rows=rows,
        row_key="id",
        selection="single",
        pagination=20,
    ).classes("w-full")
    if table_name == "accounts":
        table.add_slot(
            "body-cell-use_sims_id",
            """
            <q-td :props="props">
                <span v-html="props.value"></span>
            </q-td>
            """,
        )
        for field in ("id", "username"):
            table.add_slot(
                f"body-cell-{field}",
                """
                <q-td :props="props">
                    <q-btn flat dense no-caps
                        :label="props.value && props.value.length > 12 ? props.value.slice(0, 12) + '…' : props.value">
                        <q-popup-proxy>
                            <q-card class="q-pa-md text-body2">{{ props.value }}</q-card>
                        </q-popup-proxy>
                    </q-btn>
                </q-td>
                """,
            )
    if table_name == "sim_cards":
        for field, prefix in (("id", "sim_"), ("device_id", "dev_")):
            prefix_length = len(prefix)
            table.add_slot(
                f"body-cell-{field}",
                f"""
                <q-td :props="props">
                    <q-btn flat dense no-caps
                        :label="props.value && props.value.startsWith('{prefix}') ? '{prefix}' + props.value.slice({prefix_length}, {prefix_length + 4}) + '…' : props.value">
                        <q-popup-proxy>
                            <q-card class="q-pa-md text-body2">{{{{ props.value }}}}</q-card>
                        </q-popup-proxy>
                    </q-btn>
                </q-td>
                """,
            )

    def refresh() -> None:
        refreshed_sim_card_labels = (
            _sim_card_option_labels(service.list_sim_card_options())
            if table_name == "accounts"
            else None
        )
        table.rows = _format_table_rows_for_display(
            service.list_rows(
                table_name,
                phone_query=(
                    contact_phone_search.value
                    if contact_phone_search is not None
                    else None
                ),
            ),
            sim_card_labels=refreshed_sim_card_labels,
        )
        table.selected.clear()
        table.update()

    def clear_contact_search() -> None:
        if contact_phone_search is None:
            return
        contact_phone_search.value = ""
        refresh()

    if contact_phone_search is not None:
        contact_phone_search.on("keydown.enter", refresh)

    with ui.row().classes("items-center gap-2"):
        ui.button(icon="refresh", on_click=refresh).tooltip("刷新")
        if table_name == "devices":
            ui.button(
                icon="edit",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _open_device_dialog(ui, service, refresh, row),
                ),
            ).tooltip("编辑设备名称")
            ui.button(
                icon="delete",
                color="negative",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _confirm_unregister_device(
                        ui,
                        row["id"],
                        lambda: service.unregister_device(row["id"]),
                        refresh,
                    ),
                ),
            ).tooltip("注销设备")
        elif table_name == "contacts":
            ui.button(icon="add", on_click=lambda: _open_contact_dialog(ui, service, refresh)).tooltip("新增联系人")
            ui.button(
                icon="edit",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _open_contact_dialog(ui, service, refresh, row),
                ),
            ).tooltip("编辑联系人")
            ui.button(
                icon="delete",
                color="negative",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _confirm_archive_contact(
                        ui,
                        row["id"],
                        lambda: service.archive_contact(row["id"]),
                        refresh,
                    ),
                ),
            ).tooltip("删除联系人（归档）")
        elif table_name == "products":
            ui.button(icon="add", on_click=lambda: _open_product_dialog(ui, service, refresh)).tooltip("新增 menu")
            ui.button(
                icon="edit",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _open_product_dialog(ui, service, refresh, row),
                ),
            ).tooltip("编辑 menu")
            ui.button(
                icon="delete",
                color="negative",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _confirm_delete(
                        ui,
                        "删除 menu",
                        row["id"],
                        lambda: service.delete_product(row["id"]),
                        refresh,
                    ),
                ),
            ).tooltip("删除 menu")
        elif table_name == "accounts":
            ui.button(icon="add", on_click=lambda: _open_account_dialog(ui, service, refresh)).tooltip("新增账号")
            ui.button(
                icon="edit",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _open_account_dialog(ui, service, refresh, row),
                ),
            ).tooltip("编辑账号")
            ui.button(
                icon="delete",
                color="negative",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _confirm_delete(
                        ui,
                        "删除账号",
                        row["id"],
                        lambda: service.delete_account(row["id"]),
                        refresh,
                    ),
                ),
            ).tooltip("删除账号")
        elif table_name == "sim_cards":
            ui.button('历史记录',icon='history',on_click=lambda: _open_sim_history(ui,service)).props('flat')
            ui.button(icon='delete',color='negative',on_click=lambda: _with_selected(
                ui,table,lambda row: _confirm_delete(ui,'删除 SIM 并保留历史记录',row['id'],
                    lambda: service.delete_sim_card(row['id']),refresh),
            )).tooltip('删除 SIM（保留历史记录）')
            ui.button(
                icon="edit",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _open_sim_dialog(ui, service, refresh, row),
                ),
            ).tooltip("编辑 SIM 卡")
        elif table_name == "regions":
            ui.button(icon="add", on_click=lambda: _open_region_dialog(ui, service, refresh)).tooltip("新增地区")
            ui.button(
                icon="delete",
                color="negative",
                on_click=lambda: _with_selected(
                    ui,
                    table,
                    lambda row: _confirm_delete(
                        ui,
                        "删除地区",
                        row["id"],
                        lambda: service.delete_region(row["id"]),
                        refresh,
                    ),
                ),
            ).tooltip("删除地区")


def _table_columns(table_name: str) -> list[dict[str, str]]:
    return [
        {
            "name": column,
            "label": (
                "地区"
                if table_name == "regions" and column == "id"
                else "状态"
                if table_name == "sim_cards" and column == "enabled"
                else FIELD_LABELS.get(column, column)
            ),
            "field": column,
            "align": "left",
        }
        for column in TABLES[table_name].columns
    ]


def _format_table_rows_for_display(
    rows: list[dict[str, Any]],
    timezone: tzinfo | None = None,
    sim_card_labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return [
        _format_table_row_for_display(row, timezone, sim_card_labels)
        for row in rows
    ]


def _format_table_row_for_display(
    row: dict[str, Any],
    timezone: tzinfo | None = None,
    sim_card_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    formatted = dict(row)
    if "display_updated_at" in formatted:
        formatted["_display_updated_at_raw"] = formatted["display_updated_at"]
        formatted["display_updated_at"] = _format_unix_milliseconds(
            formatted["display_updated_at"], timezone, date_only=True
        )
    for field in (TIME_FIELDS - {"display_updated_at"}) & formatted.keys():
        formatted[field] = _format_unix_milliseconds(formatted[field], timezone)
    if sim_card_labels is not None and "use_sims_id" in formatted:
        formatted = _format_account_sim_display(formatted, sim_card_labels)
    return formatted


def _format_unix_milliseconds(
    value: Any,
    timezone: tzinfo | None = None,
    *,
    date_only: bool = False,
) -> Any:
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return value
    moment = datetime.fromtimestamp(value / 1000, tz=timezone).astimezone(timezone)
    return moment.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")


def _sim_card_option_labels(
    options: list[dict[str, Any]],
    include_deleted_ids: set[str] | None = None,
) -> dict[str, str]:
    included_deleted = include_deleted_ids or set()
    labels: dict[str, str] = {}
    for option in options:
        sim_id = str(option["id"])
        deleted = _is_deleted_sim_card(option)
        if deleted and sim_id not in included_deleted:
            continue
        phone_number = option.get("phone_number")
        if isinstance(phone_number, str) and phone_number.strip():
            label = escape(phone_number.strip())
            labels[sim_id] = _mark_deleted_sim_label(label, deleted)
            continue
        label = escape(
            f"{sim_id} / {option.get('device_id') or '-'} / "
            f"SIM {option.get('sim_number') or '-'}"
        )
        labels[sim_id] = _mark_deleted_sim_label(label, deleted)
    return labels


def _mark_deleted_sim_label(label: str, deleted: bool) -> str:
    if not deleted:
        return label
    return f'<span class="text-red-600 font-medium">{label} (deleted)</span>'


def _is_deleted_sim_card(option: dict[str, Any]) -> bool:
    status = str(option.get("status") or "").lower()
    return (
        option.get("unregistered_at") is not None
        or option.get("enabled") is False
        or status == "disabled"
    )


def _account_option_labels(options: list[dict[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for option in options:
        account_id = str(option["id"])
        username = option.get("username")
        if isinstance(username, str) and username.strip():
            labels[account_id] = f"{username.strip()} / {account_id}"
            continue
        labels[account_id] = account_id
    return labels


def _region_option_labels(
    options: list[dict[str, Any]],
    current_value: str | None = None,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    current = current_value.strip() if isinstance(current_value, str) else ""
    if current:
        labels[current] = current
    for option in options:
        region_id = str(option["id"]).strip()
        if region_id:
            labels[region_id] = region_id
    return labels


def _parse_sim_card_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    return [str(item).strip() for item in values if str(item).strip()]


def _format_account_sim_display(
    row: dict[str, Any],
    sim_card_labels: dict[str, str],
) -> dict[str, Any]:
    formatted = dict(row)
    raw_value = formatted.get("_use_sims_id_raw", formatted.get("use_sims_id"))
    formatted["_use_sims_id_raw"] = raw_value
    sim_ids = _parse_sim_card_ids(raw_value)
    formatted["use_sims_id"] = ", ".join(
        sim_card_labels[sim_id]
        for sim_id in sim_ids
        if sim_id in sim_card_labels
    )
    return formatted


def _with_selected(ui: Any, table: Any, action: Any) -> None:
    if not table.selected:
        ui.notify("请先选择一行", type="warning")
        return
    action(table.selected[0])


def _open_region_dialog(
    ui: Any,
    service: PgAdminService,
    refresh: Any,
) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        ui.label("新增地区").classes("text-base font-medium")
        region_id = ui.input("地区", value="").props("outlined dense").classes("w-full")

        def save() -> None:
            try:
                service.create_region(RegionCreate(id=region_id.value))
                dialog.close()
                refresh()
                ui.notify("已保存", type="positive")
            except Exception:
                ui.notify("保存失败，请检查地区是否为空或重复", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=save)
    dialog.open()


def _open_contact_dialog(
    ui: Any,
    service: PgAdminService,
    refresh: Any,
    row: dict[str, Any] | None = None,
) -> None:
    editing = row is not None
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        ui.label("编辑联系人" if editing else "新增联系人").classes("text-base font-medium")
        contact_id = ui.input("ID", value=(row or {}).get("id", "")).props("outlined dense").classes("w-full")
        contact_id.set_enabled(not editing)
        display_name = ui.input("显示名", value=(row or {}).get("display_name") or "").props("outlined dense").classes("w-full")
        phone_number = ui.input("手机号", value=(row or {}).get("phone_number") or "").props("outlined dense").classes("w-full")
        normalized_phone_number = ui.input("标准号码", value=(row or {}).get("normalized_phone_number") or "").props("outlined dense").classes("w-full")
        avatar_url = ui.input("头像", value=(row or {}).get("avatar_url") or "").props("outlined dense").classes("w-full")
        remark = ui.textarea("备注", value=(row or {}).get("remark") or "").props("outlined autogrow").classes("w-full")
        status = ui.select(
            ["NORMAL", "BLOCKED", "ARCHIVED"],
            label="状态",
            value=(row or {}).get("status", "NORMAL"),
        ).props("outlined dense").classes("w-full")
        source = ui.select(
            ["MANUAL", "INBOUND_AUTO", "IMPORTED"],
            label="来源",
            value=(row or {}).get("source", "MANUAL"),
        ).props("outlined dense").classes("w-full")
        areas = ui.input("地区", value=(row or {}).get("areas") or "").props("outlined dense").classes("w-full")

        def save() -> None:
            try:
                if editing:
                    service.update_contact(
                        row["id"],
                        ContactUpdate(
                            display_name=display_name.value,
                            phone_number=phone_number.value,
                            normalized_phone_number=normalized_phone_number.value,
                            avatar_url=avatar_url.value,
                            remark=remark.value,
                            status=status.value,
                            source=source.value,
                            areas=areas.value,
                        ),
                    )
                else:
                    service.create_contact(
                        ContactCreate(
                            id=contact_id.value,
                            display_name=display_name.value,
                            phone_number=phone_number.value,
                            normalized_phone_number=normalized_phone_number.value,
                            avatar_url=avatar_url.value,
                            remark=remark.value,
                            status=status.value,
                            source=source.value,
                            areas=areas.value,
                        )
                    )
                dialog.close()
                refresh()
                ui.notify("已保存", type="positive")
            except Exception:
                ui.notify("保存失败，请检查必填项、唯一键和数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=save)
    dialog.open()


def _open_product_dialog(
    ui: Any,
    service: PgAdminService,
    refresh: Any,
    row: dict[str, Any] | None = None,
) -> None:
    editing = row is not None
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        ui.label("编辑 menu" if editing else "新增 menu").classes("text-base font-medium")
        product_id = ui.input("ID", value=(row or {}).get("id", "")).props("outlined dense").classes("w-full")
        product_id.set_enabled(not editing)
        menu = ui.textarea("客服提醒", value=(row or {}).get("menu") or "").props("outlined autogrow").classes("w-full")
        update_by = ui.select(
            _account_option_labels(service.list_account_options()),
            label="所属账号",
            value=(row or {}).get("update_by") or None,
        ).props("outlined dense").classes("w-full")
        areas = ui.input("地区", value=(row or {}).get("areas") or "").props("outlined dense").classes("w-full")

        def save() -> None:
            try:
                if editing:
                    service.update_product(
                        row["id"],
                        ProductUpdate(
                            menu=menu.value,
                            update_by=update_by.value,
                            areas=areas.value,
                        ),
                    )
                else:
                    service.create_product(
                        ProductCreate(
                            id=product_id.value,
                            menu=menu.value,
                            update_by=update_by.value,
                            areas=areas.value,
                        )
                    )
                dialog.close()
                refresh()
                ui.notify("已保存", type="positive")
            except Exception:
                ui.notify("保存失败，请检查唯一键、外键和数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=save)
    dialog.open()


def _open_account_dialog(
    ui: Any,
    service: PgAdminService,
    refresh: Any,
    row: dict[str, Any] | None = None,
) -> None:
    editing = row is not None
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        ui.label("编辑账号" if editing else "新增账号").classes("text-base font-medium")
        account_id = ui.input("ID", value=(row or {}).get("id", "")).props("outlined dense").classes("w-full")
        account_id.set_enabled(not editing)
        username = ui.input("用户名", value=(row or {}).get("username", "")).props("outlined dense").classes("w-full")
        password_label = "新密码" if editing else "密码"
        password = ui.input(password_label, password=True, password_toggle_button=True).props("outlined dense").classes("w-full")
        areas = ui.select(
            _region_option_labels(
                service.list_region_options(),
                (row or {}).get("areas"),
            ),
            label="地区",
            value=(row or {}).get("areas") or None,
            clearable=True,
        ).props("outlined dense").classes("w-full")
        selected_sim_ids = _parse_sim_card_ids(
            (row or {}).get("_use_sims_id_raw", (row or {}).get("use_sims_id"))
        )
        use_sims_id = ui.select(
            _sim_card_option_labels(
                service.list_sim_card_options(),
                include_deleted_ids=set(selected_sim_ids),
            ),
            label="使用 SIM",
            value=selected_sim_ids,
            multiple=True,
        ).props(
            "outlined dense use-chips options-html display-value-html"
        ).classes("w-full")
        status = ui.select(
            ["ACTIVE", "DISABLED"],
            label="状态",
            value=(row or {}).get("status", "ACTIVE"),
        ).props("outlined dense").classes("w-full")

        def save() -> None:
            try:
                if editing:
                    service.update_account(
                        row["id"],
                        AccountUpdate(
                            username=username.value,
                            password=password.value,
                            areas=areas.value,
                            use_sims_ids=tuple(_parse_sim_card_ids(use_sims_id.value)),
                            status=status.value,
                        ),
                    )
                else:
                    service.create_account(
                        AccountCreate(
                            id=account_id.value,
                            username=username.value,
                            password=password.value,
                            areas=areas.value,
                            use_sims_ids=tuple(_parse_sim_card_ids(use_sims_id.value)),
                            status=status.value,
                        )
                    )
                dialog.close()
                refresh()
                ui.notify("已保存", type="positive")
            except Exception:
                ui.notify("保存失败，请检查必填项、唯一键、外键和数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=save)
    dialog.open()


def _open_sim_dialog(
    ui: Any,
    service: PgAdminService,
    refresh: Any,
    row: dict[str, Any],
) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        ui.label("编辑 SIM 卡").classes("text-base font-medium")
        ui.input("ID", value=row["id"]).props("outlined dense readonly").classes("w-full")
        sim_type = ui.select(["PHYSICAL", "ESIM"], label="SIM 类型", value=row.get("sim_type", "PHYSICAL")).props("outlined dense").classes("w-full")
        phone_number = ui.input("手机号", value=row.get("phone_number") or "").props("outlined dense").classes("w-full")
        carrier_name = ui.input("运营商", value=row.get("carrier_name") or "").props("outlined dense").classes("w-full")
        esim_profile_name = ui.input("客服备注", value=row.get("esim_profile_name") or "").props("outlined dense").classes("w-full")
        enabled = ui.select(
            {True: "已启用", False: "未启用"},
            label="状态",
            value=bool(row.get("enabled_raw", row.get("enabled", True))),
        ).props("outlined dense").classes("w-full")
        areas = ui.select(
            _region_option_labels(service.list_region_options(), row.get("areas")),
            label="地区",
            value=row.get("areas") or None,
            clearable=True,
        ).props("outlined dense").classes("w-full")
        display_updated_at = ui.input(
            "更新时间",
            value=_format_date_input(
                row.get("_display_updated_at_raw", row.get("display_updated_at"))
            ),
        ).props("outlined dense type=date").classes("w-full")

        def save() -> None:
            try:
                service.update_sim_card(
                    row["id"],
                    SimCardUpdate(
                        sim_type=sim_type.value,
                        phone_number=phone_number.value,
                        carrier_name=carrier_name.value,
                        esim_profile_name=esim_profile_name.value,
                        enabled=bool(enabled.value),
                        areas=areas.value,
                        display_updated_at=_parse_date_input(display_updated_at.value),
                    ),
                )
                dialog.close()
                refresh()
                ui.notify("已保存", type="positive")
            except Exception:
                ui.notify("保存失败，请检查状态值和数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=save)
    dialog.open()


def _open_device_dialog(
    ui: Any,
    service: PgAdminService,
    refresh: Any,
    row: dict[str, Any],
) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        ui.label("编辑设备名称").classes("text-base font-medium")
        ui.input("设备 ID", value=row["id"]).props("outlined dense readonly").classes("w-full")
        name = ui.input("名称 / 备注", value=row.get("name") or "").props("outlined dense").classes("w-full")

        def save() -> None:
            try:
                service.update_device(row["id"], DeviceUpdate(name=name.value))
                dialog.close()
                refresh()
                ui.notify("已保存", type="positive")
            except Exception:
                ui.notify("保存失败，请检查数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=save)
    dialog.open()


def _format_date_input(value: Any) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return ""
    return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")


def _parse_date_input(value: str | None) -> int | None:
    if not value:
        return None
    moment = datetime.combine(
        datetime.strptime(value, "%Y-%m-%d").date(),
        datetime_time.min,
    )
    return int(moment.timestamp() * 1000)


def _confirm_archive_contact(
    ui: Any,
    contact_id: str,
    archive_action: Any,
    refresh: Any,
) -> None:
    with ui.dialog() as dialog, ui.card().classes("gap-3"):
        ui.label("删除联系人").classes("text-base font-medium")
        ui.label(contact_id).classes("text-sm opacity-70")
        ui.label("不会删除历史会话，会将联系人状态改为 ARCHIVED。").classes("text-sm")

        def confirm() -> None:
            try:
                archive_action()
                dialog.close()
                refresh()
                ui.notify("已归档", type="positive")
            except Exception:
                ui.notify("删除失败，请检查数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", color="negative", on_click=confirm)
    dialog.open()


def _open_sim_history(ui: Any, service: PgAdminService) -> None:
    with ui.dialog() as dialog, ui.card().classes('w-full gap-3').style('max-width: 1200px'):
        ui.label('SIM 卡历史记录').classes('text-lg font-medium')
        ui.label('已删除或更换的旧号码仅在这里保留记录，不参与账号分配和短信检测。').classes('text-sm text-gray-600')
        rows = service.list_sim_card_history()
        for row in rows:
            row['occurred_display'] = datetime.fromtimestamp(row['occurred_at']/1000).strftime('%Y-%m-%d %H:%M:%S')
        query = ui.input('搜索历史号码或地区').props('outlined dense clearable').classes('w-80')
        columns = [{'name':key,'field':key,'label':label,'align':'left','sortable':True} for key,label in [
            ('phone_number','原手机号'),('replacement_phone','更换后手机号'),('areas','原地区'),
            ('device_name','设备'),('reason','移除原因'),('occurred_display','移除 / 更换时间'),
            ('sim_card_id','原 SIM ID')]]
        table = ui.table(columns=columns,rows=rows,row_key='id',pagination=15).classes('w-full')
        query.bind_value_to(table,'filter')
        ui.button('关闭',on_click=dialog.close).props('flat')
    dialog.open()


def _confirm_delete(
    ui: Any,
    title: str,
    item_id: str,
    delete_action: Any,
    refresh: Any,
) -> None:
    with ui.dialog() as dialog, ui.card().classes("gap-3"):
        ui.label(title).classes("text-base font-medium")
        ui.label(item_id).classes("text-sm opacity-70")

        def confirm() -> None:
            try:
                delete_action()
                dialog.close()
                refresh()
                ui.notify("已删除", type="positive")
            except Exception:
                ui.notify("删除失败，请检查数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", color="negative", on_click=confirm)
    dialog.open()


def _confirm_unregister_device(
    ui: Any,
    device_id: str,
    unregister_action: Any,
    refresh: Any,
) -> None:
    with ui.dialog() as dialog, ui.card().classes("gap-3"):
        ui.label("注销设备").classes("text-base font-medium")
        ui.label(device_id).classes("text-sm opacity-70")
        ui.label("会禁用设备，将它的 SIM 卡移入历史记录，并保留聊天数据。").classes("text-sm")

        def confirm() -> None:
            try:
                unregister_action()
                dialog.close()
                refresh()
                ui.notify("设备已注销", type="positive")
            except Exception:
                ui.notify("注销失败，请检查数据库连接", type="negative")

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("注销", icon="delete", color="negative", on_click=confirm)
    dialog.open()
