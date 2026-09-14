# Agent Contact And Menu API Design

## Goal

Add agent-facing APIs for contacts and reply menus so a customer-service agent can list contacts in their own area, update a contact remark, and fetch local canned reply text while chatting.

## Decisions

- `contacts.areas` is the source used by the new contact APIs.
- Inbound SMS handling updates `contacts.areas` from the matched receiving SIM card area.
- If the same phone number later appears through a different SIM area, the existing contact is updated to the latest inbound area.
- Agent APIs compare non-empty, trimmed `accounts.areas` and `contacts.areas`.
- Reply menus come from `products.menu` rows whose `products.areas` matches the authenticated agent area.

## API

- `GET /agent/v1/contacts`
  - Requires `Authorization: Bearer <agent-token>`.
  - Returns contacts where `contacts.areas` matches the agent area.
  - Sorts by `last_contact_at DESC NULLS LAST`, then `updated_at DESC`, then `id`.

- `PATCH /agent/v1/contacts/{contactId}/remark`
  - Requires `Authorization: Bearer <agent-token>`.
  - Body: `{"remark": "text"}`.
  - Empty or whitespace-only remark clears the stored remark.
  - Returns `{"ok": true}`.
  - Returns `403` when the contact exists but belongs to another area, and `404` when it does not exist.

- `GET /agent/v1/menus`
  - Requires `Authorization: Bearer <agent-token>`.
  - Returns local product menu rows whose `areas` matches the agent area and whose `menu` is non-empty.
  - Sorts by `update_time DESC`, then `id`.

## Tests

- Add API tests for contact list filtering, cross-area remark rejection, remark update and clearing, and menu filtering.
- Add integration coverage that inbound messages set and later update `contacts.areas`.
