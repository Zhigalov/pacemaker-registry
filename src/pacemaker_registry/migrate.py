"""Explicit, transactional SQL migrations: python -m pacemaker_registry.migrate."""

from hashlib import sha256
from pathlib import Path

from pacemaker_registry.db import connect_database


MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def migrate() -> list[str]:
    applied = []
    with connect_database() as connection:
        # Serialize deployers; ledger and schema changes commit together.
        connection.execute("SELECT pg_advisory_xact_lock(734865102)")
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY, checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        existing = dict(connection.execute("SELECT name, checksum FROM schema_migrations").fetchall())
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files or set(existing) - {path.name for path in files}:
            raise RuntimeError("Unknown or missing database migrations; refusing to change schema.")
        for path in files:
            sql = path.read_text(encoding="utf-8")
            checksum = sha256(sql.encode()).hexdigest()
            if path.name in existing:
                if existing[path.name] != checksum:
                    raise RuntimeError(f"Applied migration {path.name} has changed.")
                continue
            connection.execute(sql)
            connection.execute("INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                               (path.name, checksum))
            applied.append(path.name)
    return applied


def main() -> None:
    try:
        applied = migrate()
    except Exception:
        # Driver errors can contain credentials/DSNs; never print them.
        raise SystemExit("Migration failed; transaction rolled back. Check database access and migration history.") from None
    print("Applied: " + ", ".join(applied) if applied else "Database schema is up to date.")


if __name__ == "__main__":
    main()
