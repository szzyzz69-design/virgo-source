from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.services.device_auth_service import AuthenticatedDevice
from app.services.inbound_message_service import InboundConflict, InboundResult


class Auth:
    def authenticate(self, token): return AuthenticatedDevice("dev_1", True, "online")


class Service:
    def __init__(self, replay=False, error=None): self.replay=replay; self.error=error; self.calls=[]
    def create(self, device_id, request):
        self.calls.append((device_id, request))
        if self.error: raise self.error
        return InboundResult("msg_1", "conv_1", not self.replay)


def body():
    return {"id":"text:1","type":"SMS","sender":"+8613800138000","recipient":None,
            "simNumber":1,"subscriptionId":3,"receivedAt":"2026-06-22T08:00:00Z",
            "textMessage":{"text":"hello"},"dataMessage":None}


def client(service):
    app=create_app(Settings("postgresql://unused","reg","business"),device_auth_service=Auth(),inbound_message_service=service)
    return TestClient(app,raise_server_exceptions=False)


def test_inbox_returns_201_then_200_contract():
    first=client(Service()).post("/mobile/v1/inbox",headers={"Authorization":"Bearer token"},json=body())
    replay=client(Service(replay=True)).post("/mobile/v1/inbox",headers={"Authorization":"Bearer token"},json=body())
    assert first.status_code==201 and first.json()=={"id":"msg_1","created":True,"conversationId":"conv_1"}
    assert replay.status_code==200 and replay.json()["created"] is False


def test_inbox_authentication_precedes_bad_body():
    response=client(Service()).post("/mobile/v1/inbox",headers={"Content-Type":"application/json"},content="{bad")
    assert response.status_code==401


def test_inbox_maps_idempotency_conflict():
    response=client(Service(error=InboundConflict())).post("/mobile/v1/inbox",headers={"Authorization":"Bearer token"},json=body())
    assert response.status_code==409 and response.json()["code"]=="IDEMPOTENCY_CONFLICT"
