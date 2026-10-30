# Slack app setup — AIOS weekly release notify

Used by `make notify-aios-tickets` (standalone; not part of `create-release-ticket`).
Posts a parent message to `#guild-engineering-only` and a thread listing AIOS tickets from `stackgen-guild` + `stackgen-sre-app`.

## 1. Create the Slack app

1. Open [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Name it something like `release-utils-aios-notify`.
3. Pick the StackGen / DevOpsNow workspace.

## 2. Bot token scopes

**OAuth & Permissions** → **Bot Token Scopes** → add:

| Scope | Why |
|-------|-----|
| `chat:write` | Post the parent message and thread replies |
| `channels:read` | Resolve `#guild-engineering-only` to a channel ID (public) |
| `groups:read` | Same if the channel is private |
| `users:read` | Resolve `@suvarna` / `@sks` to user IDs for real @-mentions |
| `channels:join` | Optional — lets the bot join public channels itself |

No user-token scopes needed.

## 3. Install to the workspace

1. **OAuth & Permissions** → **Install to Workspace** → allow.
2. Copy the **Bot User OAuth Token** (`xoxb-…`).
3. Put it in `release-utils/.env`:

```bash
SLACK_BOT_TOKEN=xoxb-your-token
# optional override (default: guild-engineering-only)
SLACK_CHANNEL=guild-engineering-only
# optional but recommended — channel ID skips conversations.list (avoids rate limits)
# Right-click channel → View channel details → copy ID at the bottom
SLACK_CHANNEL_ID=C0123456789
# optional — comma-separated Slack handles to @-mention (default: suvarna,sks)
SLACK_MENTION_USERS=suvarna,sks
```

## 4. Invite the bot to the channel (required)

`#guild-engineering-only` is almost certainly **private**. Bots cannot see or post there until invited.

In that channel, run:

```text
/invite @release-utils-aios-notify
```

(Use whatever display name you gave the app.)

Then copy the **Channel ID** (channel details → bottom) into `.env` as `SLACK_CHANNEL_ID=C…`. That avoids `conversations.list` rate limits and `channel_not_found` from posting by name.

## 5. Smoke test

```bash
# Preview only (no Slack call)
make notify-aios-tickets FROM_REF=v2026.9.14 TO_REF=v2026.10.1 DRY_RUN=1

# Post for real (needs SLACK_BOT_TOKEN + bot invited)
make notify-aios-tickets FROM_REF=v2026.9.14 TO_REF=v2026.10.1
```

Optional: export `LINEAR_API_KEY` so missing assignees in older `final_tag_differences.json` files get filled from Linear.

## Troubleshooting

| Error | Fix |
|-------|-----|
| `not_in_channel` | `/invite` the bot into `#guild-engineering-only` |
| `channel_not_found` / resolve failure | Invite the bot; or set `SLACK_CHANNEL_ID=C…` in `.env` |
| `ratelimited` on `conversations.list` | Set `SLACK_CHANNEL_ID`; script also retries with Retry-After backoff |
| `invalid_auth` | Token revoked or wrong workspace — reinstall and update `.env` |
| `missing_scope` | Add the scope, reinstall the app, update the token |
