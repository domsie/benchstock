# Baseline interface

This is the implementation contract for the first vertical slice, not a claim of completed features.

Python 3.11+; standard library at runtime and for tests. The package lives directly in `benchstock/`.
`python -m benchstock` must work from the repository root without installation.

## Core

`benchstock.core.Inventory(database: str | Path, *, initialize=True)` is a context manager and has `close()`.
Construction connects, validates/creates schema v1, enables foreign keys and a bounded busy timeout.
`initialize=False` must open only an existing recognised database, using SQLite `mode=rw`; it never creates or initialises a missing/empty/unknown file. The CLI uses this mode except for `init`. The core never creates parent directories.
Reject unknown/newer schemas without rewriting them. Use an explicit immediate transaction for each mutation.

All public methods return JSON-serialisable dictionaries/lists; `InventoryError` is the base expected error, with `ValidationError`, `NotFoundError`, `ConflictError`.
Codes/SKUs are trimmed, case-sensitive ASCII `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
Quantities are real integers (bool not accepted), bounded at 1,000,000,000; signs vary by operation.
Text is nonempty after trimming, bounded to 200 characters, no control characters.

- `add_item(sku, name, location="", minimum=0) -> dict`: zero opening stock; unique SKU; location may be blank.
- `list_items() -> list[dict]`: sorted SKU; item keys `sku,name,location,minimum,on_hand,reserved,available,low_stock`; low_stock means available < minimum.
- `adjust_stock(sku, delta, reason) -> dict`: nonzero signed delta; cannot reduce on_hand below reserved; records ledger entry; returns updated item.
- `create_kit(code, name) -> dict`: unique code, status `draft`.
- `set_requirement(code, sku, quantity) -> dict`: positive quantity; insert/update only draft kits; returns full kit.
- `get_kit(code) -> dict`: `code,name,status,requirements` with each requirement `sku,name,quantity`.
- `list_kits() -> list[dict]`: full kits in code order.
- `plan_kit(code) -> dict`: read-only availability, keys `code,status,ready,lines`; lines `sku,required,on_hand,reserved,available,shortage`. Draft availability excludes all reserved kits. For reserved kit, include its own existing allocation in the planning availability. Empty draft not ready. Consumed kit not reservable.
- `reserve_kit(code) -> dict`: atomically check and reserve all requirements; insufficient stock or empty kit rejects with ConflictError; duplicate reserve conflicts; returns kit with status `reserved`.
- `release_kit(code) -> dict`: reserved -> draft; retains requirements; no stock movement.
- `consume_kit(code) -> dict`: reserved -> consumed; subtract all quantities and record ledger per SKU atomically. Terminal consumed kit is immutable, including double consume rejection.
- `history(sku=None) -> list[dict]`: entries `id,sku,delta,reason,kit_code,created_at`, id ascending; optional SKU must exist.
- `export_snapshot() -> dict`: consistent read snapshot with `schema_version:1,items,kits,movements`; deterministic ordering, no export-time timestamp.

Reservation totals are derived from reserved kits; no duplicated reservation counter. Every opening stock comes from an adjustment. No user account, telemetry, network call, money, customer field, or real personal data.

## CLI

Root: `--db PATH` (default `data/benchstock.db`), `--json` switch for machine output. Options precede subcommands.

`init`; `item add SKU NAME [--location TEXT] [--minimum INT]`; `item list`; `stock adjust SKU DELTA --reason TEXT`; `kit create CODE NAME`; `kit require CODE SKU QUANTITY`; `kit list`; `kit show CODE`; `kit plan CODE`; `kit reserve CODE`; `kit release CODE`; `kit consume CODE`; `history [--sku SKU]`; `export --format json|csv [--output PATH]`.

Read-only operations must not create missing databases. Only `init` creates a database directory/file. Other commands use an existing database. Empty/unrecognised database files must not be silently initialised by a read command.

Success exit 0; argparse/validation/not-found/conflict/storage errors exit 2. With `--json`, errors are `{"error":{"type":"...","message":"..."}}` on stderr and stdout stays empty. Never emit tracebacks for expected input/file errors. Export to a path refuses overwrite; snapshot JSON is full data, CSV is item summary only, with formula-like text made safe for spreadsheet opening and documented. UTF-8 CSV, fixed header order. Output files are never database files; file mode x handles no-overwrite atomically.

## Scope left for later

No importer, stocktake, multi-bin item locations, kit templates/reuse, partial reservations, edit/delete commands, or interactive user interface in this baseline. Those are real planned extensions with their own acceptance criteria.
