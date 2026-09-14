from scripts.trigger_agent_inbox import InboxSeedConfig, build_inbox_payload, normalize_base_url


def test_normalize_base_url_removes_trailing_slashes():
    assert normalize_base_url(" http://127.0.0.1:8000/// ") == "http://127.0.0.1:8000"


def test_build_inbox_payload_uses_sms_schema_fields():
    config = InboxSeedConfig(
        base_url="http://127.0.0.1:8000",
        database_url="postgresql://example",
        registration_token="registration-secret",
        agent_username="agent_test",
        agent_password="agent_test_123",
        area="test",
        sender="+8613800009527",
        recipient="+8613900000000",
        text="hello from test",
        sim_number=1,
        subscription_id=3,
    )

    payload = build_inbox_payload(
        config,
        message_id="seed-inbox-test",
        received_at="2026-06-29T10:00:00Z",
    )

    assert payload == {
        "id": "seed-inbox-test",
        "type": "SMS",
        "sender": "+8613800009527",
        "recipient": "+8613900000000",
        "simNumber": 1,
        "subscriptionId": 3,
        "receivedAt": "2026-06-29T10:00:00Z",
        "textMessage": {"text": "hello from test"},
        "dataMessage": None,
    }
