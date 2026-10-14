#!/usr/bin/env python3
"""
Generate input JSON by comparing appcd-dist .env between two refs.

This script fetches appcd-dev/appcd-dist/.env at:
- a "from" ref (used as current_tag)
- a "to" ref (used as new_tag)

Then it writes a file compatible with process_all_repos.py.
"""

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional

import requests

from generate_input_json import SERVICE_VERSION_MAP, resolve_env_version_tag


DEFAULT_REPO = "appcd-dev/appcd-dist"
DEFAULT_OUTPUT = "generated_files/input_file/input.json"


def _github_headers() -> Dict[str, str]:
    token = os.getenv("GITHUB_PAT") or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def parse_env_file(content: str) -> Dict[str, str]:
    env_vars: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$", line)
        if not match:
            continue
        key, value = match.groups()
        env_vars[key] = value.strip().strip('"').strip("'")
    return env_vars


def fetch_env_content(repo: str, ref: str, timeout: int) -> str:
    # Prefer raw URL for speed; if it fails, use GitHub contents API for better diagnostics.
    raw_url = f"https://raw.githubusercontent.com/{repo}/{ref}/.env"
    try:
        raw_resp = requests.get(raw_url, timeout=timeout)
        if raw_resp.status_code == 200 and raw_resp.text:
            return raw_resp.text
    except requests.RequestException:
        pass

    api_url = f"https://api.github.com/repos/{repo}/contents/.env"
    resp = requests.get(
        api_url,
        headers=_github_headers(),
        params={"ref": ref},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    content = data.get("content", "")
    encoding = data.get("encoding", "")
    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8")
    if content:
        return content
    raise ValueError(f"Received empty .env content for {repo}@{ref}")


def build_input(from_env: Dict[str, str], to_env: Dict[str, str]) -> list:
    rows = []
    for service_name, service_info in SERVICE_VERSION_MAP.items():
        version_key = service_info["version_key"]
        repository = service_info["repository"]
        rows.append(
            {
                "service": service_name,
                "repository": repository,
                "version_key": version_key,
                "current_tag": resolve_env_version_tag(
                    from_env, version_key, repository
                ),
                "new_tag": resolve_env_version_tag(to_env, version_key, repository),
            }
        )
    return rows


def write_output(output_path: str, rows: list, pretty: bool) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        else:
            json.dump(rows, f, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate input file by comparing appcd-dist .env at two refs",
    )
    parser.add_argument("--from-ref", required=True, help="Base ref/tag/branch (current_tag source)")
    parser.add_argument("--to-ref", required=True, help="Target ref/tag/branch (new_tag source)")
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"Repository containing .env (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output file path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--pretty", action="store_true", help="Pretty print JSON output")
    args = parser.parse_args()

    try:
        print(f"Fetching .env from {args.repo}@{args.from_ref} ...")
        from_content = fetch_env_content(args.repo, args.from_ref, args.timeout)
        print(f"Fetching .env from {args.repo}@{args.to_ref} ...")
        to_content = fetch_env_content(args.repo, args.to_ref, args.timeout)

        from_env = parse_env_file(from_content)
        to_env = parse_env_file(to_content)
        rows = build_input(from_env, to_env)
        write_output(args.output, rows, args.pretty)
    except requests.HTTPError as e:
        print(f"❌ GitHub request failed: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"❌ Network error while fetching .env: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to generate custom input file: {e}", file=sys.stderr)
        sys.exit(1)

    changed = sum(1 for row in rows if row["current_tag"] != row["new_tag"])
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"✅ Generated: {args.output}")
    print(f"Refs compared: {args.from_ref} → {args.to_ref}")
    print(f"Services mapped: {len(rows)} (changed: {changed})")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
