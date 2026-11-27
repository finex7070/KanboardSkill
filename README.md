# kanboard

A Claude Code skill that lets Claude read and update a [Kanboard](https://kanboard.org)
through its JSON-RPC API instead of asking you to copy-paste tickets:

> **What does ticket #42 say, which subtasks are still open - and after the fix: tick the
> subtask, move the card, leave a comment with the commit hash.**

Kanboard's API is small but full of quiet failures: writes that answer `false` instead of
an error, parameters that are dropped without a word, plugin fields the core methods do
not know. The skill tells Claude which calls to use and ships a client that does it right.

It works with a **plain Kanboard**. When the server has them, it also handles three
optional plugins, detected at run time:

| Plugin | Adds |
|---|---|
| [SubtaskDescription](https://github.com/Shaun-Fong/SubtaskDescription) | a Markdown description per subtask |
| SSS_SubTaskTags | tags per subtask |
| SSS_Filters | `subtask-tag:`, `subtask-status:`, `subtask-assignee:` in the search |

```
$ python scripts/kanboard.py plugins 12
Kanboard v1.2.58
  SubtaskDescription   yes   subtask descriptions (due_description)
  SSS_SubTaskTags      yes   subtask tags (subtask_tags)
  SSS_Filters          no    search filters subtask-status:/subtask-tag:/subtask-assignee:

$ python scripts/kanboard.py subtasks 42
subtasks of #42 (2):
  #1001   doing Login-Screen  (Erika Müller)  [Dev]
         | Validate input, show errors inline
  #1002   todo  Password reset  (unassigned)  [Dev, UI]

$ python scripts/kanboard.py add-subtask 42 "Session timeout" --description-file steps.md --tags Dev --dry-run
DRY-RUN {"method": "createSubtaskWithDescription", "params": {"due_description": "...", "task_id": 42, "title": "Session timeout"}}
DRY-RUN {"method": "setSubtaskTags", "params": {"subtask_id": "<new id>", "tags": ["Dev"]}}
```

## Install

```powershell
.\install.ps1              # -> ~/.claude/skills/kanboard
.\install.ps1 -WhatIf      # dry run
.\install.ps1 -Target <repo>\.claude\skills\kanboard   # project-scoped
```

On macOS / Linux copy `SKILL.md`, `references/` and `scripts/` to `~/.claude/skills/kanboard/`.
Requirements: Python 3.9+, stdlib only.

## Configuration

Nothing is stored in the skill. The client reads, first hit wins per value:

1. `--config <file>`, or the first `.kanboard.env` / `dev-paths.ps1` found from the working
   directory upwards
2. environment variables

```ini
# .kanboard.env - keep it out of version control
KANBOARD_URL=https://kanboard.example.com/jsonrpc.php
KANBOARD_USER=jsonrpc          # application token; or your username for a personal token
KANBOARD_TOKEN=...
KANBOARD_USER_ID=3             # needed for comments and --assign
KANBOARD_PROJECT_ID=12         # optional default for board / users / tags / search
```

`dev-paths.ps1` is the same as PowerShell variables (`$KanboardUrl = "..."`,
`$KanboardToken`, `$KanboardUser`, `$KanboardUserId`, `$KanboardProjectId`), for repos that
already keep per-machine settings that way.

Tokens: application token under *Settings -> API* (user `jsonrpc`), personal token under
*My profile -> API* (user = your username, acts with your project roles).

## Commands

| Read | |
|---|---|
| `task <id>` | card, tags, subtasks, comments |
| `subtasks <id> [--full]` | subtask list |
| `subtask <subtask_id>` | one subtask in detail |
| `board [project]`, `users [project]`, `tags [project]` | columns + open cards, member ids, tag ids |
| `search "<query>" [--project P]` | `searchTasks` with Kanboard filter syntax |
| `plugins [project]` | Kanboard version and detected plugins |
| `call <method> key=value ...` | any API method (`--write` for changing ones) |

| Write (all take `--dry-run`) | |
|---|---|
| `status <task> <subtask> todo\|doing\|done` | assigns you unless `--no-assign` |
| `move <task> <column>` | title or id, keeps the swimlane |
| `comment <task> --file note.md \| --text "..."` | UTF-8 Markdown |
| `add-subtask <task> "<title>" [--assign] [--estimate h] [--status s]` | core |
| `  ... --description[-file] ... --tags A,B` | needs SubtaskDescription / SSS_SubTaskTags |
| `describe <task> <subtask> --text \| --file \| --clear` | needs SubtaskDescription |
| `tag set\|add\|remove <subtask> <tags...>` | needs SSS_SubTaskTags |

`--json` prints raw API output for read commands. A command that needs a missing plugin
stops before writing anything and names the plugin.

## Layout

```
SKILL.md                        trigger + rules Claude follows
references/api.md               auth, config, error shapes, core methods, gotchas
references/subtask-plugins.md   plugin detection, methods, field formats, combining them, filters
scripts/kanboard.py             CLI client (stdlib)
tests/test_kanboard.py          offline tests against a mock JSON-RPC server, with and without plugins
install.ps1                     copies the skill to ~/.claude/skills
```

## Tests

```powershell
python -I tests\test_kanboard.py
```

No network, no credentials. The mock server models a plain Kanboard and one with all three
plugins, following the plugin sources (digit-only tag = id, unknown names created, tags
copied to the card, core subtask methods ignore plugin fields, `id`/`task_id` mismatch
returns `false`, method probe answers -32602 vs -32601). A live check needs a token:
`python scripts/kanboard.py plugins <project>`.

## Gotchas worth knowing

**`false` is not an error.** `updateSubtask` with a subtask id that belongs to another
task, a failed validation, a missing role on some methods: all answer `"result": false`
with HTTP 200. The client turns `false` from a write into a failure.

**Core subtask methods drop plugin fields silently.** `createSubtask(..., due_description=...)`
succeeds and stores no description. Only the plugin methods write those fields - and no
single method takes a description *and* tags, so creating both is two calls.

**A personal token answers unknown ids with HTTP 403**, not `null`: the project-role check
cannot find a project for a task that does not exist.

**Unknown tag names are created.** The tag methods accept names and create missing ones as
project tags, so a typo leaves a junk tag behind.

**Plugin detection without side effects.** Calling a plugin method with no parameters
answers `-32602` if it exists and `-32601` if not - Kanboard checks the name first.
