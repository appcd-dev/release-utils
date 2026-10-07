# StackGen release ticket workflow

This document describes the **create-release-ticket** pipeline used by release managers to compare two `appcd-dist` tags, extract Linear tickets and projects, and open an HZ release issue in Linear.

Actions are listed in **execution order**, from the top-level Make target down through each nested step.

---

## 0. Prerequisites

Before you run the pipeline:

1. Work from the `release-utils` repository root.
2. Install Python dependencies: `pip install -r requirements.txt`.
3. Authenticate to GitHub for private repos (`gh auth login` and/or `export GITHUB_PAT=…`).
4. Export a Linear API key: `export LINEAR_API_KEY=lin_api_…`.
5. Identify the two `appcd-dist` refs to compare:
   - **FROM_REF** — currently shipping / previous candidate tag  
   - **TO_REF** — new release candidate tag  

---

## 1. `create-release-ticket` (entry point)

**Purpose:** End-to-end weekly or monthly release ticket creation.

**Command:**

```bash
make create-release-ticket \
  FROM_REF=v2026.7.3 \
  TO_REF=v2026.7.7 \
  MONTH_LABEL="July 2026"
```

**Preview only (no Linear issueCreate):**

```bash
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 DRY_RUN=1
```

### 1.1 Inputs accepted by this target

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `FROM_REF` | Yes | — | Base `appcd-dist` tag/branch (current versions) |
| `TO_REF` | Yes | — | Candidate `appcd-dist` tag/branch (new versions) |
| `OUT_DIR` | No | `<FROM_REF>-<TO_REF>` | Artifact directory (e.g. `v2026.7.3-v2026.7.7`) |
| `STACKGEN_TAG` | No | `TO_REF` | Tag shown in the Linear issue title |
| `RELEASE_KIND` | No | `weekly` | `weekly` or `monthly` |
| `MONTH_LABEL` | No | _(empty)_ | Human label, e.g. `July 2026` |
| `ASSIGNEE_QUERY` | No | `gaurav@stackgen.com` | Linear assignee |
| `STATE_NAME` | No | `Todo` | Linear workflow state |
| `DRY_RUN=1` | No | off | Print issue body; do not create the issue |

### 1.2 Sequence performed by `create-release-ticket`

The target runs these four steps **in order**:

| Step | Action | Section |
|------|--------|---------|
| 1 | Clean the artifact directory | [2. Clean](#2-clean-artifact-directory) |
| 2 | Generate the service tag matrix | [3. Generate custom input](#3-generate-custom-input-file) |
| 3 | Fetch commits, tickets, and projects | [4. Fetch changes](#4-fetch_changes_between_tags_from_input) |
| 4 | Create the Linear release ticket | [5. Create Linear ticket](#5-create-release-ticket-only--linear-issue) |

### 1.3 Artifact layout produced by this run

All outputs for a given from→to pair live under one directory:

```text
v2026.7.3-v2026.7.7/
├── input_file/
│   └── input.json
├── final_tag_differences.json
├── commit_differences_with_messages.txt
└── projects_list.json
```

---

## 2. Clean artifact directory

**Purpose:** Remove any previous artifacts for this tag pair so the run starts fresh.

**What runs:**

- Deletes `OUT_DIR` (default `<FROM_REF>-<TO_REF>/`).
- Does **not** wipe unrelated directories (for example other tag-pair folders or legacy `generated_files/`).

**Operator note:** Re-running `create-release-ticket` with the same refs regenerates everything under that folder.

---

## 3. `generate-custom-input-file`

**Purpose:** Build the service version matrix by comparing `appcd-dist` `.env` at two refs.

**Underlying script:** `generate_custom_input_file.py`

### 3.1 Actions in order

1. Fetch `.env` from `appcd-dev/appcd-dist` at **FROM_REF**.
2. Fetch `.env` from `appcd-dev/appcd-dist` at **TO_REF**.
3. Map known services (`SERVICE_VERSION_MAP`) to version keys.
4. For each service, record:
   - `current_tag` ← value at FROM_REF  
   - `new_tag` ← value at TO_REF  
5. Write `OUT_DIR/input_file/input.json`.

### 3.2 What `input.json` represents

One row per service, including unchanged services. Example fields:

- `service`, `repository`, `version_key`
- `current_tag`, `new_tag`

This file is the **source of truth** for “what versions are in this candidate.”

---

## 4. `fetch_changes_between_tags_from_input`

**Purpose:** For every service whose tag changed, compare Git history and extract Linear ticket IDs; enrich with Linear metadata and related projects.

**Underlying script:** `process_all_repos.py`

### 4.1 Actions in order

1. Read `OUT_DIR/input_file/input.json`.
2. Skip services where `current_tag == new_tag` (or tags are empty / non-comparable).
3. For each bumped service:
   1. Call GitHub compare between `current_tag` and `new_tag`.
   2. Collect commit messages / PR text.
   3. Extract Linear ticket identifiers (e.g. `AIOS-273`, `CORE-1003`).
   4. Optionally resolve each ticket via Linear API (title, state, priority, project).
4. Aggregate:
   - Per-service ticket lists  
   - Unique tickets across the release (`all_tickets`)  
   - Structured per-ticket meta including Linear priority (`ticket_details`)  
   - Tickets grouped by team prefix (`tickets_by_project`), each group sorted by priority (Urgent → High → Medium → Low → None)  
   - Related Linear projects (`projects`)  
5. Write:
   - `OUT_DIR/final_tag_differences.json` — primary inventory for the ticket body  
   - `OUT_DIR/commit_differences_with_messages.txt` — raw commit audit log  
   - `OUT_DIR/projects_list.json` — projects snapshot (when present)

### 4.2 What release managers use from this step

| Artifact | Use |
|----------|-----|
| `final_tag_differences.json` | Ticket inventory, states, priorities, projects |
| `input.json` | Full component version table (changed + unchanged) |
| Commit diff log | Deep dive / format debugging |

---

## 5. `create-release-ticket-only` → Linear issue

**Purpose:** Create (or preview) the HZ Linear release issue from artifacts already on disk.

**Underlying script:** `create_monthly_release_ticket.py`

This step is invoked automatically as step 4 of `create-release-ticket`. It can also be run alone after a successful pipeline:

```bash
make create-release-ticket-only FROM_REF=v2026.7.3 TO_REF=v2026.7.7 DRY_RUN=1
```

### 5.1 Actions in order

1. Resolve artifact directory (`OUT_DIR` / `<FROM_REF>-<TO_REF>` / `GENERATED_DIR`).
2. Load `final_tag_differences.json` and, when present, `input_file/input.json`.
3. Build the issue **title**:
   - `[Weekly release] <STACKGEN_TAG>` or  
   - `[Monthly release] <STACKGEN_TAG>`
4. Build the issue **description** (see [5.2](#52-issue-description-structure)).
5. If `DRY_RUN=1`: print title + body + post-create comment + Aiden2 subticket titles and stop.
6. Otherwise (requires `LINEAR_API_KEY`):
   1. Resolve assignee (`gaurav@stackgen.com` by default).
   2. Resolve team **HZ**.
   3. Resolve workflow state **Todo**.
   4. Call Linear `issueCreate` for the main release ticket.
   5. Post a comment on the new issue:
      `Candidate build for the coming release <TO_REF>. Cc: @saumya-ctr  @abhishes  @cesar`
      (`<TO_REF>` is the StackGen/candidate tag passed as `--stackgen-tag`, defaulting from `TO_REF`).
   6. Create three Aiden2 subtickets under the release ticket (same assignee / team / status):
      - `[Aiden2][Weekly Release <TO_REF>] Automation Runs`
      - `[Aiden2][Weekly Release <TO_REF>] Validation`
      - `[Aiden2][Weekly Release <TO_REF>] Aiden2 Changelog`
      (For monthly releases the middle label is `Monthly Release`.)
   7. Print identifier + URL (+ comment + subticket confirmation).

### 5.2 Issue description structure

Sections appear in this order, separated by horizontal rules:

1. **Header** — weekly/monthly label, candidate tag, optional month label  
2. **Release Candidate Tags** — table of **all** services  
   - Columns: Component | From | To | Change  
   - Changed rows listed first and marked **Updated** (bold; Linear has no text color)  
   - Unchanged rows show the same tag in From and To  
3. **Aiden2** — tickets from `stackgen-sre-app` + `stackgen-guild`  
   Table: ID (hyperlink) | Status | Summary  
   Tickets sorted by Linear priority (Urgent → High → Medium → Low → None)  
4. **Aiden** — tickets from `aiden-ui` + `aiden` (same table format + priority sort)  
5. **Stackgen Core** — tickets from all remaining services (same table format + priority sort)  
6. **Projects** — table: Project (hyperlink) | State | Progress  

Where Features and Bug fixes can be distinguished, product sections may split into those subsections before the tables (priority order is preserved within each subsection).

### 5.3 Linear issue fields written

| Field | Value |
|-------|--------|
| Team | HZ |
| Title | `[Weekly release] <tag>` or `[Monthly release] <tag>` |
| Assignee | `gaurav@stackgen.com` |
| Status | `Todo` |
| Description | Partitioned markdown with linked tickets and projects |

### 5.4 Aiden2 subtickets

After the main release issue is created, three child issues are opened on the same team with the same assignee and status:

| Title pattern | Purpose |
|---------------|---------|
| `[Aiden2][Weekly Release <tag>] Automation Runs` | Automation run tracking for the cut |
| `[Aiden2][Weekly Release <tag>] Validation` | Validation work for the cut |
| `[Aiden2][Weekly Release <tag>] Aiden2 Changelog` | Aiden2 changelog write-up |

`<tag>` is `STACKGEN_TAG` / `TO_REF`. For monthly releases, `Weekly` becomes `Monthly`.

---

## 6. Related actions (optional / alternate paths)

These are **not** part of the default `create-release-ticket` chain, but support related release work.

### 6.1 `create-release-ticket-only`

Reuses an existing `<FROM_REF>-<TO_REF>/` folder. Use when you already ran the pipeline (or a dry-run) and only need to recreate or re-preview the Linear issue.

### 6.2 `build-changelog` (Cursor skill)

Curates documentation-oriented release notes (`RELEASE_NOTES.md`, etc.) from the same tag-diff artifacts. Prefer this when Docs needs a customer-facing or themed changelog rather than the full Linear inventory.

### 6.3 `monthly-release` / `monthly-release-no-ticket`

Alternate pipeline that builds input from production `version.json` + a single `STACKGEN_TAG` on `appcd-dist`, instead of an explicit FROM_REF→TO_REF `.env` compare.

### 6.4 Legacy `generated_files/` targets

`make generate-input`, `make full-workflow`, and `make clean` (without `GENERATED_DIR`) still use `generated_files/` for older monthly flows. The tag-pair workflow above prefers `<FROM_REF>-<TO_REF>/`.

---

## 7. Recommended operator checklist

1. Confirm **FROM_REF** and **TO_REF** with the release owner.  
2. Dry-run first:  
   `make create-release-ticket FROM_REF=… TO_REF=… DRY_RUN=1`  
3. Review printed title + description (tags, ticket tables, projects).  
4. Create for real (same command without `DRY_RUN=1`).  
5. Open the returned Linear URL; confirm assignee **Todo** and HZ team.  
6. Hand the issue (and optional curated changelog) to Docs / QA.  
7. Keep the `<FROM_REF>-<TO_REF>/` folder as the audit trail for that cut.

---

## 8. Quick reference

```bash
# Full pipeline → artifacts in v2026.7.3-v2026.7.7/ + Linear issue
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 MONTH_LABEL="July 2026"

# Preview only
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 DRY_RUN=1

# Recreate ticket from existing artifacts
make create-release-ticket-only FROM_REF=v2026.7.3 TO_REF=v2026.7.7
```

For Make help: `make help`.
