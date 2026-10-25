"""YDB persistence adapter. Scoring and registry construction stay in db.py.

All writes use serializable transactions, including name/source deduplication
and ID allocation. IDs are retained during migration so public URLs don't move.
No registry/result cache is used.
"""
import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from uuid import UUID

import ydb
from ydb.convert import aggregate_result_sets_by_index


TABLE_COLUMNS = {
    "pacemakers": {
        "id": "Int64", "last_name": "Utf8", "first_name": "Utf8",
        "created_at": "Timestamp",
    },
    "events": {
        "id": "Int64", "source_event_id": "Utf8", "name": "Utf8",
        "target_time_type": "Utf8?", "created_at": "Timestamp",
        "rating_config": "JsonDocument?", "pace_rating_config": "JsonDocument?",
    },
    "race_results": {
        "id": "Int64", "pacemaker_id": "Int64", "event_id": "Int64",
        "source_participant_id": "Utf8", "source_url": "Utf8",
        "distance_km": "Decimal(8,3)", "chip_time": "Utf8", "pace": "Utf8",
        "target_time": "Utf8", "checkpoints": "JsonDocument", "created_at": "Timestamp",
    },
    "id_counters": {"name": "Utf8", "next_id": "Int64"},
}
EVENT_FIELDS = ("id", "name", "target_time_type", "rating_config", "pace_rating_config")
RETRY = ydb.RetrySettings(max_retries=2, max_session_acquire_timeout=5,
                          backoff_ceiling=2, backoff_slot_duration=0.05)


def table(name):
    prefix = os.getenv("YDB_TABLE_PREFIX", "")
    if not re.fullmatch(r"[A-Za-z0-9_/]*", prefix) or name not in TABLE_COLUMNS:
        raise RuntimeError("Invalid YDB table path")
    return f"`{prefix}{name}`"


@lru_cache(maxsize=1)
def _connection():
    endpoint, database = os.getenv("YDB_ENDPOINT"), os.getenv("YDB_DATABASE")
    if not endpoint or not database:
        raise RuntimeError("YDB_ENDPOINT and YDB_DATABASE must be configured")
    driver = ydb.Driver(endpoint=endpoint, database=database,
                        credentials=ydb.credentials_from_env_variables())
    driver.wait(timeout=5, fail_fast=True)
    return driver, ydb.QuerySessionPool(driver, size=8)


def close_database():
    if _connection.cache_info().currsize:
        driver, pool = _connection()
        pool.stop()
        driver.stop()
        _connection.cache_clear()


def _type(type_name):
    if type_name.endswith("?"):
        return ydb.OptionalType(_type(type_name[:-1]))
    if type_name == "Decimal(8,3)":
        return ydb.DecimalType(8, 3)
    return getattr(ydb.PrimitiveType, type_name)


def _value(value, type_name):
    if value is not None:
        if type_name.rstrip("?") == "JsonDocument":
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        elif type_name == "Timestamp" and isinstance(value, datetime):
            value = timestamp_micros(value)
    return ydb.TypedValue(value, _type(type_name))


def timestamp_micros(value):
    """Avoid floating-point rounding of PostgreSQL's microsecond timestamps."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _execute(tx, query, parameters=None):
    settings = ydb.BaseRequestSettings().with_timeout(10)
    return aggregate_result_sets_by_index(tx.execute(query, parameters, settings=settings))


def _transaction(operation, *, readonly=False):
    try:
        def run(session):
            mode = ydb.QuerySnapshotReadOnly() if readonly else ydb.QuerySerializableReadWrite()
            with session.transaction(mode) as tx:
                result = operation(tx)
                tx.commit()
                return result
        return _connection()[1].retry_operation_sync(run, retry_settings=RETRY)
    except ydb.Error as error:
        # Don't include driver diagnostics: they may contain submitted data.
        raise RuntimeError("YDB request failed") from error


def _rows(tx, query, parameters=None):
    results = _execute(tx, query, parameters)
    if any(result.truncated for result in results):
        raise RuntimeError("YDB response was truncated; refusing partial data")
    return results[0].rows if results else []


def _select(tx, name, where="", params=None):
    return _rows(tx, f"SELECT * FROM {table(name)} {where};", params)


def _upsert(tx, name, row):
    columns = TABLE_COLUMNS[name]
    names = list(row)
    params = {f"${key}": _value(row[key], columns[key]) for key in names}
    declarations = "\n".join(f"DECLARE ${key} AS {columns[key]};" for key in names)
    if set(row) != set(columns):
        query = f"UPDATE {table(name)} SET " + ", ".join(
            f"{key} = ${key}" for key in names if key != "id") + " WHERE id = $id;"
    else:
        query = f"UPSERT INTO {table(name)} (" + ", ".join(names) + ") VALUES (" + ", ".join(
            f"${key}" for key in names) + ");"
    _execute(tx, declarations + "\n" + query, params)


def initialize_database():
    try:
        pool = _connection()[1]
        for name, columns in TABLE_COLUMNS.items():
            fields = ", ".join(f"{key} {kind.rstrip('?')}" + ("" if kind.endswith("?") else " NOT NULL")
                               for key, kind in columns.items())
            key = "name" if name == "id_counters" else "id"
            pool.execute_with_retries(
                f"CREATE TABLE IF NOT EXISTS {table(name)} ({fields}, PRIMARY KEY ({key}));",
                retry_settings=RETRY, settings=ydb.BaseRequestSettings().with_timeout(10))
    except ydb.Error as error:
        raise RuntimeError("YDB initialization failed") from error


def check_database():
    _transaction(lambda tx: _rows(tx, f"SELECT id FROM {table('events')} LIMIT 1;"), readonly=True)


def _json(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _event(row):
    from pacemaker_registry.db import _event_from_row
    return _event_from_row(tuple(_json(row[key]) if key.endswith("config") else row[key]
                                 for key in EVENT_FIELDS))


def get_event(event_id):
    rows = _transaction(lambda tx: _select(tx, "events", "WHERE id = $id",
                          {"$id": _value(event_id, "Int64")}), readonly=True)
    return _event(rows[0]) if rows else None


def list_events():
    rows = _transaction(lambda tx: _select(tx, "events"), readonly=True)
    return tuple(_event(row) for row in sorted(rows, key=lambda row: (row['name'].lower(), row['id'])))


def get_event_target_time_type(source_event_id):
    rows = _transaction(lambda tx: _select(tx, "events", "WHERE source_event_id = $source",
                            {"$source": _value(source_event_id, "Utf8")}), readonly=True)
    return rows[0]["target_time_type"] if rows else None


def get_result_details(result_id, include_splits=True):
    from pacemaker_registry.db import _details_from_row
    query = f"""SELECT e.id AS event_id, e.name AS event_name, e.target_time_type AS target_time_type,
        e.rating_config AS rating_config, e.pace_rating_config AS pace_rating_config,
        p.last_name AS last_name, p.first_name AS first_name, rr.source_url AS source_url,
        rr.id AS result_id, rr.distance_km AS distance_km, rr.target_time AS target_time,
        rr.chip_time AS chip_time, rr.pace AS pace, rr.checkpoints AS checkpoints
        FROM {table('race_results')} AS rr
        JOIN {table('events')} AS e ON e.id = rr.event_id
        JOIN {table('pacemakers')} AS p ON p.id = rr.pacemaker_id WHERE rr.id = $id;"""
    rows = _transaction(lambda tx: _rows(tx, query, {"$id": _value(result_id, "Int64")}), readonly=True)
    keys = ("event_id", "event_name", "target_time_type", "rating_config", "pace_rating_config",
            "last_name", "first_name", "source_url", "result_id", "distance_km", "target_time",
            "chip_time", "pace", "checkpoints")
    row = tuple(_json(rows[0][key]) if key in ("rating_config", "pace_rating_config", "checkpoints")
                else rows[0][key] for key in keys) if rows else None
    return _details_from_row(row, include_splits)


def load_registry(event_id=None, include_splits=True, min_events=1):
    from pacemaker_registry.db import _build_registry
    def read(tx):
        events = tuple(_event(row) for row in _rows(tx, f"""
            SELECT e.* FROM {table('events')} AS e
            LEFT SEMI JOIN {table('race_results')} AS rr ON rr.event_id = e.id
            ORDER BY Unicode::ToLower(name), id;"""))
        selected = event_id if event_id in {event.id for event in events} else None
        query = f"""
            $counts = SELECT pacemaker_id, COUNT(*) AS total_result_count
                FROM {table('race_results')} GROUP BY pacemaker_id;
            SELECT p.id AS pacemaker_id, p.last_name AS last_name, p.first_name AS first_name,
                rr.id AS result_id, e.id AS event_id, e.name AS event_name,
                rr.distance_km AS distance_km, rr.target_time AS target_time,
                rr.chip_time AS chip_time, rr.pace AS pace, rr.checkpoints AS checkpoints,
                history.total_result_count AS total_result_count
            FROM {table('pacemakers')} AS p
            JOIN {table('race_results')} AS rr ON rr.pacemaker_id = p.id
            JOIN {table('events')} AS e ON e.id = rr.event_id
            JOIN $counts AS history ON history.pacemaker_id = p.id
            {"WHERE e.id = $id" if selected is not None else ""}
            ORDER BY Unicode::ToLower(p.last_name), Unicode::ToLower(p.first_name),
                rr.created_at DESC, Unicode::ToLower(e.name);"""
        rows = _rows(tx, query, {"$id": _value(selected, "Int64")} if selected is not None else None)
        keys = ("pacemaker_id", "last_name", "first_name", "result_id", "event_id", "event_name",
                "distance_km", "target_time", "chip_time", "pace", "checkpoints", "total_result_count")
        tuples = [tuple(_json(row[key]) if key == 'checkpoints' else row[key] for key in keys) for row in rows]
        return _build_registry(events, tuples, selected, include_splits, min_events)
    return _transaction(read, readonly=True)


def _update_existing(name, row_id, values, previous=None):
    def write(tx):
        rows = _select(tx, name, "WHERE id = $id", {"$id": _value(row_id, "Int64")})
        if not rows or previous is not None and rows[0]["target_time"] != previous:
            return False
        # UPSERT only the changed fields; leave every other field untouched.
        _upsert(tx, name, {"id": row_id, **values})
        return True
    return _transaction(write)


def _validate_target_time(target_time):
    if not re.fullmatch(r"[0-9]{1,2}:[0-5][0-9]", target_time):
        raise ValueError("Invalid target time")


def update_result_target_time(result_id, target_time, previous_target_time):
    _validate_target_time(target_time)
    return _update_existing("race_results", result_id, {"target_time": target_time}, previous_target_time)


def save_event_rating(event_id, config):
    return _update_existing("events", event_id, {"rating_config": asdict(config)})


def save_event_pace_rating(event_id, config):
    return _update_existing("events", event_id, {"pace_rating_config": asdict(config)})


def _next_id(tx, name):
    rows = _select(tx, "id_counters", "WHERE name = $name", {"$name": _value(name, "Utf8")})
    if not rows:
        raise RuntimeError("YDB ID counters have not been initialized by migration")
    next_id = rows[0]["next_id"]
    _upsert(tx, "id_counters", {"name": name, "next_id": next_id + 1})
    return next_id


def save_race_result(result, target_time):
    from pacemaker_registry.db import _split_athlete_name, _target_time_type_from_target_time
    last_name, first_name = _split_athlete_name(result.athlete_name)
    _validate_target_time(target_time)
    source_id = str(UUID(result.source_participant_id))
    distance = Decimal(result.distance_km.replace(",", "."))
    if not distance.is_finite() or distance <= 0 or distance >= 100000:
        raise ValueError("Invalid race distance")
    if not last_name.strip(" ") or not first_name.strip(" ") or not result.event_name.strip(" "):
        raise ValueError("Empty athlete or event name")
    def write(tx):
        now = datetime.now(timezone.utc)
        people = [row for row in _select(tx, "pacemakers")
                  if (row['last_name'].strip(' ').lower(), row['first_name'].strip(' ').lower())
                  == (last_name.strip(' ').lower(), first_name.strip(' ').lower())]
        person = people[0] if people else {"id": _next_id(tx, "pacemakers"), "created_at": now}
        _upsert(tx, "pacemakers", {"id": person["id"], "last_name": last_name,
                                   "first_name": first_name, **({"created_at": now} if not people else {})})
        events = _select(tx, "events", "WHERE source_event_id = $source",
                         {"$source": _value(result.source_event_id, "Utf8")})
        event = events[0] if events else {"id": _next_id(tx, "events"), "target_time_type": None}
        _upsert(tx, "events", {"id": event["id"], "source_event_id": result.source_event_id,
            "name": result.event_name,
            "target_time_type": event["target_time_type"] or _target_time_type_from_target_time(target_time),
            **({"created_at": now, "rating_config": None, "pace_rating_config": None} if not events else {})})
        existing = _select(tx, "race_results", "WHERE source_participant_id = $source",
                            {"$source": _value(source_id, "Utf8")})
        if existing:
            return False
        _upsert(tx, "race_results", {"id": _next_id(tx, "race_results"),
            "pacemaker_id": person["id"], "event_id": event["id"],
            "source_participant_id": source_id, "source_url": result.source_url,
            "distance_km": distance, "chip_time": result.chip_time, "pace": result.pace,
            "target_time": target_time, "checkpoints": [asdict(point) for point in result.checkpoints],
            "created_at": now})
        return True
    return _transaction(write)
