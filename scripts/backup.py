"""Горячий бэкап SQLite (безопасно при работающем боте).

    python scripts/backup.py              # -> backups/coffee-YYYYmmdd-HHMMSS.db
    python scripts/backup.py --keep 30    # хранить последние 30 копий
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import get_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="backups")
    parser.add_argument("--keep", type=int, default=30)
    args = parser.parse_args()

    src_path = Path(get_config().db_path)
    if not src_path.exists():
        sys.exit(f"База не найдена: {src_path}")
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_path = out_dir / f"coffee-{datetime.now():%Y%m%d-%H%M%S}.db"

    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)  # согласованная копия даже во время записи (WAL)
    dst.close()
    src.close()
    print(f"Бэкап: {dst_path}")

    copies = sorted(out_dir.glob("coffee-*.db"))
    for old in copies[: max(0, len(copies) - args.keep)]:
        old.unlink()


if __name__ == "__main__":
    main()
