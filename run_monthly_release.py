#!/usr/bin/env python3
"""
End-to-end monthly release flow (run from repository root):

  1. Clean generated_files
  2. Generate input.json — production version.json (override: --version-json-url) + appcd-dist .env at STACKGEN_TAG
  3. Fetch ticket changes → final_tag_differences.json
  4. Create Linear monthly release issue (unless --skip-ticket)

Example:
  python run_monthly_release.py v2026.2.7
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from create_monthly_release_ticket import MonthlyTicketConfig
from release_pipeline import config as pipeline_config
from release_pipeline.pipeline import run_monthly_release_pipeline


def default_project_root() -> Path:
    """Directory containing this script (repository root when script lives at repo root)."""
    return Path(__file__).resolve().parent


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monthly release pipeline: clean → input → tickets → Linear issue",
    )
    parser.add_argument(
        "stackgen_tag",
        help=(
            "appcd-dist tag for candidate .env (e.g. v2026.2.7); "
            "passed to generate_input_json --stackgen-tag"
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Repository root (default: directory containing this script)",
    )
    parser.add_argument(
        "--version-json-url",
        default=None,
        metavar="URL",
        help=(
            "URL for deployed version.json (default: production "
            f"{pipeline_config.VERSION_JSON_PRODUCTION})"
        ),
    )
    parser.add_argument(
        "--skip-ticket",
        action="store_true",
        help="Stop after step 3 (no Linear issue)",
    )
    parser.add_argument(
        "--dry-run-ticket",
        action="store_true",
        help="Step 4: print summary only (no issueCreate)",
    )
    parser.add_argument(
        "--ticket-input",
        type=Path,
        default=None,
        help=(
            "Input JSON for step 4 "
            f"(default: {pipeline_config.FINAL_TAG_DIFF_REL} under project root)"
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("LINEAR_API_KEY"),
        help="Linear API key for step 4",
    )
    parser.add_argument(
        "--template-name",
        default="",
        help="Optional Linear issue template (empty = none)",
    )
    parser.add_argument("--template-id", default="")
    parser.add_argument(
        "--assignee-query",
        default="gaurav@stackgen.com",
        help="Assignee email or name (default: gaurav@stackgen.com)",
    )
    parser.add_argument("--team-key", default="HZ")
    parser.add_argument("--team-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--month-label", default="")
    parser.add_argument(
        "--release-kind",
        choices=("weekly", "monthly"),
        default="weekly",
        help="Title: [Weekly release] <tag> or [Monthly release] <tag>",
    )
    parser.add_argument(
        "--state-name",
        default="Todo",
        help="Linear workflow state (default: Todo)",
    )
    return parser


def main() -> int:
    parsed = build_argument_parser().parse_args()
    root = (parsed.project_root or default_project_root()).resolve()

    monthly_ticket_config = None
    if not parsed.skip_ticket:
        ticket_input_path = parsed.ticket_input
        if ticket_input_path is None:
            ticket_input_path = pipeline_config.final_tag_differences_path(root)
        monthly_ticket_config = MonthlyTicketConfig(
            input_path=ticket_input_path,
            api_key=parsed.api_key,
            template_name=parsed.template_name,
            template_id=parsed.template_id,
            assignee_query=parsed.assignee_query,
            team_key=parsed.team_key,
            team_id=parsed.team_id,
            title=parsed.title,
            month_label=parsed.month_label,
            release_kind=parsed.release_kind,
            stackgen_tag=parsed.stackgen_tag,
            state_name=parsed.state_name,
            dry_run=parsed.dry_run_ticket,
        )

    return run_monthly_release_pipeline(
        parsed.stackgen_tag,
        project_root=root,
        version_json_url=parsed.version_json_url,
        skip_ticket=parsed.skip_ticket,
        ticket_config=monthly_ticket_config,
    )


if __name__ == "__main__":
    sys.exit(main())
