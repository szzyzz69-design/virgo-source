from contextlib import contextmanager

from app.services.agent_auth_service import AuthenticatedAgent
from app.services.agent_conversation_service import AgentConversationService


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, statement, params=()):
        self.calls.append((" ".join(statement.split()), params))
        rows = self.responses.pop(0) if self.responses else []
        return Result(rows)


class Database:
    def __init__(self, responses):
        self.connection = Connection(responses)

    @contextmanager
    def transaction(self):
        yield self.connection


def test_conversation_list_uses_account_sim_binding_and_account_area():
    database = Database(
        [[("conv_1", "+16045550101", "contact_1", "OPEN", 2, "hello", "INBOUND", 123, "+16045550999")]]
    )
    service = AgentConversationService(database, object())
    agent = AuthenticatedAgent("account_1", "agent", "Regina")

    result = service.list_conversations(agent)

    statement, params = database.connection.calls[0]
    assert "JOIN account_sim_cards acs" in statement
    assert "acs.account_id = %s" in statement
    assert "BTRIM(c.areas)" not in statement
    assert params == ("account_1",)
    assert result[0].id == "conv_1"
    assert result[0].areas == "Regina"


def test_message_detail_access_uses_account_sim_binding_not_legacy_area():
    database = Database([[("conv_1",)], []])
    service = AgentConversationService(database, object())
    agent = AuthenticatedAgent("account_1", "agent", "Richmond Hill")

    assert service.list_messages("conv_1", agent) == []

    access_statement, access_params = database.connection.calls[0]
    assert "JOIN account_sim_cards acs ON acs.sim_card_id = c.sim_card_id" in access_statement
    assert "acs.account_id = %s" in access_statement
    assert "c.areas" not in access_statement
    assert access_params == ("conv_1", "account_1")


def test_mark_read_revalidates_account_sim_binding_in_update():
    database = Database([[("conv_1",)], []])
    service = AgentConversationService(database, object())
    agent = AuthenticatedAgent("account_1", "agent", "Cambridge")

    service.mark_read("conv_1", agent)

    update_statement, update_params = database.connection.calls[1]
    assert "UPDATE conversations c" in update_statement
    assert "EXISTS ( SELECT 1 FROM account_sim_cards acs" in update_statement
    assert "acs.account_id = %s" in update_statement
    assert update_params[1:] == ("conv_1", "account_1")
