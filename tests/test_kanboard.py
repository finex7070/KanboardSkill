"""Offline tests for scripts/kanboard.py against a local mock of the Kanboard JSON-RPC API.

The mock models the behaviour the skill relies on, taken from the plugin sources:
  - core createSubtask/updateSubtask only map their declared parameters
  - SubtaskDescription: createSubtaskWithDescription, updateSubtaskDescription (id + task_id)
  - SSS_SubTaskTags: names or ids, digit-only = id, unknown names created, tags copied to the task

Run:  python -I tests/test_kanboard.py      (no network, no credentials)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import kanboard  # noqa: E402

USER, TOKEN = "jsonrpc", "test-token-not-secret"


class FakeBoard:
    CORE_SUBTASK_CREATE = ["task_id", "title", "user_id", "time_estimated", "time_spent", "status"]
    CORE_SUBTASK_UPDATE = ["id", "task_id", "title", "user_id", "time_estimated", "time_spent", "status"]

    def __init__(self, plugins: bool = True):
        self.plugins = plugins
        self.calls: list[tuple[str, dict]] = []
        self.tasks = {42: {"id": "42", "project_id": "12", "column_id": "229", "swimlane_id": "12",
                             "title": "AP11 Lobby – Dev", "description": "Lobby für 4 Spieler",
                             "is_active": "1", "owner_id": "3", "position": "1", "url": "http://x/t/42"}}
        self.columns = [{"id": "228", "title": "Backlog", "position": "1"},
                        {"id": "229", "title": "Planned", "position": "2"},
                        {"id": "230", "title": "InProgress", "position": "3"}]
        self.tags = {12: {"id": "12", "name": "Bug", "project_id": "12"},
                     15: {"id": "15", "name": "UI", "project_id": "12"},
                     99: {"id": "99", "name": "Other", "project_id": "7"},
                     5: {"id": 5, "name": "Dev", "project_id": 0}}  # global tag, int types like v1.2.58
        self.task_tags: dict[int, set[int]] = {42: set()}
        self.subtasks: dict[int, dict] = {}
        self.comments: list[dict] = []
        self._next = 1001
        self._add_subtask({"task_id": 42, "title": "AP11-DEV-1 Netzwerk", "status": 1, "user_id": 3,
                           "due_description": "Erste Zeile\n**zweite** Zeile", "subtask_tags": "12"})

    # -- helpers
    def _add_subtask(self, v: dict) -> int:
        sid = self._next
        self._next += 1
        row = {"id": str(sid), "task_id": str(v["task_id"]), "title": v["title"],
               "status": str(v.get("status", 0)), "user_id": str(v.get("user_id", 0)),
               "time_estimated": str(v.get("time_estimated", 0)), "time_spent": "0",
               "name": "Erika Müller" if v.get("user_id") else None, "username": None}
        if self.plugins:
            row["due_description"] = v.get("due_description", "")
            row["subtask_tags"] = v.get("subtask_tags", "")
        self.subtasks[sid] = row
        for t in kanboard.parse_tag_ids(row.get("subtask_tags")):
            self.task_tags[int(v["task_id"])].add(t)
        return sid

    def _resolve(self, values: list, project_id: int, create: bool) -> list[int]:
        out: list[int] = []
        for v in values:
            v = str(v).strip()
            if v.isdigit():
                tid = int(v)
            else:
                tid = next((i for i, t in self.tags.items()
                            if t["name"] == v and int(t["project_id"]) == project_id), 0)
                if tid == 0 and create:
                    tid = max(self.tags) + 1
                    self.tags[tid] = {"id": str(tid), "name": v, "project_id": str(project_id)}
            if tid in self.tags and int(self.tags[tid]["project_id"]) in (project_id, 0) and tid not in out:
                out.append(tid)
        return out

    def _save_tags(self, sid: int, ids: list[int]) -> bool:
        row = self.subtasks[sid]
        row["subtask_tags"] = ",".join(map(str, ids))
        self.task_tags[int(row["task_id"])].update(ids)
        return True

    # -- dispatch
    def handle(self, method: str, p: dict):
        self.calls.append((method, p))
        plugin_methods = {"createSubtaskWithDescription", "updateSubtaskDescription", "getSubtaskTags",
                          "createSubtaskWithTags", "setSubtaskTags", "addSubtaskTags", "removeSubtaskTags"}
        if method in plugin_methods and not self.plugins:
            raise LookupError
        if method in plugin_methods and not p:
            raise TypeError  # existing method, missing arguments -> -32602 (plugin probe)
        if method == "getVersion":
            return "1.2.47"
        if method == "getTask":
            return self.tasks.get(int(p["task_id"]))
        if method == "getAllSubtasks":
            return [s for s in self.subtasks.values() if s["task_id"] == str(p["task_id"])]
        if method == "getSubtask":
            return self.subtasks.get(int(p["subtask_id"]))
        if method == "getAllComments":
            return [c for c in self.comments if c["task_id"] == p["task_id"]]
        if method == "getTaskTags":
            return {str(i): self.tags[i]["name"] for i in sorted(self.task_tags[int(p["task_id"])])}
        if method == "getTagsByProject":
            return [t for t in self.tags.values() if t["project_id"] == str(p["project_id"])]
        if method == "getProjectById":
            return {"id": int(p["project_id"]), "enable_global_tags": 1}
        if method == "getAllTags":
            return list(self.tags.values())
        if method == "getColumns":
            return self.columns
        if method == "getProjectUsers":
            return {"3": "Erika Müller", "8": "Max Mustermann"}
        if method == "getAllTasks":
            return list(self.tasks.values())
        if method == "searchTasks":
            # SSS_Filters ignores invalid values (all tasks); without it the text is a title search
            if "subtask-" in p["query"] and not self.plugins:
                return []
            return list(self.tasks.values())
        if method == "createSubtask":
            return self._add_subtask({k: p[k] for k in self.CORE_SUBTASK_CREATE if k in p})
        if method == "updateSubtask":
            s = self.subtasks.get(int(p["id"]))
            if not s or s["task_id"] != str(p["task_id"]):
                return False
            for k in self.CORE_SUBTASK_UPDATE[2:]:
                if k in p:
                    s[k] = str(p[k]) if k != "title" else p[k]
            return True
        if method == "moveTaskPosition":
            t = self.tasks[int(p["task_id"])]
            t["column_id"], t["swimlane_id"] = str(p["column_id"]), str(p["swimlane_id"])
            return True
        if method == "createComment":
            self.comments.append({"task_id": p["task_id"], "comment": p["content"], "name": "Erika Müller",
                                  "date_creation": "0"})
            return len(self.comments)
        if method == "createSubtaskWithDescription":
            v = {k: p[k] for k in self.CORE_SUBTASK_CREATE if k in p}
            v["due_description"] = str(p.get("due_description", ""))
            return self._add_subtask(v)
        if method == "updateSubtaskDescription":
            s = self.subtasks.get(int(p["id"]))
            if not s or s["task_id"] != str(p["task_id"]):
                return False
            s["due_description"] = str(p["due_description"])
            return True
        if method == "getSubtaskTags":
            s = self.subtasks[int(p["subtask_id"])]
            return {str(i): self.tags[i]["name"] for i in kanboard.parse_tag_ids(s["subtask_tags"])}
        if method in ("createSubtaskWithTags", "setSubtaskTags", "addSubtaskTags", "removeSubtaskTags"):
            if not isinstance(p.get("tags"), list):
                raise TypeError
            if method == "createSubtaskWithTags":
                sid = self._add_subtask({k: p[k] for k in self.CORE_SUBTASK_CREATE if k in p})
            else:
                sid = int(p["subtask_id"])
                if sid not in self.subtasks:
                    return False
            pid = int(self.tasks[int(self.subtasks[sid]["task_id"])]["project_id"])
            cur = kanboard.parse_tag_ids(self.subtasks[sid]["subtask_tags"])
            if method == "removeSubtaskTags":
                drop = self._resolve(p["tags"], pid, False)
                return self._save_tags(sid, [t for t in cur if t not in drop])
            new = self._resolve(p["tags"], pid, True)
            if method == "addSubtaskTags":
                new = cur + [t for t in new if t not in cur]
            self._save_tags(sid, new)
            return sid if method == "createSubtaskWithTags" else True
        raise LookupError


def make_handler(board: FakeBoard):
    import base64
    expected = "Basic " + base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.end_headers()
                return
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            try:
                resp = {"jsonrpc": "2.0", "id": req["id"], "result": board.handle(req["method"], req["params"])}
            except LookupError:
                resp = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "Method not found"}}
            except TypeError:
                resp = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32602, "message": "Invalid params"}}
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")  # no charset, like Kanboard
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return H


class KanboardTests(unittest.TestCase):
    plugins = True

    def setUp(self):
        self.board = FakeBoard(plugins=self.plugins)
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(self.board))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.board_url = f"http://127.0.0.1:{self.server.server_port}/jsonrpc.php"
        self.cfg = Path(self.tmp.name) / "dev-paths.ps1"
        self.cfg.write_text(
            "# test config\n"
            f'$KanboardUrl   = "http://127.0.0.1:{self.server.server_port}/jsonrpc.php"\n'
            f'$KanboardUser  = "{USER}"\n'
            f'$KanboardToken = "{TOKEN}"   # inline comment\n'
            "$KanboardUserId = 3\n", encoding="utf-8")
        self._env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("KANBOARD_")}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()
        os.environ.update(self._env)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kanboard.main([*args, "--config", str(self.cfg)])
        return code, out.getvalue(), err.getvalue()

    def methods(self) -> list[str]:
        return [m for m, _ in self.board.calls]


class WithPlugins(KanboardTests):
    def test_config_parsing_ps1(self):
        v = kanboard.read_config_file(self.cfg)
        self.assertEqual(v["TOKEN"], TOKEN)
        self.assertEqual(v["USER_ID"], "3")

    def test_config_env_file_found_upwards(self):
        env = Path(self.tmp.name) / ".kanboard.env"
        env.write_text(f"# comment\nKANBOARD_URL={self.board_url}\nexport KANBOARD_USER={USER}\n"
                       f"KANBOARD_TOKEN=\"{TOKEN}\"\nKANBOARD_PROJECT_ID=12  # default\n", encoding="utf-8")
        self.cfg.unlink()
        sub = Path(self.tmp.name) / "a" / "b"
        sub.mkdir(parents=True)
        old = Path.cwd()
        os.chdir(sub)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = kanboard.main(["users"])  # project from KANBOARD_PROJECT_ID
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        self.assertIn("Max Mustermann", out.getvalue())

    def test_env_vars_only(self):
        self.cfg.unlink()
        os.environ.update({"KANBOARD_URL": self.board_url, "KANBOARD_TOKEN": TOKEN})
        try:
            old = Path.cwd()
            os.chdir(self.tmp.name)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = kanboard.main(["call", "getVersion"])
        finally:
            os.chdir(old)
            for k in ("KANBOARD_URL", "KANBOARD_TOKEN"):
                os.environ.pop(k, None)
        self.assertEqual(code, 0)

    def test_plugins_detected(self):
        code, out, _ = self.run_cli("plugins", "12")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"SubtaskDescription\s+yes")
        self.assertRegex(out, r"SSS_SubTaskTags\s+yes")
        self.assertRegex(out, r"SSS_Filters\s+yes")

    def test_placeholder_token_is_missing(self):
        self.cfg.write_text('$KanboardUrl = "http://x"\n$KanboardToken = "<your API token>"\n', encoding="utf-8")
        code, _, err = self.run_cli("call", "getVersion")
        self.assertEqual(code, 1)
        self.assertIn("config missing", err)

    def test_wrong_token_is_401(self):
        self.cfg.write_text(self.cfg.read_text(encoding="utf-8").replace(TOKEN, "nope"), encoding="utf-8")
        code, _, err = self.run_cli("call", "getVersion")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", err)
        self.assertNotIn("nope", err)

    def test_task_shows_description_tags_umlauts(self):
        code, out, _ = self.run_cli("task", "42")
        self.assertEqual(code, 0)
        self.assertIn("Lobby für 4 Spieler", out)
        self.assertIn("[Bug]", out)
        self.assertIn("| Erste Zeile", out)
        self.assertNotIn("zweite", out)  # first line only without --full
        _, out, _ = self.run_cli("subtasks", "42", "--full")
        self.assertIn("**zweite** Zeile", out)

    def test_add_subtask_with_description_and_tags_uses_two_calls(self):
        code, out, err = self.run_cli("add-subtask", "42", "Neuer Bug", "--description", "Schritt **1**",
                                      "--tags", "Bug,UI", "--assign")
        self.assertEqual(code, 0, err)
        self.assertIn("createSubtaskWithDescription", self.methods())
        self.assertIn("setSubtaskTags", self.methods())
        self.assertNotIn("createSubtask", self.methods())
        s = self.board.subtasks[1002]
        self.assertEqual(s["due_description"], "Schritt **1**")
        self.assertEqual(s["subtask_tags"], "12,15")
        self.assertEqual(s["user_id"], "3")
        self.assertEqual(self.board.task_tags[42], {12, 15})  # copied to the card

    def test_add_subtask_tags_only(self):
        code, _, err = self.run_cli("add-subtask", "42", "Nur Tags", "--tags", "UI")
        self.assertEqual(code, 0, err)
        self.assertIn("createSubtaskWithTags", self.methods())
        _, p = next(c for c in self.board.calls if c[0] == "createSubtaskWithTags")
        self.assertEqual(p["tags"], ["UI"])  # a JSON array, never a string

    def test_add_subtask_plain_uses_core(self):
        code, _, _ = self.run_cli("add-subtask", "42", "Plain")
        self.assertEqual(code, 0)
        self.assertIn("createSubtask", self.methods())

    def test_status_keeps_description_and_tags(self):
        code, _, err = self.run_cli("status", "42", "1001", "done")
        self.assertEqual(code, 0, err)
        s = self.board.subtasks[1001]
        self.assertEqual(s["status"], "2")
        self.assertEqual(s["subtask_tags"], "12")
        self.assertTrue(s["due_description"].startswith("Erste"))

    def test_status_rejects_wrong_task(self):
        code, _, err = self.run_cli("status", "9999", "1001", "done")
        self.assertEqual(code, 1)
        self.assertIn("belongs to task #42", err)
        self.assertNotIn("updateSubtask", self.methods())

    def test_describe_and_clear(self):
        self.assertEqual(self.run_cli("describe", "42", "1001", "--text", "Neu")[0], 0)
        self.assertEqual(self.board.subtasks[1001]["due_description"], "Neu")
        self.assertEqual(self.run_cli("describe", "42", "1001", "--clear")[0], 0)
        self.assertEqual(self.board.subtasks[1001]["due_description"], "")
        self.assertEqual(self.board.subtasks[1001]["subtask_tags"], "12")

    def test_tag_add_remove_set(self):
        self.assertEqual(self.run_cli("tag", "add", "1001", "UI")[0], 0)
        self.assertEqual(self.board.subtasks[1001]["subtask_tags"], "12,15")
        self.assertEqual(self.run_cli("tag", "remove", "1001", "Bug")[0], 0)
        self.assertEqual(self.board.subtasks[1001]["subtask_tags"], "15")
        code, _, err = self.run_cli("tag", "set", "1001")
        self.assertEqual(code, 1)
        self.assertIn("--clear", err)
        self.assertEqual(self.run_cli("tag", "set", "1001", "--clear")[0], 0)
        self.assertEqual(self.board.subtasks[1001]["subtask_tags"], "")
        self.assertEqual(self.board.task_tags[42], {12, 15})  # never removed from the card

    def test_dry_run_sends_no_write(self):
        code, out, _ = self.run_cli("add-subtask", "42", "X", "--description", "d", "--tags", "Bug", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)
        self.assertIn("createSubtaskWithDescription", out)
        self.assertIn("setSubtaskTags", out)
        self.assertEqual(len(self.board.subtasks), 1)

    def test_move_keeps_swimlane(self):
        code, out, _ = self.run_cli("move", "42", "inprogress")
        self.assertEqual(code, 0)
        _, p = next(c for c in self.board.calls if c[0] == "moveTaskPosition")
        self.assertEqual((p["column_id"], p["swimlane_id"]), (230, 12))

    def test_comment_utf8_file(self):
        note = Path(self.tmp.name) / "note.md"
        note.write_text("Erledigt: Größe geprüft (abc1234)", encoding="utf-8")
        self.assertEqual(self.run_cli("comment", "42", "--file", str(note))[0], 0)
        self.assertEqual(self.board.comments[0]["comment"], "Erledigt: Größe geprüft (abc1234)")

    def test_tags_include_global(self):
        code, out, _ = self.run_cli("tags", "12")
        self.assertEqual(code, 0)
        self.assertIn("Dev  (global)", out)
        self.assertIn("Bug", out)
        self.assertNotIn("Other", out)  # tag of another project

    def test_global_tag_name_without_extra_calls(self):
        self.board.subtasks[1001]["subtask_tags"] = "12,5"
        _, out, _ = self.run_cli("subtasks", "42")
        self.assertIn("[Bug, Dev]", out)
        self.assertNotIn("getSubtaskTags", self.methods())

    def test_call_parses_values(self):
        code, out, _ = self.run_cli("call", "getTask", "task_id=42")
        self.assertEqual(code, 0)
        self.assertEqual(self.board.calls[-1], ("getTask", {"task_id": 42}))


class WithoutPlugins(KanboardTests):
    plugins = False

    def test_plugins_reported_missing(self):
        code, out, _ = self.run_cli("plugins", "12")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"SubtaskDescription\s+no")
        self.assertRegex(out, r"SSS_SubTaskTags\s+no")
        self.assertRegex(out, r"SSS_Filters\s+no")

    def test_read_commands_work_plain(self):
        code, out, err = self.run_cli("task", "42")
        self.assertEqual(code, 0, err)
        self.assertIn("AP11-DEV-1 Netzwerk", out)
        self.assertNotIn("plugin", out + err)  # no nagging on a plain Kanboard
        self.assertEqual(self.run_cli("subtask", "1001")[0], 0)
        self.assertEqual(self.run_cli("tags", "12")[0], 0)

    def test_core_writes_work_plain(self):
        self.assertEqual(self.run_cli("add-subtask", "42", "Plain", "--assign")[0], 0)
        self.assertIn("createSubtask", self.methods())
        self.assertEqual(self.run_cli("status", "42", "1001", "done")[0], 0)
        self.assertEqual(self.board.subtasks[1001]["status"], "2")

    def test_describe_refused_before_any_write(self):
        code, _, err = self.run_cli("describe", "42", "1001", "--text", "x")
        self.assertEqual(code, 1)
        self.assertIn("SubtaskDescription", err)
        self.assertNotIn("updateSubtaskDescription", [m for m, p in self.board.calls if p])

    def test_add_subtask_with_plugin_options_creates_nothing(self):
        for extra in (["--description", "d"], ["--tags", "Bug"], ["--description", "d", "--tags", "Bug"]):
            code, _, err = self.run_cli("add-subtask", "42", "X", *extra)
            self.assertEqual(code, 1, extra)
            self.assertIn("plugin", err)
        self.assertEqual(len(self.board.subtasks), 1)  # nothing half-created
        code, _, err = self.run_cli("tag", "add", "1001", "Bug")
        self.assertEqual(code, 1)
        self.assertIn("SSS_SubTaskTags", err)

    def test_search_warns_about_subtask_filters(self):
        code, out, err = self.run_cli("search", "subtask-tag:Bug", "--project", "12")
        self.assertEqual(code, 0)
        self.assertIn("SSS_Filters", err)
        code, out, err = self.run_cli("search", "status:open", "--project", "12")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
