#!/usr/bin/env python3
"""Post AIOS tickets from a tag-diff artifact to Slack as a parent + thread.

Reads AIOS-* tickets that appear under stackgen-guild and/or stackgen-sre-app in
final_tag_differences.json, then posts:

  Parent: weekly-release confirmation ask (TO_REF in the text)
  Thread: ticket id, title, status, assignee (Linear links)

Requires SLACK_BOT_TOKEN. Optional LINEAR_API_KEY to fill missing assignees.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests

SLACK_API = "https://slack.com/api"
LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_ISSUE_BASE = "https://linear.app/stackgen/issue"

# Services that count as Aiden2 / AIOS weekly-release scope.
AIDEN2_SERVICES = frozenset({"stackgen-guild", "stackgen-sre-app"})

# Noise / unresolved placeholders sometimes present in tag-diff JSON.
NOISE_TICKET_IDS = frozenset({"AIOS-000", "UTF-8", "UTF-16", "DEP-02", "INT-03"})

# Linear workflow states omitted from the Slack notify list.
SKIPPED_TICKET_STATES = frozenset({"released", "ready for qa"})

DEFAULT_CHANNEL = "guild-engineering-only"
# Slack handles to @-mention on the parent post (resolved to user IDs at send time).
DEFAULT_MENTION_HANDLES = ("suvarna", "sks")
# Known handle → user ID for this workspace (skips users.list / rate limits).
# Override with SLACK_MENTION_USERS=U…,U… or refresh if people leave/rejoin Slack.
DEFAULT_MENTION_USER_IDS = {
    "suvarna": "U06133VH78E",  # Suvarna Rokade
    "sks": "U05JQH3CXBL",  # Sabith
}


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs from .env into os.environ when not already set."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _normalize_ticket_id(raw: Any) -> str:
    return str(raw or "").split(":", 1)[0].strip()


def collect_aios_tickets(data: Dict[str, Any]) -> List[str]:
    """Return sorted unique AIOS-* ids from guild + sre-app services."""
    found: Set[str] = set()
    for svc in data.get("services") or []:
        name = str(svc.get("service") or "").strip().lower()
        if name not in AIDEN2_SERVICES:
            continue
        for raw in svc.get("tickets") or []:
            tid = _normalize_ticket_id(raw)
            if not tid.startswith("AIOS-"):
                continue
            if tid in NOISE_TICKET_IDS:
                continue
            found.add(tid)
    return sorted(found, key=lambda t: (t.split("-")[0], int(re.sub(r"\D", "", t) or 0), t))


def fetch_linear_assignee(api_key: str, ticket_id: str) -> Optional[str]:
    query = """
    query IssueByIdentifier($identifier: String!) {
      issue(id: $identifier) {
        assignee { name }
        state { name }
        title
        url
      }
    }
    """
    try:
        resp = requests.post(
            LINEAR_API_URL,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"query": query, "variables": {"identifier": ticket_id}},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        issue = (resp.json().get("data") or {}).get("issue") or {}
        assignee = (issue.get("assignee") or {}).get("name")
        return assignee or "Unassigned"
    except Exception:
        return None


def enrich_tickets(
    ticket_ids: Sequence[str],
    ticket_details: Dict[str, Any],
    linear_api_key: Optional[str],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    skipped_by_state = 0
    for tid in ticket_ids:
        details = ticket_details.get(tid) or {}
        title = str(details.get("title") or "").strip() or "(no title)"
        state = str(details.get("state") or "Unknown").strip()
        if state.lower() in SKIPPED_TICKET_STATES:
            skipped_by_state += 1
            continue
        assignee = str(details.get("assignee") or "").strip()
        url = str(details.get("issueUrl") or f"{LINEAR_ISSUE_BASE}/{tid}").strip()

        if not assignee or assignee.lower() in {"", "none"}:
            if linear_api_key:
                fetched = fetch_linear_assignee(linear_api_key, tid)
                assignee = fetched or "Unassigned"
                time.sleep(0.05)
            else:
                assignee = "Unassigned"

        rows.append(
            {
                "id": tid,
                "title": title,
                "state": state,
                "assignee": assignee,
                "url": url,
            }
        )
    if skipped_by_state:
        print(
            f"  skipped {skipped_by_state} ticket(s) with status "
            f"Released or Ready for QA"
        )
    return rows


def parent_text(to_ref: str, mention_tokens: Optional[Sequence[str]] = None) -> str:
    """Build the channel parent message. mention_tokens are Slack <@U…> or @handle fallbacks."""
    body = (
        f"Please find below the AIOS tickets proposed for weekly release {to_ref}. "
        "Kindly review each item and confirm which are complete and should be "
        "included in this release."
    )
    tokens = [t for t in (mention_tokens or []) if t]
    if not tokens:
        return body
    return f"{' '.join(tokens)} — {body}"


def _user_match_score(handle: str, user: Dict[str, Any]) -> int:
    """Higher score = better match for a Slack handle / display name."""
    h = handle.lstrip("@").strip().lower()
    if not h or user.get("deleted") or user.get("is_bot"):
        return 0
    profile = user.get("profile") or {}
    candidates = [
        str(user.get("name") or ""),
        str(user.get("real_name") or ""),
        str(profile.get("display_name") or ""),
        str(profile.get("display_name_normalized") or ""),
        str(profile.get("real_name") or ""),
        str(profile.get("real_name_normalized") or ""),
    ]
    # Also match email local-part (e.g. suvarna@… / sks@…)
    email = str(profile.get("email") or "")
    if "@" in email:
        candidates.append(email.split("@", 1)[0])

    best = 0
    for raw in candidates:
        c = raw.strip().lower()
        if not c:
            continue
        if c == h:
            best = max(best, 100)
        elif c.replace(".", "").replace("_", "").replace("-", "") == h.replace(
            ".", ""
        ).replace("_", "").replace("-", ""):
            best = max(best, 90)
        elif c.startswith(h) or h.startswith(c):
            best = max(best, 70)
        elif h in c.split() or any(part.startswith(h) for part in c.replace(".", " ").split()):
            best = max(best, 50)
    return best


def resolve_mention_tokens(
    token: Optional[str],
    handles: Sequence[str],
) -> List[str]:
    """
    Resolve Slack handles to <@U…> mention tokens.

    Without a bot token (dry-run), returns plain @handle text.
    Prefer DEFAULT_MENTION_USER_IDS / raw U… IDs to avoid users.list rate limits.
    """
    cleaned = [h.lstrip("@").strip() for h in handles if h and h.strip()]
    if not cleaned:
        return []
    if not token:
        return [f"@{h}" for h in cleaned]

    resolved: List[str] = []
    need_lookup: List[str] = []
    for h in cleaned:
        if re.fullmatch(r"U[A-Z0-9]+", h):
            resolved.append(f"<@{h}>")
            continue
        known = DEFAULT_MENTION_USER_IDS.get(h.lower())
        if known:
            resolved.append(f"<@{known}>")
            print(f"  mention @{h} → <@{known}> (cached id)")
            continue
        need_lookup.append(h)

    if not need_lookup:
        return resolved

    users: List[Dict[str, Any]] = []
    cursor = None
    try:
        while True:
            body: Dict[str, Any] = {"limit": 200}
            if cursor:
                body["cursor"] = cursor
            data = slack_api(token, "users.list", body)
            users.extend(data.get("members") or [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                break
    except RuntimeError as exc:
        print(f"⚠️  Could not resolve Slack mentions ({exc}); using @handles", file=sys.stderr)
        return resolved + [f"@{h}" for h in need_lookup]

    for handle in need_lookup:
        best_user = None
        best_score = 0
        for user in users:
            score = _user_match_score(handle, user)
            if score > best_score:
                best_score = score
                best_user = user
        if best_user and best_score >= 50:
            resolved.append(f"<@{best_user['id']}>")
            label = (
                (best_user.get("profile") or {}).get("display_name")
                or best_user.get("real_name")
                or best_user.get("name")
                or handle
            )
            print(f"  mention @{handle} → {label} (<@{best_user['id']}>)")
        else:
            print(f"⚠️  Slack user @{handle} not found; posting plain @{handle}", file=sys.stderr)
            resolved.append(f"@{handle}")
    return resolved


def _truncate(text: str, width: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if width <= 1:
        return text[:width]
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


def _pad(text: str, width: int) -> str:
    return _truncate(text, width).ljust(width)


# Column layout for monospace fallback / dry-run preview
_COL_TICKET = 10
_COL_STATUS = 16
_COL_ASSIGNEE = 18
_COL_TITLE = 48
_TABLE_ROWS_PER_MESSAGE = 20  # keep Slack messages scannable


def format_ascii_table(rows: Sequence[Dict[str, str]], *, total: int, offset: int = 0) -> str:
    """Aligned monospace table (code block). Works everywhere in Slack."""
    header = (
        f"{_pad('Ticket', _COL_TICKET)}  "
        f"{_pad('Status', _COL_STATUS)}  "
        f"{_pad('Assignee', _COL_ASSIGNEE)}  "
        f"{_pad('Title', _COL_TITLE)}"
    )
    rule = (
        f"{'-' * _COL_TICKET}  "
        f"{'-' * _COL_STATUS}  "
        f"{'-' * _COL_ASSIGNEE}  "
        f"{'-' * _COL_TITLE}"
    )
    body_lines = []
    for row in rows:
        body_lines.append(
            f"{_pad(row['id'], _COL_TICKET)}  "
            f"{_pad(row['state'], _COL_STATUS)}  "
            f"{_pad(row['assignee'], _COL_ASSIGNEE)}  "
            f"{_pad(row['title'], _COL_TITLE)}"
        )
    table = "\n".join([header, rule, *body_lines])
    end = offset + len(rows)
    caption = f"AIOS tickets ({offset + 1}–{end} of {total}) · guild + sre-app"
    # Compact link row under the table (IDs stay aligned inside the code block).
    links = " · ".join(f"<{r['url']}|{r['id']}>" for r in rows)
    return f"*{caption}*\n```\n{table}\n```\n{links}"


def _table_cell_text(text: str) -> Dict[str, Any]:
    return {"type": "raw_text", "text": _truncate(text, 80) or "—"}


def _table_cell_link(label: str, url: str) -> Dict[str, Any]:
    """Rich-text link cell for Slack table blocks."""
    return {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [
                    {"type": "link", "url": url, "text": label},
                ],
            }
        ],
    }


def build_table_blocks(rows: Sequence[Dict[str, str]], *, total: int, offset: int = 0) -> List[Dict[str, Any]]:
    """Native Slack table block + short context line."""
    end = offset + len(rows)
    header = [
        _table_cell_text("Ticket"),
        _table_cell_text("Status"),
        _table_cell_text("Assignee"),
        _table_cell_text("Title"),
    ]
    data_rows = [
        [
            _table_cell_link(row["id"], row["url"]),
            _table_cell_text(row["state"]),
            _table_cell_text(row["assignee"]),
            _table_cell_text(row["title"]),
        ]
        for row in rows
    ]
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*AIOS tickets ({offset + 1}–{end} of {total})* · guild + sre-app",
            },
        },
        {
            "type": "table",
            "column_settings": [
                {"align": "left"},
                {"align": "left"},
                {"align": "left"},
                {"align": "left"},
            ],
            "rows": [header, *data_rows],
        },
    ]


def chunk_row_groups(rows: Sequence[Dict[str, str]]) -> List[Tuple[int, List[Dict[str, str]]]]:
    """Split rows into (offset, chunk) pairs for multi-message threads."""
    if not rows:
        return []
    groups: List[Tuple[int, List[Dict[str, str]]]] = []
    for i in range(0, len(rows), _TABLE_ROWS_PER_MESSAGE):
        groups.append((i, list(rows[i : i + _TABLE_ROWS_PER_MESSAGE])))
    return groups


def chunk_thread_messages(rows: Sequence[Dict[str, str]]) -> List[str]:
    """Plain-text / dry-run preview of thread messages (ASCII tables)."""
    if not rows:
        return [
            "_No AIOS tickets found for stackgen-guild / stackgen-sre-app in this tag diff._"
        ]
    total = len(rows)
    return [
        format_ascii_table(chunk, total=total, offset=offset)
        for offset, chunk in chunk_row_groups(rows)
    ]


def slack_api(
    token: str,
    method: str,
    payload: Dict[str, Any],
    *,
    max_retries: int = 6,
) -> Dict[str, Any]:
    """Call Slack Web API with Retry-After backoff on ratelimited."""
    last_error: Any = None
    for attempt in range(max_retries + 1):
        resp = requests.post(
            f"{SLACK_API}/{method}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "error": f"non-json status={resp.status_code}"}

        if data.get("ok"):
            return data

        err = data.get("error") or data
        last_error = err
        if err == "ratelimited" or resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after is None and isinstance(data.get("retry_after"), (int, float)):
                retry_after = str(data["retry_after"])
            try:
                wait_s = float(retry_after) if retry_after is not None else (2 ** attempt)
            except ValueError:
                wait_s = float(2 ** attempt)
            wait_s = min(max(wait_s, 1.0), 60.0)
            if attempt >= max_retries:
                break
            print(
                f"⚠️  Slack {method} rate-limited; retrying in {wait_s:.0f}s "
                f"({attempt + 1}/{max_retries})…",
                file=sys.stderr,
            )
            time.sleep(wait_s)
            continue
        raise RuntimeError(f"Slack {method} failed: {err}")

    raise RuntimeError(f"Slack {method} failed: {last_error}")


def _lookup_channel_id_by_name(token: str, name: str) -> Optional[str]:
    """Find channel ID via conversations.list (public + private the bot can see)."""
    cursor = None
    while True:
        body: Dict[str, Any] = {
            "types": "public_channel,private_channel",
            "exclude_archived": True,
            "limit": 200,
        }
        if cursor:
            body["cursor"] = cursor
        data = slack_api(token, "conversations.list", body)
        for ch in data.get("channels") or []:
            if ch.get("name") == name:
                return str(ch["id"])
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return None


def ensure_bot_in_channel(token: str, channel_id: str) -> None:
    """Join public channels when possible. Private channels require a manual /invite."""
    try:
        slack_api(token, "conversations.join", {"channel": channel_id})
        print(f"  joined channel {channel_id}")
    except RuntimeError as exc:
        err = str(exc).lower()
        # already_in_channel / method_not_supported (private) are fine to ignore
        if any(
            x in err
            for x in (
                "already_in_channel",
                "method_not_supported_for_channel_type",
                "is_archived",
                "missing_scope",
            )
        ):
            return
        if "not_in_channel" in err or "channel_not_found" in err:
            return
        print(f"⚠️  conversations.join: {exc}", file=sys.stderr)


def resolve_channel_id(token: str, channel: str) -> str:
    """
    Resolve to a Slack channel ID for chat.postMessage.

    Private channels require an ID (and the bot must be invited). Prefer
    SLACK_CHANNEL_ID in .env to skip listing.
    """
    explicit_id = (os.getenv("SLACK_CHANNEL_ID") or "").strip()
    raw = (explicit_id or channel or "").strip()
    if not raw:
        raise ValueError("Slack channel is empty")
    if re.fullmatch(r"[CG][A-Z0-9]+", raw):
        ensure_bot_in_channel(token, raw)
        return raw

    name = raw.lstrip("#")
    print(f"  resolving #{name} → channel id…")
    found = _lookup_channel_id_by_name(token, name)
    if not found:
        raise RuntimeError(
            f"Channel #{name} was not found for this bot.\n"
            f"  1. In Slack, open #{name} and run: /invite @<your-bot-name>\n"
            f"  2. Or set SLACK_CHANNEL_ID=C… in .env "
            f"(channel details → copy Channel ID).\n"
            f"  Private channels are invisible until the bot is invited."
        )
    print(f"  #{name} → {found}")
    ensure_bot_in_channel(token, found)
    return found


def _post_thread_table_message(
    token: str,
    channel: str,
    thread_ts: str,
    chunk: Sequence[Dict[str, str]],
    *,
    total: int,
    offset: int,
) -> None:
    """Prefer Slack native table blocks; fall back to monospace ASCII table."""
    fallback_text = format_ascii_table(chunk, total=total, offset=offset)
    blocks = build_table_blocks(chunk, total=total, offset=offset)
    payload: Dict[str, Any] = {
        "channel": channel,
        "text": fallback_text,
        "thread_ts": thread_ts,
        "unfurl_links": False,
        "blocks": blocks,
    }
    try:
        slack_api(token, "chat.postMessage", payload)
    except RuntimeError as exc:
        err = str(exc).lower()
        # Older workspaces / invalid table schema → readable monospace table
        if "invalid_blocks" in err or "invalid_block" in err or "table" in err:
            slack_api(
                token,
                "chat.postMessage",
                {
                    "channel": channel,
                    "text": fallback_text,
                    "thread_ts": thread_ts,
                    "unfurl_links": False,
                },
            )
        else:
            raise


def post_thread(
    token: str,
    channel_id: str,
    to_ref: str,
    rows: Sequence[Dict[str, str]],
    mention_tokens: Optional[Sequence[str]] = None,
) -> Tuple[str, str]:
    try:
        parent = slack_api(
            token,
            "chat.postMessage",
            {
                "channel": channel_id,
                "text": parent_text(to_ref, mention_tokens),
                "unfurl_links": False,
                "link_names": True,
            },
        )
    except RuntimeError as exc:
        err = str(exc).lower()
        if "channel_not_found" in err or "not_in_channel" in err:
            raise RuntimeError(
                f"{exc}\n"
                "The bot is not a member of this channel (common for private channels).\n"
                "  • In Slack: /invite @<your-bot-name>\n"
                "  • Then set SLACK_CHANNEL_ID=C… in .env and retry."
            ) from exc
        raise
    thread_ts = parent["ts"]
    channel = parent.get("channel") or channel_id

    if not rows:
        slack_api(
            token,
            "chat.postMessage",
            {
                "channel": channel,
                "text": (
                    "_No AIOS tickets found for stackgen-guild / "
                    "stackgen-sre-app in this tag diff._"
                ),
                "thread_ts": thread_ts,
                "unfurl_links": False,
            },
        )
    else:
        total = len(rows)
        for offset, chunk in chunk_row_groups(rows):
            _post_thread_table_message(
                token, channel, thread_ts, chunk, total=total, offset=offset
            )
            time.sleep(0.2)

    permalink = ""
    try:
        link = slack_api(
            token,
            "chat.getPermalink",
            {"channel": channel, "message_ts": thread_ts},
        )
        permalink = str(link.get("permalink") or "")
    except Exception:
        pass
    return thread_ts, permalink


def resolve_artifact_path(
    generated_dir: Optional[str],
    from_ref: Optional[str],
    to_ref: Optional[str],
    input_path: Optional[str],
) -> Path:
    if input_path:
        return Path(input_path)
    if generated_dir:
        return Path(generated_dir) / "final_tag_differences.json"
    if from_ref and to_ref:
        from_safe = re.sub(r"[/ :]", "-", from_ref)
        to_safe = re.sub(r"[/ :]", "-", to_ref)
        return Path(f"{from_safe}-{to_safe}") / "final_tag_differences.json"
    raise SystemExit(
        "Provide --input, or --generated-dir, or both --from-ref and --to-ref"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Post guild/sre-app AIOS tickets from a tag-diff JSON to Slack"
    )
    parser.add_argument("--input", help="Path to final_tag_differences.json")
    parser.add_argument("--generated-dir", help="Artifact dir containing final_tag_differences.json")
    parser.add_argument("--from-ref", help="Base appcd-dist ref (with --to-ref)")
    parser.add_argument("--to-ref", required=True, help="Candidate tag shown in the Slack parent message")
    parser.add_argument(
        "--channel",
        default=os.getenv("SLACK_CHANNEL", DEFAULT_CHANNEL),
        help=f"Slack channel name or ID (default: {DEFAULT_CHANNEL})",
    )
    parser.add_argument(
        "--slack-token",
        default=os.getenv("SLACK_BOT_TOKEN"),
        help="Slack bot token (or set SLACK_BOT_TOKEN)",
    )
    parser.add_argument(
        "--linear-api-key",
        default=os.getenv("LINEAR_API_KEY"),
        help="Optional Linear key to fill missing assignees",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the parent + thread messages; do not call Slack",
    )
    parser.add_argument(
        "--skip-if-no-token",
        action="store_true",
        help="Exit 0 with a warning when SLACK_BOT_TOKEN is missing (for pipeline use)",
    )
    parser.add_argument(
        "--mention",
        action="append",
        dest="mentions",
        default=None,
        help="Slack handle to @-mention on the parent post (repeatable). "
        "Default: suvarna and sks (or SLACK_MENTION_USERS=suvarna,sks)",
    )
    args = parser.parse_args(argv)

    if not args.slack_token and not args.dry_run:
        msg = (
            "SLACK_BOT_TOKEN is not set. Add it to .env or export it. "
            "See docs/slack-aios-notify.md for Slack app setup."
        )
        if args.skip_if_no_token:
            print(f"⚠️  Skipping AIOS Slack notify: {msg}", file=sys.stderr)
            return 0
        print(f"Error: {msg}", file=sys.stderr)
        return 1

    if args.mentions:
        mention_handles = args.mentions
    else:
        env_mentions = os.getenv("SLACK_MENTION_USERS", "").strip()
        if env_mentions:
            mention_handles = [p.strip() for p in env_mentions.split(",") if p.strip()]
        else:
            mention_handles = list(DEFAULT_MENTION_HANDLES)

    artifact = resolve_artifact_path(
        args.generated_dir, args.from_ref, args.to_ref, args.input
    )
    if not artifact.is_file():
        print(f"Error: {artifact} not found", file=sys.stderr)
        return 1

    data = json.loads(artifact.read_text(encoding="utf-8"))
    ticket_ids = collect_aios_tickets(data)
    details = data.get("ticket_details") or {}
    rows = enrich_tickets(ticket_ids, details, args.linear_api_key)

    # Dry-run: plain @handles. Live post: resolve to <@U…> so Slack notifies.
    mention_tokens = resolve_mention_tokens(
        None if args.dry_run else args.slack_token,
        mention_handles,
    )

    print(f"Found {len(rows)} AIOS tickets (guild + sre-app) in {artifact}")
    print("--- parent ---")
    print(parent_text(args.to_ref, mention_tokens))
    print("--- thread ---")
    for chunk in chunk_thread_messages(rows):
        print(chunk)
        print("---")

    if args.dry_run:
        print("DRY_RUN=1 — not posting to Slack")
        return 0

    assert args.slack_token
    # Live path: re-resolve mentions with token (cached ids skip users.list).
    mention_tokens = resolve_mention_tokens(args.slack_token, mention_handles)
    channel_id = resolve_channel_id(args.slack_token, args.channel)
    thread_ts, permalink = post_thread(
        args.slack_token,
        channel_id,
        args.to_ref,
        rows,
        mention_tokens=mention_tokens,
    )
    print(f"✅ Posted to #{args.channel.lstrip('#')} (thread_ts={thread_ts})")
    if permalink:
        print(f"   {permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
