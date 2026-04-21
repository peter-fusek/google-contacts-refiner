# Beeper Desktop API — Endpoint Reference

**Captured 0.2.0 / OpenAPI 3.1.0** — 2026-04-21

Base URL: `http://localhost:23373` (localhost only; `remote_access:false` by default).

Live OpenAPI spec: `http://localhost:23373/v1/spec` (JSON). Web UI at `http://localhost:23373/v1/spec/ui` if available.

## Security

Two auth paths, both yield a bearer token:

- **Manual token**: Beeper → Settings → Developers → Approved connections → Create token. Paste into `Authorization: Bearer <token>`.
- **OAuth2 Authorization Code + PKCE** with dynamic client registration:

  - `POST /oauth/register` (RFC 7591) — returns `client_id`
  - `/oauth/authorize` + `/oauth/token` — standard PKCE flow, scopes `read` / `write`
  - `/oauth/introspect`, `/oauth/revoke`, `/oauth/userinfo`

## Endpoints (23 ops across 19 paths)

| Method | Path | Summary |
|--|--|--|
| GET | `/v1/accounts` | List connected accounts |
| GET | `/v1/accounts/{accountID}/contacts` | Search contacts |
| GET | `/v1/accounts/{accountID}/contacts/list` | List contacts |
| POST | `/v1/assets/download` | Download an asset |
| GET | `/v1/assets/serve` | Serve an asset |
| POST | `/v1/assets/upload` | Upload an asset |
| POST | `/v1/assets/upload/base64` | Upload an asset (base64) |
| GET | `/v1/chats` | List chats |
| POST | `/v1/chats` | Create or start a chat |
| GET | `/v1/chats/search` | Search chats |
| GET | `/v1/chats/{chatID}` | Retrieve chat details |
| POST | `/v1/chats/{chatID}/archive` | Archive or unarchive a chat |
| GET | `/v1/chats/{chatID}/messages` | List messages |
| POST | `/v1/chats/{chatID}/messages` | Send a message |
| PUT | `/v1/chats/{chatID}/messages/{messageID}` | Edit a message |
| POST | `/v1/chats/{chatID}/messages/{messageID}/reactions` | Add a reaction |
| DELETE | `/v1/chats/{chatID}/messages/{messageID}/reactions` | Remove a reaction |
| POST | `/v1/chats/{chatID}/reminders` | Create a chat reminder |
| DELETE | `/v1/chats/{chatID}/reminders` | Delete a chat reminder |
| POST | `/v1/focus` | Focus Beeper Desktop app |
| GET | `/v1/info` | Get Connect server info |
| GET | `/v1/messages/search` | Search messages |
| GET | `/v1/search` | Search |

## Useful extras

- Live events: WebSocket `ws://localhost:23373/v1/ws` (per-chat subscriptions, message edits/reactions/reads).
- MCP endpoint: `http://localhost:23373/v0/mcp` — Beeper exposes itself as an MCP server; Beeper Settings → Developers has one-click installs for Claude Desktop, Claude Code, Cursor, VS Code, Raycast.
- Asset handling: `/v1/assets/serve` for read, `/v1/assets/upload` (multipart) or `/v1/assets/upload/base64` for write, `/v1/assets/download` to fetch remote-hosted.
- Search: `/v1/search` (unified), `/v1/chats/search`, `/v1/messages/search` — spare us writing an index.

## Harvester-relevant flow

For Session 2 (Python `harvester/beeper_client.py`):

1. `GET /v1/info` (no auth) — health + version guard (minimum 4.1.169; tested against 4.2.742).
2. `GET /v1/accounts` — enumerate connected networks (WA, Signal, Messenger, iMessage, LinkedIn, Telegram, Instagram, X, Discord, …).
3. `GET /v1/accounts/{id}/contacts/list` — paginated, merged contacts list per network; primary match feedstock.
4. `GET /v1/chats` — paginated list of all chats (cross-network).
5. `GET /v1/chats/{chatID}/messages` — paginated, cursor-based. Since-filter via `beforeCursor`/`afterCursor` params (confirm in spec once wired).
6. Normalize per record into the shape in `interaction.md`; write `data/interactions/YYYY-MM.jsonl` on GCS.

## Gotchas
- API is **off by default**. User enables it via Settings → Developers → Beeper Desktop API toggle. Setting persists across launches when `Start on launch` is on.
- `remote_access:false` — pipeline must run on the Mac where Beeper is installed, not Cloud Run. Schedule via launchd.
- Spec version `0.2.0` (beta). Expect breaking changes; pin and regenerate this reference before each session.
- No documented rate limit, but per research: 'don't spam or networks will suspend you'. Keep to 1 request/sec during harvest.
