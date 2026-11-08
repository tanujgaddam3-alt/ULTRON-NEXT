from __future__ import annotations

from ultron.application.content.store import ContentStore


def set_legacy_schema_version(store: ContentStore, version: int) -> None:
    if version not in {7, 8, 9, 10}:
        raise ValueError("Legacy schema fixtures support versions 7 through 10.")

    with store._transaction() as connection:
        for trigger in (
            "owner_rights_attestation_events_valid_transition",
            "owner_rights_attestation_events_no_update",
            "owner_rights_attestation_events_no_delete",
            "owner_rights_attestations_no_update",
            "owner_rights_attestations_no_delete",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in (
            "owner_rights_attestation_events",
            "owner_rights_attestations",
            "owner_rights_attestation_schema_meta",
            "content_source_identities",
            "content_source_identity_schema_meta",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        for table in (
            "content_publishing_attempt_events",
            "content_publishing_attempt_secret_refs",
            "content_publishing_attempts",
            "content_private_render_operation_events",
            "content_private_render_operations",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        if version >= 8:
            ContentStore._create_publishing_attempt_tables(connection)
        if version >= 9:
            ContentStore._upgrade_publishing_attempt_schema_v9(connection)
        if version >= 10:
            ContentStore._create_private_render_operation_tables(connection)
        connection.execute(
            "UPDATE content_schema_meta SET schema_version = ? WHERE singleton = 1",
            (version,),
        )
