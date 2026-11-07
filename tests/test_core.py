"""Behavioural checks for stock integrity, transactions and kit lifecycles."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from benchstock.core import (
    APPLICATION_ID, MAX_QUANTITY, ConflictError, Inventory, NotFoundError, ValidationError,
)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / 'inventory.db'
        self.inventory = Inventory(self.database)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.inventory.close)

    def add(self, sku='P1', quantity=10):
        self.inventory.add_item(sku, f'Part {sku}', 'Drawer A', minimum=3)
        self.inventory.adjust_stock(sku, quantity, 'Initial count')

    def kit(self, code='K1', sku='P1', quantity=4):
        self.inventory.create_kit(code, f'Project {code}')
        self.inventory.set_requirement(code, sku, quantity)

    def test_zero_opening_stock_and_durable_adjustment_history(self):
        item = self.inventory.add_item(' P1 ', ' Connector ', minimum=3)
        self.assertEqual((item['on_hand'], item['available'], item['low_stock']), (0, 0, True))
        self.assertEqual(self.inventory.history(), [])
        self.inventory.adjust_stock('P1', 5, 'Initial count')
        self.inventory.adjust_stock('P1', -2, 'Used for a repair')
        with Inventory(self.database, initialize=False) as reopened:
            self.assertEqual(reopened.list_items()[0]['on_hand'], 3)
            self.assertFalse(reopened.list_items()[0]['low_stock'])
            self.assertEqual([m['delta'] for m in reopened.history('P1')], [5, -2])
            self.assertTrue(all(m['created_at'].endswith('Z') for m in reopened.history()))

    def test_negative_stock_and_upper_bound_are_rejected(self):
        self.add(quantity=1)
        before = self.inventory.export_snapshot()
        with self.assertRaises(ConflictError):
            self.inventory.adjust_stock('P1', -2, 'Count correction')
        with self.assertRaises(ConflictError):
            self.inventory.adjust_stock('P1', MAX_QUANTITY, 'Count correction')
        self.assertEqual(self.inventory.export_snapshot(), before)

    def test_item_uniqueness_and_case_sensitive_codes(self):
        self.add('p1')
        self.add('P1')
        with self.assertRaises(ConflictError):
            self.inventory.add_item('p1', 'Duplicate')
        self.assertEqual([item['sku'] for item in self.inventory.list_items()], ['P1', 'p1'])

    def test_invalid_values_leave_no_item(self):
        for sku in ('', ' ', '-a', 'a b', 'ä', 'a' * 65, None, True):
            with self.subTest(sku=sku), self.assertRaises(ValidationError):
                self.inventory.add_item(sku, 'Part')
        for name in ('', ' ', 'x' * 201, 'A\nB', 'A\x00B', 'A\x7fB', None):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.inventory.add_item('P1', name)
        for minimum in (-1, MAX_QUANTITY + 1, True, 1.2, '2'):
            with self.subTest(minimum=minimum), self.assertRaises(ValidationError):
                self.inventory.add_item('P1', 'Part', minimum=minimum)
        self.assertEqual(self.inventory.list_items(), [])

    def test_invalid_quantities_and_reasons_do_not_change_stock(self):
        self.add()
        self.kit()
        before = self.inventory.export_snapshot()
        for delta in (0, True, 0.5, '2', MAX_QUANTITY + 1, -MAX_QUANTITY - 1):
            with self.subTest(delta=delta), self.assertRaises(ValidationError):
                self.inventory.adjust_stock('P1', delta, 'Count correction')
        for reason in ('', ' ', 'x' * 201, 'Count\ncorrection'):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                self.inventory.adjust_stock('P1', 1, reason)
        for quantity in (0, -1, True, 1.5, '2', MAX_QUANTITY + 1):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                self.inventory.set_requirement('K1', 'P1', quantity)
        self.assertEqual(self.inventory.export_snapshot(), before)

    def test_missing_item_and_kit_are_explicit(self):
        self.inventory.create_kit('K1', 'Project')
        with self.assertRaises(NotFoundError):
            self.inventory.set_requirement('K1', 'missing', 1)
        with self.assertRaises(NotFoundError):
            self.inventory.adjust_stock('missing', 1, 'Initial count')
        with self.assertRaises(NotFoundError):
            self.inventory.history('missing')
        with self.assertRaises(NotFoundError):
            self.inventory.get_kit('missing')
        self.assertEqual(self.inventory.get_kit('K1')['requirements'], [])

    def test_kit_lifecycle_reserves_releases_and_consumes(self):
        self.add()
        self.kit()
        self.assertTrue(self.inventory.plan_kit('K1')['ready'])
        self.assertEqual(self.inventory.reserve_kit('K1')['status'], 'reserved')
        item = self.inventory.list_items()[0]
        self.assertEqual((item['on_hand'], item['reserved'], item['available']), (10, 4, 6))
        self.assertEqual(self.inventory.plan_kit('K1')['lines'][0]['available'], 10)
        self.assertEqual(len(self.inventory.history()), 1)
        self.inventory.release_kit('K1')
        self.assertEqual(self.inventory.list_items()[0]['available'], 10)
        self.assertEqual(self.inventory.get_kit('K1')['requirements'][0]['quantity'], 4)
        self.inventory.reserve_kit('K1')
        self.assertEqual(self.inventory.consume_kit('K1')['status'], 'consumed')
        item = self.inventory.list_items()[0]
        self.assertEqual((item['on_hand'], item['reserved'], item['available']), (6, 0, 6))
        self.assertFalse(self.inventory.plan_kit('K1')['ready'])
        self.assertEqual(self.inventory.history()[-1]['kit_code'], 'K1')
        self.assertEqual(sum(m['delta'] for m in self.inventory.history()), 6)

    def test_requirements_replace_quantities_and_preserve_order(self):
        self.add('B')
        self.add('A')
        self.kit(sku='B', quantity=2)
        self.inventory.set_requirement('K1', 'A', 3)
        self.inventory.set_requirement('K1', 'B', 5)
        requirements = self.inventory.get_kit('K1')['requirements']
        self.assertEqual([(r['sku'], r['quantity']) for r in requirements], [('A', 3), ('B', 5)])
        with self.assertRaises(ConflictError):
            self.inventory.create_kit('K1', 'Duplicate project')

    def test_insufficient_multi_item_reservation_is_all_or_nothing(self):
        self.add('A', 10)
        self.add('B', 2)
        self.kit(sku='A', quantity=5)
        self.inventory.set_requirement('K1', 'B', 3)
        before = self.inventory.export_snapshot()
        plan = self.inventory.plan_kit('K1')
        self.assertFalse(plan['ready'])
        self.assertEqual([line['shortage'] for line in plan['lines']], [0, 1])
        with self.assertRaises(ConflictError):
            self.inventory.reserve_kit('K1')
        self.assertEqual(self.inventory.export_snapshot(), before)

    def test_other_kits_reduce_availability_but_own_reservation_does_not(self):
        self.add(quantity=6)
        self.kit('K1', quantity=4)
        self.kit('K2', quantity=3)
        self.inventory.reserve_kit('K1')
        self.assertTrue(self.inventory.plan_kit('K1')['ready'])
        self.assertFalse(self.inventory.plan_kit('K2')['ready'])
        self.assertEqual(self.inventory.plan_kit('K2')['lines'][0]['shortage'], 1)
        self.assertTrue(self.inventory.list_items()[0]['low_stock'])
        with self.assertRaises(ConflictError):
            self.inventory.adjust_stock('P1', -3, 'Count correction')
        self.inventory.adjust_stock('P1', -2, 'Count correction')
        self.assertEqual(self.inventory.list_items()[0]['available'], 0)

    def test_invalid_transitions_do_not_change_state(self):
        self.add()
        self.kit()
        for action in (self.inventory.release_kit, self.inventory.consume_kit):
            with self.assertRaises(ConflictError):
                action('K1')
        self.inventory.reserve_kit('K1')
        before = self.inventory.export_snapshot()
        with self.assertRaises(ConflictError):
            self.inventory.reserve_kit('K1')
        with self.assertRaises(ConflictError):
            self.inventory.set_requirement('K1', 'P1', 1)
        self.assertEqual(self.inventory.export_snapshot(), before)
        self.inventory.consume_kit('K1')
        before = self.inventory.export_snapshot()
        for action in (self.inventory.reserve_kit, self.inventory.release_kit, self.inventory.consume_kit):
            with self.assertRaises(ConflictError):
                action('K1')
        with self.assertRaises(ConflictError):
            self.inventory.set_requirement('K1', 'P1', 1)
        self.assertEqual(self.inventory.export_snapshot(), before)

    def test_empty_kit_cannot_be_reserved(self):
        self.inventory.create_kit('K1', 'Empty project')
        self.assertFalse(self.inventory.plan_kit('K1')['ready'])
        with self.assertRaises(ConflictError):
            self.inventory.reserve_kit('K1')

    def test_consume_rolls_back_all_items_and_ledger_on_write_failure(self):
        self.add('A')
        self.add('B')
        self.kit(sku='A', quantity=2)
        self.inventory.set_requirement('K1', 'B', 3)
        self.inventory.reserve_kit('K1')
        before = self.inventory.export_snapshot()
        # Inject a storage failure on the second item, after the first changed.
        with closing(sqlite3.connect(self.database)) as fault, fault:
            fault.execute("CREATE TRIGGER reject_movement BEFORE INSERT ON movements "
                          "WHEN NEW.sku = 'B' AND NEW.kit_code IS NOT NULL "
                          "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.inventory.consume_kit('K1')
        self.assertEqual(self.inventory.export_snapshot(), before)
        with closing(sqlite3.connect(self.database)) as fault, fault:
            fault.execute('DROP TRIGGER reject_movement')
        self.inventory.consume_kit('K1')
        self.assertEqual([i['on_hand'] for i in self.inventory.list_items()], [8, 7])

    def test_competing_connections_cannot_double_reserve(self):
        self.add(quantity=1)
        self.kit('K1', quantity=1)
        self.kit('K2', quantity=1)
        barrier = Barrier(2)

        def reserve(code):
            with Inventory(self.database, initialize=False) as connection:
                barrier.wait(timeout=5)
                try:
                    connection.reserve_kit(code)
                    return 'reserved'
                except ConflictError:
                    return 'conflict'

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ['K1', 'K2']))
        self.assertCountEqual(results, ['reserved', 'conflict'])
        self.assertEqual(self.inventory.list_items()[0]['reserved'], 1)
        self.assertEqual(sorted(k['status'] for k in self.inventory.list_kits()), ['draft', 'reserved'])

    def test_snapshot_is_json_serialisable_and_deterministically_ordered(self):
        self.add('Z')
        self.add('A')
        self.kit('K2', 'Z')
        self.kit('K1', 'A')
        first = json.dumps(self.inventory.export_snapshot(), sort_keys=True)
        second = json.dumps(self.inventory.export_snapshot(), sort_keys=True)
        self.assertEqual(first, second)
        self.assertEqual([i['sku'] for i in json.loads(first)['items']], ['A', 'Z'])
        self.assertEqual([k['code'] for k in json.loads(first)['kits']], ['K1', 'K2'])

    def test_snapshot_cannot_mix_state_before_and_after_another_commit(self):
        self.add()
        # WAL allows a writer to commit while the export's read snapshot remains
        # open. This makes a mixed-state export observable if its transaction is
        # accidentally removed.
        with closing(sqlite3.connect(self.database)) as configuration, configuration:
            self.assertEqual(configuration.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        with Inventory(self.database, initialize=False) as writer:
            original_read = self.inventory.list_items

            def read_then_commit():
                items = original_read()
                writer.adjust_stock('P1', 1, 'Count correction during export')
                return items

            with patch.object(self.inventory, 'list_items', side_effect=read_then_commit):
                snapshot = self.inventory.export_snapshot()
        self.assertEqual(snapshot['items'][0]['on_hand'], 10)
        self.assertEqual(sum(m['delta'] for m in snapshot['movements']), 10)
        self.assertEqual(self.inventory.list_items()[0]['on_hand'], 11)

    def test_foreign_keys_and_numeric_constraints_are_enabled(self):
        self.assertEqual(self.inventory._connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.inventory._connection.execute(
                "INSERT INTO requirements (kit_code, sku, quantity) VALUES ('missing', 'missing', 1)"
            )
        self.add()
        with self.assertRaises(sqlite3.IntegrityError):
            self.inventory._connection.execute("UPDATE items SET on_hand = -1 WHERE sku = 'P1'")


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'inventory.db'

    def test_existing_mode_does_not_create_missing_database(self):
        with self.assertRaises(NotFoundError):
            Inventory(self.database, initialize=False)
        self.assertFalse(self.database.exists())

    def test_existing_mode_does_not_initialise_empty_file(self):
        self.database.touch()
        with self.assertRaises(ValidationError):
            Inventory(self.database, initialize=False)
        self.assertEqual(self.database.read_bytes(), b'')

    def test_unknown_database_is_not_modified(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('CREATE TABLE unrelated (value TEXT)')
            connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
        before = self.database.read_bytes()
        for initialise in (True, False):
            with self.assertRaises(ValidationError):
                Inventory(self.database, initialize=initialise)
            self.assertEqual(self.database.read_bytes(), before)

    def test_newer_schema_is_not_modified(self):
        with Inventory(self.database):
            pass
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('PRAGMA user_version = 2')
        before = self.database.read_bytes()
        for initialise in (True, False):
            with self.assertRaises(ValidationError):
                Inventory(self.database, initialize=initialise)
            self.assertEqual(self.database.read_bytes(), before)

    def test_schema_marker_alone_does_not_make_an_unrelated_db_valid(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(f'PRAGMA application_id = {APPLICATION_ID}')
            connection.execute('PRAGMA user_version = 1')
            connection.execute('CREATE TABLE items (sku TEXT)')
        before = self.database.read_bytes()
        with self.assertRaises(ValidationError):
            Inventory(self.database, initialize=False)
        self.assertEqual(self.database.read_bytes(), before)

    def test_unexpected_table_is_rejected_even_with_a_sqlite_like_name(self):
        with Inventory(self.database):
            pass
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('CREATE TABLE sqlitex_extra (value TEXT)')
        before = self.database.read_bytes()
        with self.assertRaises(ValidationError):
            Inventory(self.database, initialize=False)
        self.assertEqual(self.database.read_bytes(), before)

    def test_initialisation_is_idempotent_and_supports_memory_database(self):
        with Inventory(self.database) as first:
            first.add_item('P1', 'Part')
        with Inventory(self.database) as second:
            self.assertEqual(second.list_items()[0]['sku'], 'P1')
        with Inventory(':memory:') as memory:
            self.assertEqual(memory.list_items(), [])


if __name__ == '__main__':
    unittest.main()
