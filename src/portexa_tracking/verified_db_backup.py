"""Create and verify independent SQLite backups before any production migration.

Backups use SQLite's online backup API (WAL-safe), never raw file copies.
No source write flag is needed and no source database is modified.
"""
import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from portexa_tracking.portexa_bridge import database_path, source_connection


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_pair(destination):
    directory = Path(destination).expanduser().resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Backup directory must not be a symlink")
    source_path = database_path()
    tracking_path = Path(os.environ.get("PORTEXA_TRACKING_DB", "data/tracking.sqlite3")).expanduser().resolve(strict=True)
    if source_path == tracking_path:
        raise ValueError("Source and operations databases must be distinct")
    if not tracking_path.is_file():
        raise ValueError("Operations database not found")
    if directory == source_path.parent or directory == tracking_path.parent:
        raise ValueError("Backups must be stored in a separate directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = []
    for label, original_path in (("portexa_source", source_path), ("operations", tracking_path)):
        target = directory / f"{label}_{stamp}.sqlite3"
        try:
            with (source_connection() if label == "portexa_source" else sqlite3.connect(
                original_path.as_uri() + "?mode=ro", uri=True, timeout=20
            )) as original:
                with sqlite3.connect(target) as backup:
                    original.backup(backup)
            target.chmod(0o600)
            with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as check:
                result = check.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"Backup integrity check failed for {label}: {result}")
                tables = check.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
            output.append({
                "database": label,
                "backup_file": str(target),
                "bytes": target.stat().st_size,
                "sha256": digest(target),
                "integrity_check": "ok",
                "table_count": tables,
            })
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return {"backups": output, "source_databases_modified": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(json.dumps(backup_pair(args.destination), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
