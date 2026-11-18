"""Characterization tests: freeze observable behavior before refactoring.

These tests were captured from the pre-refactor implementation (HEAD 7b75e6f,
48/48 baseline tests passing). They are intentionally verbose and exact: error
strings, their ordering, and facade exports are part of sapper's CLI contract.
Do not relax them while moving code between modules.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import sapper


def _run_cli(*argv: str) -> tuple[int, str]:
    """Invoke the CLI in-process and capture stdout (stderr left untouched)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = sapper.main(list(argv))
    return rc, out.getvalue()


def _fact(**overrides) -> dict:
    base = {
        "id": "f001",
        "description": "d",
        "created_at": "t",
        "creator": "u",
        "source_intent": None,
    }
    base.update(overrides)
    return base


def _intent(**overrides) -> dict:
    base = {
        "id": "i001",
        "from": ["f001"],
        "to": None,
        "description": "d",
        "created_at": "t",
        "status": "open",
        "worker": None,
        "requires_confirmation": False,
        "confirmed_at": None,
    }
    base.update(overrides)
    return base


class SchemaCharacterizationTests(unittest.TestCase):
    """_node_schema_errors: exact messages and exact ordering."""

    def test_non_object(self):
        self.assertEqual(
            sapper._node_schema_errors([], "fact"), ["expected a JSON object"]
        )

    def test_fact_minimal_missing_fields_in_order(self):
        self.assertEqual(
            sapper._node_schema_errors({}, "fact"),
            [
                "missing string 'id'",
                "missing string 'description'",
                "missing string 'created_at'",
                "missing string 'creator'",
                "missing 'source_intent'",
            ],
        )

    def test_fact_valid_and_goal_reserved(self):
        self.assertEqual(sapper._node_schema_errors(_fact(), "fact"), [])
        goal = _fact(id="goal", description="g", creator="system")
        self.assertEqual(sapper._node_schema_errors(goal, "fact"), [])
        self.assertEqual(
            sapper._node_schema_errors(_fact(id="goal"), "intent")[0],
            "reserved 'id' is only valid for the goal fact",
        )

    def test_id_validation(self):
        self.assertEqual(
            sapper._node_schema_errors(_fact(id="../x"), "fact"), ["invalid 'id'"]
        )
        self.assertEqual(
            sapper._node_schema_errors(_fact(id="F001"), "fact"), ["invalid 'id'"]
        )

    def test_fact_optional_field_types(self):
        cases = [
            (_fact(title=1), "'title' must be a string or null"),
            (_fact(doc=1), "'doc' must be a string or null"),
            (_fact(source_intent=5), "'source_intent' must be a string or null"),
            (
                _fact(kind="bogus"),
                "'kind' must be null or one of: host, service, cred, vuln, loot, flag, note",
            ),
            (_fact(data=[]), "'data' must be an object or null"),
            (_fact(data={"bad key": "x"}), "'data' key must match [A-Za-z0-9_.-]+"),
            (_fact(data={"k": []}), "'data' values must be scalars"),
        ]
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(sapper._node_schema_errors(data, "fact"), [expected])

    def test_fact_data_accepts_legacy_bool_scalar(self):
        # bool is an int subclass; legacy validator accepts it. Preserve that.
        self.assertEqual(
            sapper._node_schema_errors(_fact(data={"flag": True}), "fact"), []
        )

    def test_fact_data_reports_only_first_error(self):
        data = _fact(data={"bad key": [], "k2": []})
        self.assertEqual(
            sapper._node_schema_errors(data, "fact"),
            ["'data' key must match [A-Za-z0-9_.-]+"],
        )

    def test_intent_valid_and_field_types(self):
        self.assertEqual(sapper._node_schema_errors(_intent(), "intent"), [])
        cases = [
            (_intent(**{"from": []}), "'from' must be a list of strings"),
            (_intent(**{"from": ["goal"]}), "'from' must be a list of strings"),
            (_intent(to=5), "'to' must be a string or null"),
            (_intent(description=None), "missing string 'description'"),
            (_intent(created_at=None), "missing string 'created_at'"),
            (_intent(status="bogus"), "invalid 'status'"),
            (_intent(worker=5), "'worker' must be a string or null"),
            (
                _intent(requires_confirmation="yes"),
                "missing boolean 'requires_confirmation'",
            ),
            (_intent(confirmed_at=5), "'confirmed_at' must be a string or null"),
        ]
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, sapper._node_schema_errors(data, "intent"))

    def test_intent_missing_keys_messages(self):
        data = _intent()
        for key in (
            "from",
            "description",
            "created_at",
            "status",
            "worker",
            "requires_confirmation",
            "confirmed_at",
        ):
            del data[key]
        self.assertEqual(
            sapper._node_schema_errors(data, "intent"),
            [
                "'from' must be a list of strings",
                "missing string 'description'",
                "missing string 'created_at'",
                "missing string 'status'",
                "'worker' must be a string or null",
                "missing boolean 'requires_confirmation'",
                "'confirmed_at' must be a string or null",
            ],
        )

    def test_intent_status_target_cross_checks(self):
        cases = [
            (
                _intent(status="done", to=None),
                ["completed status requires a target"],
            ),
            (
                _intent(status="complete", to=None),
                [
                    "completed status requires a target",
                    "complete status must target goal",
                ],
            ),
            (
                _intent(status="open", to="f001"),
                ["open/claimed status cannot have a target"],
            ),
            (
                _intent(status="claimed", to="f001"),
                ["open/claimed status cannot have a target"],
            ),
            (
                _intent(status="complete", to="f001"),
                ["complete status must target goal"],
            ),
            (
                _intent(status="done", to="goal"),
                ["done status cannot target goal"],
            ),
        ]
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(sapper._node_schema_errors(data, "intent"), expected)
        self.assertEqual(
            sapper._node_schema_errors(_intent(status="complete", to="goal"), "intent"),
            [],
        )
        self.assertEqual(
            sapper._node_schema_errors(_intent(status="done", to="f001"), "intent"), []
        )

    def test_hint_schema(self):
        self.assertEqual(
            sapper._node_schema_errors(
                {"id": "h001", "content": "c", "creator": "u", "created_at": "t"},
                "hint",
            ),
            [],
        )
        self.assertEqual(
            sapper._node_schema_errors({}, "hint"),
            [
                "missing string 'id'",
                "missing string 'content'",
                "missing string 'creator'",
                "missing string 'created_at'",
            ],
        )

    def test_unknown_kind_still_checks_id(self):
        # The id check is shared by all kinds; only field checks are kind-specific.
        self.assertEqual(
            sapper._node_schema_errors({}, "unknown"), ["missing string 'id'"]
        )


class FacadeContractTests(unittest.TestCase):
    """Names re-exported by the executable facade must remain importable."""

    def test_public_and_patched_names_present(self):
        expected = [
            "FGError",
            "DEFAULT_STORE",
            "GOAL_ID",
            "FACT_KINDS",
            "DATA_KEY_RE",
            "Store",
            "read_json",
            "atomic_write_json",
            "_validate_lookup_id",
            "_node_schema_errors",
            "_read_node",
            "_read_node_files",
            "build_graph_view",
            "validate_graph",
            "frontier",
            "recall",
            "render_status",
            "render_graph_text",
            "render_history_block",
            "run_claude",
            "_tmux",
            "extract_json_object",
            "_parse_verify_payload",
            "build_parser",
            "main",
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(sapper, name), f"sapper.{name} missing")

    def test_cli_smoke_outputs(self):
        with tempfile.TemporaryDirectory(prefix="sapper-char-") as store:
            self.assertEqual(
                _run_cli("--store", store, "init", "--goal", "g"),
                (0, f"initialized fact graph at {store}\ngoal: g\n"),
            )
            self.assertEqual(
                _run_cli("--store", store, "validate"),
                (0, f"graph valid: {store}\n"),
            )
            self.assertEqual(
                _run_cli("--store", store, "frontier"),
                (0, "(no ready intents)\n"),
            )
            rc, out = _run_cli("--store", store, "status")
            self.assertEqual(rc, 0)
            self.assertIn("counts: 1 facts, 0 open / 0 done intents, 0 hints", out)


class StorageConcurrencyTests(unittest.TestCase):
    """The extracted Store must keep cross-process flock semantics."""

    def test_parallel_fact_writes_are_serialized(self):
        with tempfile.TemporaryDirectory(prefix="sapper-flock-") as store:
            rc, _ = _run_cli("--store", store, "init", "--goal", "concurrent")
            self.assertEqual(rc, 0)
            cli = str(ROOT / "lib" / "sapper.py")
            procs = [
                subprocess.Popen(
                    [sys.executable, cli, "--store", store, "fact", f"fact {i}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                for i in range(4)
            ]
            for proc in procs:
                _out, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0, err)
            graph = sapper.Store(Path(store))
            facts = [f for f in graph.list_facts() if f["id"] != "goal"]
            self.assertEqual(len(facts), 4)
            self.assertEqual(len({f["id"] for f in facts}), 4)
            self.assertEqual(sapper.validate_graph(graph), [])


if __name__ == "__main__":
    unittest.main()
