---
name: build-changelog
description: >-
  Build curated StackGen release changelogs and create the HZ Linear release
  ticket (weekly style like HZ-809) from tag-diff artifacts using gh CLI,
  build_project_changelogs.py, and create_monthly_release_ticket.py. Use when
  the user says build-changelog, build changelog, release changelog, release
  notes, what's new, create release ticket, weekly release ticket, Linear
  release ticket, or asks to document changes between service tags for release
  documentation.
---

# Build Changelog

Act as a StackGen release manager. Produce a **curated, documentation-ready** changelog — not a raw ticket dump — and optionally create the **HZ Linear release ticket**.

## When invoked

1. Read this skill fully, then [reference.md](reference.md). For Linear ticket creation, also read [linear-ticket.md](linear-ticket.md).
2. Work from the `release-utils` repo root.
3. Execute the workflow; write outputs under `generated_files/release_changelog/` (or a user-specified dir).
4. If the user asked to create a Linear ticket (or `build-changelog` + create ticket), run the ticket step per [linear-ticket.md](linear-ticket.md).
5. Reply briefly with output paths, ticket URL (if created), + a short executive summary.

## Inputs

| File | Role |
|------|------|
| `generated_files/input_file/input.json` | Service → current/new tags |
| `generated_files/final_tag_differences.json` | Tickets, Linear states, projects |

Optional user overrides: release name / `STACKGEN_TAG`, audience (`customer` \| `internal` \| `both`, default `both`), project prefixes, output dir, `create_linear_ticket` (default: only when asked), `dry_run_ticket` (default true unless user says create for real).

If inputs are missing, stop and tell the user to run `make full-workflow` / `process_all_repos.py` first.

## Outputs

1. `COMPONENT_VERSIONS.md` — bumped services only
2. `PROJECT_CHANGELOGS/` — from `build_project_changelogs.py` when tokens allow
3. `RELEASE_NOTES.md` — **primary changelog deliverable**
4. `TICKET_APPENDIX.md` — full ticket index + include/exclude decisions
5. Linear issue on team **HZ** (when requested) — title `[Weekly release] <tag>`, assignee `gaurav@stackgen.com`, status `Todo`, plus three Aiden2 subtickets (Automation Runs, Validation, Aiden2 Changelog)

## Hard rules

- Do **not** invent features; every highlight must trace to GitHub release notes, compare commits/PRs, or tickets in the JSON.
- Scope = services where `current_tag != new_tag` and both are real tags (not `main` / empty).
- Prefer themes over repos in Highlights; collapse cross-service duplicates.
- Exclude noise IDs (e.g. `UTF-8`, `UTF-16`, stub `DEP-02` / `INT-03`).
- Investigate thin services (`ticket_count == 0` or empty release bodies) via `gh compare`.
- Prefer `gh` for GitHub; use `GITHUB_PAT` / `GH_TOKEN` for the Python script; **`LINEAR_API_KEY` required to create the Linear ticket**.
- **Never create a Linear issue without explicit user intent.** Prefer `--dry-run` first; only omit dry-run when the user clearly asked to create the ticket.
- Do not commit, push, or open PRs unless asked.

## Quick workflow

```
Progress:
- [ ] 1. Scope bumped services from input.json
- [ ] 2. Inventory + triage tickets from final_tag_differences.json
- [ ] 3. Fetch GitHub release notes / compare via gh
- [ ] 4. Run build_project_changelogs.py (if token available)
- [ ] 5. Curate Highlights / Fixes / Follow-ups by audience
- [ ] 6. Write RELEASE_NOTES.md + appendix + versions
- [ ] 7. Self-check (see reference.md)
- [ ] 8. Linear release ticket (when asked) — dry-run then create (see linear-ticket.md)
```

## Key commands

```bash
# Full pipeline: clean → custom input → fetch → Linear ticket
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 MONTH_LABEL="July 2026" DRY_RUN=1

# Create for real (only after user confirms)
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 MONTH_LABEL="July 2026"
```

## Additional resources

- Changelog steps + `RELEASE_NOTES.md` template: [reference.md](reference.md)
- Linear ticket title/body + safety rules: [linear-ticket.md](linear-ticket.md)
- Standalone prompt: `prompts/build_release_changelog_agent.md`
