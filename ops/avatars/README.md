# Profile avatars

Profiles are stored in `pacemakers`; names remain imported, read-only values.
Migration: `python -m pacemaker_registry.migrate` (explicitly, before publishing the app).

Production bucket: `pacersreg-avatars-b1genq2j3vqm14gio5vl`, folder
`b1genq2j3vqm14gio5vl`, STANDARD, maximum 1 GiB, versioning disabled.
Set `AVATAR_BUCKET` on the existing Serverless container. Keep its network,
service account, database secret reference, disabled logging and `min_instances=0`.

`policy.json` grants anonymous reads of `avatars/*` only. Application service
account `aje9jaio43ql5o6jjelm` may put/delete that prefix only. Listing and bucket
configuration remain private. IAM tokens come from the runtime metadata service;
no static keys or additional secrets are needed.

Uploads accept JPEG/PNG/WebP, at most 5 MiB and 16 megapixels. The server produces
a centered 512×512 WebP with metadata removed. A new random object key prevents
browser cache staleness. After the database edit commits, the previous image is
deleted; there is no version history or restore UI. Failed/conflicting saves
attempt to delete their new upload. Storage cleanup failure does not undo a saved
profile; an interrupted request can leave an unreferenced object.

Database dumps include avatar keys, not image bytes. Include Object Storage
objects in the project's backup procedure. Profile editing is intentionally
public until the separate role-model task. A version field prevents concurrent
edits from silently overwriting each other.
