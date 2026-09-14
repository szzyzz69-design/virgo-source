from dataclasses import dataclass
import logging
import secrets
import time

from psycopg.types.json import Jsonb

from app.database import Database
from app.schemas.inbound_message import InboundMessageRequest, inbound_digest, received_millis
from app.schemas.message import normalize_phone
from app.services.agent_event_publisher import NoOpAgentEventPublisher
from app.services.inbound_publisher import InboundMessagePublisher


logger = logging.getLogger(__name__)


class InboundConflict(Exception): pass
class InboundDeviceUnavailable(Exception): pass
class InboundValidation(Exception): pass


@dataclass(frozen=True, slots=True)
class InboundResult:
    id: str
    conversation_id: str
    created: bool
    areas: str | None = None
    sim_card_id: str | None = None
    text_content: str | None = None
    state: str = "Received"
    created_at: int | None = None
    agent_account_ids: tuple[str, ...] = ()


class InboundMessageService:
    def __init__(
        self,
        database: Database,
        publisher: InboundMessagePublisher,
        agent_publisher=NoOpAgentEventPublisher(),
    ):
        self._database=database; self._publisher=publisher; self._agent_publisher=agent_publisher

    def create(self, device_id: str, request: InboundMessageRequest) -> InboundResult:
        now=time.time_ns()//1_000_000; received=received_millis(request)
        if received > now + 300_000: raise InboundValidation
        digest=inbound_digest(request)
        with self._database.transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"inbox:{device_id}:{request.id}",))
            device=connection.execute("SELECT enabled FROM devices WHERE id=%s FOR UPDATE",(device_id,)).fetchone()
            if device is None or not device[0]: raise InboundDeviceUnavailable
            existing=connection.execute(
                "SELECT id,conversation_id,metadata FROM messages WHERE device_id=%s AND direction='INBOUND' AND idempotency_key=%s LIMIT 1",
                (device_id,request.id),).fetchone()
            if existing:
                if (existing[2] or {}).get("requestDigest") != digest: raise InboundConflict
                return InboundResult(existing[0],existing[1],False)
            sim=None
            if request.sim_number is not None:
                sim=connection.execute("SELECT id,sim_number,areas FROM sim_cards WHERE device_id=%s AND sim_number=%s LIMIT 1",(device_id,request.sim_number)).fetchone()
            if sim is None and request.recipient:
                try: normalized_recipient=normalize_phone(request.recipient)
                except ValueError: normalized_recipient=None
                if normalized_recipient:
                    sim=connection.execute(
                        "SELECT id,sim_number,areas FROM sim_cards WHERE device_id=%s AND regexp_replace(phone_number,'[\\s()\\-]','','g')=%s LIMIT 1",
                        (device_id,normalized_recipient),).fetchone()
            sim_id=sim[0] if sim else None; sim_number=sim[1] if sim else request.sim_number; area=sim[2] if sim else None
            agent_account_ids=()
            if sim_id is not None:
                agent_account_ids=tuple(
                    row[0] for row in connection.execute(
                        """
                        SELECT a.id
                        FROM account_sim_cards acs
                        JOIN accounts a ON a.id = acs.account_id
                        WHERE acs.sim_card_id = %s
                          AND a.status = 'ACTIVE'
                        ORDER BY a.id
                        """,
                        (sim_id,),
                    ).fetchall()
                )
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"conversation:{device_id}:{request.sender}:{sim_id or 'none'}",))
            contact_id=f"contact_{secrets.token_hex(16)}"
            contact_id=connection.execute(
                """INSERT INTO contacts(id,phone_number,normalized_phone_number,source,last_contact_at,created_at,updated_at,areas)
                VALUES(%s,%s,%s,'INBOUND_AUTO',%s,%s,%s,%s)
                ON CONFLICT(normalized_phone_number) DO UPDATE SET phone_number=EXCLUDED.phone_number,last_contact_at=EXCLUDED.last_contact_at,updated_at=EXCLUDED.updated_at,areas=EXCLUDED.areas
                RETURNING id""",(contact_id,request.sender,request.sender,received,now,now,area)).fetchone()[0]
            conversation=connection.execute(
                """SELECT id FROM conversations WHERE external_phone_number=%s AND device_id=%s
                AND sim_card_id IS NOT DISTINCT FROM %s::varchar AND status='OPEN' FOR UPDATE""",
                (request.sender,device_id,sim_id)).fetchone()
            conversation_id=conversation[0] if conversation else f"conv_{secrets.token_hex(16)}"
            if conversation is None:
                connection.execute("""INSERT INTO conversations(id,external_phone_number,contact_id,device_id,sim_card_id,sim_number,areas,status,created_at,updated_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)""",(conversation_id,request.sender,contact_id,device_id,sim_id,sim_number,area,now,now))
            message_id=f"msg_{secrets.token_hex(16)}"
            text=request.text_message.text if request.text_message else None
            data=request.data_message.data if request.data_message else None
            metadata={"requestDigest":digest,"simNumber":request.sim_number,"subscriptionId":request.subscription_id,"recipient":request.recipient}
            connection.execute("""INSERT INTO messages(id,conversation_id,direction,message_type,text_content,data_base64,data_port,from_phone_number,to_phone_number,state,device_id,sim_card_id,sim_number,idempotency_key,received_at,metadata,created_at,updated_at)
            VALUES(%s,%s,'INBOUND',%s,%s,%s,NULL,%s,%s,'Received',%s,%s,%s,%s,%s,%s,%s,%s)""",
            (message_id,conversation_id,request.type,text,data,request.sender,request.recipient,device_id,sim_id,sim_number,request.id,received,Jsonb(metadata),now,now))
            connection.execute("""INSERT INTO message_state_history(message_id,state,source,reason,occurred_at,created_at)
            VALUES(%s,'Received','DEVICE','Received by Android device',%s,%s)""",(message_id,received,now))
            preview=text[:255] if text else "[Data SMS]"
            connection.execute("""UPDATE conversations SET unread_count=unread_count+1,last_message_preview=%s,last_message_direction='INBOUND',last_message_at=%s,updated_at=%s WHERE id=%s""",(preview,received,now,conversation_id))
            connection.execute("UPDATE devices SET status='online',last_seen_at=%s,updated_at=%s WHERE id=%s",(now,now,device_id))
            result=InboundResult(
                id=message_id,
                conversation_id=conversation_id,
                created=True,
                areas=area,
                sim_card_id=sim_id,
                text_content=text,
                state="Received",
                created_at=now,
                agent_account_ids=agent_account_ids,
            )
        try: self._publisher.publish(device_id,result.id,result.conversation_id)
        except Exception: logger.exception("Inbound publisher failed for message %s",result.id)
        for account_id in result.agent_account_ids:
            try:
                self._agent_publisher.publish_inbound_message(
                    account_id,
                    result.id,
                    result.conversation_id,
                    result.sim_card_id,
                    text_content=result.text_content,
                    state=result.state,
                    created_at=result.created_at,
                )
            except Exception: logger.exception("Agent event publisher failed for message %s",result.id)
        return result
