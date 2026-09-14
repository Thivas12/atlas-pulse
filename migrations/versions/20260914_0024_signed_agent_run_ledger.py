"""Create the immutable signed agent-run governance ledger.

Revision ID: 20260914_0024
Revises: 20260912_0009
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260914_0024"
down_revision: str | None = "20260912_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one hash-linked ledger and reject mutation of committed entries."""
    op.execute(
        """
        CREATE TABLE agent_run_ledger_entries (
            sequence_no bigint PRIMARY KEY,
            entry_id varchar(71) NOT NULL UNIQUE,
            previous_entry_id varchar(71) UNIQUE,
            event_type varchar(32) NOT NULL,
            occurred_at timestamptz NOT NULL,
            artifact_id varchar(80) NOT NULL UNIQUE,
            manifest_id varchar(73) NOT NULL,
            proposal_id varchar(73) NOT NULL,
            signing_key_id varchar(72) NOT NULL,
            entry_json jsonb NOT NULL,
            inserted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_agent_run_ledger_previous_entry
                FOREIGN KEY (previous_entry_id)
                REFERENCES agent_run_ledger_entries (entry_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_agent_run_ledger_sequence
                CHECK (sequence_no > 0),
            CONSTRAINT ck_agent_run_ledger_genesis
                CHECK ((sequence_no = 1) = (previous_entry_id IS NULL)),
            CONSTRAINT ck_agent_run_ledger_entry_id
                CHECK (entry_id ~ '^ledger-[0-9a-f]{64}$'),
            CONSTRAINT ck_agent_run_ledger_manifest_id
                CHECK (manifest_id ~ '^manifest-[0-9a-f]{64}$'),
            CONSTRAINT ck_agent_run_ledger_proposal_id
                CHECK (proposal_id ~ '^proposal-[0-9a-f]{64}$'),
            CONSTRAINT ck_agent_run_ledger_signing_key
                CHECK (signing_key_id ~ '^ed25519-[0-9a-f]{64}$'),
            CONSTRAINT ck_agent_run_ledger_event_type
                CHECK (event_type IN ('approval_granted', 'approval_revoked', 'preflight_recorded')),
            CONSTRAINT ck_agent_run_ledger_json_columns
                CHECK (
                    jsonb_typeof(entry_json) = 'object'
                    AND (entry_json ->> 'sequence')::bigint IS NOT DISTINCT FROM sequence_no
                    AND (entry_json ->> 'entry_id') IS NOT DISTINCT FROM entry_id
                    AND (entry_json ->> 'previous_entry_id') IS NOT DISTINCT FROM previous_entry_id
                    AND (entry_json ->> 'event_type') IS NOT DISTINCT FROM event_type
                    AND (entry_json ->> 'occurred_at')::timestamptz IS NOT DISTINCT FROM occurred_at
                    AND (entry_json ->> 'artifact_id') IS NOT DISTINCT FROM artifact_id
                    AND (entry_json ->> 'manifest_id') IS NOT DISTINCT FROM manifest_id
                    AND (entry_json ->> 'proposal_id') IS NOT DISTINCT FROM proposal_id
                    AND (entry_json #>> '{signature,key_id}') IS NOT DISTINCT FROM signing_key_id
                )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_agent_run_ledger_single_genesis
        ON agent_run_ledger_entries ((previous_entry_id IS NULL))
        WHERE previous_entry_id IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_agent_run_ledger_approval_events
        ON agent_run_ledger_entries (event_type, artifact_id, occurred_at)
        WHERE event_type IN ('approval_granted', 'approval_revoked')
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_agent_run_ledger_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'agent_run_ledger_entries is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_run_ledger_no_update_or_delete
        BEFORE UPDATE OR DELETE ON agent_run_ledger_entries
        FOR EACH ROW EXECUTE FUNCTION reject_agent_run_ledger_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_run_ledger_no_truncate
        BEFORE TRUNCATE ON agent_run_ledger_entries
        FOR EACH STATEMENT EXECUTE FUNCTION reject_agent_run_ledger_mutation()
        """
    )


def downgrade() -> None:
    """Remove the ledger and its mutation guard."""
    op.execute("DROP TRIGGER IF EXISTS agent_run_ledger_no_truncate ON agent_run_ledger_entries")
    op.execute(
        "DROP TRIGGER IF EXISTS agent_run_ledger_no_update_or_delete ON agent_run_ledger_entries"
    )
    op.execute("DROP TABLE agent_run_ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS reject_agent_run_ledger_mutation()")
