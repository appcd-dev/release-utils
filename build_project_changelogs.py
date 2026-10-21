#!/usr/bin/env python3
"""
Build per-project release changelogs from GitHub Releases notes.

Reads the tag-diff output (generated_files/final_tag_differences.json by default),
and for each service fetches the GitHub Release notes published between the service's
current_tag and new_tag. Release-note lines are grouped by Linear ticket prefix
(e.g. DPP, AIOS), each referenced ticket is enriched with its Linear title + state,
and one Markdown changelog is written per project.

Environment:
    GITHUB_PAT / GH_TOKEN   GitHub token used for the Releases API (required).
    LINEAR_API_KEY          Linear API key used to enrich ticket titles/state (optional).

Usage:
    python build_project_changelogs.py
    python build_project_changelogs.py --input generated_files/final_tag_differences.json \
        --projects DPP AIOS --output-dir generated_files
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

GITHUB_API = "https://api.github.com"
LINEAR_API = "https://api.linear.app/graphql"

# Ticket IDs: [PROJ-123] or PROJ-123 at a word boundary.
TICKET_PATTERN = re.compile(
    r'\[([A-Z]{2,6}-\d{1,6})\]'
    r'|(?:^|[\s(])([A-Z]{2,6}-\d{1,6})(?=[\s:|\)\]\-,.]|$)'
)


def ticket_ids_in(text: str) -> List[str]:
    """Return ticket IDs found in text, order-preserving and de-duplicated."""
    found: List[str] = []
    for m in TICKET_PATTERN.finditer(text):
        tid = m.group(1) or m.group(2)
        if tid and tid not in found:
            found.append(tid)
    return found


class GitHubReleases:
    """Fetches GitHub Release notes for a repo/tag range."""

    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        self._cache: Dict[str, List[Dict]] = {}

    def list_releases(self, repo: str) -> List[Dict]:
        """All releases for a repo, newest first (API order). Cached per repo."""
        if repo in self._cache:
            return self._cache[repo]
        releases: List[Dict] = []
        page = 1
        while True:
            url = f"{GITHUB_API}/repos/{repo}/releases"
            resp = requests.get(
                url, headers=self.headers,
                params={"per_page": 100, "page": page}, timeout=30,
            )
            if resp.status_code != 200:
                print(f"  ⚠️  Could not list releases for {repo}: "
                      f"HTTP {resp.status_code} {resp.text[:120]}", file=sys.stderr)
                break
            batch = resp.json()
            if not batch:
                break
            releases.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        self._cache[repo] = releases
        return releases

    def release_by_tag(self, repo: str, tag: str) -> Optional[Dict]:
        """Fetch a single release by its tag name."""
        url = f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}"
        resp = requests.get(url, headers=self.headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return None

    def releases_in_range(self, repo: str, from_tag: str, to_tag: str) -> List[Dict]:
        """
        Releases published after `from_tag` up to and including `to_tag`.

        Uses the newest-first list ordering to bound the range. Falls back to the
        single `to_tag` release when the range cannot be bounded.
        """
        releases = self.list_releases(repo)
        by_index = {r.get("tag_name"): i for i, r in enumerate(releases)}
        idx_to = by_index.get(to_tag)
        idx_from = by_index.get(from_tag)

        if idx_to is None:
            single = self.release_by_tag(repo, to_tag)
            if single:
                return [single]
            print(f"  ⚠️  No release found for {repo}@{to_tag}", file=sys.stderr)
            return []

        if idx_from is None:
            print(f"  ⚠️  {repo}: current tag {from_tag} has no release; "
                  f"using only {to_tag}'s notes", file=sys.stderr)
            return [releases[idx_to]]

        # Newest-first: the newer tag has the smaller index.
        lo, hi = sorted((idx_to, idx_from))
        # Exclude the older (from) release, include the newer (to) release.
        return releases[lo:hi]


class LinearEnricher:
    """Resolves Linear ticket title + state. No-op without an API key."""

    def __init__(self, api_key: Optional[str], verbose: bool = False):
        self.api_key = api_key
        self.verbose = verbose
        self._cache: Dict[str, Optional[Dict[str, str]]] = {}

    def details(self, ticket_id: str) -> Optional[Dict[str, str]]:
        if not self.api_key:
            return None
        if ticket_id in self._cache:
            return self._cache[ticket_id]

        query = """
        query IssueByIdentifier($identifier: String!) {
            issue(id: $identifier) {
                identifier
                title
                description
                state { name }
                assignee { name }
                project { name }
            }
        }
        """
        try:
            resp = requests.post(
                LINEAR_API,
                headers={"Authorization": self.api_key, "Content-Type": "application/json"},
                json={"query": query, "variables": {"identifier": ticket_id}},
                timeout=10,
            )
            result = None
            if resp.status_code == 200:
                data = resp.json()
                issue = data.get("data", {}).get("issue") if "errors" not in data else None
                if issue:
                    result = {
                        "id": issue["identifier"],
                        "title": issue.get("title", ""),
                        "description": issue.get("description") or "",
                        "state": issue["state"]["name"] if issue.get("state") else "Unknown",
                        "assignee": issue["assignee"]["name"] if issue.get("assignee") else "Unassigned",
                        "project": issue["project"]["name"] if issue.get("project") else "No Project",
                    }
        except Exception:
            result = None

        self._cache[ticket_id] = result
        return result

    def label(self, ticket_id: str) -> str:
        d = self.details(ticket_id)
        if d and d.get("title"):
            return f"**{ticket_id}**: {d['title']} _({d['state']})_"
        return f"**{ticket_id}**"

    @staticmethod
    def _blurb(description: str, limit: int = 280) -> str:
        """Concise 'what it adds' line from a Linear description (markdown stripped)."""
        text = re.sub(r"```.*?```", " ", description, flags=re.DOTALL)  # code blocks
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)          # links/images
        text = re.sub(r"^\s*[-*+]\s+", " ", text, flags=re.MULTILINE)   # list bullets
        text = re.sub(r"[#>*_`~]+", " ", text)                           # md markup (keep hyphens)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""
        # First sentence, or first `limit` chars on a word boundary.
        sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        out = sentence if len(sentence) <= limit else text
        if len(out) > limit:
            out = out[:limit].rsplit(" ", 1)[0] + "…"
        return out

    def summary(self, ticket_id: str) -> str:
        """One-line explanation of what the ticket adds, from title + description."""
        d = self.details(ticket_id)
        if not d:
            return f"- **{ticket_id}** — _(details unavailable)_"
        title = d.get("title", "") or ticket_id
        blurb = self._blurb(d.get("description", ""))
        state = d.get("state", "Unknown")
        line = f"- **{ticket_id}: {title}** _({state})_"
        if blurb:
            line += f" — {blurb}"
        return line


def prefix_of(ticket_id: str) -> str:
    return ticket_id.split("-", 1)[0]


def build_changelog(input_path: Path, projects: List[str], output_dir: Path,
                    verbose: bool = False) -> List[Path]:
    token = os.getenv("GITHUB_PAT") or os.getenv("GH_TOKEN")
    if not token:
        raise SystemExit("Error: set GITHUB_PAT or GH_TOKEN to call the GitHub Releases API.")

    data = json.loads(input_path.read_text(encoding="utf-8"))
    services = data.get("services", [])

    gh = GitHubReleases(token)
    linear = LinearEnricher(os.getenv("LINEAR_API_KEY"), verbose=verbose)

    projects = [p.upper() for p in projects]
    # project -> list of per-service sections
    per_project: Dict[str, List[Dict]] = defaultdict(list)

    for svc in services:
        repo = svc.get("repo_path")
        from_tag = svc.get("current_tag")
        to_tag = svc.get("new_tag")
        if not repo or not from_tag or not to_tag or from_tag == to_tag:
            continue
        if svc.get("status") not in (None, "success"):
            continue

        if verbose:
            print(f"→ {svc.get('service', repo)}: {from_tag} → {to_tag}")

        releases = gh.releases_in_range(repo, from_tag, to_tag)
        if not releases:
            continue

        # project -> list of (release_tag, note_line) and referenced tickets
        buckets: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        tickets_seen: Dict[str, set] = defaultdict(set)

        for rel in releases:
            rel_tag = rel.get("tag_name", "?")
            body = rel.get("body") or ""
            for raw_line in body.splitlines():
                line = raw_line.rstrip()
                if not line.strip():
                    continue
                ids = ticket_ids_in(line)
                if not ids:
                    continue
                line_prefixes = {prefix_of(t) for t in ids}
                for proj in projects:
                    if proj in line_prefixes:
                        buckets[proj].append((rel_tag, line.strip()))
                        for t in ids:
                            if prefix_of(t) == proj:
                                tickets_seen[proj].add(t)

        for proj in projects:
            if not buckets[proj] and not tickets_seen[proj]:
                continue
            per_project[proj].append({
                "service": svc.get("service", repo),
                "repo": repo,
                "from_tag": from_tag,
                "to_tag": to_tag,
                "release_tags": [r.get("tag_name") for r in releases],
                "tickets": sorted(tickets_seen[proj]),
                "lines": buckets[proj],
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    written: List[Path] = []

    for proj in projects:
        sections = per_project.get(proj, [])
        out_path = output_dir / f"{proj}_changelog.md"
        lines: List[str] = []
        lines.append(f"# {proj} Release Changelog")
        lines.append("")
        lines.append(f"_Generated: {generated_at} — source: GitHub Releases notes_")
        lines.append("")

        if not sections:
            lines.append(f"No {proj} tickets referenced in the release notes for this tag range.")
            lines.append("")
        else:
            for sec in sections:
                lines.append(f"## {sec['service']} ({sec['from_tag']} → {sec['to_tag']})")
                rels = ", ".join(t for t in sec["release_tags"] if t)
                lines.append(f"_Releases: {rels}_")
                lines.append("")
                if sec["tickets"]:
                    lines.append("**Tickets**")
                    lines.append("")
                    for tid in sec["tickets"]:
                        lines.append(f"- {linear.label(tid)}")
                    lines.append("")
                if sec["lines"]:
                    lines.append("**Release note entries**")
                    lines.append("")
                    for rel_tag, note in sec["lines"]:
                        note = note.lstrip("-*• ").strip()
                        lines.append(f"- ({rel_tag}) {note}")
                    lines.append("")

            # Summary: what each ticket adds to the release (unique across services).
            all_tickets = sorted({t for sec in sections for t in sec["tickets"]})
            if all_tickets:
                lines.append("---")
                lines.append("")
                lines.append("## Summary — what each ticket adds to this release")
                lines.append("")
                for tid in all_tickets:
                    lines.append(linear.summary(tid))
                lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        written.append(out_path)
        print(f"✅ Wrote {out_path} ({len(sections)} service section(s))")

    return written


def main():
    parser = argparse.ArgumentParser(
        description="Build per-project (DPP/AIOS) changelogs from GitHub Releases notes.")
    parser.add_argument(
        "--input", default="generated_files/final_tag_differences.json",
        help="Tag-diff JSON produced by the release pipeline.")
    parser.add_argument(
        "--projects", nargs="+", default=["DPP", "AIOS"],
        help="Ticket-prefix projects to build changelogs for (default: DPP AIOS).")
    parser.add_argument(
        "--output-dir", default="generated_files",
        help="Directory to write <PROJECT>_changelog.md files.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Error: input file not found: {input_path}")

    build_changelog(input_path, args.projects, Path(args.output_dir), verbose=args.verbose)


if __name__ == "__main__":
    main()
