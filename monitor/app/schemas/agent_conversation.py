from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic import StringConstraints


AgentReplyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class AgentConversationItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    external_phone_number: str = Field(alias="externalPhoneNumber")
    service_phone_number: str | None = Field(alias="servicePhoneNumber")
    contact_id: str = Field(alias="contactId")
    areas: str
    status: str
    unread_count: int = Field(alias="unreadCount")
    last_message_preview: str | None = Field(alias="lastMessagePreview")
    last_message_direction: str | None = Field(alias="lastMessageDirection")
    last_message_at: int | None = Field(alias="lastMessageAt")


class AgentConversationSearchItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    contact_phone_number: str = Field(alias="contactPhoneNumber")
    remark: str | None
    service_phone_number: str | None = Field(alias="servicePhoneNumber")
    conversation_id: str = Field(alias="conversationId")


class AgentMessageAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    part_id: int = Field(alias="partId")
    content_type: str = Field(alias="contentType")
    name: str | None
    size: int | None
    url: str | None


class AgentMessageItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    conversation_id: str = Field(alias="conversationId")
    direction: str
    message_type: str = Field(alias="messageType")
    text_content: str | None = Field(alias="textContent")
    state: str
    from_phone_number: str | None = Field(alias="fromPhoneNumber")
    to_phone_number: str | None = Field(alias="toPhoneNumber")
    created_at: int = Field(alias="createdAt")
    received_at: int | None = Field(alias="receivedAt")
    sent_at: int | None = Field(alias="sentAt")
    delivered_at: int | None = Field(alias="deliveredAt")
    customer_sim_card: str | None = Field(default=None, alias="customerSimCard")
    customer_remark: str | None = Field(default=None, alias="customerRemark")
    attachments: list[AgentMessageAttachment] = Field(default_factory=list)


class AgentReplyRequest(BaseModel):
    text: AgentReplyText
