from typing import Protocol


class InboundMessagePublisher(Protocol):
    def publish(self, device_id: str, message_id: str, conversation_id: str) -> None: ...


class NoOpInboundMessagePublisher:
    def publish(self, device_id: str, message_id: str, conversation_id: str) -> None:
        return None
