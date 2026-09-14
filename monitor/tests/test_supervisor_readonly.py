from pathlib import Path

from app.api.supervisor import create_supervisor_router
from app.config import Settings


class ReadOnlyService:
    def list_agents(self, timezone):
        return []

    def list_conversations(self, *args):
        return {"items": [], "nextCursor": None}

    def get_conversation(self, *args):
        return {}

    def list_messages(self, *args):
        return {"items": [], "nextBefore": None}


def test_supervisor_exposes_no_business_write_routes():
    settings = Settings(
        "postgresql://unused",
        "registration-secret",
        "business-secret",
        supervisor_username="admin",
        supervisor_password="password",
        supervisor_session_secret="x" * 32,
    )
    router = create_supervisor_router(settings, ReadOnlyService())
    routes = {
        (method, route.path)
        for route in router.routes
        for method in route.methods
    }

    assert routes == {
        ("POST", "/supervisor/api/login"),
        ("POST", "/supervisor/api/logout"),
        ("GET", "/supervisor/api/me"),
        ("GET", "/supervisor/api/agents"),
        ("GET", "/supervisor/api/conversations"),
        ("GET", "/supervisor/api/conversations/{conversation_id}"),
        ("GET", "/supervisor/api/conversations/{conversation_id}/messages"),
    }


def test_supervisor_service_contains_no_database_mutation_sql():
    source = Path("app/services/supervisor_service.py").read_text(encoding="utf-8").upper()
    for statement in ("UPDATE ", "INSERT ", "DELETE ", "ALTER ", "DROP ", "TRUNCATE "):
        assert statement not in source
    assert "UNREAD_COUNT" not in source


def test_frontend_has_no_read_state_or_business_write_controls():
    source = Path("supervisor/src/main.js").read_text(encoding="utf-8")
    assert "未读" not in source
    assert "unread" not in source.lower()
    assert "/read" not in source
    assert "sendReply" not in source
    assert "openRemark" not in source
    assert "ez-copy" not in source
    assert "接收时间" in source
    assert "发送时间" in source
