# Architecture and tradeoffs

## Layers

`cli.py` handles argument parsing, output and file destinations. `core.py` owns validation and state transitions. SQLite persists items, kit requirements, kit state and physical stock movements. The CLI does not issue SQL or reproduce allocation rules.

The runtime has no third-party dependencies. This makes the first version easy to inspect and run offline, at the cost of a deliberately simple terminal interface. Python's standard-library SQLite module is the storage adapter; the database does not need a separate server.

## Stock model

An item has one physical balance. Its reserved quantity is calculated from the requirements of kits whose status is `reserved`. Available quantity is `on_hand - reserved`. Keeping reservations derived from kit state avoids maintaining a second counter that can drift from the requirements.

Every physical change has a movement entry. Adding an item starts it at zero, and an explicit adjustment supplies its initial stock. Reservation and release change allocation only. Consumption reduces stock and records one movement per required item, within the same transaction as the terminal kit transition.

A reserved kit's own planning view adds back its allocation. Otherwise it would appear short because of the stock it already owns. Competing draft kits do not get that allowance.

## Transaction boundaries

Each mutation validates its inputs and uses `BEGIN IMMEDIATE` to serialise writers before reading balances. Stock checks and writes take place in that same transaction. A failed requirement or a failed movement rolls back the whole operation. A second local process observes committed state rather than independently claiming the same available parts.

The JSON export reads items, kits and movement history inside one read transaction. Ordered queries make identical database states produce identical snapshots; no export-time timestamp is added. This is a snapshot of application data, not a database backup/restore implementation.

The busy timeout is bounded. Exhausting it is an error to retry later, not an instruction to bypass locking. SQLite file-lock behaviour on network-mounted drives is outside the supported deployment model; use local storage.

## Schema and validation

The initial schema is version 1. The implementation recognises its application schema, rejects unsupported versions and does not silently downgrade or repurpose other databases. Future migrations are part of B03/B06 in the roadmap.

Identifiers and quantities have bounded types and values. SQL values are passed as parameters. Display strings cannot include control characters. Unrecognised or empty databases are never initialised during ordinary CLI reads. Existing export files are opened with exclusive creation so a failed overwrite check cannot erase them.

These rules are routine data-integrity measures. The product is not a security, compliance or auditing system, and direct modification of the SQLite file is outside the application's consistency guarantees.

## Deliberate limits

- One label per item for storage; no per-bin quantity yet.
- Whole units only; no decimals, conversions or costs.
- All-or-nothing reservations; no partial picking or substitute parts.
- A reserved kit cannot be edited; release it first.
- A consumed kit is terminal; there is no reverse-consumption command.
- Physical stock history is recorded; full history of reservation/release events is not.
- Export is implemented; import, restore and schema upgrades are future work.
- No deletion or general rename workflow in the baseline.
- No web interface, user accounts, attachments, telemetry or network integration.

These are product boundaries, not intentionally failing paths. Unsupported actions should remain unavailable or return an explicit error.

## Source references

- Python SQLite API and transaction behaviour: https://docs.python.org/3.12/library/sqlite3.html
- Python unittest: https://docs.python.org/3.12/library/unittest.html
- GitHub's Python CI guidance: https://docs.github.com/en/actions/tutorials/build-and-test-code/python

These are technical references. No project code was copied from another repository or generated from a repository template.
