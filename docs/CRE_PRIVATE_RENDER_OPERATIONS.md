# CRE private render operations

Private edit-project render recovery uses additive content schema v10. Runtime
startup does not apply this migration automatically. Schema-v7, v8, and v9
databases are not runtime-ready; their durable private-render endpoints become
available only after the operator performs the explicit migration. A v9
fallback can continue running without the endpoints until migration is complete.

## Migration procedure

1. Schedule an approved maintenance window and stop writers using the content
   database. Do not copy a live SQLite database file with active WAL writers.
2. Make a transaction-consistent backup of the schema-v7, v8, or v9 database
   and keep it separate from the database being migrated. Preserve the corresponding
   managed content-storage directory as well.
3. Run the migration from the application environment:

   ```sh
   python -m tools.migrate_cre_private_render_operations \
     /path/to/content.db --backup /path/to/content.db.pre-cre-render-v10
   ```

   Both paths must already exist as separate regular files. The tool refuses
   symlinks, missing files, identical paths, invalid SQLite databases, and
   schema versions other than v7, v8, v9, or an already migrated v10 database. It
   requires the backup schema version to match the database before migration;
   the operator is still responsible for verifying that the backup is a
   transaction-consistent copy of the intended database.
4. Confirm the command reports schema v10 and perform the deployment's normal
   database integrity and application checks before reopening writes.

The migration adds only the private render-operation and event tables, their
index, and state/identity triggers. Existing projects, source assets, edit
plans, clip-render records, and provenance are not rewritten. Repeating the
migration on v10 validates the schema and is safe.

## Compatibility and rollback

The application supports the existing v9 publishing schema and reads both
legacy CRE plan v1 and structured plan v2 data. Durable render submission and
recovery are enabled only on schema v10. Older application versions may reject
a v10 database as newer than they support.

Rollback is a restore operation, not a reverse migration: stop database
writers, restore the pre-migration database backup, and restore the matching
managed content storage snapshot. Operations and artifact references created
after migration are not present in the old database; newly written output
files may therefore be orphaned if storage is not restored consistently.
Do not delete those files as part of rollback without a separately verified
cleanup procedure.

## Render recovery behavior

Each submitted render uses a UUIDv4 operation ID as its owner-scoped
idempotency key. Retrying that operation ID with the same project and render
options returns its existing persisted state; a different request fingerprint
conflicts. The key is persisted with the operation and is not time-expired or
automatically deleted; it remains reserved for that owner until an explicit
future retention/cleanup policy removes the operation. The operation pins its
plan JSON/digest, timeline, source IDs, keys, and hashes before claiming the
renderer.

The renderer runs outside the SQLite write transaction. A successful artifact
is written to managed storage and its byte count and SHA-256 are persisted
before the operation becomes `completed`. Artifact reads require the
authenticated owner and completed state, then verify both values again before
delivery. The existing 16 MiB output cap and bounded transport limits remain
in force.

If a process restarts while an operation is `rendering`, startup changes it to
`reconciliation_required`; it is never automatically rendered again. This is
deliberately not an exactly-once guarantee. A crash can leave an orphaned
output or an uncertain renderer outcome that requires operator investigation.
