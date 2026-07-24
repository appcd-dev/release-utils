# StackGen release-utils

Release engineering toolkit for **StackGen** product cuts.

This repository helps release managers:

1. Compare service versions between two distribution tags (or deployed vs candidate).
2. Extract Linear tickets and projects shipped in that delta.
3. Open a structured **HZ** Linear release issue for QA, Docs, and engineering.
4. Optionally curate changelog / “what’s new” documentation from the same artifacts.

Scripts and Make targets live at the **repository root**. Detailed operator steps for the primary ticket flow are in [`docs/create-release-ticket.md`](docs/create-release-ticket.md).

---

## Who this is for

| Role | Typical use |
|------|-------------|
| Release manager | Run `create-release-ticket`, review the Linear issue, hand off to QA/Docs |
| Docs | Use ticket tables / curated changelog for release notes |
| Engineering | Inspect per-service tag diffs and commit logs |
| CI | Run tag-diff artifact generation without creating Linear issues |

---

## Concepts

| Term | Meaning |
|------|---------|
| **appcd-dist** | Distribution repo whose `.env` pins every service image/tag for a StackGen release |
| **FROM_REF** | Base `appcd-dist` tag or branch (versions you are leaving) |
| **TO_REF** | Candidate `appcd-dist` tag or branch (versions you are shipping) |
| **input.json** | Per-service matrix: repo + `current_tag` → `new_tag` |
| **final_tag_differences.json** | Tickets, Linear states, and projects for bumped services |
| **HZ release ticket** | Linear issue on team **HZ** summarizing the cut for the org |

```text
FROM_REF (.env)  ──compare──►  TO_REF (.env)
        │                            │
        └──────── input.json ────────┘
                      │
              process_all_repos
                      │
         final_tag_differences.json
                      │
              Linear HZ issue
```

---

## Quick start

### 1. Install

```bash
cd release-utils
pip install -r requirements.txt
```

### 2. Credentials

```bash
export GITHUB_PAT=ghp_...          # or GH_TOKEN — private repo / API access
export LINEAR_API_KEY=lin_api_...  # ticket enrichment + issue create
```

Optional local file (never commit secrets):

```bash
make setup   # copies env.template → .env if missing; smoke-tests Linear
```

### 3. Primary workflow — create a release ticket

Compare two `appcd-dist` tags, extract changes, and open (or preview) the Linear issue:

```bash
# Preview (recommended first)
make create-release-ticket \
  FROM_REF=v2026.7.3 \
  TO_REF=v2026.7.7 \
  MONTH_LABEL="July 2026" \
  DRY_RUN=1

# Create the Linear issue for real
make create-release-ticket \
  FROM_REF=v2026.7.3 \
  TO_REF=v2026.7.7 \
  MONTH_LABEL="July 2026"
```

Artifacts are written under **`<FROM_REF>-<TO_REF>/`** (for example `v2026.7.3-v2026.7.7/`), not under a shared `generated_files/` folder.

Full step-by-step documentation: [`docs/create-release-ticket.md`](docs/create-release-ticket.md).

---

## What `create-release-ticket` does

Executed **in order**:

| Step | Action | Result |
|------|--------|--------|
| 1 | Clean the tag-pair directory | Removes `<FROM_REF>-<TO_REF>/` if it exists |
| 2 | `generate-custom-input-file` | Reads both `.env` files → `input.json` |
| 3 | `fetch_changes_between_tags_from_input` | GitHub compares + Linear enrichment |
| 4 | `create-release-ticket-only` | Builds and creates the HZ Linear issue |

### Linear issue shape

| Field | Value |
|-------|--------|
| Team | HZ |
| Title | `[Weekly release] <TO_REF>` or `[Monthly release] <TO_REF>` |
| Assignee | `gaurav@stackgen.com` |
| Status | `Todo` |
| Body | Release Candidate Tags (all services) · AIOS / DPP / CORE tables (linked ID, status, summary) · other teams · Projects (linked) |

### Artifact directory layout

```text
v2026.7.3-v2026.7.7/
├── input_file/
│   └── input.json
├── final_tag_differences.json
├── commit_differences_with_messages.txt
└── projects_list.json
```

### Useful variants

```bash
# Recreate / re-preview the ticket from existing artifacts
make create-release-ticket-only FROM_REF=v2026.7.3 TO_REF=v2026.7.7 DRY_RUN=1

# Custom artifact folder name
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 OUT_DIR=july-rc
```

Parameters: `FROM_REF`, `TO_REF`, `OUT_DIR`, `STACKGEN_TAG`, `RELEASE_KIND`, `MONTH_LABEL`, `ASSIGNEE_QUERY`, `STATE_NAME`, `DRY_RUN=1`. Run `make help` for the full list.

---

## Alternate workflow — deployed vs candidate (`monthly-release`)

Use this when **FROM** should be whatever is currently deployed (via `version.json`) and **TO** is a single `appcd-dist` tag.

```bash
# Artifacts only (no Linear create)
make monthly-release-no-ticket STACKGEN_TAG=v2026.7.7

# Full pipeline including Linear issue (legacy monthly path)
make monthly-release STACKGEN_TAG=v2026.7.7

# Or via Python
python run_monthly_release.py v2026.7.7 --skip-ticket
python run_monthly_release.py v2026.7.7 --dry-run-ticket
```

Defaults:

- Deployed versions: `https://cloud.stackgen.com/version.json` (override with `VERSION_JSON_URL` / `--version-json-url`)
- Candidate versions: raw `.env` at `https://raw.githubusercontent.com/appcd-dev/appcd-dist/<STACKGEN_TAG>/.env`
- Artifacts: `generated_files/`

---

## Makefile reference

Run `make help` from the repo root for the live list.

| Target | Purpose |
|--------|---------|
| `make setup` | Create `.env` from template; test Linear |
| `make create-release-ticket FROM_REF=… TO_REF=…` | **Primary:** clean → input → fetch → Linear ticket |
| `make create-release-ticket-only FROM_REF=… TO_REF=…` | Linear ticket from existing tag-pair artifacts |
| `make generate-custom-input-file FROM_REF=… TO_REF=…` | Build `input.json` from two `appcd-dist` refs |
| `make fetch_changes_between_tags_from_input` | Process `input.json` → ticket/project JSON |
| `make generate-input STACKGEN_TAG=…` | `version.json` + candidate `.env` → `input.json` |
| `make generate-input-custom STACKGEN_TAG=…` | Interactive `version.json` URL picker |
| `make full-workflow STACKGEN_TAG=…` | `generate-input` + fetch |
| `make monthly-release STACKGEN_TAG=…` | Clean → prod input → fetch → Linear |
| `make monthly-release-no-ticket STACKGEN_TAG=…` | Same without Linear create |
| `make clean` | Remove `generated_files/` (or `GENERATED_DIR=…`) |
| `make test-linear` | Linear API connectivity check |
| `make config` | Print effective Make variables |

Override the artifact root for shared targets with `GENERATED_DIR=…` (defaults to `generated_files`).

---

## Repository layout

```text
release-utils/
├── Makefile                          # Operator entry points
├── docs/
│   └── create-release-ticket.md      # Full create-release-ticket sequence
├── .cursor/skills/build-changelog/   # Cursor skill for curated changelogs
├── release_pipeline/                 # monthly-release orchestration
├── generate_custom_input_file.py     # FROM_REF / TO_REF → input.json
├── generate_input_json.py            # version.json + .env → input.json
├── process_all_repos.py              # Tag compares + Linear enrichment
├── create_monthly_release_ticket.py  # HZ Linear issue body + create
├── run_monthly_release.py            # One-shot monthly pipeline
├── build_project_changelogs.py       # Per-project Markdown from GitHub Releases
├── compare_tags.py                   # Single-repo GitHub compare helper
├── fetch_version.py / compare_versions.py
├── env.template
└── .github/workflows/tags-diff-release.yml
```

---

## Script catalog

### Core release path

| Script | Role |
|--------|------|
| `generate_custom_input_file.py` | Compare two `appcd-dist` `.env` refs into `input.json` |
| `generate_input_json.py` | Build `input.json` from deployed `version.json` + candidate `.env` |
| `process_all_repos.py` | For each bumped service: GitHub compare, ticket extract, Linear enrich |
| `create_monthly_release_ticket.py` | Format and create the HZ Linear release issue |
| `run_monthly_release.py` | Orchestrate clean → input → fetch → optional ticket |

### Supporting utilities

| Script | Role |
|--------|------|
| `build_project_changelogs.py` | Per Linear-prefix changelogs from GitHub Release notes |
| `compare_tags.py` | Low-level GitHub tag compare |
| `fetchTicketChangesInBuildsForRepo.py` | Single-repo ticket extraction |
| `verify_latest_tags_vs_appcd_dist_env.py` | Latest GitHub tags vs `appcd-dist` `main` `.env` |
| `fetch_version.py` / `fetch_version_json.py` | Fetch environment `version.json` |
| `compare_versions.py` | Deployed versions vs a repo `.env` |
| `parse_ui_changes_tickets.py` / `scan_ticket_formats.py` | Ticket ID parsing helpers |
| `test_linear_api.py` | Linear API smoke test |
| `generate_whats_new_pdf.py` | PDF “what’s new” (content curated separately) |

---

## Curated changelogs (Cursor)

For documentation-oriented release notes (highlights/fixes, not the full ticket dump), use the project skill:

**.cursor/skills/build-changelog/**

In Cursor chat, say `build-changelog` (optionally with a release name and audience). The skill reads tag-diff JSON, uses `gh` / GitHub Releases where needed, and writes curated Markdown under an output directory.

Linear ticket creation remains a separate, explicit step (`create-release-ticket` or the skill’s ticket instructions).

---

## GitHub Actions

Workflow: **Monthly release (tag diff)** — `.github/workflows/tags-diff-release.yml`

- Trigger: manual (`workflow_dispatch`)
- Inputs: `stackgen_candidate_tag` (required); optional `version_json_url`
- Runs: `python run_monthly_release.py "<tag>" --skip-ticket`
- Uploads artifact: `monthly-release-generated-files` from `generated_files/`

Repository secrets:

- **GITHUB_PAT** — required for GitHub API access  
- **LINEAR_API_KEY** — optional; richer ticket/project fields in the JSON  

---

## Security

- Store tokens in environment variables or CI secrets only. Never commit `.env` or API keys.
- GitHub credentials need **read** access to the service repos you compare and to `appcd-dist`.
- Linear keys need permission to read issues/projects and to create issues on team **HZ**.
- Prefer `DRY_RUN=1` before the first live `issueCreate` for a new cut.

---

## Further reading

| Document | Contents |
|----------|----------|
| [`docs/create-release-ticket.md`](docs/create-release-ticket.md) | Operator guide: full descending sequence for `create-release-ticket` |
| `make help` | Live Make target and parameter list |
| `.cursor/skills/build-changelog/SKILL.md` | Curated changelog skill instructions |

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `FROM_REF and TO_REF are required` | Pass both on the Make command line |
| Empty or missing tags in `input.json` | Confirm refs exist on `appcd-dist` and `version_key` names match `.env` |
| GitHub 404 / rate limit | Set `GITHUB_PAT` / `GH_TOKEN`; confirm repo access |
| No Linear titles / cannot create issue | Export `LINEAR_API_KEY`; run `make test-linear` |
| Ticket-only cannot find JSON | Ensure `<FROM_REF>-<TO_REF>/final_tag_differences.json` exists, or pass `GENERATED_DIR` / `OUT_DIR` |
