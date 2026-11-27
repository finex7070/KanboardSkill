#!/usr/bin/env python3
"""kanboard.py - Kanboard JSON-RPC client. Works with a plain Kanboard; uses the optional
plugins SubtaskDescription, SSS_SubTaskTags and SSS_Filters when the server has them.

Python 3.9+, stdlib only.

Config (keep it out of version control, never print the token). First hit wins per value:
  1. --config <file>, or the first config file found from the working directory upwards:
       .kanboard.env     KANBOARD_URL=https://kanboard.example.com/jsonrpc.php
                         KANBOARD_USER=jsonrpc
                         KANBOARD_TOKEN=...
       dev-paths.ps1     $KanboardUrl = "..."   (PowerShell variable file, same names
                         without the underscore: $KanboardUser, $KanboardToken, ...)
  2. environment variables KANBOARD_URL, KANBOARD_USER, KANBOARD_TOKEN, KANBOARD_USER_ID,
     KANBOARD_PROJECT_ID

KANBOARD_USER is "jsonrpc" for the application token, or your username for a personal token.
KANBOARD_USER_ID (your numeric user id) is needed by comment and assign. KANBOARD_PROJECT_ID
is an optional default project.

Run `kanboard.py -h` for the commands. Every write command accepts --dry-run, which prints
the JSON-RPC calls instead of sending them. `kanboard.py plugins` shows which optional
plugins the server has.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STATUS_BY_NAME = {"todo": 0, "doing": 1, "inprogress": 1, "done": 2}
STATUS_NAME = {0: "todo", 1: "doing", 2: "done"}

PLUGINS = {
    "description": {
        "name": "SubtaskDescription",
        "url": "https://github.com/Shaun-Fong/SubtaskDescription",
        "methods": ("createSubtaskWithDescription", "updateSubtaskDescription"),
    },
    "tags": {
        "name": "SSS_SubTaskTags",
        "url": "",
        "methods": ("getSubtaskTags", "createSubtaskWithTags", "setSubtaskTags",
                    "addSubtaskTags", "removeSubtaskTags"),
    },
    "filters": {"name": "SSS_Filters", "url": "", "methods": ()},
}
PLUGIN_OF_METHOD = {m: key for key, p in PLUGINS.items() for m in p["methods"]}
CONFIG_KEYS = ("URL", "USER", "TOKEN", "USER_ID", "PROJECT_ID")
CONFIG_FILES = (".kanboard.env", "dev-paths.ps1")


class KanboardError(Exception):
    pass


class PluginMissing(KanboardError):
    def __init__(self, key: str, what: str):
        p = PLUGINS[key]
        where = f" ({p['url']})" if p["url"] else ""
        super().__init__(f"{what} needs the Kanboard plugin {p['name']}{where}, "
                         "which this server does not have")


# --------------------------------------------------------------------------- config

def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def read_config_file(path: Path) -> dict[str, str]:
    """Read KANBOARD_* values from a .env-style file or $Kanboard* from a PowerShell file.
    Returns canonical keys URL, USER, TOKEN, USER_ID, PROJECT_ID."""
    ps_names = {"Kanboard" + "".join(w.capitalize() for w in k.split("_")): k for k in CONFIG_KEYS}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = re.match(r'^\s*\$(Kanboard\w+)\s*=\s*(.+?)\s*(?:#.*)?$', line)
        if m and m.group(1) in ps_names:
            values[ps_names[m.group(1)]] = _unquote(m.group(2))
            continue
        m = re.match(r'^\s*(?:export\s+)?KANBOARD_(\w+)\s*=\s*(.*?)\s*$', line)
        if m and m.group(1) in CONFIG_KEYS:
            values[m.group(1)] = _unquote(re.sub(r'\s+#.*$', "", m.group(2)))
    return values


def find_config_file(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        for name in CONFIG_FILES:
            p = d / name
            if p.is_file():
                return p
    return None


def load_config(config_file: str | None) -> dict[str, Any]:
    file_values: dict[str, str] = {}
    source = "environment"
    path = Path(config_file) if config_file else find_config_file(Path.cwd())
    if path is not None:
        if not path.is_file():
            raise KanboardError(f"config file not found: {path}")
        file_values = read_config_file(path)
        source = str(path)

    def pick(key: str) -> str | None:
        v = file_values.get(key) or os.environ.get("KANBOARD_" + key)
        if v in (None, "") or v.startswith("<"):  # "<token>" = template placeholder
            return None
        if key in ("USER_ID", "PROJECT_ID") and v == "0":
            return None
        return v

    cfg = {k.lower(): pick(k) for k in CONFIG_KEYS}
    cfg["user"] = cfg["user"] or "jsonrpc"
    cfg["source"] = source
    if not cfg["url"] or not cfg["token"]:
        raise KanboardError(
            "Kanboard config missing: set KANBOARD_URL and KANBOARD_TOKEN (plus KANBOARD_USER "
            "for a personal token, KANBOARD_USER_ID for comments/assigning) as env vars or in a "
            f".kanboard.env / dev-paths.ps1 file that is not committed. (looked in: {source})")
    return cfg


# --------------------------------------------------------------------------- client

class Kanboard:
    def __init__(self, cfg: dict[str, Any], dry_run: bool = False):
        self.url = cfg["url"]
        self.user_id = int(cfg["user_id"]) if cfg.get("user_id") else None
        self.project_id = int(cfg["project_id"]) if cfg.get("project_id") else None
        self.dry_run = dry_run
        token = f'{cfg["user"]}:{cfg["token"]}'.encode("utf-8")
        self._auth = "Basic " + base64.b64encode(token).decode("ascii")
        self._id = 0
        self._plugins: dict[str, bool | None] = {}

    def call(self, method: str, **params: Any) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": self._auth,
            "User-Agent": "kanboard-skill",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            hint = {401: " (wrong user/token? application token needs user 'jsonrpc')",
                    403: " (no project access - or the task/subtask id does not exist: with a personal "
                         "token Kanboard answers unknown ids with 403, not null)"}.get(e.code, "")
            raise KanboardError(f"{method}: HTTP {e.code}{hint}") from None
        except urllib.error.URLError as e:
            raise KanboardError(f"{method}: cannot reach {self.url}: {e.reason}") from None
        # Kanboard sends no charset; the body is UTF-8
        text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise KanboardError(f"{method}: no JSON answer: {text[:200]!r}") from None
        if data.get("error"):
            err = data["error"]
            if isinstance(err, dict) and err.get("code") == -32601 and method in PLUGIN_OF_METHOD:
                raise PluginMissing(PLUGIN_OF_METHOD[method], method)
            if isinstance(err, dict) and err.get("code") == 403:
                raise KanboardError(f"{method}: forbidden - the token's user lacks the project role")
            raise KanboardError(f"{method}: {json.dumps(err, ensure_ascii=False)}")
        return data.get("result")

    def write(self, method: str, **params: Any) -> Any:
        """Call a changing method. In dry-run mode only print it. A false result is an error."""
        if self.dry_run:
            print("DRY-RUN " + json.dumps({"method": method, "params": params}, ensure_ascii=False))
            return True
        result = self.call(method, **params)
        if result is False or result is None:
            raise KanboardError(f"{method} returned {json.dumps(result)} - wrong id, task/subtask "
                                "mismatch, validation failed or missing project role")
        return result

    def need_user_id(self) -> int:
        if not self.user_id:
            raise KanboardError("this command needs KANBOARD_USER_ID (your numeric Kanboard user "
                                "id; find it with `users <project>`, or `call getMe` with a personal token)")
        return self.user_id

    def need_project(self, value: int | None) -> int:
        pid = value or self.project_id
        if not pid:
            raise KanboardError("no project id: pass it, or set KANBOARD_PROJECT_ID")
        return pid

    # -- optional plugins, probed without side effects and cached per run

    def has(self, key: str, project_id: int | None = None) -> bool | None:
        """True / False whether the server has optional plugin `key` (see PLUGINS).
        'filters' needs a project and answers None when the project has no tasks."""
        cache_key = key if key != "filters" else f"filters:{project_id}"
        if cache_key not in self._plugins:
            self._plugins[cache_key] = self._probe(key, project_id)
        return self._plugins[cache_key]

    def require(self, key: str, what: str) -> None:
        if not self.has(key):
            raise PluginMissing(key, what)

    def _probe(self, key: str, project_id: int | None) -> bool | None:
        if key in ("description", "tags"):
            # Calling a method without its parameters: -32602 (invalid params) means the
            # method exists, -32601 (method not found) means the plugin is not installed.
            method = PLUGINS[key]["methods"][-1] if key == "description" else "getSubtaskTags"
            try:
                self.call(method)
            except PluginMissing:
                return False
            except KanboardError as e:
                if "-32602" in str(e):
                    return True
                raise
            return True
        if key == "filters":
            # An invalid value makes the SSS_Filters subtask-status filter add no condition,
            # so the search returns every task of the project. Without the plugin the text
            # is a title search that matches nothing.
            if not project_id:
                return None
            if self.call("searchTasks", project_id=project_id, query="subtask-status:x"):
                return True
            has_tasks = (self.call("getAllTasks", project_id=project_id, status_id=1)
                         or self.call("getAllTasks", project_id=project_id, status_id=0))
            return False if has_tasks else None
        raise ValueError(key)


# --------------------------------------------------------------------------- helpers

def parse_tag_ids(csv: Any) -> list[int]:
    """subtasks.subtask_tags is a CSV of tag ids ('12,15'); '' or missing = no tags."""
    if not csv:
        return []
    return [int(x) for x in str(csv).split(",") if x.strip().isdigit()]


def split_tags(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        out.extend(t.strip() for t in v.split(",") if t.strip())
    return out


def project_tags(kb: Kanboard, project_id: int) -> list[dict]:
    """Tags assignable in the project: its own plus the global ones (project_id 0) when the
    project has global tags enabled. getTagsByProject alone misses the global tags."""
    tags = list(kb.call("getTagsByProject", project_id=project_id) or [])
    project = kb.call("getProjectById", project_id=project_id) or {}
    if str(project.get("enable_global_tags", "1")) == "1":
        try:
            tags += [t for t in kb.call("getAllTags") or [] if str(t["project_id"]) == "0"]
        except KanboardError:
            pass  # getAllTags may be refused for some roles; getSubtaskTags still resolves them
    return tags


def tag_names_for(kb: Kanboard, subtasks: list[dict], project_id: int | None) -> dict[int, str]:
    """Map tag id -> name for the tags used by these subtasks."""
    wanted = {i for s in subtasks for i in parse_tag_ids(s.get("subtask_tags"))}
    if not wanted:
        return {}
    names: dict[int, str] = {}
    if project_id:
        names = {int(t["id"]): t["name"] for t in project_tags(kb, project_id)}
    for s in subtasks:  # fallback for anything the lists did not cover
        if set(parse_tag_ids(s.get("subtask_tags"))) - names.keys():
            got = kb.call("getSubtaskTags", subtask_id=int(s["id"])) or {}
            names.update({int(k): v for k, v in got.items()})
    return names


def read_text_arg(text: str | None, file: str | None) -> str | None:
    if file is not None:
        if file == "-":
            return sys.stdin.read()
        return Path(file).read_text(encoding="utf-8-sig")
    return text


def first_line(s: str, width: int = 70) -> str:
    line = (s or "").strip().splitlines()[0] if (s or "").strip() else ""
    return line if len(line) <= width else line[: width - 1] + "…"


def dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in (text or "").splitlines())


# --------------------------------------------------------------------------- read commands

def print_subtasks(kb: Kanboard, task_id: int, project_id: int | None, full: bool, as_json: bool) -> None:
    subtasks = kb.call("getAllSubtasks", task_id=task_id) or []
    if as_json:
        dump(subtasks)
        return
    if not subtasks:
        print("(no subtasks)")
        return
    # Without the plugins the rows simply lack due_description / subtask_tags.
    names = tag_names_for(kb, subtasks, project_id)
    print(f"subtasks of #{task_id} ({len(subtasks)}):")
    for s in subtasks:
        status = STATUS_NAME.get(int(s.get("status", 0)), s.get("status_name", "?"))
        who = s.get("name") or s.get("username") or "unassigned"
        tags = [names.get(i, f"#{i}") for i in parse_tag_ids(s.get("subtask_tags"))]
        tag_txt = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  #{s['id']:<6} {status:<5} {s['title']}  ({who}){tag_txt}")
        desc = s.get("due_description") or ""
        if desc.strip():
            print(indent(desc, "         | ") if full else f"         | {first_line(desc)}")


def cmd_task(kb: Kanboard, a: argparse.Namespace) -> None:
    task = kb.call("getTask", task_id=a.task_id)
    if not task:
        raise KanboardError(f"task {a.task_id} not found (or no access)")
    if a.json:
        dump({"task": task,
              "subtasks": kb.call("getAllSubtasks", task_id=a.task_id),
              "comments": kb.call("getAllComments", task_id=a.task_id),
              "tags": kb.call("getTaskTags", task_id=a.task_id)})
        return
    state = "open" if str(task.get("is_active")) == "1" else "closed"
    tags = kb.call("getTaskTags", task_id=a.task_id) or {}
    print("=" * 70)
    print(f"#{task['id']}  {task['title']}")
    print(f"state: {state} | project: {task['project_id']} | column: {task['column_id']} "
          f"| swimlane: {task['swimlane_id']} | owner: {task.get('owner_id')}")
    if tags:
        print("tags: " + ", ".join(tags.values()))
    print(f"url: {task.get('url')}")
    print("-" * 70)
    print(task.get("description") or "(no description)")
    print("-" * 70)
    print_subtasks(kb, a.task_id, int(task["project_id"]), a.full, False)
    if not a.no_comments:
        comments = kb.call("getAllComments", task_id=a.task_id) or []
        print("-" * 70)
        print(f"comments ({len(comments)}):" if comments else "(no comments)")
        for c in comments:
            print(f"\n  [{c.get('date_creation')}] {c.get('name') or c.get('username')}:")
            print(indent(c.get("comment", ""), "  "))


def cmd_subtasks(kb: Kanboard, a: argparse.Namespace) -> None:
    project_id = None
    if not a.json:
        task = kb.call("getTask", task_id=a.task_id)
        project_id = int(task["project_id"]) if task else None
    print_subtasks(kb, a.task_id, project_id, a.full, a.json)


def cmd_subtask(kb: Kanboard, a: argparse.Namespace) -> None:
    s = kb.call("getSubtask", subtask_id=a.subtask_id)
    if not s:
        raise KanboardError(f"subtask {a.subtask_id} not found")
    if a.json:
        dump(s)
        return
    tags = kb.call("getSubtaskTags", subtask_id=a.subtask_id) if s.get("subtask_tags") else {}
    print(f"#{s['id']} (task #{s['task_id']})  {s['title']}")
    print(f"status: {STATUS_NAME.get(int(s['status']), s['status'])} | user_id: {s.get('user_id')}"
          f" | estimated: {s.get('time_estimated')}h | spent: {s.get('time_spent')}h")
    if tags:
        print("tags: " + ", ".join(f"{v} ({k})" for k, v in tags.items()))
    if "due_description" in s:
        print("-" * 70)
        print(s["due_description"] or "(no description)")


def cmd_board(kb: Kanboard, a: argparse.Namespace) -> None:
    pid = kb.need_project(a.project_id)
    columns = kb.call("getColumns", project_id=pid) or []
    tasks = kb.call("getAllTasks", project_id=pid, status_id=1) or []
    if a.json:
        dump({"columns": columns, "tasks": tasks})
        return
    for col in sorted(columns, key=lambda c: int(c["position"])):
        inside = sorted((t for t in tasks if str(t["column_id"]) == str(col["id"])),
                        key=lambda t: int(t["position"]))
        print(f"== {col['title']} (column {col['id']}, {len(inside)} cards)")
        for t in inside:
            print(f"  #{t['id']:<6} {t['title']}")


def cmd_users(kb: Kanboard, a: argparse.Namespace) -> None:
    pid = kb.need_project(a.project_id)
    users = kb.call("getProjectUsers", project_id=pid) or {}
    if a.json:
        dump(users)
        return
    for uid, name in users.items():
        print(f"  {uid:<5} {name}")


def cmd_tags(kb: Kanboard, a: argparse.Namespace) -> None:
    pid = kb.need_project(a.project_id)
    tags = project_tags(kb, pid)
    if a.json:
        dump(tags)
        return
    for t in sorted(tags, key=lambda t: (str(t["project_id"]) == "0", t["name"].lower())):
        scope = "  (global)" if str(t["project_id"]) == "0" else ""
        print(f"  {t['id']:<6} {t['name']}{scope}")


def cmd_search(kb: Kanboard, a: argparse.Namespace) -> None:
    pid = kb.need_project(a.project_id)
    tasks = kb.call("searchTasks", project_id=pid, query=a.query) or []
    if re.search(r"(^|\s)subtask-(status|tag|assignee):", a.query) and kb.has("filters", pid) is False:
        print("warning: subtask-status:/subtask-tag:/subtask-assignee: need the plugin SSS_Filters, "
              "which this server does not have - Kanboard searched them as title text. Filter "
              "getAllSubtasks rows yourself instead.", file=sys.stderr)
    if a.json:
        dump(tasks)
        return
    print(f"{len(tasks)} task(s) for {a.query!r}:")
    for t in tasks:
        state = "open" if str(t.get("is_active")) == "1" else "closed"
        print(f"  #{t['id']:<6} {state:<6} {t['title']}")


def cmd_plugins(kb: Kanboard, a: argparse.Namespace) -> None:
    pid = a.project_id or kb.project_id
    found = {key: kb.has(key, pid) for key in PLUGINS}
    if a.json:
        dump({"version": kb.call("getVersion"), "project_id": pid,
              **{PLUGINS[k]["name"]: v for k, v in found.items()}})
        return
    print(f"Kanboard {kb.call('getVersion')}")
    labels = {"description": "subtask descriptions (due_description)",
              "tags": "subtask tags (subtask_tags)",
              "filters": "search filters subtask-status:/subtask-tag:/subtask-assignee:"}
    for key, ok in found.items():
        state = {True: "yes", False: "no", None: "?"}[ok]
        note = "  - unknown: pass a project that has tasks" if ok is None else ""
        print(f"  {PLUGINS[key]['name']:<20} {state:<4}  {labels[key]}{note}")


def cmd_call(kb: Kanboard, a: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    for item in a.params:
        if item.startswith("{"):
            params.update(json.loads(item))
            continue
        if "=" not in item:
            raise KanboardError(f"parameter {item!r}: use key=value or a JSON object")
        k, v = item.split("=", 1)
        try:
            params[k] = json.loads(v)  # numbers, true/false, arrays, quoted strings
        except json.JSONDecodeError:
            params[k] = v
    if a.write:
        dump(kb.write(a.method, **params))
    else:
        dump(kb.call(a.method, **params))


# --------------------------------------------------------------------------- write commands

def _check_subtask(kb: Kanboard, task_id: int, subtask_id: int) -> dict:
    s = kb.call("getSubtask", subtask_id=subtask_id)
    if not s:
        raise KanboardError(f"subtask {subtask_id} not found")
    if int(s["task_id"]) != task_id:
        raise KanboardError(f"subtask {subtask_id} belongs to task #{s['task_id']}, not #{task_id}")
    return s


def cmd_status(kb: Kanboard, a: argparse.Namespace) -> None:
    status = STATUS_BY_NAME.get(a.status.lower())
    if status is None:
        raise KanboardError(f"unknown status {a.status!r}: use todo | doing | done")
    _check_subtask(kb, a.task_id, a.subtask_id)
    params: dict[str, Any] = {"id": a.subtask_id, "task_id": a.task_id, "status": status}
    if not a.no_assign:
        params["user_id"] = kb.need_user_id()
    kb.write("updateSubtask", **params)
    if not kb.dry_run:
        print(f"subtask #{a.subtask_id} -> {STATUS_NAME[status]}")


def cmd_move(kb: Kanboard, a: argparse.Namespace) -> None:
    task = kb.call("getTask", task_id=a.task_id)
    if not task:
        raise KanboardError(f"task {a.task_id} not found")
    pid = int(task["project_id"])
    columns = kb.call("getColumns", project_id=pid) or []
    target = next((c for c in columns if str(c["id"]) == a.column or c["title"].lower() == a.column.lower()), None)
    if target is None:
        raise KanboardError("unknown column {!r}. Columns: {}".format(
            a.column, ", ".join(f"{c['title']} ({c['id']})" for c in columns)))
    kb.write("moveTaskPosition", project_id=pid, task_id=a.task_id, column_id=int(target["id"]),
             position=a.position, swimlane_id=int(task["swimlane_id"]))
    if not kb.dry_run:
        print(f"moved #{a.task_id} '{task['title']}' -> {target['title']}")


def cmd_comment(kb: Kanboard, a: argparse.Namespace) -> None:
    content = read_text_arg(a.text, a.file)
    if not content or not content.strip():
        raise KanboardError("empty comment")
    cid = kb.write("createComment", task_id=a.task_id, user_id=kb.need_user_id(), content=content)
    if not kb.dry_run:
        print(f"added comment #{cid} to task #{a.task_id}")


def cmd_add_subtask(kb: Kanboard, a: argparse.Namespace) -> None:
    description = read_text_arg(a.description, a.description_file)
    tags = split_tags(a.tags or [])
    params: dict[str, Any] = {"task_id": a.task_id, "title": a.title}
    if a.assign:
        params["user_id"] = kb.need_user_id()
    if a.estimate is not None:
        params["time_estimated"] = a.estimate
    if a.status is not None:
        params["status"] = STATUS_BY_NAME[a.status]

    # Check the plugins before writing anything, so a missing one never leaves half a subtask.
    if description is not None:
        kb.require("description", "--description")
    if tags:
        kb.require("tags", "--tags")

    # No method creates a subtask with description AND tags: create with one, then set the other.
    if description is not None:
        sid = kb.write("createSubtaskWithDescription", due_description=description, **params)
        if tags:
            try:
                kb.write("setSubtaskTags", subtask_id=int(sid) if not kb.dry_run else "<new id>", tags=tags)
            except KanboardError as e:
                raise KanboardError(f"subtask #{sid} was created, but setting its tags failed: {e}") from None
    elif tags:
        sid = kb.write("createSubtaskWithTags", tags=tags, **params)
    else:
        sid = kb.write("createSubtask", **params)
    if not kb.dry_run:
        print(f"created subtask #{sid} on task #{a.task_id}")


def cmd_describe(kb: Kanboard, a: argparse.Namespace) -> None:
    text = read_text_arg(a.text, a.file)
    if text is None and not a.clear:
        raise KanboardError("give --text, --file or --clear")
    kb.require("description", "describe")
    _check_subtask(kb, a.task_id, a.subtask_id)
    kb.write("updateSubtaskDescription", id=a.subtask_id, task_id=a.task_id,
             due_description="" if a.clear else text)
    if not kb.dry_run:
        print(f"description of subtask #{a.subtask_id} " + ("cleared" if a.clear else "updated"))


def cmd_tag(kb: Kanboard, a: argparse.Namespace) -> None:
    tags = split_tags(a.tags)
    method = {"set": "setSubtaskTags", "add": "addSubtaskTags", "remove": "removeSubtaskTags"}[a.action]
    if a.action == "set" and not tags and not a.clear:
        raise KanboardError("`tag set` with no tags clears them all - pass --clear to confirm")
    if a.action != "set" and not tags:
        raise KanboardError("no tags given")
    kb.require("tags", "tag")
    kb.write(method, subtask_id=a.subtask_id, tags=tags)
    if not kb.dry_run:
        now = kb.call("getSubtaskTags", subtask_id=a.subtask_id) or {}
        print(f"subtask #{a.subtask_id} tags: " + (", ".join(now.values()) if now else "(none)"))


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="dev-paths.ps1 to read (default: search upwards from cwd)")
    common.add_argument("--json", action="store_true", help="raw JSON output (read commands)")
    common.add_argument("--dry-run", action="store_true", help="print write calls instead of sending them")

    p = argparse.ArgumentParser(prog="kanboard.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    def add(name: str, func, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, parents=[common])
        sp.set_defaults(func=func)
        return sp

    sp = add("plugins", cmd_plugins, "Kanboard version and which optional plugins the server has")
    sp.add_argument("project_id", type=int, nargs="?", help="needed to detect SSS_Filters")

    sp = add("task", cmd_task, "task + tags + subtasks (descriptions, tags) + comments")
    sp.add_argument("task_id", type=int)
    sp.add_argument("--full", action="store_true", help="full subtask descriptions")
    sp.add_argument("--no-comments", action="store_true")

    sp = add("subtasks", cmd_subtasks, "subtasks of a task with status, assignee, tags, description")
    sp.add_argument("task_id", type=int)
    sp.add_argument("--full", action="store_true", help="full descriptions instead of the first line")

    sp = add("subtask", cmd_subtask, "one subtask in detail")
    sp.add_argument("subtask_id", type=int)

    sp = add("board", cmd_board, "open cards of a project by column")
    sp.add_argument("project_id", type=int, nargs="?")

    sp = add("users", cmd_users, "project members with numeric user id")
    sp.add_argument("project_id", type=int, nargs="?")

    sp = add("tags", cmd_tags, "tags of a project (id, name)")
    sp.add_argument("project_id", type=int, nargs="?")

    sp = add("search", cmd_search, "searchTasks with Kanboard filter syntax")
    sp.add_argument("query")
    sp.add_argument("--project", dest="project_id", type=int)

    sp = add("call", cmd_call, "any JSON-RPC method: call getTask task_id=42")
    sp.add_argument("method")
    sp.add_argument("params", nargs="*", help="key=value (value parsed as JSON if possible) or a JSON object")
    sp.add_argument("--write", action="store_true", help="treat as a write (honours --dry-run, false = error)")

    sp = add("status", cmd_status, "WRITE set subtask status todo|doing|done (assigns you)")
    sp.add_argument("task_id", type=int)
    sp.add_argument("subtask_id", type=int)
    sp.add_argument("status")
    sp.add_argument("--no-assign", action="store_true", help="keep the current assignee")

    sp = add("move", cmd_move, "WRITE move a card to a column (title or id)")
    sp.add_argument("task_id", type=int)
    sp.add_argument("column")
    sp.add_argument("--position", type=int, default=1)

    sp = add("comment", cmd_comment, "WRITE add a markdown comment")
    sp.add_argument("task_id", type=int)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="UTF-8 markdown file, - for stdin")
    g.add_argument("--text")

    sp = add("add-subtask", cmd_add_subtask, "WRITE create a subtask; --description / --tags need the plugins")
    sp.add_argument("task_id", type=int)
    sp.add_argument("title")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--description")
    g.add_argument("--description-file", help="UTF-8 markdown file, - for stdin")
    sp.add_argument("--tags", action="append", help="tag names or ids, comma separated; repeatable")
    sp.add_argument("--assign", action="store_true", help="assign to $KanboardUserId")
    sp.add_argument("--estimate", type=float, help="time_estimated in hours")
    sp.add_argument("--status", choices=["todo", "doing", "done"])

    sp = add("describe", cmd_describe, "WRITE set or clear a subtask description (SubtaskDescription)")
    sp.add_argument("task_id", type=int)
    sp.add_argument("subtask_id", type=int)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file", help="UTF-8 markdown file, - for stdin")
    g.add_argument("--clear", action="store_true")

    sp = add("tag", cmd_tag, "WRITE set|add|remove subtask tags, names or ids (SSS_SubTaskTags)")
    sp.add_argument("action", choices=["set", "add", "remove"])
    sp.add_argument("subtask_id", type=int)
    sp.add_argument("tags", nargs="*")
    sp.add_argument("--clear", action="store_true", help="allow `tag set` with no tags")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows consoles: keep umlauts intact
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    try:
        kb = Kanboard(load_config(a.config), dry_run=a.dry_run)
        a.func(kb, a)
    except KanboardError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
