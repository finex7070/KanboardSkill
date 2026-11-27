# Kanboard JSON-RPC reference

Endpoint: `https://<your-kanboard>/jsonrpc.php`, JSON-RPC 2.0 over `POST`, HTTP Basic
auth. Named parameters (`"params": {"task_id": 42}`) work for every method, core and
plugin; use them, positional arrays break as soon as a signature gains a parameter.
Verified against Kanboard v1.2.58.

## 1. Authentication and config

| Token | Basic-auth user | Where to create it | Acts as |
|---|---|---|---|
| Application token | `jsonrpc` | Settings -> API -> "API token" (admin only) | the application: no project-role checks, but no own user - pass `user_id` explicitly |
| Personal token | your username | Avatar -> My profile -> API -> generate | you, limited by your project roles; `getMe` / `getMyProjects` work only with this |

The client reads, first hit wins per value:

1. `--config <file>`, or the first of `.kanboard.env` / `dev-paths.ps1` found from the
   working directory upwards (checked in that order per directory):

   ```ini
   # .kanboard.env
   KANBOARD_URL=https://kanboard.example.com/jsonrpc.php
   KANBOARD_USER=jsonrpc          # or your username for a personal token
   KANBOARD_TOKEN=...
   KANBOARD_USER_ID=3             # comments and --assign
   KANBOARD_PROJECT_ID=12         # optional default project
   ```

   ```powershell
   # dev-paths.ps1 (PowerShell variable file, for repos that already keep one)
   $KanboardUrl    = "https://kanboard.example.com/jsonrpc.php"
   $KanboardUser   = "jsonrpc"
   $KanboardToken  = "..."
   $KanboardUserId = 3
   ```

2. env vars `KANBOARD_URL`, `KANBOARD_USER`, `KANBOARD_TOKEN`, `KANBOARD_USER_ID`,
   `KANBOARD_PROJECT_ID`

Keep the config file out of version control (`.gitignore`). A value starting with `<`
(`"<your API token>"` in a committed template) counts as missing. The user id is not
secret; find it with `users <project>` or `call getMe` (personal token).

HTTP 401 = the user/token pair is rejected (expired, regenerated, application token sent
with a username, or personal token sent as `jsonrpc`). It is never a client bug - ask the
user to renew the token, do not hunt for other credentials on the machine.

## 2. Answers and errors

- Success: `{"result": ...}`. **Many methods report failure as `"result": false`**, not
  as an error: wrong id, task/subtask mismatch, failed validation. Treat `false` from a
  write as a failure.
- **Unknown ids with a personal token give HTTP 403**, not `null`: the project-role check
  runs first and cannot find a project for a task/subtask that does not exist (verified
  2026-11-27 with `getSubtask` on a deleted subtask). The application token skips the role
  check, so there the call should return `null` (not verified). So a 403 on one id while other calls work usually means "wrong id".
- `{"error": {"code": -32601}}` = method not found. For a plugin method: plugin not
  installed on that server.
- `{"error": {"code": -32602}}` = invalid params (missing required name, array expected).
- HTTP 403 = personal token without the needed project role, or an id that does not exist
  (see above).
- Number types depend on the Kanboard version: v1.2.58 returns real integers
  (`"id": 1001`, `"status": 2`), older versions strings (`"1001"`). Cast before
  comparing (`str(x) == "2"` or `int(x) == 2`), never rely on one type.
- Kanboard sends JSON without a charset. Decode as UTF-8 explicitly. Windows PowerShell
  5.1 `Invoke-RestMethod` decodes as ISO-8859-1 and mangles umlauts - a PowerShell
  client should use `Invoke-WebRequest`, decode `RawContentStream` as UTF-8 itself, and
  send the body as UTF-8 bytes.

## 3. Methods

Required parameters in **bold**.

### Tasks

| Method | Parameters | Result |
|---|---|---|
| `getTask` | **task_id** | task row or null. `url` is the web link |
| `getTaskByReference` | **project_id**, **reference** | task row or null |
| `getAllTasks` | **project_id**, **status_id** (1 open, 0 closed) | list |
| `searchTasks` | **project_id**, **query** | list; query = Kanboard filter syntax (`references/subtask-plugins.md` §5) |
| `createTask` | **title**, **project_id**, color_id, column_id, owner_id, creator_id, date_due, description, category_id, score, swimlane_id, priority, tags (array of names), date_started | task id or false |
| `updateTask` | **id**, title, color_id, owner_id, date_due, description, category_id, score, priority, tags, date_started | true/false. `tags` **replaces** all task tags |
| `moveTaskPosition` | **project_id**, **task_id**, **column_id**, **position** (1 = top), **swimlane_id** | true/false. Pass the task's current `swimlane_id` to stay in the lane |
| `moveTaskToProject` | **task_id**, **project_id**, swimlane_id, column_id, category_id, owner_id | true/false |
| `openTask` / `closeTask` | **task_id** | true/false |

### Subtasks (core)

| Method | Parameters | Result |
|---|---|---|
| `getSubtask` | **subtask_id** | row incl. plugin fields `due_description`, `subtask_tags` |
| `getAllSubtasks` | **task_id** | list, same fields, plus `name`/`username` of the assignee and `status_name` |
| `createSubtask` | **task_id**, **title**, user_id, time_estimated, time_spent, status | id or false. **Drops description and tags** |
| `updateSubtask` | **id**, **task_id**, title, user_id, time_estimated, time_spent, status | true/false. Keeps description and tags |
| `removeSubtask` | **subtask_id** | true/false |

Status: `0` todo, `1` in progress, `2` done. `time_*` are hours (float).

### Comments

| Method | Parameters | Result |
|---|---|---|
| `getAllComments` | **task_id** | list with `comment`, `name`, `username`, `date_creation` (unix time) |
| `createComment` | **task_id**, **user_id**, **content** (Markdown) | comment id or false |
| `updateComment` | **id**, **content** | true/false |
| `removeComment` | **comment_id** | true/false |

### Board, people, tags

| Method | Parameters | Result |
|---|---|---|
| `getColumns` | **project_id** | list with `id`, `title`, `position` |
| `getActiveSwimlanes` | **project_id** | list |
| `getProjectUsers` | **project_id** | `{"user_id": "Name"}` |
| `getAssignableUsers` | **project_id** | same, only users who may be assigned |
| `getProjectById` / `getProjectByName` | **project_id** / **name** | project row |
| `getMe`, `getMyProjects` | - | personal token only |
| `getTagsByProject` | **project_id** | list `{id, name, project_id, color_id}`; global tags (project 0) are not in it |
| `getAllTags` | - | every tag; `project_id` 0 = global (usable in every project with global tags enabled) |
| `getTaskTags` | **task_id** | `{"tag_id": "name"}` |
| `setTaskTags` | **project_id**, **task_id**, **tags** (array of names) | true/false, replaces all; unknown names are created |
| `createTag` | **project_id**, **tag**, color_id | tag id |

### Links and files (rare)

`getAllTaskLinks(task_id)`, `createTaskLink(task_id, opposite_task_id, link_id)`,
`getAllTaskFiles(task_id)`, `createTaskFile(project_id, task_id, filename, blob)` with
`blob` = base64.

## 4. Gotchas

- `updateSubtask` and `updateSubtaskDescription` need **both** `id` and `task_id`; a
  subtask id with the wrong task id gives `false`, not an error. The client checks the
  pair with `getSubtask` before writing.
- `createComment` with an application token still needs a real `user_id` - the comment
  is shown under that user.
- `moveTaskPosition` without the task's `swimlane_id` moves the card to the default lane.
- `updateTask` / `setTaskTags` replace the whole tag list; read `getTaskTags` first if
  only one tag should change.
- PowerShell `ConvertTo-Json` turns a one-element array into a scalar unless you wrap it
  (`@(,"Bug")` or `-AsArray`); the tag methods then fail with -32602. Same for
  `-Depth`: the default 2 truncates nested params.
- Git Bash output mangles umlauts in some setups (display only); run in PowerShell with a
  UTF-8 console or trust `--json`.
