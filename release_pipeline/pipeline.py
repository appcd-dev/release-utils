"""Orchestrate monthly release: clean → generate input → fetch changes → create Linear ticket."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from . import config
from .constants import (
    LINEAR_TICKET_DETAIL_CREATED,
    LINEAR_TICKET_DETAIL_DRY_RUN,
    LINEAR_TICKET_DETAIL_SKIPPED,
    STEP_CREATE_LINEAR_TICKET,
)
from .steps import (
    StepResult,
    find_step_result,
    skipped_linear_ticket_step,
    step_clean_generated,
    step_create_monthly_release_ticket,
    step_fetch_ticket_changes,
    step_generate_input,
)

if TYPE_CHECKING:
    from create_monthly_release_ticket import MonthlyTicketConfig


def _final_status_line(*, skip_ticket_step: bool, step_results: List[StepResult]) -> str:
    """Single-line summary of whether a Linear issue was created."""
    if skip_ticket_step:
        return (
            "Linear monthly release ticket: not created "
            "(step 4 skipped with --skip-ticket)"
        )

    ticket_step = find_step_result(step_results, STEP_CREATE_LINEAR_TICKET)
    if ticket_step is None:
        return "Linear monthly release ticket: unknown"

    detail_text = ticket_step.detail or ""
    detail_lower = detail_text.lower()

    if LINEAR_TICKET_DETAIL_SKIPPED.lower() in detail_lower or "skipped" in detail_lower:
        return "Linear monthly release ticket: not created (step skipped)"

    if ticket_step.ok and LINEAR_TICKET_DETAIL_DRY_RUN.lower() in detail_lower:
        return (
            "Linear monthly release ticket: not created "
            "(dry-run only; no issueCreate)"
        )

    if ticket_step.ok and detail_text == LINEAR_TICKET_DETAIL_CREATED:
        return "Linear monthly release ticket: created"

    if not ticket_step.ok:
        return "Linear monthly release ticket: not created (failed — see logs above)"

    return f"Linear monthly release ticket: see step 4 — {ticket_step.detail}"


def _print_section_banner(title: str) -> None:
    rule = "━" * 60
    print(f"\n{rule}\n{title}\n{rule}\n")


def run_monthly_release_pipeline(
    stackgen_tag: str,
    *,
    project_root: Optional[Path] = None,
    version_json_url: Optional[str] = None,
    skip_ticket: bool = False,
    ticket_config: Optional["MonthlyTicketConfig"] = None,
) -> int:
    """
    Run steps 1–3 always; step 4 when skip_ticket is False.

    If ticket_config is None and skip_ticket is False, defaults to final_tag_differences.json under root.
    """
    from create_monthly_release_ticket import MonthlyTicketConfig

    root_path = (project_root or config.project_root()).resolve()

    if not stackgen_tag or not stackgen_tag.strip():
        print("Error: stackgen_tag is required (e.g. v2026.2.7)", file=sys.stderr)
        return 1

    resolved_ticket_config: Optional[MonthlyTicketConfig] = ticket_config
    if not skip_ticket and resolved_ticket_config is None:
        resolved_ticket_config = MonthlyTicketConfig(
            input_path=config.final_tag_differences_path(root_path),
            release_kind="weekly",
            stackgen_tag=stackgen_tag.strip(),
        )
    elif (
        not skip_ticket
        and resolved_ticket_config is not None
        and not (resolved_ticket_config.stackgen_tag or "").strip()
    ):
        resolved_ticket_config.stackgen_tag = stackgen_tag.strip()

    step_results: List[StepResult] = []

    try:
        _print_section_banner(f"Step 1 — Clean {config.GENERATED_DIR}")
        step_results.append(step_clean_generated(root_path))

        tag_label = stackgen_tag.strip()
        version_url_effective = (
            (version_json_url or "").strip() or config.VERSION_JSON_PRODUCTION
        )
        version_summary = (
            "production version.json"
            if version_url_effective == config.VERSION_JSON_PRODUCTION
            else version_url_effective
        )
        _print_section_banner(
            f"Step 2 — Generate input ({version_summary} + appcd-dist @ {tag_label})"
        )
        step_results.append(
            step_generate_input(
                root_path,
                stackgen_tag,
                version_json_url=version_json_url,
            )
        )

        _print_section_banner("Step 3 — Fetch tickets and details")
        step_results.append(step_fetch_ticket_changes(root_path))

        if skip_ticket:
            _print_section_banner("Step 4 — Skipped (--skip-ticket)")
            step_results.append(skipped_linear_ticket_step())
        else:
            assert resolved_ticket_config is not None
            config_for_ticket = resolved_ticket_config
            if not config_for_ticket.input_path.is_absolute():
                config_for_ticket = replace(
                    config_for_ticket,
                    input_path=(root_path / config_for_ticket.input_path).resolve(),
                )
            _print_section_banner("Step 4 — Create monthly release ticket in Linear")
            step_results.append(step_create_monthly_release_ticket(config_for_ticket))
    except Exception as exc:
        print(f"\n❌ Pipeline aborted: {exc}", file=sys.stderr)
        return 1

    _print_section_banner("Pipeline summary")
    for step_result in step_results:
        print(step_result)

    _print_section_banner("Final status")
    print(
        _final_status_line(
            skip_ticket_step=skip_ticket,
            step_results=step_results,
        )
    )

    failed_steps = [result for result in step_results if not result.ok]
    if failed_steps:
        return 1
    print("\n✅ Monthly release pipeline completed successfully.")
    return 0
