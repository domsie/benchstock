"""Run a synthetic workbench example in a new, explicitly chosen database."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="New database path; existing files are refused")
    args = parser.parse_args()
    database = args.db.resolve()
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        # Claim the path before initialisation, so a concurrent file cannot be reset.
        descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    except OSError as error:
        print(f"demo: {error}", file=sys.stderr)
        return 2

    commands = (
        ("init",),
        ("item", "add", "BOLT-M3", "M3 bolt", "--location", "BIN-A", "--minimum", "8"),
        ("stock", "adjust", "BOLT-M3", "24", "--reason", "Synthetic opening count"),
        ("item", "add", "USB-C", "USB-C adapter", "--location", "BIN-B", "--minimum", "2"),
        ("stock", "adjust", "USB-C", "5", "--reason", "Synthetic opening count"),
        ("kit", "create", "LOGGER", "Prototype logger kit"),
        ("kit", "require", "LOGGER", "BOLT-M3", "6"),
        ("kit", "require", "LOGGER", "USB-C", "2"),
        ("kit", "plan", "LOGGER"),
        ("kit", "reserve", "LOGGER"),
        ("item", "list"),
        ("kit", "consume", "LOGGER"),
        ("item", "list"),
        ("history",),
    )
    root = Path(__file__).resolve().parents[1]
    for command in commands:
        print("\n> " + " ".join(command), flush=True)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "benchstock", "--db", str(database), *command],
                cwd=root,
                check=False,
            )
        except OSError as error:
            print(f"demo: {error}", file=sys.stderr)
            return 2
        if result.returncode:
            print(f"Demo stopped; its partial database remains at {database}.", file=sys.stderr)
            return result.returncode
    print(f"\nSynthetic demo complete. Database: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
