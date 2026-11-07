# Benchstock

An offline parts inventory and kit planner for a personal workbench.

Benchstock keeps track of small components, cables and adapters, and the quantities set aside for projects. It answers a practical question: do I have enough unreserved parts to assemble this kit? Stock changes are recorded in a local SQLite database. Reserving a kit does not consume its parts; finishing it does.

This is a newly started, AI-assisted prototype, first developed on 7 November 2026. It has no claimed production deployment or established user base. See [development notes](docs/DEVELOPMENT.md) for provenance and [the roadmap](ROADMAP.md) for proposed next steps.

## Current scope

- Add items with a SKU, description, storage label and minimum stock level.
- Record stock adjustments with a reason and inspect the movement history.
- Define kits, check shortages and reserve their complete requirements atomically.
- Release a reservation or consume a completed kit without double-counting stock.
- List physical, reserved and available quantities, including low-stock flags.
- Export a full JSON snapshot or a spreadsheet-friendly CSV inventory summary.
- Use readable terminal output or JSON for shell automation.

The application and test suite use Python's standard library. There is no service to run, runtime package to download, account to create, API key, telemetry or external model. It is intended for object metadata and quantities, not customer records, purchase histories or financial accounting.

## Requirements and quick start

Python 3.11 or later. Commands below run directly from the repository root. On Windows, use `py -3` in place of `python` if necessary. On some Linux systems the command is `python3`.

```console
python -m benchstock --db data/demo.db init
python -m benchstock --db data/demo.db item add USB-C-1M "USB-C cable, 1 m" --location "Drawer A" --minimum 2
python -m benchstock --db data/demo.db stock adjust USB-C-1M 5 --reason "Initial count"
python -m benchstock --db data/demo.db kit create DESK "Desk wiring kit"
python -m benchstock --db data/demo.db kit require DESK USB-C-1M 2
python -m benchstock --db data/demo.db kit plan DESK
python -m benchstock --db data/demo.db kit reserve DESK
python -m benchstock --db data/demo.db item list
python -m benchstock --db data/demo.db kit consume DESK
python -m benchstock --db data/demo.db history
```

After reserving the kit, the cable has **5 on hand, 2 reserved, 3 available**. After consuming it, it has **3 on hand, 0 reserved, 3 available**. A competing kit cannot reserve stock already assigned to this one.

`init` is the only CLI command that creates a database. Repeating it on a valid Benchstock database preserves the data. Other commands reject missing, empty or unrecognised databases. `data/` and database files are excluded from Git.

For a complete example using invented data:

```console
python examples/demo.py --db data/workbench-demo.db
```

Use a fresh path: the demonstration refuses to overwrite an existing file.

## Commands

Global options come **before** the command: `--db PATH`, `--json`.

| Command | Purpose |
| --- | --- |
| `init` | Create a database or validate an existing one |
| `item add SKU NAME [--location TEXT] [--minimum INT]` | Register an item at zero stock |
| `item list` | Show quantities and low-stock status |
| `stock adjust SKU DELTA --reason TEXT` | Add or remove stock with an explanation |
| `kit create CODE NAME` | Create a draft kit |
| `kit require CODE SKU QUANTITY` | Add or replace a draft kit's requirement |
| `kit list` / `kit show CODE` | Inspect kits and their requirements |
| `kit plan CODE` | Check availability without changing stock |
| `kit reserve CODE` | Reserve the entire kit or leave everything unchanged |
| `kit release CODE` | Return a reserved kit to draft |
| `kit consume CODE` | Consume a reserved kit and record its stock movements |
| `history [--sku SKU]` | Inspect the append-only application movement history |
| `export --format json\|csv [--output PATH]` | Write a snapshot/summary; omit the path for stdout |

Successful commands exit with code `0`; input, state and storage errors exit with `2`. With `--json`, results go to stdout and structured errors to stderr. A plan with shortages is a successful query with `ready: false`; the corresponding reservation is rejected.

```console
python -m benchstock --db data/demo.db --json item list
python -m benchstock --db data/demo.db export --format json --output snapshot.json
python -m benchstock --db data/demo.db export --format csv --output inventory.csv
```

Exports refuse to overwrite files. JSON contains items, kits and movements in stable order. CSV contains only the current item summary, not kit or movement data. Formula-like CSV text is escaped for opening in spreadsheets; JSON retains exact text. Export files may contain whatever item descriptions you entered, so keep private working exports outside the repository. The baseline has no JSON/CSV restore or import command.

For `export`, `--format` determines the stdout data format even when `--json` is present; `--json` still controls errors and the confirmation returned for a file export.

## Data rules

- SKUs and kit codes are case-sensitive and use letters, digits, dots, underscores or hyphens, beginning with a letter or digit; at most 64 characters.
- Quantities are whole units, bounded to one billion. Fractional stock and unit conversion are outside the current scope.
- Item names, locations and adjustment reasons are bounded plain text. A location is optional.
- Available stock is physical stock minus reservations. Adjustments cannot remove reserved stock.
- A kit follows `draft -> reserved -> consumed`; `reserved -> draft` releases it. Consumed kits are immutable records.
- A stock movement records a change in physical quantity. Reserve/release operations do not move physical stock and do not create movement entries. Full reservation event history is not implemented.

See [architecture and tradeoffs](docs/ARCHITECTURE.md) for transaction behaviour and limitations.

## Development and tests

No test framework installation is needed:

```console
python -m unittest discover -s tests -v
python -m compileall -q benchstock tests examples
```

Optional installation into a virtual environment adds a `benchstock` command:

```console
python -m venv .venv
# Activate .venv using the instructions for your shell.
python -m pip install .
benchstock --help
```

Packaging uses setuptools as a build dependency; running from the checkout and running tests do not. The GitHub Actions configuration covers Linux and Windows with Python 3.11 and 3.12. That is configured CI coverage, not a claim that hosted jobs have already run. Local validation results and remaining checks are documented in [development notes](docs/DEVELOPMENT.md).

## Status and next work

The current release candidate is a complete CLI slice, with further product work in [ROADMAP.md](ROADMAP.md): previewed CSV import, stocktakes, multiple bins, reusable kit templates, an interactive terminal view, and backup/restore with schema upgrades. Those features are not implemented. There is no browser interface, cloud synchronisation, multi-user access control, barcode support or hardware integration in this version.

The project is intended for a single person's local inventory. The database is not an authenticated or tamper-proof record. Keep it on a local disk and back it up; do not use the prototype as the sole record of irreplaceable data.

## Licence

MIT. See [LICENSE](LICENSE). Third-party dependency and provenance notes are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
