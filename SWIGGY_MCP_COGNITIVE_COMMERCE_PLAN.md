# Cognitive Commerce: Swiggy MCP Integration

## Summary

Swiggy is added as the first **action connector** on top of the existing cognitive
layer (mood, EI context, conversation history, and the location signal already
built for nearby-places). The flow is:

```
Understand (mood/context) -> Infer (latent need) -> Recommend -> Ask -> Act
```

Swiggy is a thin adapter at the "Act" end, not a new intelligence subsystem. The
need-inference step reuses `get_user_cognitive_context`/EI signals that already
exist (`app.py`'s `chat_global`, `emotional_intelligence/ei_adapter.py`) — no new
mood-modeling code is being built for this feature.

**Hard rule carried through the whole design:** the system never places a real
order without an explicit, separate user confirmation step. Recommendation and
execution are two different endpoints; nothing can jump straight from inference
to a placed order.

## What's real vs. what needs re-verification

Confirmed via Swiggy's own docs (`https://mcp.swiggy.com/builders/docs/`) as of
2026-08-30:

- **Three independent MCP servers**: Food (`mcp.swiggy.com/food`, ~18 tools),
  Instamart (`mcp.swiggy.com/im`, ~19 tools), Dineout (`mcp.swiggy.com/dineout`,
  ~12 tools). They do not share carts, orders, or sessions — wiring one does not
  give you the others.
- **Auth is OAuth 2.1 + PKCE, per end user**, not a static API key. A real user
  must complete a phone+OTP login through Swiggy's own auth screen; the app never
  sees or stores a Swiggy password. Session-based tool calls — the user's token is
  attached server-side, never passed as a tool argument.
- **India-only.** Swiggy serves Indian consumers exclusively — this feature is a
  no-op (hide the entry point) for any user account without an Indian delivery
  context.
- **Production access is invite-based.** A real order cannot be placed against
  the production servers until Swiggy approves an application and an SLA/rate-limit
  agreement is signed. Development against `http://localhost` is free and unrestricted.
- **Dynamic Client Registration** — no pre-issued client ID; the app registers
  itself with an OAuth redirect URI at setup time.
- Confirmed tool names from the docs: `get_addresses`, `search_restaurants`
  (requires an `addressId` from `get_addresses` first). A full "order food
  end-to-end" flow is documented as chaining **7 tools**; the complete list of
  all ~49 tool names/schemas is **not fully enumerated in the public docs** — the
  two overview pages disagree on the total count (35 vs 49). **Before writing
  `swiggy_mcp_client.py`, pull the live tool list via MCP's own `tools/list` call
  against the dev server** rather than hardcoding names from this doc.
- Payment: one documented recipe explicitly covers Cash-on-Delivery. Online
  payment gateway behavior through the MCP tool surface is not documented —
  treat COD as the only confirmed path for v1 and verify card/UPI checkout
  live before promising it.

**Blocking prerequisite found while scoping this:** prod (129.213.21.239) is
served over a bare IP with nginx `server_name _` — no registered domain, no TLS
cert bound to a hostname. OAuth 2.1 redirect URIs for a production Dynamic Client
Registration will very likely need a real HTTPS domain. **This needs to be
resolved (point a domain at the Oracle Cloud box + issue a cert, e.g. via
Let's Encrypt/certbot) before the production OAuth linking flow can work at all.**
None of this blocks local development, which runs against `http://localhost`.

## Architecture

```
                    User message (chat, any client)
                               |
                               v
              existing chat_global() context assembly
        (ei_context, personal_notes, direct_messages, nearby_context)
                               |
                               v
                 NEW: _detect_food_order_intent(prompt)
                               |
                    match --> build need-inference context
                     (mood/EI + time-of-day + existing lat/lon
                      signal + past order category, if linked)
                               |
                               v
                    swiggy_mcp_client.search(...)
                     (search_restaurants / instamart search,
                      via the user's linked OAuth token)
                               |
                               v
              LLM composes reply + structured action_card
             ("3 options, here's why, want me to order?")
                               |
                               v
             Response includes reply (text, unchanged shape)
             AND optional action_card (new, additive field)
                               |
                               v
                    Frontend renders action_card buttons:
                 [Order this]  [Show more]  [Not hungry]
                               |
                          user taps "Order this"
                               |
                               v
              NEW: POST /commerce/swiggy/confirm
        (re-fetches the item server-side, never trusts client
         price/id blindly beyond what was just shown; requires
         a linked, non-expired Swiggy OAuth token)
                               |
                               v
                swiggy_mcp_client.place_order(...)
                               |
                               v
                 commerce_actions row written (audit trail)
                               |
                               v
                    Order confirmation shown to user
```

## Data model (additions to `schema.sql`)

Purely additive — no existing table is touched.

```sql
-- One row per user who has linked their real Swiggy account. Tokens are
-- encrypted at rest (see backend section) -- this table never stores a
-- Swiggy password, only OAuth 2.1 tokens obtained via PKCE.
CREATE TABLE IF NOT EXISTS swiggy_accounts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMPTZ,
    default_address_id TEXT,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

-- Audit trail for every suggestion shown and every action actually taken --
-- this is the paper trail that proves the "never auto-order" rule held, and
-- is what a user-facing "recent Swiggy activity" list would read from later.
CREATE TABLE IF NOT EXISTS commerce_actions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'swiggy',
    server TEXT NOT NULL, -- 'food' | 'instamart' | 'dineout'
    action TEXT NOT NULL, -- 'suggested' | 'confirmed' | 'order_placed' | 'order_failed' | 'dismissed'
    inferred_need TEXT,   -- e.g. "comfort + low-effort meal", for later review/debugging
    item_summary TEXT,    -- human-readable snapshot of what was suggested/ordered
    external_order_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_commerce_actions_user ON commerce_actions (user_id, created_at DESC);
```

## Backend (`app.py` + new module)

### New file: `commerce/swiggy_mcp_client.py`

Thin MCP client, mirroring how `costlens_agent` is vendored as an isolated,
additive module rather than woven into `app.py` directly.

- `get_authorize_url(user_id) -> str` — builds the PKCE authorization URL,
  stores the PKCE `code_verifier` + `state` server-side (short-lived, keyed by
  `state`) for the callback to retrieve.
- `exchange_code_for_token(code, state) -> dict` — completes PKCE, returns
  access/refresh tokens.
- `call_tool(server: str, tool: str, params: dict, user_id: int) -> dict` —
  looks up the user's decrypted token from `swiggy_accounts`, makes the MCP
  `tools/call` request against `mcp.swiggy.com/{server}`, refreshes the token
  first if `token_expires_at` has passed. Raises a typed
  `SwiggyNotConnectedError` if the user has no row / a revoked row, which
  callers turn into "ask the user to connect their account" rather than a 500.
- Token encryption: reuse whatever at-rest secret-encryption primitive already
  exists in this codebase (check `flutter_secure_storage`-equivalent on the
  backend side / any existing `cryptography.fernet` usage before adding a new
  dependency) so this doesn't introduce a second, inconsistent encryption
  scheme.

### New intent detection (`app.py`, alongside `_NEARBY_INTENT_RE`)

```python
_FOOD_ORDER_INTENT_RE = re.compile(
    r"\border\s+(?:food|something|dinner|lunch|breakfast)\b|"
    r"\b(?:i'?m|im)\s+hungry\b|\bfeel like eating\b|"
    r"\bswiggy\b|\binstamart\b|\bdineout\b|\border\s+in\b",
    re.IGNORECASE,
)
```

Mirrors `_detect_nearby_category`'s shape: cheap regex gate so this costs
nothing on every unrelated message, exactly like the nearby-places feature.

### Need inference (new, small — not a new ML system)

A plain prompt-construction function, reusing what's already computed in
`chat_global()` for this same request rather than re-deriving it:

```python
def _build_food_need_context(ei_context: str, hour: int, lat, lon) -> str:
    # ei_context already carries mood/recent-statement signal (existing).
    # hour comes from datetime.now() -- no new dependency.
    # lat/lon reuse the exact same optional fields the nearby-places
    # feature already threads through from both Flutter and React clients.
    ...
```

This composes into the LLM prompt as one more context block (same
`_prepend()` pattern already used for `nearby_context`/`personal_notes_context`)
— it does **not** call Swiggy itself; it just gives the LLM enough to decide
*whether* to suggest food and *what kind*, before any external call is made.

### Wiring into `chat_global()` — additive only

Same shape as the nearby-places integration that's already live:

```python
commerce_context = ""
action_card = None
if _FOOD_ORDER_INTENT_RE.search(prompt_lower):
    try:
        commerce_context, action_card = _handle_food_order_intent(
            user_id, prompt, ei_context, lat, lon, cur
        )
    except Exception:
        commerce_context = ""  # optional and additive, same guarantee as ei_context
if commerce_context:
    transcripts_section = _prepend(transcripts_section, commerce_context)
```

`action_card` (a small dict: `{items: [...], server: "food", need: "..."}` or
`None`) rides alongside the existing `reply` in the JSON response:

```python
return jsonify({"reply": reply, "matched_speaker": ..., "conversations_used": ...,
                 "action_card": action_card})
```

**This is the one existing-contract change**, and it's purely additive — every
current consumer of `/chat/global` (React, Flutter) already ignores unknown
response fields, so nothing breaks for a client that hasn't been updated yet.

### New routes (all new files/functions, nothing existing touched)

- `GET /integrations/swiggy/connect` — `@login_required`, redirects to
  `get_authorize_url(user_id)`.
- `GET /integrations/swiggy/callback` — PKCE callback, writes/updates the
  `swiggy_accounts` row, redirects back into the app.
- `GET /integrations/swiggy/status` — `{"connected": bool, "connected_at": ...}`
  for the Settings screen.
- `POST /integrations/swiggy/disconnect` — sets `revoked_at`, does **not**
  delete the row (keeps the audit trail intact).
- `POST /commerce/swiggy/confirm` — body: `{action_card_id or item refs}`.
  Re-validates the user has a live, non-revoked `swiggy_accounts` row; re-runs
  a fresh lookup of the specific item (never trusts a stale price/availability
  the client is echoing back from a few messages ago); calls
  `swiggy_mcp_client.call_tool(..., "place_order", ...)`; writes a
  `commerce_actions` row either way (`order_placed` or `order_failed`); returns
  the real result to show the user.

## Frontend — React (`frontend/src`)

- **`icons.jsx`**: one new icon (Swiggy/food-order glyph), same convention as
  existing icons.
- **New `ActionCard.jsx`**: renders `action_card` — a short "why" line, up to 3
  item options, and `[Order this]` / `[Show more]` / `[Not hungry]` buttons.
  Purely presentational; the parent owns what happens on each button.
- **`MessageBubble.jsx`**: accept an optional `actionCard` prop (alongside the
  existing `imageUrl` prop added for image chat), render `<ActionCard>` when
  present. Existing messages without it are completely unaffected.
- **`ChatThread.jsx`**: pass `m.actionCard` through to `MessageBubble`, same
  one-line addition pattern as `imageUrl` at `ChatThread.jsx:63`.
- **`ChatsSection.jsx`**: `sendGlobalMessage` already receives the full
  response `data` — add `actionCard: data.action_card || null` onto the
  appended assistant message object. New `confirmSwiggyOrder(actionCard)`
  handler posts to `/commerce/swiggy/confirm` and appends a follow-up system
  message with the result. No changes to the existing send path for messages
  that don't produce an action card.
- **New `SettingsSwiggyCard.jsx`** (or a section within the existing settings
  page): shows connect/disconnect state via `GET /integrations/swiggy/status`,
  a "Connect Swiggy Account" button that navigates the browser to
  `/integrations/swiggy/connect` (full OAuth redirect, not a fetch call).

## Frontend — Flutter (`throughline`)

Mirrors React exactly, using this app's existing equivalents:

- **`lib/models/chat_message.dart`**: add an optional `actionCard` field.
  Decision needed (see Open Questions): unlike `localImage` (deliberately
  session-only/never serialized), an `action_card` **should** round-trip
  through `toJson()`/`fromJson()` so a suggestion is still tappable after an
  app restart — but a *confirmed/expired* card must render as inert once its
  window has passed (add a client-side `expiresAt` or rely on the backend
  rejecting a stale confirm with a clear error either way).
- **`lib/widgets/action_card.dart`** (new): same three-button layout as
  React's `ActionCard.jsx`.
- **`lib/widgets/message_bubble.dart`**: render `ActionCard` when
  `message.actionCard != null`, same slot pattern already used for
  `message.localImage` at `message_bubble.dart:42-48`.
- **`lib/services/conversation_service.dart`**: `sendGlobalChat` already
  returns the parsed response — surface `action_card` alongside `reply`. New
  `confirmSwiggyOrder(actionCard)` calling `POST /commerce/swiggy/confirm`,
  same multipart-free JSON-post pattern as the rest of this file.
- **`lib/screens/settings/settings_screen.dart`**: new list item ("Connect
  Swiggy Account" / "Connected ✓ Disconnect"), following the existing
  card-based layout already used for the nudges/cognitive-intelligence toggles
  at the top of this file — this is a link-out action, not a `SwitchListTile`
  toggle, since it drives a real OAuth browser flow
  (`url_launcher` — check whether it's already a dependency before adding it).

## Non-breaking guarantees

- `chat_global()`'s existing behavior (retrieval, EI context, nearby-places,
  direct messages, personal notes) is untouched; the only change to that
  function's *output* is one new optional JSON key (`action_card`, `None` by
  default) that existing clients silently ignore.
- All new DB tables are additive; no existing table gains or loses a column
  except nothing here touches existing tables at all.
- The food-intent regex is checked, cheaply, only after the existing
  nearby-places/EI/DM context assembly — a message that doesn't mention food
  never triggers a Swiggy call, exactly like nearby-places today.
- New routes are new files/functions under a new `commerce/` package plus a
  handful of new `@app.route` blocks — no existing route handler is modified.
- React/Flutter changes are additive props and one new optional field read
  off an existing response object — every other message-send path (per-
  conversation chat, image chat, live transcription) is unaffected.

## Safety guarantees (non-negotiable, per the user's own design)

1. **No tool that spends money is ever called except from `/commerce/swiggy/confirm`**,
   which only runs after an explicit button tap — never from the
   recommendation path, never automatically from a background job.
2. Every suggestion shown and every confirm attempt is written to
   `commerce_actions` — a full audit trail, including failures.
3. The Swiggy password is never seen or stored — only OAuth tokens, encrypted
   at rest, revocable via `/integrations/swiggy/disconnect` at any time.
4. A user who has never connected Swiggy sees the feature degrade to
   "would you like me to help you order food? Connect your Swiggy account in
   Settings first" — never a silent failure, never a fake/simulated order.
5. `place_order` re-validates the item server-side at confirm time rather than
   trusting anything echoed back from the client — prevents a stale price or
   an unavailable item from silently going through.

## Rollout phases

- **V1 (this plan)**: Food server only, COD only, recommend-confirm-order
  flow, both clients. Instamart/Dineout are structurally supported by the same
  `swiggy_mcp_client.call_tool(server=...)` abstraction but not wired into any
  UI yet — a deliberate "thin action layer" scope cut, matching the user's own
  "don't make Swiggy a giant subsystem" principle.
- **V2 (later, not in this plan)**: Instamart ("order in 5 min") and Dineout
  (reservation suggestions) reuse the same action-card/confirm plumbing with a
  different `server` value and tool set — no new architecture needed, just
  more `_detect_*_intent` functions and prompt variants.

## Open questions requiring a decision before implementation starts

1. **Domain + TLS for prod** — apply for a domain and issue a cert for
   129.213.21.239 before the OAuth redirect URI can be registered for
   production. Development can proceed against `http://localhost` in the
   meantime without this.
2. **Apply for Swiggy production access now** (invite-based, per their docs) —
   this has unknown lead time and should be kicked off in parallel with
   development, not after.
3. **Exact tool schemas** — pull the live `tools/list` output from the dev
   MCP server before writing `swiggy_mcp_client.py`; do not hardcode tool
   names/params from this doc's partial listing.
4. **Payment method** — confirm whether online payment (not just COD) is
   actually reachable through the MCP tool surface, or whether v1 should be
   explicitly COD-only in the UI copy.
5. **Persisted vs. session-only action cards on Flutter** — see the note under
   the Flutter section; needs an explicit call on whether a suggestion should
   survive an app restart.

## Verification plan

1. Local dev: complete the OAuth PKCE flow end-to-end against
   `http://localhost` per Swiggy's dev docs, confirm a `swiggy_accounts` row
   is written with a real (encrypted) token.
2. Send a chat message matching `_FOOD_ORDER_INTENT_RE`; confirm
   `action_card` is present in the response and `commerce_actions` gets a
   `suggested` row.
3. Tap "Order this"; confirm `/commerce/swiggy/confirm` re-validates the item,
   places a real order against the dev environment, and writes an
   `order_placed` row with a real `external_order_id`.
4. Tap "Not hungry"; confirm no Swiggy call is made and a `dismissed` row is
   written (proves the negative path never silently calls the paid tool).
5. Disconnect the account mid-session; confirm a subsequent food-intent
   message degrades to "connect your account" rather than erroring.
6. Regression pass: re-run the existing nearby-places manual test (shop/
   restaurant/trek queries) and a normal non-food chat message on both
   clients — confirm zero behavior change from before this feature existed.
7. React `npm run build` and Flutter `flutter analyze` both clean, matching
   the bar set by the location-aware feature's own rollout.
