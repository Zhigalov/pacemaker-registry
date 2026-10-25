# Staged PostgreSQL → YDB migration

## Actual status — 2026-10-25

**Blocked at deployment, not completed.** Application commit `4b3dcd0` is pushed;
image `cr.yandex/crpm35ct1ll9c4alvvno/pacemaker-registry:4b3dcd0` is published
(digest `sha256:461a7b76d767420ccf9ecda28a8e50518c2d7725a8197c3670840f85d493bfd1`).
The final PG snapshot at `2026-10-25T13:38:48.424123+03:00` was imported and
all 103 athletes, 8 events and 141 results verified field by field. All three
ID counters start at 150. No production writes have been sent to YDB.

`Revision.Deploy` returned gRPC `Unavailable` on three attempts; the official
REST route `/containers/v1/revisions:deploy` returned HTTP 503, code 14,
`Unavailable` too. No new revision or deployment operation appeared. The old
revision `bba1hgjnjta5ivi16jic` remains ACTIVE. One diagnostic request ID:
`603805b6-4792-42cc-b2a3-94216d3bd51d`.
Cloud incident: <https://status.yandex.cloud/ru/incidents/2136>.

**DNS still points to the VM; it was not changed. PG's database-level
`default_transaction_read_only=on` remains in force, explicitly requested by
the owner after the deployment failure.** The old app requires read-write
connections, so `/ready` returns 503 and the home page shows a DB error instead
of the registry. Do not describe the service as restored. The owner explicitly
declined temporarily reopening PG writes. Do not reset this without new consent.

Remaining steps: unblock deployment/obtain another approved deployment path,
run `ops/deploy_ydb.py --image ... --apply`, verify backend/pages/TLS, switch DNS
with the guarded script, measure YDB on the same Serverless endpoint and compare.
Do not change networking as an unapproved workaround. The owner approved return
from the temporary VM to Serverless with `min_instances=1`; not new hosts or a
different architecture. Do not delete PG or the VM.

Final snapshot (private, outside Git):
`/Users/zhigalov/Documents/Codex/pacersreg-backups/2026-10-25-ydb-cutover.json`.
The separately verified/restored PG dump and backup ZIP remain in
`/Users/zhigalov/Documents/Codex/pacersreg-backups/`.

Verification SHA-256 over canonical sorted rows (UTC timestamps, exact Decimal):

| Table | Rows | SHA-256 |
| --- | ---: | --- |
| pacemakers | 103 | d8207332bcd2a33af6062da289ab67cda20b319bc5241e269288b91dbc80cb7c |
| events | 8 | c3a4d202655a5ed6aa125f075ff0cd8ac7ea5426cd19e95b61be888d6ab15dd7 |
| race_results | 141 | 81a1cd8704d09134b9442b072cf89f787a5a624eac8a2b350fd1f0c52336f045 |

Raw MD5 of `jsonb::text` from a locally restored PG is **not** a semantic
cross-database comparison: JSON numeric trailing zero representation can differ.
Source-envelope MD5 still verifies transport faithfully. Final adapter validation
uses parsed JSON equality + exact decimal/timestamp normalization and SHA-256.

## PostgreSQL baseline — warm HTTP latency in milliseconds

20 sequential samples per route after one warmup; same client machine.
Serverless: 1 CPU, 100%, 256 MiB, concurrency 8, min_instances 1, timeout 15s.
All baseline responses were HTTP 200. YDB production measurements are **not yet
available**, because deployment is blocked. No speedup/regression claim is made.

| Route | PG VM median / p95 | PG Serverless median / p95 |
| --- | ---: | ---: |
| `/ready` | 70.55 / 72.95 | 85.00 / 99.68 |
| `/` | 124.65 / 128.37 | 184.54 / 241.24 |
| `/?event_id=1` | 79.17 / 84.18 | 96.63 / 103.95 |
| `/?min_events=2` | 100.94 / 114.92 | 132.55 / 151.29 |
| `/events` | 74.15 / 78.81 | 86.55 / 91.22 |
| `/events/1` | 74.43 / 81.99 | 88.00 / 119.67 |
| `/results/1` | 72.10 / 80.70 | 88.76 / 94.96 |

Tests: 204 offline Python tests passed, 2 opt-in tests skipped in default suite;
all 6 YDB tests passed when enabled, including real serializable concurrent
deduplication and comparisons of all result details/filters against restored PG.
3 JavaScript analytics tests passed. Test-only tables were removed after testing.

## Resources

- Folder: `b1genq2j3vqm14gio5vl`.
- YDB Serverless: `etn685h40uqd8416ire4` (`pacemaker-registry-ydb`).
- Endpoint: `grpcs://ydb.serverless.yandexcloud.net:2135`.
- Database: `/ru-central1/b1g2035eiff72jcbalkd/etn685h40uqd8416ire4`.
- Provisioned capacity: 0 RU/s; throttle: 100 RU/s; storage limit: 1 GiB.
- Deletion protection enabled; daily system backup retention: 7 days.
- Existing app service account has `ydb.editor` **on this database only**,
  separately approved by the owner. No permanent keys were created.
- Original PostgreSQL cluster: `c9qss8qosce6jivl15pm`. Do not delete it.
- Original temporary VM: `fv435m0kn12bvqiqcgnu`. Retained, not modified.

## Application configuration

`DATABASE_BACKEND` defaults to `postgresql`; set it to `ydb` to use YDB.
Also set `YDB_ENDPOINT`, `YDB_DATABASE`, `YDB_METADATA_CREDENTIALS=1`.
Serverless uses the existing service account through metadata, not a static key.
Preserve the existing network, secret references, resources and `min_instances=1`.

The three business tables retain their IDs and all original data. YDB stores
JSONB fields as `JsonDocument`, distances as `Decimal(8,3)`, UUIDs as canonical
text, and timestamps as UTC instants with microsecond precision. `id_counters`
retains the PostgreSQL sequences' next IDs, including gaps. Serializable write
transactions enforce source-ID/name uniqueness and parent references. Flag
edits still use optimistic concurrency. All scoring and registry construction
remain shared with PostgreSQL. No response cache, pagination or precomputed
ratings were added. Text ordering uses Unicode case folding rather than the
platform-dependent PostgreSQL collation; ranking values do not change.

## Import and verification

The private snapshot is a WebSQL **text** export (`snapshot::text`, never a
JSONB-typed browser cell): JavaScript otherwise rounds PostgreSQL's 64-bit
sequence metadata. The envelope includes the source-side MD5 and byte length.
`ops/ydb_migration.py` checks both before import. Keep exports outside Git,
inside a private directory (0700) and with file permissions 0600.

With short-lived SDK credentials supplied securely through the environment:

```sh
python ops/ydb_migration.py /absolute/private/path/websql-export.json
python ops/ydb_migration.py /absolute/private/path/websql-export.json --verify-only
```

The importer never connects to or changes PostgreSQL. It UPSERTs the snapshot,
seeds counters monotonically and reads back every field. Extra or missing rows,
changed JSON, decimal values, timestamps or IDs fail verification. Bulk loading
is not atomic across tables: never send production traffic before verification
finishes. Reimport is safe **only before YDB accepts new production writes**;
do not overwrite a live YDB with an older PostgreSQL snapshot.

## Cutover and rollback

1. Take and test a full PostgreSQL backup; benchmark the old Serverless revision.
2. Import the snapshot and test the adapter in isolated `test_migration_*` tables.
3. Build/push the tested commit before starting the maintenance window.
4. Briefly stop new PG writes with the database-owner command:
   `ALTER DATABASE pacemaker_registry SET default_transaction_read_only = on;`.
   Wait for pre-existing app requests/connections to finish (request timeout 15s).
   The old app requests read-write connections, so this also causes temporary
   unavailability there. Do not leave this maintenance state on deployment failure.
5. Export a new single-query snapshot, import it and verify every field again.
6. Deploy YDB to the existing Serverless container; preserve configuration.
7. Verify readiness, pages and TLS through the existing API Gateway, then run
   `python ops/temporary-vm/switch_dns.py --to serverless --apply`.
8. Verify public DNS, app counts/IDs and backend configuration. Keep PG read-only
   so stale DNS clients cannot create divergent writes. Keep the VM and PG.

Before **any** production write reaches YDB, rollback can reset the PG default,
redeploy the old PG configuration and point DNS to the VM. Reset PG read-only
from an owner session with `SET default_transaction_read_only = off;` followed
by `ALTER DATABASE pacemaker_registry RESET default_transaction_read_only;`.
Check a fresh session before restoring traffic.

After YDB accepts writes, a DNS-only rollback is **not safe**: freeze new writes,
export YDB, reconcile all changes back into PostgreSQL, verify data/counters and
only then restore PG traffic. Never blindly switch back to a stale database.

## Tests and measurements

```sh
python -m pytest -q
# Optional: isolated live YDB CRUD/concurrency + restored-PG parity tests.
# Supply SDK credentials, YDB_INTEGRATION=1 and, for parity,
# YDB_TEST_SNAPSHOT and YDB_TEST_POSTGRES_URL through the environment.
python -m pytest -q tests/test_ydb_storage.py
python ops/benchmark.py https://bbapk9h0bc3hh8o1audt.containers.yandexcloud.net /private/path/report.json
```

Same machine/client, keep-alive, sequential requests, one warmup + 20 samples
per route, full response download; report median and nearest-rank p95. Compare
old/new DB **on the same Serverless resources**, separately from the earlier
VM baseline. No personal response bodies are written to the report.
