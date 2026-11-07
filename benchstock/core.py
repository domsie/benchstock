"""Transactional, offline inventory and kit reservations backed by SQLite.

Each mutation obtains SQLite's write lock before checking availability. Reserved
quantities are derived from kit requirements, so there is no second stock counter
to reconcile. Instances are deliberately single-threaded; use one per thread.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
from typing import Iterator


SCHEMA_VERSION = 1
APPLICATION_ID = 0x424E5354  # BNST
MAX_QUANTITY = 1_000_000_000
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class InventoryError(Exception):
    """An expected inventory operation failed."""


class ValidationError(InventoryError):
    """An input or database schema is invalid."""


class NotFoundError(InventoryError):
    """A requested database, item or kit does not exist."""


class ConflictError(InventoryError):
    """The requested operation conflicts with the current inventory state."""


_SCHEMA = (
    """CREATE TABLE items (
        sku TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        minimum INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(minimum) = 'integer' AND minimum BETWEEN 0 AND 1000000000),
        on_hand INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(on_hand) = 'integer' AND on_hand BETWEEN 0 AND 1000000000)
    )""",
    """CREATE TABLE kits (
        code TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft', 'reserved', 'consumed'))
    )""",
    """CREATE TABLE requirements (
        kit_code TEXT NOT NULL REFERENCES kits(code),
        sku TEXT NOT NULL REFERENCES items(sku),
        quantity INTEGER NOT NULL
            CHECK(typeof(quantity) = 'integer' AND quantity BETWEEN 1 AND 1000000000),
        PRIMARY KEY (kit_code, sku)
    )""",
    """CREATE TABLE movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL REFERENCES items(sku),
        delta INTEGER NOT NULL
            CHECK(typeof(delta) = 'integer' AND delta != 0
                  AND delta BETWEEN -1000000000 AND 1000000000),
        reason TEXT NOT NULL,
        kit_code TEXT REFERENCES kits(code),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )""",
)


def _code(value: str, label: str) -> str:
    if not isinstance(value, str) or not _CODE.fullmatch(value.strip()):
        raise ValidationError(
            f"{label} must be 1-64 characters: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit."
        )
    return value.strip()


def _text(value: str, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text.")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in value):
        raise ValidationError(f"{label} cannot contain control characters.")
    result = value.strip()
    if len(result) > 200 or (not result and not allow_empty):
        raise ValidationError(f"{label} must contain {'0' if allow_empty else '1'}-200 characters.")
    return result


def _integer(value: int, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_QUANTITY:
        raise ValidationError(f"{label} must be an integer between {minimum} and {MAX_QUANTITY}.")
    return value


class Inventory:
    """A local inventory connection.

    ``initialize=False`` never creates a database and rejects empty files. Only
    the CLI's explicit ``init`` command uses ``initialize=True``. Parent directory
    creation is left to the caller. No migrations beyond schema v1 are attempted.
    """

    def __init__(self, database: str | Path, *, initialize: bool = True):
        if not isinstance(database, (str, Path)) or not str(database):
            raise ValidationError("database must be a file path or ':memory:'.")
        path = str(database)
        if not initialize:
            if path == ':memory:' or not Path(path).is_file():
                raise NotFoundError("Database does not exist; run 'init' first.")
            path = Path(path).resolve().as_uri() + '?mode=rw'
        self._connection = sqlite3.connect(
            path, timeout=5.0, isolation_level=None, uri=not initialize
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA foreign_keys = ON')
        try:
            self._prepare_schema(initialize)
        except BaseException:
            self._connection.close()
            raise

    def __enter__(self) -> Inventory:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        """Close this connection. Calling close more than once is harmless."""
        self._connection.close()

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[None]:
        # Nested read helpers reuse the surrounding mutation/export snapshot.
        if self._connection.in_transaction:
            yield
            return
        self._connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def _prepare_schema(self, initialize: bool) -> None:
        with self._transaction(write=initialize):
            version = self._connection.execute('PRAGMA user_version').fetchone()[0]
            app_id = self._connection.execute('PRAGMA application_id').fetchone()[0]
            definitions = self._connection.execute(
                "SELECT type, name, sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
            ).fetchall()
            if version == 0 and app_id == 0 and not definitions:
                if not initialize:
                    raise ValidationError("Database is empty; run 'init' first.")
                for statement in _SCHEMA:
                    self._connection.execute(statement)
                self._connection.execute(f'PRAGMA application_id = {APPLICATION_ID}')
                self._connection.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
                return
            if version != SCHEMA_VERSION or app_id != APPLICATION_ID:
                raise ValidationError(
                    f"Unrecognised database or unsupported schema version {version}; "
                    f"expected a Benchstock schema {SCHEMA_VERSION} database."
                )
            # Exact canonical definitions also catch altered constraints and
            # unexpected triggers, not just a database with matching table names.
            normalise = lambda sql: ' '.join(sql.split())
            expected = {normalise(statement) for statement in _SCHEMA}
            actual = {normalise(row['sql']) for row in definitions if row['sql']}
            if actual != expected or len(definitions) != len(_SCHEMA):
                raise ValidationError("Unrecognised or modified Benchstock database schema.")

    def _items(self, sku: str | None = None) -> list[dict]:
        query = """SELECT i.sku, i.name, i.location, i.minimum, i.on_hand,
                          COALESCE(r.quantity, 0) AS reserved
                   FROM items AS i
                   LEFT JOIN (
                       SELECT req.sku, SUM(req.quantity) AS quantity
                       FROM requirements AS req JOIN kits ON kits.code = req.kit_code
                       WHERE kits.status = 'reserved' GROUP BY req.sku
                   ) AS r ON r.sku = i.sku"""
        parameters = ()
        if sku is not None:
            query += ' WHERE i.sku = ?'
            parameters = (sku,)
        rows = self._connection.execute(query + ' ORDER BY i.sku', parameters).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['available'] = item['on_hand'] - item['reserved']
            item['low_stock'] = item['available'] < item['minimum']
            items.append(item)
        return items

    def _item(self, sku: str) -> dict:
        items = self._items(sku)
        if not items:
            raise NotFoundError(f"Item '{sku}' does not exist.")
        return items[0]

    def add_item(self, sku: str, name: str, location: str = '', minimum: int = 0) -> dict:
        sku, name = _code(sku, 'sku'), _text(name, 'name')
        location = _text(location, 'location', allow_empty=True)
        minimum = _integer(minimum, 'minimum')
        with self._transaction(write=True):
            if self._items(sku):
                raise ConflictError(f"Item '{sku}' already exists.")
            self._connection.execute(
                'INSERT INTO items (sku, name, location, minimum) VALUES (?, ?, ?, ?)',
                (sku, name, location, minimum),
            )
            return self._item(sku)

    def list_items(self) -> list[dict]:
        with self._transaction():
            return self._items()

    def adjust_stock(self, sku: str, delta: int, reason: str) -> dict:
        sku = _code(sku, 'sku')
        delta = _integer(delta, 'delta', minimum=-MAX_QUANTITY)
        if delta == 0:
            raise ValidationError('delta must not be zero.')
        reason = _text(reason, 'reason')
        with self._transaction(write=True):
            item = self._item(sku)
            new_stock = item['on_hand'] + delta
            if new_stock < item['reserved']:
                raise ConflictError('Adjustment would reduce stock below the reserved quantity.')
            if new_stock > MAX_QUANTITY:
                raise ConflictError(f'Adjustment would exceed the stock limit of {MAX_QUANTITY}.')
            self._connection.execute('UPDATE items SET on_hand = ? WHERE sku = ?', (new_stock, sku))
            self._connection.execute(
                'INSERT INTO movements (sku, delta, reason) VALUES (?, ?, ?)', (sku, delta, reason)
            )
            return self._item(sku)

    def _kit(self, code: str) -> dict:
        row = self._connection.execute('SELECT code, name, status FROM kits WHERE code = ?', (code,)).fetchone()
        if row is None:
            raise NotFoundError(f"Kit '{code}' does not exist.")
        kit = dict(row)
        kit['requirements'] = [dict(r) for r in self._connection.execute(
            'SELECT req.sku, i.name, req.quantity FROM requirements AS req '
            'JOIN items AS i ON i.sku = req.sku WHERE req.kit_code = ? ORDER BY req.sku',
            (code,),
        )]
        return kit

    def create_kit(self, code: str, name: str) -> dict:
        code, name = _code(code, 'code'), _text(name, 'name')
        with self._transaction(write=True):
            if self._connection.execute('SELECT 1 FROM kits WHERE code = ?', (code,)).fetchone():
                raise ConflictError(f"Kit '{code}' already exists.")
            self._connection.execute('INSERT INTO kits (code, name) VALUES (?, ?)', (code, name))
            return self._kit(code)

    def set_requirement(self, code: str, sku: str, quantity: int) -> dict:
        code, sku = _code(code, 'code'), _code(sku, 'sku')
        quantity = _integer(quantity, 'quantity', minimum=1)
        with self._transaction(write=True):
            self._require_status(self._kit(code), 'draft')
            self._item(sku)
            self._connection.execute(
                'INSERT INTO requirements (kit_code, sku, quantity) VALUES (?, ?, ?) '
                'ON CONFLICT(kit_code, sku) DO UPDATE SET quantity = excluded.quantity',
                (code, sku, quantity),
            )
            return self._kit(code)

    def get_kit(self, code: str) -> dict:
        code = _code(code, 'code')
        with self._transaction():
            return self._kit(code)

    def list_kits(self) -> list[dict]:
        with self._transaction():
            codes = self._connection.execute('SELECT code FROM kits ORDER BY code').fetchall()
            return [self._kit(row['code']) for row in codes]

    @staticmethod
    def _require_status(kit: dict, expected: str) -> None:
        if kit['status'] != expected:
            raise ConflictError(
                f"Kit '{kit['code']}' is {kit['status']}; this operation requires {expected}."
            )

    def _plan(self, kit: dict) -> dict:
        items = {item['sku']: item for item in self._items()}
        lines = []
        for requirement in kit['requirements']:
            item = items[requirement['sku']]
            required = requirement['quantity']
            available = item['available'] + (required if kit['status'] == 'reserved' else 0)
            lines.append({
                'sku': requirement['sku'], 'required': required, 'on_hand': item['on_hand'],
                'reserved': item['reserved'], 'available': available,
                'shortage': max(0, required - available),
            })
        return {
            'code': kit['code'], 'status': kit['status'],
            'ready': bool(lines) and kit['status'] != 'consumed' and all(line['shortage'] == 0 for line in lines),
            'lines': lines,
        }

    def plan_kit(self, code: str) -> dict:
        code = _code(code, 'code')
        with self._transaction():
            return self._plan(self._kit(code))

    def reserve_kit(self, code: str) -> dict:
        code = _code(code, 'code')
        with self._transaction(write=True):
            kit = self._kit(code)
            self._require_status(kit, 'draft')
            plan = self._plan(kit)
            if not plan['lines']:
                raise ConflictError('An empty kit cannot be reserved.')
            if not plan['ready']:
                shortages = ', '.join(f"{line['sku']}: {line['shortage']}" for line in plan['lines'] if line['shortage'])
                raise ConflictError(f'Insufficient available stock ({shortages}).')
            self._connection.execute("UPDATE kits SET status = 'reserved' WHERE code = ?", (code,))
            return self._kit(code)

    def release_kit(self, code: str) -> dict:
        code = _code(code, 'code')
        with self._transaction(write=True):
            self._require_status(self._kit(code), 'reserved')
            self._connection.execute("UPDATE kits SET status = 'draft' WHERE code = ?", (code,))
            return self._kit(code)

    def consume_kit(self, code: str) -> dict:
        code = _code(code, 'code')
        with self._transaction(write=True):
            kit = self._kit(code)
            self._require_status(kit, 'reserved')
            for requirement in kit['requirements']:
                sku, quantity = requirement['sku'], requirement['quantity']
                updated = self._connection.execute(
                    'UPDATE items SET on_hand = on_hand - ? WHERE sku = ? AND on_hand >= ?',
                    (quantity, sku, quantity),
                )
                if updated.rowcount != 1:
                    raise ConflictError('Stock no longer covers the reserved kit.')
                self._connection.execute(
                    'INSERT INTO movements (sku, delta, reason, kit_code) VALUES (?, ?, ?, ?)',
                    (sku, -quantity, f'Consumed by kit {code}', code),
                )
            self._connection.execute("UPDATE kits SET status = 'consumed' WHERE code = ?", (code,))
            return self._kit(code)

    def history(self, sku: str | None = None) -> list[dict]:
        if sku is not None:
            sku = _code(sku, 'sku')
        with self._transaction():
            query = 'SELECT id, sku, delta, reason, kit_code, created_at FROM movements'
            parameters = ()
            if sku is not None:
                self._item(sku)
                query += ' WHERE sku = ?'
                parameters = (sku,)
            return [dict(row) for row in self._connection.execute(query + ' ORDER BY id', parameters)]

    def export_snapshot(self) -> dict:
        """Return a consistently ordered, single-transaction read snapshot."""
        with self._transaction():
            return {
                'schema_version': SCHEMA_VERSION,
                'items': self.list_items(),
                'kits': self.list_kits(),
                'movements': self.history(),
            }
