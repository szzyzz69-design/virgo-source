from fastapi import FastAPI

from app.api.agent_auth import AgentAuthenticationService, create_agent_auth_router
from app.api.agent_conversation import (
    AgentConversationQueryService,
    create_agent_conversation_router,
)
from app.api.agent_contact import (
    AgentContactQueryService,
    create_agent_contact_router,
)
from app.api.agent_events import AgentEventsRegistry, create_agent_events_router
from app.api.events import create_events_router
from app.api.device import (
    DeviceAuthenticationService,
    DeviceRegistrationService,
    create_device_router,
)
from app.api.message import MessageCreationService, create_message_router
from app.api.message_pull import MessagePullingService, create_message_pull_router
from app.api.message_status import MessageStateUpdatingService, create_message_status_router
from app.api.inbox import InboundCreatingService, create_inbox_router
from app.config import Settings
from app.database import Database
from app.errors import install_error_handling
from app.api.mms_webhook import MmsHandlingService, create_mms_webhook_router
from app.services.device_auth_service import DeviceAuthService
from app.services.device_service import DeviceService
from app.services.agent_auth_service import AgentAuthService
from app.services.agent_conversation_service import AgentConversationService
from app.services.agent_contact_service import AgentContactService
from app.services.agent_event_publisher import (
    AgentEventRegistry,
    RegistryAgentEventPublisher,
)
from app.services.message_publisher import (
    MessageEnqueuedPublisher,
    RegistryMessageEnqueuedPublisher,
)
from app.services.message_service import MessageCommandService
from app.services.message_pull_service import MessagePullService
from app.services.message_state_service import MessageStateService
from app.services.inbound_message_service import InboundMessageService
from app.services.inbound_publisher import InboundMessagePublisher, NoOpInboundMessagePublisher
from app.services.mms_webhook_service import MmsWebhookService
from app.services.sse import SseConnectionRegistry
from app.services.object_storage import S3ObjectStorage
from pg.admin_ui import mount_admin_ui
from app.services.sms_check_service import SmsCheckService
from app.api.sms_checks import create_sms_checks_router


def create_app(
    settings: Settings,
    device_service: DeviceRegistrationService | None = None,
    device_auth_service: DeviceAuthenticationService | None = None,
    message_service: MessageCreationService | None = None,
    message_publisher: MessageEnqueuedPublisher | None = None,
    sse_registry: SseConnectionRegistry | None = None,
    message_pull_service: MessagePullingService | None = None,
    message_state_service: MessageStateUpdatingService | None = None,
    inbound_message_service: InboundCreatingService | None = None,
    inbound_publisher: InboundMessagePublisher | None = None,
    agent_auth_service: AgentAuthenticationService | None = None,
    agent_conversation_service: AgentConversationQueryService | None = None,
    agent_contact_service: AgentContactQueryService | None = None,
    agent_event_registry: AgentEventsRegistry | None = None,
    mms_webhook_service: MmsHandlingService | None = None,
) -> FastAPI:
    app = FastAPI(title="Virgo SMS Gateway")
    install_error_handling(app)
    database = Database(settings.database_url)
    service = (
        device_service
        if device_service is not None
        else DeviceService(database)
    )
    auth_service = (
        device_auth_service
        if device_auth_service is not None
        else DeviceAuthService(database)
    )
    registry = (
        sse_registry
        if sse_registry is not None
        else SseConnectionRegistry()
    )
    publisher = (
        message_publisher
        if message_publisher is not None
        else RegistryMessageEnqueuedPublisher(registry)
    )
    agent_registry = agent_event_registry or AgentEventRegistry()
    business_message_service = message_service or MessageCommandService(
        database,
        online_window_seconds=settings.device_online_window_seconds,
        publisher=publisher,
    )
    sms_checks = SmsCheckService(database, publisher)
    pull_service = message_pull_service or MessagePullService(database, sms_checks)
    state_service = message_state_service or MessageStateService(database, sms_checks)
    inbound_service = inbound_message_service or InboundMessageService(
        database,
        inbound_publisher or NoOpInboundMessagePublisher(),
        RegistryAgentEventPublisher(agent_registry),
        sms_checks=sms_checks,
    )
    mms_service = mms_webhook_service
    if mms_service is None:
        import boto3

        s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )
        mms_storage = S3ObjectStorage(
            client=s3_client,
            bucket=settings.s3_bucket,
            public_base_url=settings.s3_public_base_url,
        )
        mms_service = MmsWebhookService(
            database,
            mms_storage,
            inbound_publisher or NoOpInboundMessagePublisher(),
            RegistryAgentEventPublisher(agent_registry),
        )
    agent_auth = agent_auth_service or AgentAuthService(database)
    agent_conversations = agent_conversation_service or AgentConversationService(
        database,
        business_message_service,
    )
    agent_contacts = agent_contact_service or AgentContactService(database)
    app.include_router(
        create_device_router(
            settings.private_registration_token,
            service,
            auth_service,
        )
    )
    app.include_router(
        create_message_router(
            settings.business_api_token,
            business_message_service,
        )
    )
    app.include_router(create_events_router(auth_service, registry))
    app.include_router(create_message_pull_router(auth_service, pull_service))
    app.include_router(create_message_status_router(auth_service, state_service))
    app.include_router(create_inbox_router(auth_service, inbound_service))
    app.include_router(
        create_mms_webhook_router(
            mms_service,
            signing_key=settings.mms_webhook_signing_key,
            timestamp_tolerance_seconds=settings.mms_webhook_timestamp_tolerance_seconds,
        )
    )
    app.include_router(create_agent_auth_router(agent_auth))
    app.include_router(
        create_agent_conversation_router(agent_auth, agent_conversations)
    )
    app.include_router(create_agent_contact_router(agent_auth, agent_contacts))
    app.include_router(create_agent_events_router(agent_auth, agent_registry))
    app.include_router(create_sms_checks_router(settings.business_api_token, sms_checks))
    mount_admin_ui(app, database, sms_checks=sms_checks)
    return app
