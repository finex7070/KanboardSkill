# Subtask descriptions, subtask tags, subtask filters

All of this is **optional**. A plain Kanboard works with every core command of the skill;
these three plugins add subtask features the core API does not know. The two that store
data use extra columns of the `subtasks` table, so core read methods already return it -
but core write methods do not accept it.

| Plugin | Folder on the server | Column | API |
|---|---|---|---|
| SubtaskDescription ([Shaun-Fong/SubtaskDescription](https://github.com/Shaun-Fong/SubtaskDescription)) | `plugins/Subtaskdescription` (case-sensitive) | `due_description` TEXT, Markdown | 2 methods |
| SSS_SubTaskTags | `plugins/SSS_SubTaskTags` | `subtask_tags` TEXT, CSV of tag ids | 5 methods |
| SSS_Filters | `plugins/SSS_Filters` | - | filter attributes for `searchTasks` |

## 0. Is the plugin there?

`kanboard.py plugins [project]` answers it without changing anything. By hand:

- **Method plugins**: call a plugin method *without parameters*. `-32602 Invalid params`
  = installed, `-32601 Method not found` = not installed. (Kanboard checks the method name
  before the arguments.)
- **Fields**: a `getSubtask` row without `due_description` / `subtask_tags` = not installed.
- **SSS_Filters** has no method. `searchTasks(project_id, "subtask-status:x")`: the plugin
  ignores the invalid value and returns every task of the project; without it the text is
  a title search and returns nothing. Inconclusive on a project without tasks.

Never pass plugin fields to core methods "just in case" - check first.

## 1. Reading

`getSubtask(subtask_id)` and `getAllSubtasks(task_id)` return both fields:

```json
{"id": "1001", "task_id": "42", "title": "AP11-DEV-3 Lobby", "status": "1",
 "user_id": "3", "due_description": "Check with **4 players**", "subtask_tags": "12,15"}
```

- `due_description`: Markdown string, `""` when empty (never null on a current plugin).
- `subtask_tags`: ids only, comma separated, `""` when none. Resolve the names with
  `getSubtaskTags(subtask_id)` -> `{"12": "Bug", "15": "UI"}` (empty: `{}`), or for many
  subtasks at once with `getTagsByProject(project_id)` and a lookup (global tags, project
  id 0, are not in that list - fall back to `getSubtaskTags` for ids it does not know).
  `getSubtaskTags` needs only the project viewer role.

## 2. SubtaskDescription methods

Role: project member.

### createSubtaskWithDescription

`task_id`*, `title`*, `due_description`, `user_id`, `time_estimated`, `time_spent`,
`status` -> new subtask id, or `false`.

```json
{"jsonrpc":"2.0","id":1,"method":"createSubtaskWithDescription",
 "params":{"task_id":42,"title":"Lobby test","due_description":"Check with **4 players**"}}
```

### updateSubtaskDescription

`id`*, `task_id`*, `due_description`* (`""` clears) -> `true` / `false`.
`false` also when `id` does not belong to `task_id`. Changes **only** the description;
title, status, assignee and tags stay.

## 3. SSS_SubTaskTags methods

Role: project member (except `getSubtaskTags`, viewer). `tags` is always a **JSON array**
of names and/or ids: `["Bug", "UI"]`, `[12, 15]`, `["Bug", 15]`. A string instead of an
array is rejected (-32602).

| Method | Parameters | Result |
|---|---|---|
| `getSubtaskTags` | subtask_id | `{id: name}` |
| `createSubtaskWithTags` | task_id*, title*, tags, user_id, time_estimated, time_spent, status | id / false |
| `setSubtaskTags` | subtask_id*, tags* | true / false. Replaces; `[]` clears |
| `addSubtaskTags` | subtask_id*, tags* | true / false. Keeps existing |
| `removeSubtaskTags` | subtask_id*, tags* | true / false |

How tag values are resolved (`SubtaskTagModel::resolveProjectTagIds`):

- **Digit-only value = id**, anything else = name. A tag literally named `"2024"` can
  therefore only be addressed by its id. `"3D"` is a name, not id 3.
- Names are looked up in the subtask's project. **Unknown names are created as new project
  tags** by `set`, `add` and `createSubtaskWithTags` (like core `setTaskTags`). A typo
  creates a junk tag - list `getTagsByProject` first and reuse the exact spelling.
  `remove` never creates.
- Ids of tags that are not assignable in the project (other project; global tag while
  global tags are off) are **silently dropped**, result is still `true`. Read back with
  `getSubtaskTags` when it matters.
- Every tag that lands on a subtask is **also added to the parent task's tags**. Removing
  it from the subtask, or deleting the subtask, never removes it from the task (a task tag
  does not remember where it came from).

## 4. Combining both plugins

There is no method that takes description and tags together, and the core methods drop
both. Patterns:

| Goal | Calls |
|---|---|
| new subtask with description + tags | `createSubtaskWithDescription` -> id, then `setSubtaskTags(id, tags)` |
| new subtask with tags only | `createSubtaskWithTags` |
| new subtask with description only | `createSubtaskWithDescription` |
| change status / title / assignee | core `updateSubtask` - description and tags survive (the tags plugin re-reads the stored value when no tags are passed; the description plugin only touches the field when it is present) |
| change description | `updateSubtaskDescription` - tags survive |
| change tags | `setSubtaskTags` / `addSubtaskTags` / `removeSubtaskTags` - description survives |

The two-call create is not atomic. If `setSubtaskTags` fails after the create succeeded,
the subtask exists without tags: report the new id and retry only the tag call - never
create the subtask a second time. `kanboard.py add-subtask` does exactly this.

Do not try to pass `due_description` or `subtask_tags` through `createSubtask` /
`updateSubtask`: the JSON-RPC layer maps only the declared parameters, so the extra keys
are ignored and the call still returns success. (It only errors with "Too many arguments"
when the total number of keys exceeds the method's parameter count - so a short call
fails silently and a full one fails loudly. Neither stores the field.)

## 5. Searching (SSS_Filters)

`searchTasks(project_id, query)` uses the same filter language as the board search box,
and SSS_Filters extends it:

| Filter | Values | Matches cards with ... |
|---|---|---|
| `subtask-status:` | `todo`, `inprogress`, `done`; several with `\|` or `,` | at least one subtask in one of these states |
| `subtask-tag:` | tag name (case-insensitive, any project) or id; several with `\|` or `,`; `none` | at least one subtask carrying one of the tags; `none` = a subtask without tags |
| `subtask-assignee:` | username or full name (leading `@` ok), exactly one; `none` | at least one subtask assigned to that user; `none` = an unassigned subtask |

Combine with core filters: `status:open`, `column:"In Progress"`, `assignee:me`,
`tag:Bug`, `swimlane:`, `category:`, `due:`, free text for the title.

```
subtask-tag:Bug subtask-status:todo|inprogress status:open
subtask-assignee:jdoe subtask-status:inprogress
```

Each `subtask-*` filter is its own `EXISTS` check: the line above finds cards that have
*some* Bug subtask and *some* open subtask, not necessarily the same one. For "open Bug
subtasks", search to narrow the cards, then filter the rows of `getAllSubtasks` yourself
(`status != 2` and the tag id in `subtask_tags`).

An unknown tag name or user makes the filter match nothing (no error).
