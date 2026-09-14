import asyncio
import json
from threading import Thread

from app.services.sse import SseConnectionRegistry, encode_message_enqueued
from app.services.message_publisher import RegistryMessageEnqueuedPublisher


async def read_one_event(registry, connection):
    stream = registry.stream(connection)
    try:
        return await anext(stream)
    finally:
        await stream.aclose()


async def collect_events(registry, connection):
    return [event async for event in registry.stream(connection)]


def test_message_event_uses_android_contract():
    assert encode_message_enqueued("msg_1") == (
        "id: msg_1\n"
        "event: MessageEnqueued\n"
        'data: {"messageId":"msg_1"}\n\n'
    )


def test_message_event_json_escapes_untrusted_identifier():
    message_id = 'msg_"line\n'
    event = encode_message_enqueued(message_id)

    assert event.startswith("id: msg_\\\"line\\n\n")
    data = event.split("data: ", 1)[1].splitlines()[0]
    assert json.loads(data) == {"messageId": message_id}


def test_publish_targets_only_the_registered_device():
    registry = SseConnectionRegistry(heartbeat_seconds=0.01)
    first = registry.register("dev_1")
    second = registry.register("dev_2")

    assert registry.publish_message("dev_1", "msg_1") is True
    assert asyncio.run(read_one_event(registry, first)).startswith("id: msg_1\n")
    assert asyncio.run(read_one_event(registry, second)).startswith(": ping ")


def test_publish_without_connection_is_a_successful_no_op():
    registry = SseConnectionRegistry()

    assert registry.publish_message("dev_missing", "msg_1") is False


def test_registry_message_publisher_delivers_to_registry():
    registry = SseConnectionRegistry(heartbeat_seconds=0.01)
    connection = registry.register("dev_1")
    publisher = RegistryMessageEnqueuedPublisher(registry)

    publisher.publish("dev_1", "msg_1")

    event = asyncio.run(read_one_event(registry, connection))
    assert event.startswith("id: msg_1\n")


def test_new_connection_replaces_old_and_old_cleanup_preserves_new():
    registry = SseConnectionRegistry(heartbeat_seconds=0.01)
    old = registry.register("dev_1")
    new = registry.register("dev_1")

    assert asyncio.run(collect_events(registry, old)) == []
    registry.unregister(old)
    assert registry.publish_message("dev_1", "msg_1") is True
    event = asyncio.run(read_one_event(registry, new))
    assert event.startswith("id: msg_1\n")


def test_idle_connection_emits_sse_comment_heartbeat():
    registry = SseConnectionRegistry(heartbeat_seconds=0.001)
    connection = registry.register("dev_1")

    heartbeat = asyncio.run(read_one_event(registry, connection))

    assert heartbeat.startswith(": ping ")
    assert heartbeat.endswith("Z\n\n")


def test_closing_stream_unregisters_connection():
    registry = SseConnectionRegistry(heartbeat_seconds=0.001)
    connection = registry.register("dev_1")
    asyncio.run(read_one_event(registry, connection))

    assert registry.publish_message("dev_1", "msg_1") is False


def test_publish_wakes_async_stream_from_sync_thread():
    async def scenario():
        registry = SseConnectionRegistry(heartbeat_seconds=10)
        connection = registry.register("dev_1")

        async def read_event():
            stream = registry.stream(connection)
            try:
                return await anext(stream)
            finally:
                await stream.aclose()

        event_task = asyncio.create_task(read_event())
        await asyncio.sleep(0)

        publisher_thread = Thread(
            target=registry.publish_message,
            args=("dev_1", "msg_1"),
        )
        publisher_thread.start()
        publisher_thread.join()

        event = await asyncio.wait_for(event_task, timeout=1)
        assert event.startswith("id: msg_1\n")

    asyncio.run(scenario())
