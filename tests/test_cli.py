"""Black-box command-line tests against real temporary SQLite databases."""

from __future__ import annotations

from contextlib import closing
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.database = self.path / "data" / "inventory.db"
        self.call("init")

    def call(self, *args: str, db: Path | None = None, expected: int = 0, json_mode: bool = True) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-m", "benchstock", "--db", str(db or self.database)]
        if json_mode:
            command.append("--json")
        command.extend(args)
        environment = dict(os.environ, PYTHONUTF8="1")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=environment, timeout=15)
        self.assertEqual(result.returncode, expected, f"{args!r}\nstdout={result.stdout}\nstderr={result.stderr}")
        self.assertNotIn("Traceback", result.stderr)
        if expected == 0:
            self.assertEqual(result.stderr, "")
        else:
            self.assertEqual(result.stdout, "")
            if json_mode:
                error = json.loads(result.stderr)["error"]
                self.assertIsInstance(error["type"], str)
                self.assertTrue(error["message"])
        return result

    def result(self, *args: str, **kwargs):
        return json.loads(self.call(*args, **kwargs).stdout)

    def add_part(self, sku: str = "BOLT", quantity: int = 10) -> None:
        self.call("item", "add", sku, "M3 bolt", "--location", "BIN-A", "--minimum", "4")
        if quantity:
            self.call("stock", "adjust", sku, str(quantity), "--reason", "Opening count")

    def make_kit(self, code: str = "KIT", quantity: int = 4, sku: str = "BOLT") -> None:
        self.call("kit", "create", code, "Prototype kit")
        self.call("kit", "require", code, sku, str(quantity))

    def snapshot(self):
        return self.result("export", "--format", "json")

    def test_persisted_kit_lifecycle_and_ledger(self) -> None:
        self.add_part()
        self.make_kit()
        self.assertTrue(self.result("kit", "plan", "KIT")["ready"])
        self.assertEqual(self.result("kit", "reserve", "KIT")["status"], "reserved")
        item = self.result("item", "list")[0]
        self.assertEqual((item["on_hand"], item["reserved"], item["available"]), (10, 4, 6))
        self.assertTrue(self.result("kit", "plan", "KIT")["ready"])
        self.call("kit", "release", "KIT")
        self.assertEqual(self.result("item", "list")[0]["available"], 10)
        self.call("kit", "reserve", "KIT")
        self.assertEqual(self.result("kit", "consume", "KIT")["status"], "consumed")
        item = self.result("item", "list")[0]
        self.assertEqual((item["on_hand"], item["reserved"], item["available"]), (6, 0, 6))
        movements = self.result("history", "--sku", "BOLT")
        self.assertEqual([entry["delta"] for entry in movements], [10, -4])
        self.assertEqual(movements[-1]["kit_code"], "KIT")
        self.assertEqual(self.result("kit", "list")[0]["status"], "consumed")

    def test_failed_multi_item_reservation_has_no_partial_effect(self) -> None:
        self.add_part("BOLT", 10)
        self.add_part("CABLE", 1)
        self.make_kit(quantity=4)
        self.call("kit", "require", "KIT", "CABLE", "2")
        plan = self.result("kit", "plan", "KIT")
        self.assertFalse(plan["ready"])
        self.assertEqual(next(line["shortage"] for line in plan["lines"] if line["sku"] == "CABLE"), 1)
        before = self.snapshot()
        self.call("kit", "reserve", "KIT", expected=2)
        self.assertEqual(self.snapshot(), before)

    def test_reserved_stock_is_protected_and_double_consume_rejected(self) -> None:
        self.add_part()
        self.make_kit(quantity=8)
        self.call("kit", "reserve", "KIT")
        before = self.snapshot()
        self.call("stock", "adjust", "BOLT", "-3", "--reason", "Correction", expected=2)
        self.call("kit", "reserve", "KIT", expected=2)
        self.call("kit", "require", "KIT", "BOLT", "3", expected=2)
        self.assertEqual(self.snapshot(), before)
        self.call("kit", "consume", "KIT")
        consumed = self.snapshot()
        self.call("kit", "consume", "KIT", expected=2)
        self.call("kit", "release", "KIT", expected=2)
        self.assertEqual(self.snapshot(), consumed)

    def test_json_errors_for_usage_and_domain_inputs(self) -> None:
        for command in (
            ("nothing",), ("item",), ("item", "add", "BOLT", "Bolt", "--minimum", "nan"),
            ("stock", "adjust", "MISSING", "2"), ("kit", "show", "MISSING"),
            ("item", "add", "bad/sku", "Name"),
            ("item", "add", "BOLT", "Bolt", "--minimum", "1000000001"),
        ):
            with self.subTest(command=command):
                self.call(*command, expected=2)
        self.assertEqual(self.result("item", "list"), [])

    def test_read_and_write_commands_never_create_missing_database(self) -> None:
        missing = self.path / "missing" / "database.db"
        for command in (("item", "list"), ("item", "add", "A", "Adapter"), ("export", "--format", "json")):
            with self.subTest(command=command):
                self.call(*command, db=missing, expected=2)
                self.assertFalse(missing.parent.exists())

    def test_empty_and_unrelated_database_are_not_initialised(self) -> None:
        empty = self.path / "empty.db"
        empty.touch()
        self.call("item", "list", db=empty, expected=2)
        self.assertEqual(empty.read_bytes(), b"")
        unrelated = self.path / "unrelated.db"
        with closing(sqlite3.connect(unrelated)) as connection, connection:
            connection.execute("CREATE TABLE other_project (value TEXT)")
        before = unrelated.read_bytes()
        self.call("item", "list", db=unrelated, expected=2)
        self.assertEqual(unrelated.read_bytes(), before)
        self.call("init", db=unrelated, expected=2)
        self.assertEqual(unrelated.read_bytes(), before)

    def test_init_existing_inventory_does_not_reset_it(self) -> None:
        self.add_part()
        before = self.snapshot()
        self.call("init")
        self.assertEqual(self.snapshot(), before)

    def test_json_export_is_deterministic_full_snapshot_and_utf8(self) -> None:
        self.call("item", "add", "ADAPTER", "Adapter weiß", "--location", "Schachtel")
        first = self.call("export", "--format", "json").stdout
        self.assertEqual(first, self.call("export", "--format", "json").stdout)
        snapshot = json.loads(first)
        self.assertEqual(set(snapshot), {"schema_version", "items", "kits", "movements"})
        self.assertEqual(snapshot["schema_version"], 1)
        target = self.path / "snapshot.json"
        self.call("export", "--format", "json", "--output", str(target))
        self.assertEqual(target.read_text(encoding="utf-8"), first)
        self.assertIn("weiß", first)

    def test_csv_has_fixed_columns_and_escapes_formula_text(self) -> None:
        self.call("item", "add", "ADAPTER", "=SUM(1,2)", "--location", "+BIN", "--minimum", "2")
        self.call("item", "add", "BOLT", "M3 bolt", "--location", "@drawer")
        result = self.call("export", "--format", "csv")
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(list(rows[0]), ["sku", "name", "location", "minimum", "on_hand", "reserved", "available", "low_stock"])
        self.assertEqual(rows[0]["name"], "'=SUM(1,2)")
        self.assertEqual(rows[0]["location"], "'+BIN")
        self.assertEqual(rows[1]["location"], "'@drawer")
        self.assertEqual(rows[0]["minimum"], "2")
        self.assertEqual(rows[0]["low_stock"], "true")
        self.assertEqual(self.result("item", "list")[0]["name"], "=SUM(1,2)")

    def test_export_refuses_existing_file_database_and_sidecar(self) -> None:
        self.add_part()
        target = self.path / "keep.txt"
        target.write_text("preserve me", encoding="utf-8")
        self.call("export", "--format", "csv", "--output", str(target), expected=2)
        self.assertEqual(target.read_text(encoding="utf-8"), "preserve me")
        before = self.snapshot()
        self.call("export", "--format", "json", "--output", str(self.database), expected=2)
        sidecar = Path(str(self.database) + "-journal")
        self.call("export", "--format", "json", "--output", str(sidecar), expected=2)
        self.assertFalse(sidecar.exists())
        self.assertEqual(self.snapshot(), before)

    def test_file_errors_are_reported_without_tracebacks(self) -> None:
        self.call("export", "--format", "json", "--output", str(self.path / "absent" / "snapshot.json"), expected=2)
        self.call("init", db=self.path, expected=2)
        self.call("init", db=Path(":memory:"), expected=2)

    def test_export_refuses_a_symlink_loop_without_traceback(self) -> None:
        target = self.path / "loop.csv"
        try:
            target.symlink_to(target.name)
        except (OSError, NotImplementedError):
            self.skipTest("This environment does not allow creating symbolic links")
        self.call("export", "--format", "csv", "--output", str(target), expected=2)
        self.assertTrue(target.is_symlink())

    def test_help_does_not_create_database(self) -> None:
        missing = self.path / "no-help-db" / "db.sqlite"
        for command in (("--help",), ("kit", "reserve", "--help")):
            self.call(*command, db=missing)
        self.assertFalse(missing.parent.exists())

    def test_human_output_is_readable_and_reports_low_stock(self) -> None:
        self.add_part(quantity=3)
        listing = self.call("item", "list", json_mode=False).stdout
        self.assertIn("On hand", listing)
        self.assertIn("Below minimum", listing)
        self.assertIn("BOLT", listing)
        self.make_kit(quantity=4)
        plan = self.call("kit", "plan", "KIT", json_mode=False).stdout
        self.assertIn("not ready", plan)
        self.assertIn("Shortage", plan)

    def test_demo_uses_new_explicit_database_and_refuses_reuse(self) -> None:
        demo_db = self.path / "demo" / "demo.db"
        command = [sys.executable, str(ROOT / "examples" / "demo.py"), "--db", str(demo_db)]
        completed = subprocess.run(command, cwd=self.path, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Synthetic demo complete", completed.stdout)
        items = self.result("item", "list", db=demo_db)
        self.assertEqual({item["sku"]: item["on_hand"] for item in items}, {"BOLT-M3": 18, "USB-C": 3})
        before = demo_db.read_bytes()
        repeated = subprocess.run(command, cwd=self.path, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(repeated.returncode, 2)
        self.assertNotIn("Traceback", repeated.stderr)
        self.assertEqual(demo_db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
