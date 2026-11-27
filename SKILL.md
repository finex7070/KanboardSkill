---
name: kanboard
description: "Read and update a Kanboard instance through its JSON-RPC API instead of asking for copy-paste. Use WHENEVER a Kanboard ticket, card, task or subtask comes up: a ticket number like #1234 in a commit subject, branch or message, 'what does ticket X say', the subtask list of a card, moving a card to another column, setting a subtask to todo/doing/done, adding a status comment with commit hashes after finished work, creating subtasks, or searching cards. Also handles the optional plugins SubtaskDescription (subtask descriptions, field due_description), SSS_SubTaskTags (subtask tags, field subtask_tags) and SSS_Filters (subtask-tag:/subtask-status:/subtask-assignee: search) when the server has them, and helps when writing code that talks to the Kanboard API. Do NOT use for Kanboard plugin PHP development (templates, hooks, schema) beyond the API surface, and not for server setup."
---

# kanboard

Kanboard's API is small but full of quiet failures: writes that answer `false` instead of
an error, parameters that are dropped without a word, plugin fields the core methods do
not know. This skill answers from the real board and writes through the right methods.

## 1. Config and safety

`python <skill>/scripts/kanboard.py <command> ...` reads `KANBOARD_URL`, `KANBOARD_TOKEN`,
`KANBOARD_USER`, `KANBOARD_USER_ID`, `KANBOARD_PROJECT_ID` from a `.kanboard.env` or
`dev-paths.ps1` found upwards from the working directory, or from env vars - details in
`references/api.md` §1.

- **Never print, log, echo or commit the token.** Check the config by running a read
  command (`plugins`), not by showing the file.
- **401** = wrong or expired token: tell the user to renew it (`references/api.md` §1).
  Do not search the machine for other credentials.
- **Reading** is always fine. **Writing** (status, move, comment, add-subtask, describe,
  tag, `call --write`) changes a shared board with no undo: do it when the user asked for
  it or the project's CLAUDE.md / memory authorises it. Otherwise show `--dry-run` and ask.
- Read the ids first (`task <id>` / `subtasks <id>`), then write. Write card text in the
  language the board already uses.

## 2. Commands

| Command | Use it for |
|---|---|
| `task <task_id>` | **the default.** Card, task tags, subtasks (status, assignee, and with the plugins tags and first line of the description), comments. `--full` for whole descriptions |
| `subtasks <task_id>` | only the subtask list. `--full` |
| `subtask <subtask_id>` | one subtask in detail |
| `board [project]` / `users [project]` / `tags [project]` | columns with open cards / member ids / tag ids (project + global) |
| `search "<query>" [--project P]` | Kanboard filter syntax |
| `plugins [project]` | Kanboard version and which optional plugins exist. **Run it once before using plugin features on an unknown server** |
| `call <method> k=v ...` | any other API method; add `--write` for changing ones |
| `status <task> <subtask> todo\|doing\|done` | WRITE. Assigns you unless `--no-assign` |
| `move <task> <column>` | WRITE. Column title or id, same swimlane |
| `comment <task> --file note.md` | WRITE. UTF-8 Markdown, `--text` for one-liners |
| `add-subtask <task> "<title>" [--description-file f] [--tags A,B] [--assign]` | WRITE. `--description` / `--tags` need the plugins |
| `describe <task> <subtask> --file f` / `--clear` | WRITE. Needs SubtaskDescription |
| `tag set\|add\|remove <subtask> <tags...>` | WRITE. Needs SSS_SubTaskTags |

Every write takes `--dry-run`; `--json` gives raw API output on read commands. Commands
that need a missing plugin stop **before** writing anything and name the plugin.

## 3. Core rules

- Many methods report failure as `"result": false`, not as an error. The client treats
  `false` from a write as a failure; when writing your own code, do the same.
- `updateSubtask` needs `id` **and** `task_id`; a mismatched pair returns `false`.
- `moveTaskPosition` needs the task's current `swimlane_id`, or the card changes lanes.
- `createComment` needs a `user_id`, also with the application token.
- `updateTask(tags)` / `setTaskTags` replace all task tags.
- With a personal token an unknown task/subtask id answers **HTTP 403**, not `null`.
- Subtask status: 0 todo, 1 in progress, 2 done.

## 4. Optional plugins

Only when `plugins` says *yes*. Full reference: `references/subtask-plugins.md`.

- **Core `createSubtask` / `updateSubtask` ignore `due_description` and `subtask_tags`.**
  Use the plugin methods; core `updateSubtask` keeps both fields intact.
- **No method creates a subtask with description AND tags.** `add-subtask` creates with
  `createSubtaskWithDescription`, then calls `setSubtaskTags`. If the second call fails,
  the subtask exists without tags: report the id, retry only the tag call.
- `subtask_tags` is a **CSV of tag ids** (`"12,15"`); `getSubtaskTags` maps them to names.
- **Unknown tag names are created as project tags** - check `tags <project>` first. A
  digit-only value is an id. Subtask tags are also added to the parent card, never removed.
- Without SSS_Filters, `subtask-*:` search terms are searched as title text and match
  nothing; filter `getAllSubtasks` rows yourself.

## 5. Project conventions

Ticket formats in commit subjects, which project/columns belong to a repo, and whether
Claude may update cards on its own are **per project**: read them from the project's
CLAUDE.md or memory. Ask the board (`board`, `users`, `tags`) for ids instead of guessing.

## Notes

- Python 3.9+, stdlib only. UTF-8 in and out (umlauts survive Windows consoles).
- API methods, parameters, error shapes, PowerShell pitfalls: `references/api.md`.
- Plugin methods, field formats, combining them, filter syntax: `references/subtask-plugins.md`.
- Answer in the user's language.
