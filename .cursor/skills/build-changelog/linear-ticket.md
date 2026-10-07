# Linear release ticket

## Full pipeline (`create-release-ticket`)

```
clean <FROM_REF>-<TO_REF>/
  → generate-custom-input-file
  → fetch_changes_between_tags_from_input
  → create Linear issue
```

Artifacts land in `<FROM_REF>-<TO_REF>/` (e.g. `v2026.7.3-v2026.7.7/`):

```
v2026.7.3-v2026.7.7/
  input_file/input.json
  final_tag_differences.json
  commit_differences_with_messages.txt
  projects_list.json
```

Override with `OUT_DIR=my-dir` if needed.
### Required

| Param | Meaning |
|-------|---------|
| `FROM_REF` | appcd-dist base ref/tag (current) |
| `TO_REF` | appcd-dist candidate ref/tag (new) |

### Optional

| Param | Default | Meaning |
|-------|---------|---------|
| `STACKGEN_TAG` | `TO_REF` | Tag used in Linear title |
| `RELEASE_KIND` | `weekly` | `weekly` \| `monthly` |
| `MONTH_LABEL` | _(empty)_ | e.g. `July 2026` |
| `ASSIGNEE_QUERY` | `gaurav@stackgen.com` | Linear assignee |
| `STATE_NAME` | `Todo` | Linear workflow state |
| `DRY_RUN=1` | off | Preview body; skip `issueCreate` |

```bash
export LINEAR_API_KEY=lin_api_...

make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 MONTH_LABEL="July 2026"
make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 DRY_RUN=1
```

## Ticket-only (reuse artifacts)

```bash
make create-release-ticket-only TO_REF=v2026.7.7 DRY_RUN=1
```

## Fields written to Linear

| Field | Value |
|-------|--------|
| **Assignee** | `gaurav@stackgen.com` |
| **Status** | `Todo` |
| **Title** | `[Weekly release] <TO_REF>` / `[Monthly release] …` |
| **Team** | HZ |
| **Body** | Tag table + product ticket tables (Aiden2 / Aiden / Stackgen Core, priority-sorted) + linked Projects |

### Aiden2 subtickets (auto-created)

After the main issue is created, three child issues are opened under it (same assignee / team / status):

1. `[Aiden2][Weekly Release <TO_REF>] Automation Runs`
2. `[Aiden2][Weekly Release <TO_REF>] Validation`
3. `[Aiden2][Weekly Release <TO_REF>] Aiden2 Changelog`

For monthly releases the middle label uses `Monthly Release`.

## Skill behavior

1. Prefer `make create-release-ticket FROM_REF=… TO_REF=…`
2. Dry-run first unless the user explicitly asked to create
3. Return the new issue URL when created
4. If `LINEAR_API_KEY` is missing, stop and ask for it
