# MMS Webhook Support Design

## Goal

Add server-side MMS receiving support for Android SMS Gateway while keeping inbound MMS visible in the existing conversation, agent message, SSE, and PostgreSQL-backed message flows.

## Confirmed Approach

MMS will be represented as an inbound `messages` row with `message_type = 'MMS'`. Image and other MMS parts will be stored in S3-compatible object storage, while PostgreSQL stores the conversation linkage, message metadata, and attachment metadata.

This keeps current conversation behavior intact: contacts, conversations, unread counts, last-message previews, message history, and agent-facing conversation APIs continue to use the same core tables. The main extension is an attachment surface on top of messages.

## API Surface

Add:

```http
POST /api/v1/webhooks/android-sms-gateway/mms
Content-Type: application/json
X-Timestamp: <unix_seconds>
X-Signature: <hex_hmac_sha256>
```

The endpoint accepts:

- `mms:received`
- `mms:downloaded`

The response returns `200` after successful durable handling:

```json
{
  "ok": true,
  "messageId": "12345",
  "created": true
}
```

Duplicate deliveries with identical content return `created: false`. Duplicate deliveries with conflicting key fields return `409 IDEMPOTENCY_CONFLICT`.

## Authentication

MMS webhooks use HMAC SHA-256:

```text
signature = HMAC_SHA256(signing_key, raw_body + timestamp)
```

The server reads `X-Timestamp` and `X-Signature`. Requests outside the configured replay window are rejected with `401 UNAUTHORIZED`. If no signing key is configured, local or transitional deployments may accept unsigned requests.

## Configuration

Add S3-compatible object storage settings through `config.toml` and environment overrides where appropriate:

- `mms_webhook_signing_key`
- `mms_webhook_timestamp_tolerance_seconds`, default `300`
- `s3_endpoint_url`
- `s3_region`
- `s3_bucket`
- `s3_access_key_id`
- `s3_secret_access_key`
- `s3_public_base_url`

Add runtime dependency:

```text
boto3
```

## Database Model

Extend `messages`:

- allow `message_type = 'MMS'`
- allow MMS rows to have nullable `text_content`
- store MMS request digest and webhook metadata in `messages.metadata`

Add `message_attachments`:

- `id`
- `message_id`
- `part_id`
- `content_type`
- `name`
- `size`
- `s3_bucket`
- `s3_key`
- `url`
- `etag`
- `metadata`
- `created_at`
- `updated_at`

Attachment idempotency uses a unique key on `(message_id, part_id)`.

## Data Flow

For `mms:received`:

1. Validate webhook envelope and payload.
2. Verify signature when configured.
3. Lock by `(deviceId, payload.messageId)`.
4. Resolve receiving SIM using `simNumber` first, then `recipient`.
5. Create or reuse contact and conversation using the same routing rules as inbound SMS.
6. Insert an inbound `MMS` message if it does not exist.
7. Set state to `Received`, store notification metadata, and update conversation preview.
8. Publish existing inbound conversation events.

For `mms:downloaded`:

1. Validate envelope, payload, attachment MIME types, Base64, and size limits.
2. Verify signature when configured.
3. Lock by `(deviceId, payload.messageId)`.
4. Create or reuse the same inbound `MMS` message.
5. Upload each attachment with `data` to S3.
6. Upsert attachment metadata by `(message_id, part_id)`.
7. Update text body, message metadata, and conversation preview.
8. Publish existing inbound conversation events.

If `mms:downloaded` arrives before `mms:received`, the message is created with downloaded content. A later `mms:received` must not remove text, attachments, or downloaded metadata.

## S3 Object Keys

Use deterministic, scoped keys:

```text
mms/{deviceId}/{messageId}/{partId}-{safeName}
```

When `name` is missing, generate:

```text
mms-{messageId}-part-{partId}.{extension}
```

Only `image/jpeg` and `image/png` are treated as displayable images initially. Other supported MIME types may be stored as ordinary attachments.

## Agent Conversation Compatibility

Extend agent message responses with:

```json
"attachments": [
  {
    "id": "att_...",
    "partId": 17,
    "contentType": "image/jpeg",
    "name": "photo.jpg",
    "size": 125684,
    "url": "https://..."
  }
]
```

Existing SMS and DATA_SMS responses return an empty attachment list. Existing response fields remain unchanged.

Conversation preview rules:

- MMS with body: first 255 chars of body.
- MMS without body and with image attachment: `[MMS image]`.
- MMS without body and without attachment: `[MMS]`.

## Error Handling

Return the existing API error envelope:

```json
{
  "code": "VALIDATION_ERROR",
  "message": "payload.messageId is required",
  "requestId": "req_..."
}
```

Map errors as:

- `400 VALIDATION_ERROR` for malformed JSON, invalid payload fields, invalid Base64.
- `401 UNAUTHORIZED` for missing, expired, or invalid signatures when signing is configured.
- `403 DEVICE_FORBIDDEN` when the device cannot be used.
- `409 IDEMPOTENCY_CONFLICT` when the same MMS key is reused with conflicting content.
- `413 PAYLOAD_TOO_LARGE` when a single attachment or total attachments exceed limits.
- `415 UNSUPPORTED_MEDIA_TYPE` when attachment MIME type is not allowed.
- `500 INTERNAL_ERROR` for unexpected persistence or object storage failures.

## Testing Strategy

Use TDD for implementation.

Unit tests:

- MMS schema accepts valid `mms:received`.
- MMS schema accepts valid `mms:downloaded`.
- MMS schema rejects invalid event, missing required fields, invalid timestamps, invalid Base64, and unsupported MIME types.
- Signature verifier accepts valid signatures and rejects invalid or expired signatures.
- S3 storage client receives decoded bytes with expected bucket, key, and content type.

API tests:

- Webhook returns `200` and `created: true` for first `mms:received`.
- Replayed identical webhook returns `200` and `created: false`.
- Conflicting replay returns `409`.
- Invalid signature returns `401`.
- Oversized attachment returns `413`.

Integration tests:

- `mms:downloaded` creates a conversation and inbound MMS message.
- Attachments are persisted in `message_attachments` with S3 metadata.
- Agent conversation message list includes MMS attachments.
- `mms:downloaded` before `mms:received` does not regress the message.

## Non-Goals

- Outbound MMS sending.
- Image thumbnail generation.
- Virus scanning.
- Public unauthenticated attachment download endpoints.
- Changing existing SMS and DATA_SMS request contracts.
