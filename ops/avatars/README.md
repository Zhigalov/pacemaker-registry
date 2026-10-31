# Profile avatars

Profiles are stored in `pacemakers`; names remain imported, read-only values.
Migration: `python -m pacemaker_registry.migrate` (explicitly, before publishing the app).

Production bucket: `pacersreg-avatars-b1genq2j3vqm14gio5vl`, folder
`b1genq2j3vqm14gio5vl`, STANDARD, maximum 1 GiB, versioning disabled.
Set `AVATAR_BUCKET` on the existing Serverless container. Keep its network,
service account, database secret reference, disabled logging and `min_instances=0`.

Object Storage checks base permissions **before** `policy.json`: policy Allow
rules alone do not grant access. Avatar operation requires `public-read` for
objects and bucket ACL READ+WRITE for service account `aje9jaio43ql5o6jjelm`, with
`policy.json` kept in place to restrict public reads and application put/delete
to `avatars/*`. Public listing and public config read remain disabled. Removing
the policy would expand the base permissions to all objects in this bucket.
The existing cloud owner `ajec2uiqurm9nspakfjn` retains full management of this
bucket and its objects through a separately approved administrator rule;
otherwise the policy blocks legitimate ACL updates. This rule does not apply to
the application service account or public visitors.

Yandex requires ACL WRITE together with READ; WRITE alone returns 501
NotImplemented. The prefix policy still denies application listing and bucket
configuration despite these base permissions. Apply the base settings only after
explicit approval, keeping the policy:

```bash
yc storage bucket update --name pacersreg-avatars-b1genq2j3vqm14gio5vl \
  --public-read \
  --grants grantee-id=aje9jaio43ql5o6jjelm,grant-type=grant-type-account,permission=permission-read \
  --grants grantee-id=aje9jaio43ql5o6jjelm,grant-type=grant-type-account,permission=permission-write
```

Verify upload/replacement/removal through the application and check that listing,
config reads and writes outside `avatars/*` remain denied. IAM tokens come from
the runtime metadata service; no static keys or additional secrets are needed.

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
