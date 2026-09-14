import time

from app.database import Database
from app.schemas.agent_conversation import (
    AgentConversationItem,
    AgentConversationSearchItem,
    AgentMessageAttachment,
    AgentMessageItem,
    AgentReplyRequest,
)
from app.schemas.message import MessageCreateRequest
from app.services.agent_auth_service import AuthenticatedAgent
from app.services.message_service import MessageCommandService, MessageCreateResult


class ConversationForbidden(Exception):
    pass


class ConversationNotFound(Exception):
    pass


class AgentConversationService:
    def __init__(
        self,
        database: Database,
        message_service: MessageCommandService,
    ):
        self._database = database
        self._message_service = message_service

    def list_conversations(self, agent: AuthenticatedAgent) -> list[AgentConversationItem]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.external_phone_number, c.contact_id, c.areas,
                       c.status, c.unread_count, c.last_message_preview,
                       c.last_message_direction, c.last_message_at,
                       s.phone_number
                FROM conversations c
                LEFT JOIN sim_cards s ON s.id = c.sim_card_id
                WHERE c.status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                  AND NULLIF(BTRIM(c.areas), '') IS NOT DISTINCT FROM NULLIF(BTRIM(%s), '')
                ORDER BY c.last_message_at DESC NULLS LAST, c.updated_at DESC, c.id
                """,
                (agent.areas,),
            ).fetchall()
        return [
            AgentConversationItem(
                id=row[0],
                externalPhoneNumber=row[1],
                contactId=row[2],
                areas=row[3],
                status=row[4],
                unreadCount=row[5],
                lastMessagePreview=row[6],
                lastMessageDirection=row[7],
                lastMessageAt=row[8],
                servicePhoneNumber=row[9],
            )
            for row in rows
        ]

    def search_conversations(
        self,
        agent: AuthenticatedAgent,
        phone_number: str,
    ) -> list[AgentConversationSearchItem]:
        phone_number_pattern = f"%{phone_number}%"
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT ct.phone_number, ct.remark, s.phone_number, c.id
                FROM conversations c
                JOIN contacts ct ON ct.id = c.contact_id
                JOIN sim_cards s ON s.id = c.sim_card_id
                WHERE c.status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                  AND NULLIF(BTRIM(c.areas), '') IS NOT DISTINCT FROM NULLIF(BTRIM(%s), '')
                  AND ct.normalized_phone_number LIKE %s
                ORDER BY c.last_message_at DESC NULLS LAST, c.updated_at DESC, c.id
                """,
                (agent.areas, phone_number_pattern),
            ).fetchall()
        return [
            AgentConversationSearchItem(
                contactPhoneNumber=row[0],
                remark=row[1],
                servicePhoneNumber=row[2],
                conversationId=row[3],
            )
            for row in rows
        ]

    def list_messages(
        self,
        conversation_id: str,
        agent: AuthenticatedAgent,
    ) -> list[AgentMessageItem]:
        self._ensure_area_access(conversation_id, agent.areas)
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT m.id, m.conversation_id, m.direction, m.message_type,
                       m.text_content, m.state, m.from_phone_number,
                       m.to_phone_number, m.created_at, m.received_at,
                       m.sent_at, m.delivered_at,
                       COALESCE(s.phone_number, c.sim_card_id),
                       s.esim_profile_name
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                LEFT JOIN sim_cards s ON s.id = c.sim_card_id
                WHERE m.conversation_id = %s
                ORDER BY m.created_at ASC, m.id ASC
                """,
                (conversation_id,),
            ).fetchall()
            message_ids = [row[0] for row in rows]
            attachments_by_message = {message_id: [] for message_id in message_ids}
            if message_ids:
                attachment_rows = connection.execute(
                    """
                    SELECT message_id, id, part_id, content_type, name, size, url
                    FROM message_attachments
                    WHERE message_id = ANY(%s::varchar[])
                    ORDER BY message_id, part_id
                    """,
                    (message_ids,),
                ).fetchall()
                for attachment in attachment_rows:
                    attachments_by_message[attachment[0]].append(
                        AgentMessageAttachment(
                            id=attachment[1],
                            partId=attachment[2],
                            contentType=attachment[3],
                            name=attachment[4],
                            size=attachment[5],
                            url=attachment[6],
                        )
                    )
        return [
            AgentMessageItem(
                id=row[0],
                conversationId=row[1],
                direction=row[2],
                messageType=row[3],
                textContent=row[4],
                state=row[5],
                fromPhoneNumber=row[6],
                toPhoneNumber=row[7],
                createdAt=row[8],
                receivedAt=row[9],
                sentAt=row[10],
                deliveredAt=row[11],
                customerSimCard=row[12],
                customerRemark=row[13],
                attachments=attachments_by_message.get(row[0], []),
            )
            for row in rows
        ]

    def mark_read(self, conversation_id: str, agent: AuthenticatedAgent) -> None:
        self._ensure_area_access(conversation_id, agent.areas)
        now = time.time_ns() // 1_000_000
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE conversations c
                SET unread_count = 0,
                    updated_at = %s
                WHERE c.id = %s
                  AND c.status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                  AND NULLIF(BTRIM(c.areas), '') IS NOT DISTINCT FROM NULLIF(BTRIM(%s), '')
                """,
                (now, conversation_id, agent.areas),
            )

    def reply(
        self,
        conversation_id: str,
        agent: AuthenticatedAgent,
        request: AgentReplyRequest,
        idempotency_key: str,
    ) -> MessageCreateResult:
        conversation = self._load_reply_conversation(conversation_id, agent)
        return self._message_service.create(
            MessageCreateRequest(
                phoneNumbers=[conversation[0]],
                text=request.text,
                deviceId=conversation[1],
                simNumber=conversation[2],
                conversationId=conversation_id,
                metadata={"source": "agent", "agentId": agent.id},
            ),
            idempotency_key,
        )

    def _load_reply_conversation(
        self,
        conversation_id: str,
        agent: AuthenticatedAgent,
    ):
        with self._database.transaction() as connection:
            conversation = connection.execute(
                """
                SELECT c.external_phone_number, c.device_id, c.sim_number
                FROM conversations c
                JOIN account_sim_cards acs ON acs.sim_card_id = c.sim_card_id
                WHERE c.id = %s
                  AND c.status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                  AND NULLIF(BTRIM(c.areas), '') IS NOT DISTINCT FROM NULLIF(BTRIM(%s), '')
                  AND acs.account_id = %s
                """,
                (conversation_id, agent.areas, agent.id),
            ).fetchone()
            if conversation is not None:
                return conversation
            exists = connection.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = %s
                  AND status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                """,
                (conversation_id,),
            ).fetchone()
        if exists is not None:
            raise ConversationForbidden
        raise ConversationNotFound

    def _ensure_area_access(self, conversation_id: str, agent_area: str | None) -> None:
        with self._database.transaction() as connection:
            allowed = connection.execute(
                """
                SELECT c.id
                FROM conversations c
                WHERE c.id = %s
                  AND c.status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                  AND NULLIF(BTRIM(c.areas), '') IS NOT DISTINCT FROM NULLIF(BTRIM(%s), '')
                """,
                (conversation_id, agent_area),
            ).fetchone()
            if allowed is not None:
                return
            exists = connection.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = %s
                  AND status IN ('OPEN', 'CLOSED', 'ARCHIVED')
                """,
                (conversation_id,),
            ).fetchone()
        if exists is not None:
            raise ConversationForbidden
        raise ConversationNotFound
