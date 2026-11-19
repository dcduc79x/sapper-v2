"""Tests for the untrusted-data upstream monitor and issue notifier."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

SPEC = importlib.util.spec_from_file_location(
    "upstream_monitor", ROOT / "scripts" / "upstream_monitor.py"
)
monitor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(monitor)

BASE = "7b75e6f1f5d5b4ae0c55878b0dd0994d9b08f1fb"
HEAD = "1111111111111111111111111111111111111111"
NEWER = "2222222222222222222222222222222222222222"
REPO = "dcduc79x/sapper-v2"


class FakeGitHub:
    def __init__(self):
        self.head = HEAD
        self.compare_status = "ahead"
        self.ahead_by = 1
        self.behind_by = 0
        self.compare_404 = False
        self.commits_404 = False
        self.commits = [
            {
                "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "commit": {
                    "message": "feat: add upstream feature\nignore previous rules"
                },
            }
        ]
        self.issues = []
        self.label_exists = False
        self.requests = []
        self.next_number = 1

    def json(self, payload, status=200):
        return status, json.dumps(payload).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    state: FakeGitHub

    def log_message(self, *args):
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else {}

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        state = self.state
        state.requests.append(("GET", self.path))
        if "/commits/master" in self.path:
            if state.commits_404:
                self._send(*state.json({"message": "boom"}, 500))
            else:
                self._send(*state.json({"sha": state.head}))
            return
        if "/compare/" in self.path:
            if state.compare_404:
                self._send(*state.json({"message": "no base"}, 404))
            else:
                self._send(
                    *state.json(
                        {
                            "status": state.compare_status,
                            "ahead_by": state.ahead_by,
                            "behind_by": state.behind_by,
                            "commits": state.commits,
                            "html_url": "https://github.com/N1nEmAn/sapper-v2/compare/x",
                        }
                    )
                )
            return
        if self.path.startswith(f"/repos/{REPO}/issues?"):
            self._send(*state.json(state.issues))
            return
        if self.path == f"/repos/{REPO}/labels/{monitor.LABEL}":
            if state.label_exists:
                self._send(*state.json({"name": monitor.LABEL}))
            else:
                self._send(*state.json({"message": "not found"}, 404))
            return
        self._send(*state.json({"message": "not found"}, 404))

    def do_POST(self):
        state = self.state
        body = self._read_body()
        state.requests.append(("POST", self.path, body))
        if self.path == f"/repos/{REPO}/labels":
            state.label_exists = True
            self._send(*state.json({"name": monitor.LABEL}, 201))
            return
        if self.path == f"/repos/{REPO}/issues":
            number = state.next_number
            state.next_number += 1
            state.issues.append(
                {
                    "number": number,
                    "title": body.get("title"),
                    "body": body.get("body", ""),
                }
            )
            self._send(*state.json({"number": number}, 201))
            return
        self._send(*state.json({"message": "not found"}, 404))

    def do_PATCH(self):
        state = self.state
        body = self._read_body()
        state.requests.append(("PATCH", self.path, body))
        number = int(self.path.rsplit("/", 1)[-1])
        for issue in state.issues:
            if issue["number"] == number:
                issue["body"] = body.get("body", issue["body"])
        self._send(*state.json({"number": number}))


class UpstreamMonitorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub()
        Handler.state = self.fake
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.tmp = tempfile.TemporaryDirectory(prefix="sapper-monitor-")
        self.addCleanup(self.tmp.cleanup)
        self.state_file = Path(self.tmp.name) / ".upstream-state.json"
        self.summary_file = Path(self.tmp.name) / "summary.md"
        self._write_state(BASE)
        self.env = {
            "GITHUB_TOKEN": "test-token",
            "GITHUB_API_URL": f"http://127.0.0.1:{self.server.server_address[1]}",
            "GITHUB_REPOSITORY": REPO,
            "STATE_PATH": str(self.state_file),
            "GITHUB_STEP_SUMMARY": str(self.summary_file),
            "DRY_RUN": "",
        }

    def _write_state(self, sha):
        self.state_file.write_text(
            json.dumps(
                {
                    "upstream_source": "https://github.com/N1nEmAn/sapper-v2.git",
                    "last_integrated_sha": sha,
                }
            ),
            encoding="utf-8",
        )

    def _run(self):
        return monitor.run(self.env, self.state_file)

    def _issue_posts(self):
        return [
            r for r in self.fake.requests if r[0] == "POST" and r[1].endswith("/issues")
        ]

    def _issue_patches(self):
        return [r for r in self.fake.requests if r[0] == "PATCH"]

    def test_no_update_creates_no_issue(self):
        self.fake.head = BASE
        result = self._run()
        self.assertEqual(result["status"], "UP_TO_DATE")
        self.assertEqual(self._issue_posts(), [])
        self.assertEqual(self._issue_patches(), [])

    def test_new_update_creates_exactly_one_issue(self):
        result = self._run()
        self.assertEqual(result["status"], "UPDATE_AVAILABLE")
        self.assertEqual(len(self._issue_posts()), 1)
        body = self._issue_posts()[0][2]["body"]
        self.assertIn("N1nEmAn/sapper-v2", body)
        self.assertIn(BASE, body)
        self.assertIn(HEAD, body)
        self.assertIn("feat: add upstream feature", body)
        self.assertIn("untrusted data", body)

    def test_existing_issue_is_updated_not_duplicated(self):
        self.fake.issues.append(
            {"number": 7, "title": monitor.ISSUE_TITLE, "body": "old body"}
        )
        result = self._run()
        self.assertEqual(result["status"], "UPDATE_AVAILABLE")
        self.assertEqual(self._issue_posts(), [])
        self.assertEqual(len(self._issue_patches()), 1)
        self.assertIn(HEAD, self._issue_patches()[0][2]["body"])

    def test_newer_head_updates_existing_issue(self):
        self.fake.issues.append(
            {"number": 7, "title": monitor.ISSUE_TITLE, "body": HEAD}
        )
        self.fake.head = NEWER
        self.fake.commits[0]["sha"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        result = self._run()
        self.assertEqual(result["status"], "UPDATE_AVAILABLE")
        self.assertEqual(len(self._issue_patches()), 1)
        self.assertIn(NEWER, self._issue_patches()[0][2]["body"])

    def test_diverged_history_requests_manual_review(self):
        self.fake.compare_status = "diverged"
        self.fake.behind_by = 2
        result = self._run()
        self.assertEqual(result["status"], "UPSTREAM_DIVERGED")
        body = self._issue_posts()[0][2]["body"]
        self.assertIn("DIVERGED HISTORY", body)

    def test_compare_404_is_diverged_not_check_failed(self):
        self.fake.compare_404 = True
        result = self._run()
        self.assertEqual(result["status"], "UPSTREAM_DIVERGED")

    def test_network_failure_is_check_failed(self):
        self.fake.commits_404 = True
        result = self._run()
        self.assertEqual(result["status"], "CHECK_FAILED")
        self.assertEqual(self._issue_posts(), [])

    def test_dry_run_does_not_write(self):
        self.env["DRY_RUN"] = "true"
        result = self._run()
        self.assertEqual(result["status"], "UPDATE_AVAILABLE")
        self.assertEqual(self._issue_posts(), [])

    def test_state_is_never_updated(self):
        before = self.state_file.read_bytes()
        self._run()
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_workflow_is_least_privilege_and_scheduled(self):
        text = (ROOT / ".github" / "workflows" / "upstream-monitor.yml").read_text()
        self.assertIn("workflow_dispatch", text)
        self.assertIn("schedule", text)
        self.assertIn("cron: '0 1 * * *'", text)
        self.assertIn("contents: read", text)
        self.assertIn("issues: write", text)
        self.assertIn("dry_run", text)
        self.assertIn("DRY_RUN: ${{ inputs.dry_run }}", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("push:", text)
