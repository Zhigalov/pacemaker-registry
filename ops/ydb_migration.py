#!/usr/bin/env python3
"""Import a checksum-verified WebSQL snapshot into YDB; never connects to PG.

Run with YDB_ENDPOINT/YDB_DATABASE and short-lived SDK credentials supplied
through the environment. Snapshot files contain personal data: keep them out
of Git and use permissions 0600. Output contains counts/hashes, not row values.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import ydb

from pacemaker_registry import ydb_storage as storage


DATA_TABLES = ("pacemakers", "events", "race_results")


def read_snapshot(path):
    envelope = json.loads(Path(path).read_text())
    item = envelope[0]
    raw = item["backup"].encode("utf-8")
    if hashlib.md5(raw).hexdigest() != item["snapshot_md5"] or len(raw) != item["snapshot_bytes"]:
        raise ValueError("Snapshot checksum/length mismatch; refusing to import")
    snapshot = json.loads(raw)
    if snapshot["format"] != "pacersreg-websql-v1" or set(snapshot["tables"]) != set(DATA_TABLES):
        raise ValueError("Unexpected snapshot format or tables")
    validate_rows(snapshot["tables"])
    return snapshot


def validate_rows(tables):
    for name in DATA_TABLES:
        rows = tables[name]
        if any(set(row) != set(storage.TABLE_COLUMNS[name]) for row in rows):
            raise ValueError(f"Column mismatch in {name}")
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate IDs in {name}")
    people, events, results = (tables[name] for name in DATA_TABLES)
    names = [(r['last_name'].strip(' ').lower(), r['first_name'].strip(' ').lower()) for r in people]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate athlete names")
    for rows, key in ((events, "source_event_id"), (results, "source_participant_id")):
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate source key: {key}")
    people_ids, event_ids = {row['id'] for row in people}, {row['id'] for row in events}
    if any(row['pacemaker_id'] not in people_ids or row['event_id'] not in event_ids for row in results):
        raise ValueError("Broken references")


def canonical_row(row):
    row = dict(row)
    for key in ("checkpoints", "rating_config", "pace_rating_config"):
        if key in row:
            row[key] = storage._json(row[key])
    if "distance_km" in row:
        row["distance_km"] = format(Decimal(str(row["distance_km"])), ".3f")
    created = row["created_at"]
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    elif isinstance(created, int):
        created = datetime.fromtimestamp(created / 1_000_000, timezone.utc)
    if created.tzinfo is None:
        # The SDK decodes Timestamp as naive UTC, not machine-local time.
        created = created.replace(tzinfo=timezone.utc)
    row["created_at"] = created.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return row


def canonical_table(rows):
    return [canonical_row(row) for row in sorted(rows, key=lambda row: row['id'])]


def read_ydb():
    return storage._transaction(lambda tx: {name: [dict(row) for row in storage._select(tx, name)]
                                            for name in DATA_TABLES}, readonly=True)


def verify(snapshot):
    actual = read_ydb()
    report = {}
    for name in DATA_TABLES:
        expected_rows = canonical_table(snapshot["tables"][name])
        actual_rows = canonical_table(actual[name])
        if actual_rows != expected_rows:
            raise ValueError(f"YDB verification failed for {name}; not safe to switch production")
        digest = hashlib.sha256(json.dumps(expected_rows, sort_keys=True, ensure_ascii=False,
                                            separators=(",", ":")).encode()).hexdigest()
        report[name] = {"rows": len(actual_rows), "sha256": digest}
    return report


def import_snapshot(snapshot):
    storage.initialize_database()
    driver, _ = storage._connection()
    for name in DATA_TABLES:
        columns = storage.TABLE_COLUMNS[name]
        types = ydb.StructType()
        for key, kind in columns.items():
            types.add_member(key, storage._type(kind))
        rows = []
        for source in snapshot["tables"][name]:
            row = dict(source)
            row["created_at"] = storage.timestamp_micros(datetime.fromisoformat(row["created_at"]))
            if "distance_km" in row:
                row["distance_km"] = Decimal(str(row["distance_km"]))
            for key in ("checkpoints", "rating_config", "pace_rating_config"):
                if key in row and row[key] is not None:
                    row[key] = json.dumps(row[key], ensure_ascii=False, allow_nan=False)
            rows.append(row)
        path = os.environ["YDB_DATABASE"].rstrip('/') + '/' + storage.table(name).strip('`')
        if rows:
            ydb.retry_operation_sync(lambda: driver.table_client.bulk_upsert(
                path, rows, types, settings=ydb.BaseRequestSettings().with_timeout(20)),
                retry_settings=storage.RETRY)
    def seed(tx):
        for name in DATA_TABLES:
            state = snapshot["sequence_states"][name + "_id_seq"]
            next_id = max(max((row['id'] for row in snapshot['tables'][name]), default=0) + 1,
                          state['last_value'] + int(state['is_called']))
            existing = storage._select(tx, "id_counters", "WHERE name = $name",
                                        {"$name": storage._value(name, "Utf8")})
            next_id = max(next_id, existing[0]['next_id']) if existing else next_id
            storage._upsert(tx, "id_counters", {"name": name, "next_id": next_id})
    storage._transaction(seed)
    return verify(snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        snapshot = read_snapshot(args.snapshot)
        report = verify(snapshot) if args.verify_only else import_snapshot(snapshot)
        print(json.dumps({"source_captured_at": snapshot['captured_at'], "verified": report}, indent=2))
    finally:
        storage.close_database()


if __name__ == "__main__":
    main()
