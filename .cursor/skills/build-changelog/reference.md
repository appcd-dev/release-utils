# Build Changelog — detailed reference

## Preconditions

- Repo root: `release-utils`
- Auth: `gh` logged in, or `GITHUB_PAT` / `GH_TOKEN`
- Optional: `LINEAR_API_KEY` for richer ticket blurbs
- Default output dir: `generated_files/release_changelog`

## Step 1 — Scope the release surface

From `input.json`, bumped services are those where:

- `current_tag` and `new_tag` are both non-empty
- both are real version tags (not `main`)
- `current_tag != new_tag`

Write `COMPONENT_VERSIONS.md` with only those rows. Cross-check `final_tag_differences.json` → `services[]` with `status: success`. Note mismatches.

## Step 2 — Inventory tickets

From `final_tag_differences.json`:

- Unique tickets from bumped services
- Titles/states from `all_tickets` when present
- Group via `tickets_by_project`
- Deduplicate tickets spanning multiple services

Flag noise / invalid IDs (examples: `UTF-8`, `UTF-16`, URL-stub `DEP-02`, `INT-03`). Exclude from curated notes; list under Ignored IDs if useful.

## Step 3 — Fetch GitHub narratives

For each bumped service:

1. List releases and keep those after `current_tag` through `new_tag` inclusive when possible:

```bash
gh api repos/<owner>/<repo>/releases --paginate \
  --jq '.[] | {tag:.tag_name, name:.name, published_at:.published_at, body:.body}'
```

2. If bodies are empty/thin or `ticket_count == 0`:

```bash
gh api repos/<owner>/<repo>/compare/<current_tag>...<new_tag> \
  --jq '.commits[] | "\(.sha[0:7]) \(.commit.message | split("\n")[0])"'
```

Optionally resolve PR numbers from commit messages with `gh pr view`.

Save raw excerpts under `<output>/raw/<service>.md` when helpful for auditability. Parallelize independent `gh` calls across repos.

## Step 4 — Per-project changelogs

If `GITHUB_PAT` or `GH_TOKEN` is set:

```bash
mkdir -p "<output>/PROJECT_CHANGELOGS"
python build_project_changelogs.py \
  --input generated_files/final_tag_differences.json \
  --projects <prefixes in this release> \
  --output-dir "<output>/PROJECT_CHANGELOGS" \
  --verbose
```

On failure, continue with `gh` notes and document the failure; do not abort.

## Step 5 — Triage for documentation

| Bucket | Criteria |
|--------|----------|
| **Highlights** | User-visible feature/major improvement; Released, Ready for Release, Done, or Deployed to Dev if shipping |
| **Fixes** | Bug-shaped, customer-impacting |
| **Follow-ups** | In Progress / In Review / Backlog / Triage / spikes — only if partial ship or known limitation |
| **Exclude** | Noise IDs, chores, duplicates of an already-covered highlight |

- Audience `customer`: Highlights + Fixes only
- Audience `internal` / `both`: add Known limitations / still in flight
- Group Highlights by **theme** (Knowledge Hub, Slack, OCI/policy UI, SRE onboarding), not by git repo

## Step 6 — `RELEASE_NOTES.md` template

```markdown
# StackGen Release Notes — <RELEASE_NAME>

_Generated: <ISO timestamp>_
_Sources: generated_files/input_file/input.json, generated_files/final_tag_differences.json, GitHub Releases / compare_

## Component versions

| Service | Repository | From | To |
|---------|------------|------|----|
| ... | ... | ... | ... |

## Highlights

- **Short title** (`TICKET-123`) — one sentence of impact. _Services: ui, appcd_

## Fixes

- **Short title** (`TICKET-123`) — one sentence. _Services: ..._

## Known limitations / follow-ups

- ...

## Gaps & verification notes

- Tag bumps with no tickets / empty release notes (and what compare found)
- Duplicate tickets collapsed across services
- Items excluded as noise

## See also

- Per-project changelogs in `PROJECT_CHANGELOGS/`
- Full ticket appendix: `TICKET_APPENDIX.md`
```

Writing rules:

- One line per curated item
- Linear ID in backticks
- Prefer clear language over env-prefixed QA titles when release notes are better
- Prefer GitHub release-note wording over Linear title when both exist
- Cross-service tickets once, with all services listed

## Step 7 — `TICKET_APPENDIX.md`

Sorted unique tickets: ID, title, state, services, include/exclude decision.

## Step 8 — Self-check

- [ ] Every bumped service in Component versions
- [ ] No unchanged service listed as shipping
- [ ] No fabricated tickets or features
- [ ] Thin services investigated via `gh compare`
- [ ] Noise IDs excluded from Highlights/Fixes
- [ ] Duplicates not repeated as separate highlights
- [ ] Chat summary lists output paths and any auth/API blockers

## Step 9 — Create Linear release ticket (when asked)

Follow [linear-ticket.md](linear-ticket.md). Reference content/style: [HZ-809](https://linear.app/stackgen/issue/HZ-809/weekly-release-july-2026-v202677).

```bash
# Always dry-run first unless user already confirmed create
make create-release-ticket STACKGEN_TAG=<tag> MONTH_LABEL="<Month Year>" DRY_RUN=1
# Then, only if user wants it created:
make create-release-ticket STACKGEN_TAG=<tag> MONTH_LABEL="<Month Year>"
```

Requires `LINEAR_API_KEY`. Save the returned issue URL into the chat summary.

## Chat response shape

Keep it short:

1. Paths to generated files (`RELEASE_NOTES.md` first)
2. Linear ticket URL (or dry-run confirmation) if that step ran
3. 5–10 line executive summary of the release
4. Gaps / excluded noise / auth failures if any
