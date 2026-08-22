"""Add immutable import and scrutiny lifecycle records.

Revision ID: 20260822_audit_lifecycle
Revises:
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "20260822_audit_lifecycle"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This repository predates Alembic. On a fresh database, create the
    # complete current metadata; on an existing deployment, apply only the
    # lifecycle additions below.
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("entities"):
        from app.db.base import Base
        from app.db import models  # noqa: F401
        Base.metadata.create_all(bind=bind)
        return

    inspector = sa.inspect(bind)
    # `create_all` may already have created the new tables in a developer
    # database without altering legacy tables. Make this migration safe to run
    # in that partially-upgraded state.
    if inspector.has_table("import_batches"):
        _upgrade_existing_schema(inspector)
        return

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("financial_period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_import_batches_entity_id", "import_batches", ["entity_id"])
    op.create_index("ix_import_batches_content_sha256", "import_batches", ["content_sha256"])
    with op.batch_alter_table("transactions") as batch:
        batch.add_column(sa.Column("import_batch_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_transactions_import_batch", "import_batches", ["import_batch_id"], ["id"], ondelete="CASCADE")
    with op.batch_alter_table("trial_balance_snapshots") as batch:
        batch.add_column(sa.Column("import_batch_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_snapshots_import_batch", "import_batches", ["import_batch_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_snapshot_batch_account", ["import_batch_id", "ledger_account_id"])
    op.create_table(
        "scrutiny_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("financial_period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("import_batches.id", ondelete="SET NULL")),
        sa.Column("triggered_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("rule_set_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    with op.batch_alter_table("exceptions") as batch:
        batch.add_column(sa.Column("scrutiny_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("rule_version", sa.String(50), nullable=False, server_default="1"))
        batch.create_foreign_key("fk_exceptions_scrutiny_run", "scrutiny_runs", ["scrutiny_run_id"], ["id"], ondelete="CASCADE")
    op.create_table(
        "review_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exception_id", sa.Integer(), sa.ForeignKey("exceptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("auditor_notes", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("financial_periods") as batch:
        batch.create_unique_constraint("uq_financial_period_entity_dates", ["entity_id", "period_start", "period_end"])
    with op.batch_alter_table("ledger_accounts") as batch:
        batch.create_unique_constraint("uq_ledger_account_entity_name", ["entity_id", "name"])


def downgrade() -> None:
    raise NotImplementedError("Audit evidence migrations are intentionally irreversible.")


def _upgrade_existing_schema(inspector) -> None:
    transaction_columns = {column["name"] for column in inspector.get_columns("transactions")}
    if "import_batch_id" not in transaction_columns:
        with op.batch_alter_table("transactions") as batch:
            batch.add_column(sa.Column("import_batch_id", sa.Integer(), nullable=True))

    snapshot_columns = {column["name"] for column in inspector.get_columns("trial_balance_snapshots")}
    if "import_batch_id" not in snapshot_columns:
        with op.batch_alter_table("trial_balance_snapshots") as batch:
            batch.add_column(sa.Column("import_batch_id", sa.Integer(), nullable=True))

    if not inspector.has_table("scrutiny_runs"):
        op.create_table(
            "scrutiny_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("financial_period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False),
            sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("import_batches.id", ondelete="SET NULL")),
            sa.Column("triggered_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("rule_set_version", sa.String(50), nullable=False, server_default="1"),
            sa.Column("status", sa.String(30), nullable=False, server_default="COMPLETED"),
            sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime()),
        )

    exception_columns = {column["name"] for column in inspector.get_columns("exceptions")}
    with op.batch_alter_table("exceptions") as batch:
        if "scrutiny_run_id" not in exception_columns:
            batch.add_column(sa.Column("scrutiny_run_id", sa.Integer(), nullable=True))
        if "fingerprint" not in exception_columns:
            batch.add_column(sa.Column("fingerprint", sa.String(64), nullable=True))
        if "rule_version" not in exception_columns:
            batch.add_column(sa.Column("rule_version", sa.String(50), nullable=False, server_default="1"))

    if not inspector.has_table("review_actions"):
        op.create_table(
            "review_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("exception_id", sa.Integer(), sa.ForeignKey("exceptions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("status", sa.String(50), nullable=False),
            sa.Column("auditor_notes", sa.String(2000)),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
