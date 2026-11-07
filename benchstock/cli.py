"""Command-line interface for the local Benchstock inventory."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .core import Inventory, InventoryError, ValidationError


class UsageError(ValueError):
    """Invalid command-line syntax, including nested subcommands."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="benchstock",
        description="Offline parts inventory and project-kit reservations.",
        epilog="Put --db and --json before the command. Only init creates a database.",
    )
    parser.add_argument("--db", type=Path, default=Path("data/benchstock.db"))
    parser.add_argument("--json", action="store_true", help="JSON results and errors")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create or validate a local database")

    item = commands.add_parser("item", help="List or register parts")
    items = item.add_subparsers(dest="operation", required=True)
    add = items.add_parser("add", help="Register a new part with zero stock")
    add.add_argument("sku")
    add.add_argument("name")
    add.add_argument("--location", default="")
    add.add_argument("--minimum", type=int, default=0)
    items.add_parser("list", help="Show quantities and available stock")

    stock = commands.add_parser("stock", help="Record physical stock movements")
    stocks = stock.add_subparsers(dest="operation", required=True)
    adjust = stocks.add_parser("adjust", help="Apply a signed quantity change")
    adjust.add_argument("sku")
    adjust.add_argument("delta", type=int)
    adjust.add_argument("--reason", required=True)

    kit = commands.add_parser("kit", help="Plan, reserve and consume project kits")
    kits = kit.add_subparsers(dest="operation", required=True)
    create = kits.add_parser("create", help="Start an empty draft kit")
    create.add_argument("code")
    create.add_argument("name")
    require = kits.add_parser("require", help="Set the quantity of a part in a draft kit")
    require.add_argument("code")
    require.add_argument("sku")
    require.add_argument("quantity", type=int)
    kits.add_parser("list", help="Show all kits")
    for action, help_text in (
        ("show", "Show a kit and its requirements"),
        ("plan", "Check availability without changing stock"),
        ("reserve", "Reserve every required part atomically"),
        ("release", "Return a reserved kit to draft"),
        ("consume", "Use every reserved part and complete the kit"),
    ):
        sub = kits.add_parser(action, help=help_text)
        sub.add_argument("code")

    history = commands.add_parser("history", help="Show the stock movement ledger")
    history.add_argument("--sku")
    export = commands.add_parser("export", help="Export a snapshot or an inventory CSV")
    export.add_argument("--format", choices=("json", "csv"), required=True)
    export.add_argument(
        "--output", type=Path,
        help="New UTF-8 file; existing files are never overwritten. Default: stdout.",
    )
    return parser


def _dispatch(args: argparse.Namespace, inventory: Inventory) -> Any:
    if args.command == "init":
        return {"database": str(args.db), "schema_version": 1}
    if args.command == "item":
        if args.operation == "list":
            return inventory.list_items()
        return inventory.add_item(args.sku, args.name, args.location, args.minimum)
    if args.command == "stock":
        return inventory.adjust_stock(args.sku, args.delta, args.reason)
    if args.command == "kit":
        if args.operation == "create":
            return inventory.create_kit(args.code, args.name)
        if args.operation == "require":
            return inventory.set_requirement(args.code, args.sku, args.quantity)
        if args.operation == "list":
            return inventory.list_kits()
        method = {
            "show": inventory.get_kit,
            "plan": inventory.plan_kit,
            "reserve": inventory.reserve_kit,
            "release": inventory.release_kit,
            "consume": inventory.consume_kit,
        }[args.operation]
        return method(args.code)
    if args.command == "history":
        return inventory.history(args.sku)
    return inventory.export_snapshot()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _table(rows: list[dict[str, Any]], columns: Sequence[tuple[str, str]]) -> str:
    if not rows:
        return "No entries.\n"
    cells = [[str(row.get(key, "")) for key, _ in columns] for row in rows]
    headers = [heading for _, heading in columns]
    widths = [max(len(headers[i]), *(len(row[i]) for row in cells)) for i in range(len(headers))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(headers, widths)).rstrip()]
    lines.extend("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in cells)
    return "\n".join(lines) + "\n"


def _human(args: argparse.Namespace, result: Any) -> str:
    if args.command == "init":
        return f"Database ready: {result['database']} (schema {result['schema_version']})\n"
    if args.command in ("item", "stock"):
        rows = result if isinstance(result, list) else [result]
        rendered = _table(rows, (
            ("sku", "SKU"), ("name", "Name"), ("location", "Location"),
            ("on_hand", "On hand"), ("reserved", "Reserved"),
            ("available", "Available"), ("minimum", "Minimum"),
        ))
        low = [row["sku"] for row in rows if row["low_stock"]]
        if low:
            rendered += "Below minimum available stock: " + ", ".join(low) + "\n"
        return rendered
    if args.command == "kit" and args.operation == "plan":
        title = f"Kit {result['code']} ({result['status']}): "
        title += "requirements covered" if result["ready"] else "not ready to reserve"
        return title + "\n" + _table(result["lines"], (
            ("sku", "SKU"), ("required", "Required"), ("on_hand", "On hand"),
            ("reserved", "Reserved"), ("available", "Available to kit"), ("shortage", "Shortage"),
        ))
    if args.command == "kit":
        kits = result if isinstance(result, list) else [result]
        if not kits:
            return "No kits.\n"
        lines = []
        for kit in kits:
            lines.append(f"{kit['code']} ({kit['status']}): {kit['name']}")
            for requirement in kit["requirements"]:
                lines.append(f"  {requirement['sku']}: {requirement['quantity']} x {requirement['name']}")
            if not kit["requirements"]:
                lines.append("  No requirements yet.")
        return "\n".join(lines) + "\n"
    if args.command == "history":
        return _table(result, (
            ("id", "ID"), ("sku", "SKU"), ("delta", "Change"),
            ("reason", "Reason"), ("kit_code", "Kit"), ("created_at", "Time (UTC)"),
        ))
    return ""


CSV_COLUMNS = ("sku", "name", "location", "minimum", "on_hand", "reserved", "available", "low_stock")


def _spreadsheet_text(value: str) -> str:
    # Spreadsheet applications may ignore leading whitespace and a byte-order mark.
    candidate = value
    while candidate and (candidate[0].isspace() or candidate[0] == "\ufeff"):
        candidate = candidate[1:]
    if candidate.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def _csv(snapshot: dict[str, Any]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for item in snapshot["items"]:
        row = []
        for field in CSV_COLUMNS:
            value = item[field]
            if isinstance(value, str):
                value = _spreadsheet_text(value)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            row.append(value)
        writer.writerow(row)
    return stream.getvalue()


def _export(args: argparse.Namespace, snapshot: dict[str, Any]) -> None:
    text = _json(snapshot) if args.format == "json" else _csv(snapshot)
    if args.output is None:
        sys.stdout.write(text)
        return
    try:
        target = args.output.resolve()
        database = args.db.resolve()
    except RuntimeError as error:
        # pathlib on Python 3.11/3.12 reports a symlink loop as RuntimeError.
        raise ValidationError(str(error)) from error
    protected = {database, *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
    if target in protected:
        raise ValidationError("Export path must differ from the database and its sidecar files.")
    with args.output.open("x", encoding="utf-8", newline="") as output:
        output.write(text)
    if args.json:
        sys.stdout.write(_json({"path": str(args.output), "format": args.format, "items": len(snapshot["items"])}))
    else:
        label = "Full snapshot" if args.format == "json" else "Inventory summary"
        sys.stdout.write(f"{label} exported to {args.output}\n")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    option_arguments = arguments[:arguments.index("--")] if "--" in arguments else arguments
    json_errors = "--json" in option_arguments
    try:
        args = build_parser().parse_args(arguments)
        json_errors = args.json
        if str(args.db) == ":memory:":
            raise ValidationError("The CLI requires a file database; :memory: is only supported through the Python API.")
        if args.command == "init":
            args.db.parent.mkdir(parents=True, exist_ok=True)
        with Inventory(args.db, initialize=args.command == "init") as inventory:
            result = _dispatch(args, inventory)
        if args.command == "export":
            _export(args, result)
        else:
            sys.stdout.write(_json(result) if args.json else _human(args, result))
        return 0
    except (UsageError, InventoryError, OSError, sqlite3.Error, UnicodeError) as error:
        if json_errors:
            sys.stderr.write(_json({"error": {"type": type(error).__name__, "message": str(error)}}))
        else:
            sys.stderr.write(f"benchstock: error: {error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted.\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
