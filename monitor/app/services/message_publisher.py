from typing import Protocol

from app.services.sse import SseConnectionRegistry


class MessageEnqueuedPublisher(Protocol):
    def publish(self, device_id: str, message_id: str) -> None:
        raise NotImplementedError


class NoOpMessageEnqueuedPublisher:
    def publish(self, device_id: str, message_id: str) -> None:
        return None


class RegistryMessageEnqueuedPublisher:
    def __init__(self, registry: SseConnectionRegistry):
        self._registry = registry

    def publish(self, device_id: str, message_id: str) -> None:
        self._registry.publish_message(device_id, message_id)
