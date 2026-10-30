#!/usr/bin/env python3
"""
Refresh ticket Status columns on a Linear release issue description.

Reads a release ticket (e.g. HZ-809) created by create_monthly_release_ticket.py,
finds every linked ticket in the markdown tables, fetches current Linear state
(and optionally title), then writes the updated description back.

Usage:
  export LINEAR_API_KEY=lin_api_...
  python update_release_ticket_statuses.py HZ-809 --dry-run
  python update_release_ticket_statuses.py HZ-809
  make update-ticket-statuses ISSUE=HZ-809 DRY_RUN=1
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

LINEAR_API_URL = "https://api.linear.app/graphql"

# Table row: | [PLAT-123](url) | In Review | Some title |
#        or: | PLAT-123 | In Review | Some title |
# Groups: id_cell, ticket_from_link, ticket_bare, status, summary
_TICKET_ROW_RE = re.compile(
    r"^\|\s*"
    r"("
    r"\[([A-Za-z]{2,10}-\d+)\]\([^)]+\)"
    r"|"
    r"([A-Za-z]{2,10}-\d+)"
    r")"
    r"\s*\|\s*"
    r"([^|]*)"
    r"\s*\|\s*"
    r"(.*?)\s*\|?\s*$"
)

_TICKET_ID_RE = re.compile(r"^[A-Za-z]{2,10}-\d+$")

# Footer stamp so re-runs replace the previous note instead of stacking.
_REFRESH_STAMP_RE = re.compile(
    r"\n*_Ticket statuses last refreshed:[^\n]*_\s*$"
    re.IGNORECASE,
)


def linear_request(
    api_key: str,
    query: str,
    variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    resp = requests.post(LINEAR_API_URL, headers=headers, json=payload, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        data = None

    if resp.status_code >= 400:
        if isinstance(data, dict) and data.get("errors"):
            messages = "; ".join(
                e.get("message", "Unknown error") for e in data["errors"]
            )
            raise RuntimeError(f"Linear API HTTP {resp.status_code}: {messages}")
        raise RuntimeError(
            f"Linear API HTTP {resp.status_code}: {resp.text[:800]}"
        )

    if isinstance(data, dict) and data.get("errors"):
        messages = "; ".join(
            e.get("message", "Unknown error") for e in data["errors"]
        )
        raise RuntimeError(f"Linear API error: {messages}")
    return data.get("data", {}) if isinstance(data, dict) else {}


def fetch_issue(api_key: str, identifier: str) -> Dict[str, Any]:
    query = """
    query IssueByIdentifier($identifier: String!) {
      issue(id: $identifier) {
        id
        identifier
        title
        url
        description
      }
    }
    """
    issue = linear_request(
        api_key, query, {"identifier": identifier}
    ).get("issue")
    if not issue:
        raise RuntimeError(f"Linear issue not found: {identifier}")
    return issue


def fetch_ticket_state(
    api_key: str, ticket_id: str
) -> Optional[Dict[str, str]]:
    query = """
    query IssueByIdentifier($identifier: String!) {
      issue(id: $identifier) {
        identifier
        title
        state { name }
      }
    }
    """
    try:
        issue = linear_request(
            api_key, query, {"identifier": ticket_id}
        ).get("issue")
    except RuntimeError as exc:
        print(f"  warning: {ticket_id}: {exc}", file=sys.stderr)
        return None
    if not issue:
        print(f"  warning: {ticket_id}: not found", file=sys.stderr)
        return None
    state = (issue.get("state") or {}).get("name") or "—"
    return {
        "id": str(issue.get("identifier") or ticket_id),
        "title": str(issue.get("title") or "").strip(),
        "state": str(state).strip() or "—",
    }


def _md_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def extract_ticket_ids(description: str, exclude: Optional[str] = None) -> List[str]:
    """Unique ticket IDs from Status tables, in first-seen order."""
    seen: Dict[str, None] = {}
    exclude_norm = (exclude or "").strip().upper()
    for line in (description or "").splitlines():
        match = _TICKET_ROW_RE.match(line.strip())
        if not match:
            continue
        ticket_id = (match.group(2) or match.group(3) or "").strip()
        if not ticket_id or not _TICKET_ID_RE.match(ticket_id):
            continue
        if ticket_id.upper() == exclude_norm:
            continue
        seen.setdefault(ticket_id, None)
    return list(seen.keys())


def refresh_description(
    description: str,
    status_by_id: Dict[str, Dict[str, str]],
    *,
    refresh_titles: bool = False,
) -> Tuple[str, List[Tuple[str, str, str]]]:
    """
    Rewrite Status (and optionally Summary) cells.

    Returns (new_description, changes) where each change is
    (ticket_id, old_status, new_status).
    """
    changes: List[Tuple[str, str, str]] = []
    out_lines: List[str] = []

    for line in (description or "").splitlines():
        stripped = line.strip()
        match = _TICKET_ROW_RE.match(stripped)
        if not match:
            out_lines.append(line)
            continue

        ticket_id = (match.group(2) or match.group(3) or "").strip()
        details = status_by_id.get(ticket_id)
        if not details:
            out_lines.append(line)
            continue

        id_cell = (match.group(1) or ticket_id).strip()
        old_status = (match.group(4) or "").strip()
        new_status = _md_cell(details.get("state") or "—") or "—"
        summary = (match.group(5) or "").strip()
        if refresh_titles and details.get("title"):
            summary = _md_cell(details["title"]) or "—"
        if not summary:
            summary = "—"

        out_lines.append(f"| {id_cell} | {new_status} | {summary} |")
        if old_status != new_status:
            changes.append((ticket_id, old_status, new_status))

    body = "\n".join(out_lines)
    # Preserve trailing newline convention used by create_monthly_release_ticket.
    if description.endswith("\n") and not body.endswith("\n"):
        body += "\n"

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = _REFRESH_STAMP_RE.sub("", body).rstrip()
    body += f"\n\n_Ticket statuses last refreshed: {stamp}_\n"
    return body, changes


def update_issue_description(
    api_key: str, issue_uuid: str, description: str
) -> Dict[str, Any]:
    mutation = """
    mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue { id identifier url title }
      }
    }
    """
    payload = linear_request(
        api_key,
        mutation,
        {"id": issue_uuid, "input": {"description": description}},
    ).get("issueUpdate", {})
    if not payload.get("success"):
        raise RuntimeError("issueUpdate returned success=false")
    issue = payload.get("issue")
    if not issue:
        raise RuntimeError("issueUpdate returned no issue")
    return issue


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch latest Linear statuses for tickets listed on a release "
            "issue and update that issue's description tables."
        )
    )
    parser.add_argument(
        "issue",
        help="Linear issue identifier to update (e.g. HZ-809).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("LINEAR_API_KEY"),
        help="Linear API key (default: LINEAR_API_KEY env).",
    )
    parser.add_argument(
        "--refresh-titles",
        action="store_true",
        help="Also rewrite the Summary column from current Linear titles.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print status changes only; do not call issueUpdate.",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    api_key = (args.api_key or "").strip()
    issue_id = (args.issue or "").strip()

    if not issue_id:
        print("Error: pass a Linear issue id (e.g. HZ-809).", file=sys.stderr)
        return 1
    if not api_key:
        print(
            "Error: LINEAR_API_KEY not set. Pass --api-key or export LINEAR_API_KEY.",
            file=sys.stderr,
        )
        return 1

    print(f"Loading release issue {issue_id}…")
    parent = fetch_issue(api_key, issue_id)
    description = parent.get("description") or ""
    if not description.strip():
        print("Error: issue description is empty; nothing to refresh.", file=sys.stderr)
        return 1

    ticket_ids = extract_ticket_ids(description, exclude=parent.get("identifier"))
    if not ticket_ids:
        print(
            "Error: no ticket table rows found in the description "
            "(expected | ID | Status | Summary |).",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(ticket_ids)} ticket(s). Fetching current statuses…")
    status_by_id: Dict[str, Dict[str, str]] = {}
    for idx, ticket_id in enumerate(ticket_ids, 1):
        details = fetch_ticket_state(api_key, ticket_id)
        if details:
            status_by_id[ticket_id] = details
            print(f"  [{idx}/{len(ticket_ids)}] {ticket_id}: {details['state']}")
        else:
            print(f"  [{idx}/{len(ticket_ids)}] {ticket_id}: (skipped)")

    if not status_by_id:
        print("Error: could not fetch status for any ticket.", file=sys.stderr)
        return 1

    new_description, changes = refresh_description(
        description,
        status_by_id,
        refresh_titles=args.refresh_titles,
    )

    print("")
    if changes:
        print(f"Status changes ({len(changes)}):")
        for ticket_id, old, new in changes:
            print(f"  {ticket_id}: {old or '—'} → {new}")
    else:
        print("No status column changes (all already current).")

    unchanged = len(status_by_id) - len(changes)
    missing = len(ticket_ids) - len(status_by_id)
    print(
        f"Summary: {len(changes)} updated · {unchanged} unchanged · "
        f"{missing} fetch failures"
    )

    if args.dry_run:
        print("")
        print("Dry run: description not written to Linear.")
        print(f"Would update: {parent.get('url') or issue_id}")
        return 0

    if new_description == description and not changes:
        # Stamp may still differ; write if stamp section is the only delta.
        pass

    updated = update_issue_description(api_key, parent["id"], new_description)
    print("")
    print(f"Updated {updated.get('identifier')}: {updated.get('url')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(1)
