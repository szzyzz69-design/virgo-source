import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from threading import Event, Lock
from typing import AsyncIterator


_CLOSE = object()


def encode_message_enqueued(message_id: str) -> str:
    encoded_id = json.dumps(message_id, ensure_ascii=False)[1:-1]
    data = json.dumps(
        {"messageId": message_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"id: {encoded_id}\n"
        "event: MessageEnqueued\n"
        f"data: {data}\n\n"
    )


def encode_heartbeat() -> str:
    occurred_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    return f": ping {occurred_at}\n\n"


@dataclass(slots=True)
class SseConnection:
    device_id: str
    events: asyncio.Queue[object] = field(default_factory=asyncio.Queue)
    closed: Event = field(default_factory=Event)
    loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    loop_lock: Lock = field(default_factory=Lock, init=False)

    def bind_loop(self) -> None:
        with self.loop_lock:
            self.loop = asyncio.get_running_loop()

    def put_event(self, item: object) -> None:
        with self.loop_lock:
            loop = self.loop
        if loop is None or loop.is_closed():
            self.events.put_nowait(item)
            return
        loop.call_soon_threadsafe(self.events.put_nowait, item)

    def close(self) -> None:
        if not self.closed.is_set():
            self.closed.set()
            self.put_event(_CLOSE)


class SseConnectionRegistry:
    def __init__(self, *, heartbeat_seconds: float = 20):
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        self._heartbeat_seconds = heartbeat_seconds
        self._connections: dict[str, SseConnection] = {}
        self._lock = Lock()

    def register(self, device_id: str) -> SseConnection:
        connection = SseConnection(device_id)
        with self._lock:
            previous = self._connections.get(device_id)
            self._connections[device_id] = connection
        if previous is not None:
            previous.close()
        return connection

    def unregister(self, connection: SseConnection) -> None:
        with self._lock:
            if self._connections.get(connection.device_id) is connection:
                del self._connections[connection.device_id]
        connection.close()

    def publish_message(self, device_id: str, message_id: str) -> bool:
        with self._lock:
            connection = self._connections.get(device_id)
            if connection is None or connection.closed.is_set():
                return False
            connection.put_event(encode_message_enqueued(message_id))
            return True

    async def stream(self, connection: SseConnection) -> AsyncIterator[str]:
        connection.bind_loop()
        try:
            while not connection.closed.is_set():
                try:
                    item = await asyncio.wait_for(
                        connection.events.get(),
                        timeout=self._heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    yield encode_heartbeat()
                    continue
                if item is _CLOSE:
                    return
                yield str(item)
        finally:
            self.unregister(connection)
