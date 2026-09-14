from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_inbox_creates_sms_and_replays_without_duplicate_unread(clean_database):
    class Publisher:
        def __init__(self): self.events=[]
        def publish(self, device_id, message_id, conversation_id):
            self.events.append((device_id,message_id,conversation_id))

    marker=clean_database.track_push_token("pytest-inbox-"+uuid4().hex)
    sender=clean_database.track_phone("+86"+str(uuid4().int)[:11])
    client_id=clean_database.track_message_key("text:"+uuid4().hex)
    recipient="+8613900000000"
    publisher=Publisher()
    app=create_app(Settings(clean_database.dsn,"registration-secret","business-secret"),inbound_publisher=publisher)
    with TestClient(app,raise_server_exceptions=False) as client:
        registration=client.post("/mobile/v1/device",headers={"Authorization":"Bearer registration-secret"},json={
            "name":"inbox-phone","pushToken":marker,"simCards":[{"slotIndex":0,"simNumber":1,"phoneNumber":recipient}]}).json()
        clean_database.track(registration["id"])
        headers={"Authorization":f"Bearer {registration['token']}"}
        assert client.patch("/mobile/v1/device",headers=headers,json={"id":registration["id"],"pushToken":marker,
            "simCards":[{"slotIndex":0,"simNumber":1,"phoneNumber":recipient}]}).status_code==200
        body={"id":client_id,"type":"SMS","sender":sender,"recipient":recipient,"simNumber":1,
              "subscriptionId":3,"receivedAt":datetime.now(timezone.utc).isoformat(),
              "textMessage":{"text":"hello inbound"},"dataMessage":None}
        first=client.post("/mobile/v1/inbox",headers=headers,json=body)
        replay=client.post("/mobile/v1/inbox",headers=headers,json=body)
        conflict=client.post("/mobile/v1/inbox",headers=headers,json={**body,"textMessage":{"text":"different"}})
    assert first.status_code==201 and replay.status_code==200
    assert conflict.status_code==409
    assert first.json()["id"]==replay.json()["id"]
    assert first.json()["conversationId"]==replay.json()["conversationId"]
    with psycopg.connect(clean_database.dsn) as connection:
        message=connection.execute("SELECT direction,state,text_content,sim_card_id FROM messages WHERE id=%s",(first.json()["id"],)).fetchone()
        unread=connection.execute("SELECT unread_count FROM conversations WHERE id=%s",(first.json()["conversationId"],)).fetchone()[0]
        history=connection.execute("SELECT state,source FROM message_state_history WHERE message_id=%s",(first.json()["id"],)).fetchone()
    assert message[0:3]==("INBOUND","Received","hello inbound") and message[3] is not None
    assert unread==1 and history==("Received","DEVICE")
    assert publisher.events==[(registration["id"],first.json()["id"],first.json()["conversationId"])]


def test_inbox_accepts_data_sms_without_port(clean_database):
    marker=clean_database.track_push_token("pytest-inbox-data-"+uuid4().hex)
    sender=clean_database.track_phone("+7"+str(uuid4().int)[:12])
    client_id=clean_database.track_message_key("data:"+uuid4().hex)
    app=create_app(Settings(clean_database.dsn,"registration-secret","business-secret"))
    with TestClient(app,raise_server_exceptions=False) as client:
        registration=client.post("/mobile/v1/device",headers={"Authorization":"Bearer registration-secret"},json={
            "name":"data-phone","pushToken":marker,"simCards":[{"slotIndex":0,"simNumber":1}]}).json()
        clean_database.track(registration["id"])
        headers={"Authorization":f"Bearer {registration['token']}"}
        response=client.post("/mobile/v1/inbox",headers=headers,json={"id":client_id,"type":"DATA_SMS","sender":sender,
            "recipient":None,"simNumber":None,"subscriptionId":9,"receivedAt":datetime.now(timezone.utc).isoformat(),
            "textMessage":None,"dataMessage":{"data":"AQJ/"}})
    assert response.status_code==201
    with psycopg.connect(clean_database.dsn) as connection:
        row=connection.execute("SELECT message_type,data_base64,data_port,sim_card_id FROM messages WHERE id=%s",(response.json()["id"],)).fetchone()
    assert row==("DATA_SMS","AQJ/",None,None)
