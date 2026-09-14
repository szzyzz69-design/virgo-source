# Agent SIM Card List API Design

## Goal

Add an agent-facing API that returns the phone numbers assigned to the currently authenticated customer-service account, including each SIM card's carrier and area.

## Decisions

- The API path is `GET /agent/v1/sim-cards`.
- Authentication reuses the existing agent bearer token flow.
- Ownership is determined by `account_sim_cards.account_id = authenticated_agent.id`.
- Response data comes from `sim_cards.phone_number`, `sim_cards.carrier_name`, and `sim_cards.areas`.
- `phoneNumber`, `carrierName`, and `areas` are nullable because Android may not always expose a phone number and admin data may be incomplete.
- Results are sorted by `sim_cards.phone_number ASC NULLS LAST`, then `device_id`, `sim_number`, and `id` for stable output.

## API

`GET /agent/v1/sim-cards`

Headers:

```http
Authorization: Bearer <agent-token>
```

Response:

```json
[
  {
    "id": "sim_abc",
    "phoneNumber": "+8613800000000",
    "carrierName": "China Mobile",
    "areas": "north"
  }
]
```

If the account has no bound SIM cards, the response is `[]`.

## Implementation

- Add an `AgentSimCardItem` schema.
- Add `list_sim_cards(agent)` to the agent contact service boundary, or an equivalent small agent-facing query service that follows the existing router pattern.
- Add `GET /agent/v1/sim-cards` to the existing `/agent/v1` agent contact router.
- Wire no new database schema changes; the existing `account_sim_cards` and `sim_cards` tables are sufficient.

## Tests

- An authenticated agent sees only SIM cards bound to its account.
- The response includes `id`, `phoneNumber`, `carrierName`, and `areas`.
- Unbound SIM cards are not returned even if they share the same area.
- An account with no bound SIM cards receives an empty list.
