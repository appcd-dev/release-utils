#!/usr/bin/env python3
"""Create a weekly/monthly release issue in Linear from tag-diff artifacts."""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

LINEAR_API_URL = "https://api.linear.app/graphql"

# Product sections in the release ticket body (order matters).
PRODUCT_TICKET_SECTIONS = ("Aiden2", "Aiden", "Stackgen Core")

# Service name → product label. Unlisted services fall under "Stackgen Core".
AIDEN2_SERVICES = frozenset({"stackgen-sre-app", "stackgen-guild"})
AIDEN_SERVICES = frozenset({"aiden-ui", "aiden"})


@dataclass
class MonthlyTicketConfig:
    """Inputs for creating the release Linear issue (Step 4 of the release pipeline)."""

    input_path: Path
    api_key: Optional[str] = None
    template_name: str = ""
    template_id: str = ""
    assignee_query: str = "gaurav@stackgen.com"
    team_key: str = "HZ"
    team_id: str = ""
    title: str = ""
    month_label: str = ""
    # "weekly" → "[Weekly release] <tag>"; "monthly" → "[Monthly release] <tag>"
    release_kind: str = "weekly"
    stackgen_tag: str = ""
    state_name: str = "Todo"
    # Optional path to input.json (all services). Auto-detected next to final_tag_differences.json.
    services_input_path: Optional[Path] = None
    dry_run: bool = False


def default_release_title(release_kind: str, month_label: str, stackgen_tag: str = "") -> str:
    """Title: [Weekly release] vX.Y.Z or [Monthly release] vX.Y.Z."""
    kind = (release_kind or "weekly").strip().lower()
    kind_label = "Weekly" if kind == "weekly" else "Monthly"
    tag = (stackgen_tag or "").strip() or (month_label or "").strip() or "unknown"
    return f"[{kind_label} release] {tag}"


LINEAR_WORKSPACE = "stackgen"
LINEAR_ISSUE_BASE = f"https://linear.app/{LINEAR_WORKSPACE}/issue"

# Noise / non-issue identifiers sometimes extracted from commit text.
NOISE_TICKET_IDS = frozenset({"UTF-8", "UTF-16", "DEP-02", "INT-03"})


def product_label_for_service(service_name: str) -> str:
    """Map a tag-diff service name to a release-ticket product section."""
    name = (service_name or "").strip().lower()
    if name in AIDEN2_SERVICES:
        return "Aiden2"
    if name in AIDEN_SERVICES:
        return "Aiden"
    return "Stackgen Core"


def _normalize_ticket_id(raw: Any) -> str:
    ticket = str(raw or "").split(":", 1)[0].strip()
    return ticket


def group_tickets_by_product(data: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Group tickets by product label based on which service(s) they appear under:

    - Aiden2: stackgen-sre-app, stackgen-guild
    - Aiden: aiden-ui, aiden
    - Stackgen Core: all remaining services

    A ticket that appears under multiple product buckets is assigned once,
    preferring Aiden2 > Aiden > Stackgen Core.
    """
    ticket_labels: Dict[str, set] = {}

    services = data.get("services")
    if isinstance(services, list):
        for svc in services:
            if not isinstance(svc, dict):
                continue
            label = product_label_for_service(str(svc.get("service") or ""))
            for raw in svc.get("tickets") or []:
                ticket = _normalize_ticket_id(raw)
                if not ticket or ticket in NOISE_TICKET_IDS:
                    continue
                ticket_labels.setdefault(ticket, set()).add(label)

    # Tickets present in the aggregate list but not attached to any service
    # still belong on the release ticket under Stackgen Core.
    all_tickets = data.get("all_tickets")
    if isinstance(all_tickets, list):
        for item in all_tickets:
            ticket = _normalize_ticket_id(item)
            if not ticket or ticket in NOISE_TICKET_IDS or "-" not in ticket:
                continue
            ticket_labels.setdefault(ticket, set()).add("Stackgen Core")

    # Fallback when services[] is missing: use tickets_by_project / all_tickets
    # and put everything under Stackgen Core.
    if not ticket_labels:
        grouped_raw = data.get("tickets_by_project")
        if isinstance(grouped_raw, dict):
            for ticket_list in grouped_raw.values():
                if not isinstance(ticket_list, list):
                    continue
                for raw in ticket_list:
                    ticket = _normalize_ticket_id(raw)
                    if ticket and ticket not in NOISE_TICKET_IDS:
                        ticket_labels.setdefault(ticket, set()).add("Stackgen Core")

    grouped: Dict[str, List[str]] = {section: [] for section in PRODUCT_TICKET_SECTIONS}
    for ticket, labels in ticket_labels.items():
        for section in PRODUCT_TICKET_SECTIONS:
            if section in labels:
                grouped[section].append(ticket)
                break

    for section in grouped:
        # De-dupe only here; priority sort is applied in load_release_data.
        grouped[section] = list(dict.fromkeys(grouped[section]))
    return grouped

# process_all_repos.py formats all_tickets as: "TICKET-ID : status : title"
_TICKET_WITH_META = re.compile(r"^(\S+)\s*:\s*(.+?)\s*:\s*(.+)$", re.DOTALL)
_TICKET_ID_ONLY = re.compile(r"^([A-Za-z]+-\d+)$")

# Linear priority: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low.
# Sort Urgent → High → Medium → Low → None.
_PRIORITY_NONE_RANK = 99


def priority_sort_rank(priority: Any) -> int:
    """Map Linear priority to ascending sort rank (Urgent first, none last)."""
    try:
        value = int(priority)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else _PRIORITY_NONE_RANK


def parse_ticket_priority(raw: Any) -> int:
    """Normalize a Linear priority value to int (0 when missing/invalid)."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def sort_tickets_by_priority(
    tickets: List[str],
    ticket_meta: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Stable priority order within a project/product section; tie-break by id."""
    return sorted(
        tickets,
        key=lambda tid: (
            priority_sort_rank((ticket_meta.get(tid) or {}).get("priority", 0)),
            tid,
        ),
    )


def parse_ticket_line(line: str) -> Tuple[str, str, str]:
    """Return (ticket_id, state, title) from an all_tickets line."""
    line_stripped = (line or "").strip()
    if not line_stripped:
        return "", "", ""
    meta_match = _TICKET_WITH_META.match(line_stripped)
    if meta_match:
        return (
            meta_match.group(1).strip(),
            meta_match.group(2).strip(),
            meta_match.group(3).strip(),
        )
    id_only_match = _TICKET_ID_ONLY.match(line_stripped)
    if id_only_match:
        return id_only_match.group(1), "", ""
    return line_stripped, "", ""


def ticket_meta_from_all_tickets(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Map ticket id -> {title, state, priority} from artifact fields.

    Prefers structured `ticket_details` (includes Linear priority). Falls back to
    parsing `all_tickets` strings (priority defaults to 0 when absent).
    """
    meta: Dict[str, Dict[str, Any]] = {}
    raw_entries = data.get("all_tickets")
    if isinstance(raw_entries, list):
        for raw_line in raw_entries:
            if not isinstance(raw_line, str):
                continue
            ticket_id, state, title = parse_ticket_line(raw_line)
            if ticket_id:
                meta[ticket_id] = {"title": title, "state": state, "priority": 0}

    details_raw = data.get("ticket_details")
    if isinstance(details_raw, dict):
        for ticket_id, details in details_raw.items():
            tid = str(ticket_id or "").strip()
            if not tid or not isinstance(details, dict):
                continue
            existing = meta.get(tid) or {}
            meta[tid] = {
                "title": str(details.get("title") or existing.get("title") or ""),
                "state": str(details.get("state") or existing.get("state") or ""),
                "priority": parse_ticket_priority(details.get("priority", 0)),
            }
    return meta


def ticket_summaries_from_all_tickets(data: Dict[str, Any]) -> Dict[str, str]:
    """Map ticket id -> title/summary from `all_tickets` strings (see process_all_repos)."""
    return {
        ticket_id: str(details.get("title", "") or "")
        for ticket_id, details in ticket_meta_from_all_tickets(data).items()
    }


def linear_issue_url(ticket_id: str) -> str:
    return f"{LINEAR_ISSUE_BASE}/{ticket_id}"


def default_services_input_path(tag_diff_path: Path) -> Optional[Path]:
    """Resolve input.json next to final_tag_differences.json when present."""
    candidate = tag_diff_path.parent / "input_file" / "input.json"
    return candidate if candidate.is_file() else None


def _service_tag_row(svc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(svc.get("service") or "").strip()
    if not name:
        return None
    from_tag = str(svc.get("current_tag") or "").strip()
    to_tag = str(svc.get("new_tag") or "").strip()
    changed = bool(from_tag and to_tag and from_tag != to_tag)
    return {
        "service": name,
        "from": from_tag or "—",
        "to": to_tag or "—",
        "changed": changed,
    }


def release_candidate_rows(
    data: Dict[str, Any],
    *,
    include_unchanged: bool = True,
) -> List[Dict[str, Any]]:
    """
    Service tag rows from a services list (input.json or final_tag_differences.json).

    When include_unchanged is True (default), every service is listed; unchanged
    tags show the same value in From/To with changed=False.
    """
    rows: List[Dict[str, Any]] = []
    services = data.get("services")
    if not isinstance(services, list):
        # input.json is a bare list
        if isinstance(data, list):
            services = data
        else:
            return rows
    for svc in services:
        if not isinstance(svc, dict):
            continue
        row = _service_tag_row(svc)
        if not row:
            continue
        if not include_unchanged and not row["changed"]:
            continue
        rows.append(row)
    # Changed services first, then alphabetical within each group.
    return sorted(rows, key=lambda r: (not r["changed"], str(r["service"]).lower()))


def release_candidate_tag_lines(data: Dict[str, Any]) -> List[str]:
    """Bumped service tags as plain lines (legacy helper)."""
    return [
        f"{r['service']}: {r['from']} → {r['to']}"
        for r in release_candidate_rows(data, include_unchanged=False)
    ]


def load_release_candidate_rows(
    tag_diff_path: Path,
    services_input_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Prefer full service list from input.json (all services); fall back to
    final_tag_differences.json services[].
    """
    path = services_input_path or default_services_input_path(tag_diff_path)
    if path and path.is_file():
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return release_candidate_rows({"services": raw}, include_unchanged=True)
        if isinstance(raw, dict):
            return release_candidate_rows(raw, include_unchanged=True)

    with tag_diff_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return release_candidate_rows(data, include_unchanged=True)

def all_linear_projects(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All projects from final_tag_differences.json, sorted by name."""
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        return []
    projects = [p for p in raw_projects if isinstance(p, dict)]
    return sorted(projects, key=lambda row: str(row.get("name", "") or "").lower())


def linear_request(api_key: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
            messages = "; ".join(e.get("message", "Unknown error") for e in data["errors"])
            raise RuntimeError(f"Linear API HTTP {resp.status_code}: {messages}")
        raise RuntimeError(f"Linear API HTTP {resp.status_code}: {resp.text[:800]}")

    if isinstance(data, dict) and data.get("errors"):
        messages = "; ".join(e.get("message", "Unknown error") for e in data["errors"])
        raise RuntimeError(f"Linear API error: {messages}")
    return data.get("data", {}) if isinstance(data, dict) else {}


def load_release_data(
    input_path: Path,
    services_input_path: Optional[Path] = None,
) -> Tuple[
    Dict[str, List[str]],
    Dict[str, Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """Load product-grouped tickets, ticket meta, RC tag rows, and projects."""
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    ticket_meta = ticket_meta_from_all_tickets(data)
    rc_rows = load_release_candidate_rows(input_path, services_input_path)
    projects = all_linear_projects(data)
    grouped = group_tickets_by_product(data)
    for section, tickets in grouped.items():
        grouped[section] = sort_tickets_by_priority(tickets, ticket_meta)
    return grouped, ticket_meta, rc_rows, projects


def read_grouped_tickets(input_path: Path) -> Dict[str, List[str]]:
    grouped, _, _, _ = load_release_data(input_path)
    return grouped


def _is_bug_like(title: str) -> bool:
    t = (title or "").lower()
    return any(tok in t for tok in ("[bug]", "bug fix", "fails", "error", "broken", "incorrect"))


def _md_cell(text: str) -> str:
    """Escape pipe characters so markdown tables stay intact."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _ticket_table_row(ticket_id: str, meta: Dict[str, Dict[str, Any]]) -> str:
    details = meta.get(ticket_id) or {}
    title = _md_cell(str(details.get("title") or ""))
    state = _md_cell(str(details.get("state") or "—")) or "—"
    link = f"[{ticket_id}]({linear_issue_url(ticket_id)})"
    return f"| {link} | {state} | {title or '—'} |"


def _append_ticket_table(
    lines: List[str],
    tickets: List[str],
    ticket_meta: Dict[str, Dict[str, Any]],
) -> None:
    lines.append("| ID | Status | Summary |")
    lines.append("| --- | --- | --- |")
    for ticket_id in tickets:
        lines.append(_ticket_table_row(ticket_id, ticket_meta))
    lines.append("")


def _append_ticket_section(
    lines: List[str],
    heading: str,
    tickets: List[str],
    ticket_meta: Dict[str, Dict[str, Any]],
) -> None:
    lines.append(f"## {heading}")
    lines.append("")
    if not tickets:
        lines.append("_None_")
        lines.append("")
        return

    # Preserve caller priority order within Features / Bug fixes splits.
    features = []
    bugs = []
    for ticket_id in tickets:
        title = str((ticket_meta.get(ticket_id) or {}).get("title", "") or "")
        if _is_bug_like(title):
            bugs.append(ticket_id)
        else:
            features.append(ticket_id)

    if features and bugs:
        lines.append(f"### Features / changes ({len(features)})")
        lines.append("")
        _append_ticket_table(lines, features, ticket_meta)
        lines.append(f"### Bug fixes ({len(bugs)})")
        lines.append("")
        _append_ticket_table(lines, bugs, ticket_meta)
    else:
        _append_ticket_table(lines, tickets, ticket_meta)

def build_summary(
    grouped: Dict[str, List[str]],
    month_label: str,
    ticket_id_to_summary: Optional[Dict[str, str]] = None,
    in_progress_projects: Optional[List[Dict[str, Any]]] = None,
    release_kind: str = "weekly",
    stackgen_tag: str = "",
    rc_tag_lines: Optional[List[str]] = None,
    projects: Optional[List[Dict[str, Any]]] = None,
    ticket_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    rc_rows: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Readable Linear markdown for release managers + docs:

    - Partitioned sections with ---
    - Component tag table (from → to)
    - Ticket tables: ID (link) | Status | Summary (priority-sorted within each section)
    - Project lists with Linear hyperlinks + state/progress
    """
    _ = (ticket_id_to_summary, in_progress_projects, rc_tag_lines)

    kind = (release_kind or "weekly").strip().lower()
    kind_label = "Weekly" if kind == "weekly" else "Monthly"
    tag = (stackgen_tag or "").strip()
    ticket_meta = ticket_meta or {}
    projects = projects if projects is not None else []
    rc_rows = list(rc_rows or [])

    lines: List[str] = []
    header = f"**{kind_label} release**"
    if tag:
        header += f" candidate `{tag}`"
    if month_label:
        header += f" — {month_label}"
    lines.append(header)
    lines.append("")
    lines.append(
        "_Auto-generated from release-utils tag diff. "
        "Click ticket / project links to open in Linear._"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Release Candidate Tags (all services; changed rows highlighted) ---
    lines.append("## Release Candidate Tags")
    lines.append("")
    lines.append(
        "_Changed components are listed first and marked **Updated** "
        "(Linear markdown has no text color; bold = changed)._"
    )
    lines.append("")
    lines.append("| Component | From | To | Change |")
    lines.append("| --- | --- | --- | --- |")
    if tag:
        lines.append(f"| **stackgen** | — | **`{tag}`** | **Updated** |")
    if rc_rows:
        for row in rc_rows:
            service = _md_cell(str(row["service"]))
            from_tag = _md_cell(str(row["from"]))
            to_tag = _md_cell(str(row["to"]))
            if row.get("changed"):
                lines.append(
                    f"| **`{service}`** | `{from_tag}` | **`{to_tag}`** | **Updated** |"
                )
            else:
                # Same tag (or empty) — show both columns for clarity
                display_to = to_tag if to_tag != "—" else from_tag
                lines.append(
                    f"| `{service}` | `{from_tag}` | `{display_to}` | Unchanged |"
                )
    elif not tag:
        lines.append("| — | — | — | _(no services)_ |")
    lines.append("")
    changed_count = sum(1 for r in rc_rows if r.get("changed"))
    lines.append(
        f"_Services: {len(rc_rows)} total · "
        f"**{changed_count} updated** · "
        f"{len(rc_rows) - changed_count} unchanged_"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Product ticket sections (by service) ---
    for section in PRODUCT_TICKET_SECTIONS:
        tickets = grouped.get(section, [])
        _append_ticket_section(
            lines, f"{section} ({len(tickets)})", tickets, ticket_meta
        )
        lines.append("---")
        lines.append("")

    # --- Projects ---
    lines.append(f"## Projects ({len(projects)})")
    lines.append("")
    if not projects:
        lines.append("_None_")
        lines.append("")
    else:
        lines.append("| Project | State | Progress |")
        lines.append("| --- | --- | --- |")
        for project in projects:
            display_name = str(project.get("name") or "Untitled").strip() or "Untitled"
            project_url = str(project.get("url") or "").strip()
            state = str(project.get("state") or "—").strip() or "—"
            progress_value = project.get("progress")
            percent_label = "—"
            if isinstance(progress_value, (int, float)):
                percent_label = f"{progress_value * 100:.0f}%"
            name_cell = (
                f"[{display_name}]({project_url})" if project_url else display_name
            )
            lines.append(f"| {name_cell} | `{state}` | {percent_label} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def resolve_assignee_id(api_key: str, assignee_query: str) -> str:
    graphql_query = """
    query {
      users(first: 250) {
        nodes { id name displayName email }
      }
    }
    """
    user_nodes = linear_request(api_key, graphql_query).get("users", {}).get("nodes", [])
    query_normalized = assignee_query.lower().strip()

    # Prefer exact email match when the query looks like an email.
    if "@" in query_normalized:
        for user in user_nodes:
            if str(user.get("email", "")).lower().strip() == query_normalized:
                return user["id"]

    selected_user = None
    for user in user_nodes:
        searchable = " ".join(
            str(user.get(key, "")) for key in ("name", "displayName", "email")
        ).lower()
        if query_normalized in searchable:
            selected_user = user
            email = str(user.get("email", "")).lower().strip()
            if email == query_normalized:
                return user["id"]
            if (
                user.get("name", "").lower() == query_normalized
                or user.get("displayName", "").lower() == query_normalized
            ):
                break
    if not selected_user:
        raise RuntimeError(f"Could not find assignee matching '{assignee_query}'")
    return selected_user["id"]


def resolve_template_id(api_key: str, template_name: str) -> str:
    """Resolve template id using root Query.templates (a list, not a connection)."""
    template_name_normalized = template_name.lower().strip()
    if not template_name_normalized:
        raise RuntimeError("template_name is empty")

    query_templates = """
    query {
      templates {
        id
        name
        type
        __typename
      }
    }
    """

    response_data = linear_request(api_key, query_templates)
    template_rows = response_data.get("templates", [])
    template_list = template_rows if isinstance(template_rows, list) else []
    if not template_list:
        raise RuntimeError("No templates returned by Linear `templates` query")

    issue_templates = [
        row
        for row in template_list
        if "issue" in str(row.get("type", "")).lower()
    ]
    search_pool = issue_templates or template_list

    for template_row in search_pool:
        if str(template_row.get("name", "")).lower().strip() == template_name_normalized:
            return template_row["id"]
    for template_row in search_pool:
        if template_name_normalized in str(template_row.get("name", "")).lower():
            return template_row["id"]

    available_names = ", ".join(
        sorted(str(row.get("name", "")) for row in search_pool if row.get("name"))
    )
    raise RuntimeError(
        f"Template '{template_name}' not found in templates. "
        f"Available: {available_names or 'none'}"
    )


def resolve_team_id(api_key: str, team_key: str) -> str:
    """Resolve Linear team id by team key (issue prefix), e.g. HZ -> team for HZ-123 issues."""
    key = team_key.strip()
    if not key:
        raise RuntimeError("team_key is empty")
    query = """
    query TeamByKey($filter: TeamFilter) {
      teams(first: 10, filter: $filter) {
        nodes { id key name }
      }
    }
    """
    variables: Dict[str, Any] = {"filter": {"key": {"eqIgnoreCase": key}}}
    response_data = linear_request(api_key, query, variables)
    team_nodes = response_data.get("teams", {}).get("nodes", [])
    for team in team_nodes:
        if str(team.get("key", "")).upper() == key.upper():
            return team["id"]
    if team_nodes:
        return team_nodes[0]["id"]
    raise RuntimeError(f"No Linear team found for key '{team_key}'")


def resolve_state_id(api_key: str, team_id: str, state_name: str = "Todo") -> str:
    """Resolve a workflow state id on the team by name (default: Todo)."""
    wanted = (state_name or "Todo").strip().lower()
    query = """
    query TeamStates($id: String!) {
      team(id: $id) {
        states {
          nodes { id name type }
        }
      }
    }
    """
    team = linear_request(api_key, query, {"id": team_id}).get("team") or {}
    nodes = (team.get("states") or {}).get("nodes") or []
    for state in nodes:
        if str(state.get("name", "")).strip().lower() == wanted:
            return state["id"]
    # Fallback: first unstarted state (Linear's usual Todo type).
    for state in nodes:
        if str(state.get("type", "")).strip().lower() == "unstarted":
            return state["id"]
    available = ", ".join(str(s.get("name", "")) for s in nodes if s.get("name"))
    raise RuntimeError(
        f"Could not find workflow state '{state_name}' on team. "
        f"Available: {available or 'none'}"
    )


def create_issue(
    api_key: str,
    title: str,
    summary: str,
    assignee_id: str,
    team_id: str,
    state_id: Optional[str] = None,
    template_id: str = "",
    parent_id: str = "",
) -> Dict[str, Any]:
    mutation = """
    mutation IssueCreate($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier title url state { name } assignee { email } }
      }
    }
    """

    base: Dict[str, Any] = {
        "title": title,
        "description": summary,
        "assigneeId": assignee_id,
        "teamId": team_id,
    }
    if state_id:
        base["stateId"] = state_id
    if parent_id:
        base["parentId"] = parent_id

    candidate_inputs: List[Dict[str, Any]] = []
    if template_id:
        candidate_inputs.append({**base, "templateId": template_id})
        candidate_inputs.append({**base, "issueTemplateId": template_id})
    candidate_inputs.append(base)

    last_error: Optional[Exception] = None
    for issue_input in candidate_inputs:
        try:
            payload = linear_request(api_key, mutation, {"input": issue_input}).get(
                "issueCreate", {}
            )
            if not payload.get("success"):
                raise RuntimeError("issueCreate returned success=false")
            created_issue = payload.get("issue")
            if not created_issue:
                raise RuntimeError("issueCreate returned no issue")
            return created_issue
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Failed creating Linear issue: {last_error}")


# Subtickets created under the main release issue (same assignee / team / state).
AIDEN2_SUBTICKET_SUFFIXES = (
    "Automation Runs",
    "Validation",
    "Aiden2 Changelog",
)


def aiden2_subticket_titles(release_kind: str, release_tag: str) -> List[str]:
    """Titles for Aiden2 release follow-up subtickets."""
    kind = (release_kind or "weekly").strip().lower()
    kind_label = "Weekly" if kind == "weekly" else "Monthly"
    tag = (release_tag or "").strip() or "unknown"
    prefix = f"[Aiden2][{kind_label} Release {tag}]"
    return [f"{prefix} {suffix}" for suffix in AIDEN2_SUBTICKET_SUFFIXES]


def create_aiden2_subtickets(
    api_key: str,
    parent_id: str,
    release_kind: str,
    release_tag: str,
    assignee_id: str,
    team_id: str,
    state_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Create the three Aiden2 subtickets under the parent release issue."""
    created: List[Dict[str, Any]] = []
    for title in aiden2_subticket_titles(release_kind, release_tag):
        issue = create_issue(
            api_key,
            title=title,
            summary="",
            assignee_id=assignee_id,
            team_id=team_id,
            state_id=state_id,
            parent_id=parent_id,
        )
        created.append(issue)
    return created


# Handles mentioned on the candidate-build comment after ticket create.
CANDIDATE_BUILD_CC_HANDLES = ("saumya-ctr", "abhishes", "cesar")


def candidate_build_comment_body(release_id: str) -> str:
    """Comment body posted on the new release ticket; release_id comes from TO_REF / stackgen_tag."""
    release = (release_id or "").strip() or "unknown"
    cc = "  ".join(f"@{handle}" for handle in CANDIDATE_BUILD_CC_HANDLES)
    return f"Candidate build for the coming release {release}. Cc: {cc}"


def create_issue_comment(api_key: str, issue_id: str, body: str) -> Dict[str, Any]:
    """Post a comment on an existing Linear issue (by UUID)."""
    mutation = """
    mutation CommentCreate($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
        comment { id body url }
      }
    }
    """
    payload = linear_request(
        api_key,
        mutation,
        {"input": {"issueId": issue_id, "body": body}},
    ).get("commentCreate", {})
    if not payload.get("success"):
        raise RuntimeError("commentCreate returned success=false")
    comment = payload.get("comment")
    if not comment:
        raise RuntimeError("commentCreate returned no comment")
    return comment


def run_create_monthly_release(cfg: MonthlyTicketConfig) -> int:
    """Create the Linear release issue from structured config. Used by CLI and release pipeline."""
    input_path = cfg.input_path
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    month_label = cfg.month_label.strip() or datetime.now().strftime("%B %Y")
    release_kind = (cfg.release_kind or "weekly").strip().lower()
    stackgen_tag = cfg.stackgen_tag.strip()
    grouped, ticket_meta, rc_rows, projects = load_release_data(
        input_path,
        services_input_path=cfg.services_input_path,
    )
    ticket_id_to_summary = {
        tid: (meta.get("title") or "") for tid, meta in ticket_meta.items()
    }
    summary = build_summary(
        grouped,
        month_label,
        ticket_id_to_summary,
        release_kind=release_kind,
        stackgen_tag=stackgen_tag,
        projects=projects,
        ticket_meta=ticket_meta,
        rc_rows=rc_rows,
    )
    title = cfg.title.strip() or default_release_title(
        release_kind, month_label, stackgen_tag
    )

    api_key = cfg.api_key if cfg.api_key is not None else os.getenv("LINEAR_API_KEY")

    print("Preparing Linear release ticket")
    print("=" * 60)
    print(f"Input:          {input_path}")
    print(f"Release kind:   {release_kind}")
    print(f"StackGen tag:   {stackgen_tag or '(none)'}")
    print(f"Assignee:       {cfg.assignee_query}")
    print(f"Status:         {cfg.state_name}")
    print(f"Template name:  {cfg.template_name or '(none)'}")
    print(f"Team:           {cfg.team_id.strip() or f'key={cfg.team_key!r}'}")
    print(f"Title:          {title}")
    print()

    comment_body = candidate_build_comment_body(stackgen_tag)
    subticket_titles = aiden2_subticket_titles(release_kind, stackgen_tag)

    if cfg.dry_run:
        print("[DRY RUN] Summary that will be sent:")
        print()
        print(summary)
        print()
        print("[DRY RUN] Comment that will be posted after create:")
        print("-" * 60)
        print(comment_body)
        print("-" * 60)
        print()
        print("[DRY RUN] Aiden2 subtickets that will be created under the release ticket:")
        print("-" * 60)
        for sub_title in subticket_titles:
            print(f"  - {sub_title}")
        print(f"  Assignee: {cfg.assignee_query}")
        print("-" * 60)
        return 0

    if not api_key:
        print("Error: LINEAR_API_KEY not set. Pass --api-key or export LINEAR_API_KEY.", file=sys.stderr)
        return 1

    try:
        assignee_id = resolve_assignee_id(api_key, cfg.assignee_query)
        team_id = cfg.team_id.strip() or resolve_team_id(api_key, cfg.team_key)
        state_id = resolve_state_id(api_key, team_id, cfg.state_name)
        template_id = cfg.template_id.strip()
        if not template_id and cfg.template_name.strip():
            template_id = resolve_template_id(api_key, cfg.template_name)

        print(f"Resolved assignee id: {assignee_id}")
        print(f"Resolved team id:     {team_id}")
        print(f"Resolved state id:    {state_id} ({cfg.state_name})")
        if template_id:
            print(f"Resolved template id: {template_id}")
        print()
        print("=" * 60)
        print("Ticket content")
        print("=" * 60)
        print(f"Title:\n{title}")
        print()
        print(f"Assignee: {cfg.assignee_query}")
        print(f"Status:   {cfg.state_name}")
        print()
        print("Description (issue body):")
        print("-" * 60)
        print(summary)
        print("-" * 60)
        print()
        print("Post-create comment:")
        print("-" * 60)
        print(comment_body)
        print("-" * 60)
        print()
        print("Aiden2 subtickets (parent = release ticket):")
        print("-" * 60)
        for sub_title in subticket_titles:
            print(f"  - {sub_title}")
        print(f"  Assignee: {cfg.assignee_query}")
        print("-" * 60)
        print()

        issue = create_issue(
            api_key,
            title,
            summary,
            assignee_id,
            team_id,
            state_id=state_id,
            template_id=template_id,
        )
        print()
        print("✅ Release ticket created")
        print(f"Identifier: {issue.get('identifier')}")
        print(f"Title:      {issue.get('title')}")
        print(f"URL:        {issue.get('url')}")
        state = (issue.get("state") or {}).get("name")
        if state:
            print(f"Status:     {state}")
        assignee_email = (issue.get("assignee") or {}).get("email")
        if assignee_email:
            print(f"Assignee:   {assignee_email}")

        issue_uuid = str(issue.get("id") or "").strip()
        if not issue_uuid:
            print(
                "⚠️  Ticket created but missing id; skipped comment and subtickets.",
                file=sys.stderr,
            )
            return 0

        comment = create_issue_comment(api_key, issue_uuid, comment_body)
        print()
        print("✅ Candidate-build comment added")
        if comment.get("url"):
            print(f"Comment URL: {comment.get('url')}")

        subtickets = create_aiden2_subtickets(
            api_key,
            parent_id=issue_uuid,
            release_kind=release_kind,
            release_tag=stackgen_tag,
            assignee_id=assignee_id,
            team_id=team_id,
            state_id=state_id,
        )
        print()
        print(f"✅ Created {len(subtickets)} Aiden2 subticket(s)")
        for child in subtickets:
            print(
                f"  - {child.get('identifier')}: {child.get('title')} "
                f"({child.get('url')})"
            )
        return 0
    except Exception as exc:
        print(f"❌ Failed to create ticket: {exc}", file=sys.stderr)
        return 1


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a release ticket in Linear (HZ team). "
            "Title: '[Weekly release] <tag>' / '[Monthly release] <tag>'. "
            "Assignee: gaurav@stackgen.com. Status: Todo."
        )
    )
    parser.add_argument("--input", "-i", default="generated_files/final_tag_differences.json")
    parser.add_argument(
        "--services-input",
        default="",
        help=(
            "Path to input.json with all services (default: "
            "<dir of --input>/input_file/input.json when present)."
        ),
    )
    parser.add_argument("--api-key", default=os.getenv("LINEAR_API_KEY"))
    parser.add_argument(
        "--template-name",
        default="",
        help="Optional Linear issue template name (empty = no template).",
    )
    parser.add_argument("--template-id", default="")
    parser.add_argument(
        "--assignee-query",
        default="gaurav@stackgen.com",
        help="Assignee email or name (default: gaurav@stackgen.com).",
    )
    parser.add_argument(
        "--team-key",
        default="HZ",
        help="Linear team key (issue prefix, e.g. HZ for issues HZ-123). Ignored if --team-id is set.",
    )
    parser.add_argument(
        "--team-id",
        default="",
        help="Linear team UUID. Overrides --team-key when set.",
    )
    parser.add_argument("--title", default="")
    parser.add_argument("--month-label", default="")
    parser.add_argument(
        "--release-kind",
        choices=("weekly", "monthly"),
        default="weekly",
        help="Title flavor: [Weekly release] or [Monthly release].",
    )
    parser.add_argument(
        "--stackgen-tag",
        default="",
        help="Candidate StackGen/appcd-dist tag included in the title (e.g. v2026.7.7).",
    )
    parser.add_argument(
        "--state-name",
        default="Todo",
        help="Linear workflow state name (default: Todo).",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    services_input = Path(args.services_input) if args.services_input.strip() else None
    cfg = MonthlyTicketConfig(
        input_path=Path(args.input),
        api_key=args.api_key,
        template_name=args.template_name,
        template_id=args.template_id,
        assignee_query=args.assignee_query,
        team_key=args.team_key,
        team_id=args.team_id,
        title=args.title,
        month_label=args.month_label,
        release_kind=args.release_kind,
        stackgen_tag=args.stackgen_tag,
        state_name=args.state_name,
        services_input_path=services_input,
        dry_run=args.dry_run,
    )
    return run_create_monthly_release(cfg)


if __name__ == "__main__":
    sys.exit(main())
