"""Add canonical ledger ingestion records and provenance fields.

Revision ID: 20260904_canonical_ledger_ingestion
Revises: 20260825_manufacturing_profile
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_canonical_ledger_ingestion"
down_revision = "20260825_manufacturing_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("entities"):
        from app.db.base import Base
        from app.db import models  # noqa: F401

        Base.metadata.create_all(bind=bind)
        return

    if not _has_table("import_batches"):
        _create_import_batches()
    else:
        _add_import_batch_columns()

    if not _has_table("journal_entries"):
        _create_journal_entries()
    else:
        _add_journal_entry_columns()

    if not _has_table("journal_lines"):
        _create_journal_lines()
    else:
        _add_journal_line_columns()

    if not _has_table("balance_checkpoints"):
        _create_balance_checkpoints()
    else:
        _add_balance_checkpoint_columns()

    _upgrade_ledger_accounts()
    _upgrade_scrutiny_runs()


def downgrade() -> None:
    raise NotImplementedError("Canonical ledger evidence migrations are intentionally irreversible.")


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_columns(table_name: str, columns: dict[str, sa.Column]) -> None:
    missing = [column for name, column in columns.items() if name not in _columns(table_name)]
    if not missing:
        return
    if _row_count(table_name):
        unsafe = [column.name for column in missing if not column.nullable and column.server_default is None]
        if unsafe:
            raise RuntimeError(
                f"Cannot add required column(s) {', '.join(unsafe)} to populated partial table "
                f"'{table_name}' without a backfill"
            )
    with op.batch_alter_table(table_name) as batch:
        for column in missing:
            batch.add_column(column)


def _row_count(table_name: str) -> int:
    table = sa.table(table_name)
    return int(op.get_bind().execute(sa.select(sa.func.count()).select_from(table)).scalar_one())


def _create_import_batches() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "financial_period_id",
            sa.Integer(),
            sa.ForeignKey("financial_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(50), nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("kind", sa.String(30), nullable=False, server_default="journal"),
        sa.Column("raw_source_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("coverage_start", sa.Date(), nullable=True),
        sa.Column("coverage_end", sa.Date(), nullable=True),
        sa.Column("source_family", sa.String(50), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("validation_report", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_import_batches_entity_id", "import_batches", ["entity_id"])
    op.create_index("ix_import_batches_financial_period_id", "import_batches", ["financial_period_id"])
    op.create_index("ix_import_batches_content_sha256", "import_batches", ["content_sha256"])


def _add_import_batch_columns() -> None:
    _add_columns(
        "import_batches",
        {
            "kind": sa.Column("kind", sa.String(30), nullable=False, server_default="journal"),
            "raw_source_bytes": sa.Column("raw_source_bytes", sa.LargeBinary(), nullable=True),
            "coverage_start": sa.Column("coverage_start", sa.Date(), nullable=True),
            "coverage_end": sa.Column("coverage_end", sa.Date(), nullable=True),
            "source_family": sa.Column("source_family", sa.String(50), nullable=True),
            "source_metadata": sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        },
    )


def _create_journal_entries() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "import_batch_id",
            sa.Integer(),
            sa.ForeignKey("import_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_document_id", sa.String(255), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("document_type", sa.String(100), nullable=True),
        sa.Column("narration", sa.String(1000), nullable=True),
        sa.UniqueConstraint("import_batch_id", "source_document_id", name="uq_journal_entry_batch_document"),
    )
    op.create_index("ix_journal_entries_import_batch_id", "journal_entries", ["import_batch_id"])
    op.create_index("ix_journal_entries_entity_id", "journal_entries", ["entity_id"])
    op.create_index("ix_journal_entries_posting_date", "journal_entries", ["posting_date"])


def _add_journal_entry_columns() -> None:
    _add_columns(
        "journal_entries",
        {
            "import_batch_id": sa.Column("import_batch_id", sa.Integer(), nullable=False),
            "entity_id": sa.Column("entity_id", sa.Integer(), nullable=False),
            "source_document_id": sa.Column("source_document_id", sa.String(255), nullable=False),
            "posting_date": sa.Column("posting_date", sa.Date(), nullable=False),
            "document_date": sa.Column("document_date", sa.Date(), nullable=True),
            "document_type": sa.Column("document_type", sa.String(100), nullable=True),
            "narration": sa.Column("narration", sa.String(1000), nullable=True),
        },
    )
    _ensure_unique("journal_entries", "uq_journal_entry_batch_document", ["import_batch_id", "source_document_id"])
    _ensure_foreign_keys(
        "journal_entries",
        {
            "fk_journal_entries_import_batch": ("import_batches", ["import_batch_id"], ["id"], "CASCADE"),
            "fk_journal_entries_entity": ("entities", ["entity_id"], ["id"], "CASCADE"),
        },
    )
    _ensure_indexes(
        "journal_entries",
        {
            "ix_journal_entries_import_batch_id": ["import_batch_id"],
            "ix_journal_entries_entity_id": ["entity_id"],
            "ix_journal_entries_posting_date": ["posting_date"],
        },
    )


def _create_journal_lines() -> None:
    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "journal_entry_id",
            sa.Integer(),
            sa.ForeignKey("journal_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ledger_account_id",
            sa.Integer(),
            sa.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("posting_key", sa.String(20), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("clearing_document", sa.String(255), nullable=True),
        sa.Column("profit_center", sa.String(255), nullable=True),
        sa.Column("cost_center", sa.String(255), nullable=True),
        sa.Column("text", sa.String(1000), nullable=True),
        sa.Column("supplier", sa.String(255), nullable=True),
        sa.Column("wbs", sa.String(255), nullable=True),
        sa.Column("purchasing_document", sa.String(255), nullable=True),
        sa.Column("customer", sa.String(255), nullable=True),
        sa.Column("dimensions", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("journal_entry_id", "source_row_number", name="uq_journal_line_entry_row"),
    )
    op.create_index("ix_journal_lines_journal_entry_id", "journal_lines", ["journal_entry_id"])
    op.create_index("ix_journal_lines_ledger_account_id", "journal_lines", ["ledger_account_id"])


def _add_journal_line_columns() -> None:
    _add_columns(
        "journal_lines",
        {
            "journal_entry_id": sa.Column("journal_entry_id", sa.Integer(), nullable=False),
            "ledger_account_id": sa.Column("ledger_account_id", sa.Integer(), nullable=False),
            "source_row_number": sa.Column("source_row_number", sa.Integer(), nullable=False),
            "amount": sa.Column("amount", sa.Numeric(20, 2), nullable=False),
            "side": sa.Column("side", sa.String(10), nullable=False),
            "posting_key": sa.Column("posting_key", sa.String(20), nullable=True),
            "quantity": sa.Column("quantity", sa.Numeric(20, 4), nullable=True),
            "currency": sa.Column("currency", sa.String(10), nullable=True),
            "reference": sa.Column("reference", sa.String(255), nullable=True),
            "clearing_document": sa.Column("clearing_document", sa.String(255), nullable=True),
            "profit_center": sa.Column("profit_center", sa.String(255), nullable=True),
            "cost_center": sa.Column("cost_center", sa.String(255), nullable=True),
            "text": sa.Column("text", sa.String(1000), nullable=True),
            "supplier": sa.Column("supplier", sa.String(255), nullable=True),
            "wbs": sa.Column("wbs", sa.String(255), nullable=True),
            "purchasing_document": sa.Column("purchasing_document", sa.String(255), nullable=True),
            "customer": sa.Column("customer", sa.String(255), nullable=True),
            "dimensions": sa.Column("dimensions", sa.JSON(), nullable=False, server_default="{}"),
            "source_metadata": sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        },
    )
    _ensure_unique("journal_lines", "uq_journal_line_entry_row", ["journal_entry_id", "source_row_number"])
    _ensure_foreign_keys(
        "journal_lines",
        {
            "fk_journal_lines_journal_entry": ("journal_entries", ["journal_entry_id"], ["id"], "CASCADE"),
            "fk_journal_lines_ledger_account": ("ledger_accounts", ["ledger_account_id"], ["id"], "RESTRICT"),
        },
    )
    _ensure_indexes(
        "journal_lines",
        {
            "ix_journal_lines_journal_entry_id": ["journal_entry_id"],
            "ix_journal_lines_ledger_account_id": ["ledger_account_id"],
        },
    )


def _create_balance_checkpoints() -> None:
    op.create_table(
        "balance_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "import_batch_id",
            sa.Integer(),
            sa.ForeignKey("import_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "ledger_account_id",
            sa.Integer(),
            sa.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("balance_date", sa.Date(), nullable=False),
        sa.Column("balance", sa.Numeric(20, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "import_batch_id", "ledger_account_id", "balance_date", name="uq_balance_checkpoint_batch_account_date"
        ),
    )
    op.create_index("ix_balance_checkpoints_import_batch_id", "balance_checkpoints", ["import_batch_id"])
    op.create_index("ix_balance_checkpoints_entity_id", "balance_checkpoints", ["entity_id"])
    op.create_index("ix_balance_checkpoints_ledger_account_id", "balance_checkpoints", ["ledger_account_id"])


def _add_balance_checkpoint_columns() -> None:
    _add_columns(
        "balance_checkpoints",
        {
            "import_batch_id": sa.Column("import_batch_id", sa.Integer(), nullable=False),
            "entity_id": sa.Column("entity_id", sa.Integer(), nullable=False),
            "ledger_account_id": sa.Column("ledger_account_id", sa.Integer(), nullable=False),
            "balance_date": sa.Column("balance_date", sa.Date(), nullable=False),
            "balance": sa.Column("balance", sa.Numeric(20, 2), nullable=False),
            "currency": sa.Column("currency", sa.String(10), nullable=True),
            "source_metadata": sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        },
    )
    _ensure_unique(
        "balance_checkpoints",
        "uq_balance_checkpoint_batch_account_date",
        ["import_batch_id", "ledger_account_id", "balance_date"],
    )
    _ensure_foreign_keys(
        "balance_checkpoints",
        {
            "fk_balance_checkpoints_import_batch": ("import_batches", ["import_batch_id"], ["id"], "CASCADE"),
            "fk_balance_checkpoints_entity": ("entities", ["entity_id"], ["id"], "CASCADE"),
            "fk_balance_checkpoints_ledger_account": (
                "ledger_accounts",
                ["ledger_account_id"],
                ["id"],
                "RESTRICT",
            ),
        },
    )
    _ensure_indexes(
        "balance_checkpoints",
        {
            "ix_balance_checkpoints_import_batch_id": ["import_batch_id"],
            "ix_balance_checkpoints_entity_id": ["entity_id"],
            "ix_balance_checkpoints_ledger_account_id": ["ledger_account_id"],
        },
    )


def _upgrade_ledger_accounts() -> None:
    columns = _columns("ledger_accounts")
    with op.batch_alter_table("ledger_accounts") as batch:
        if "external_code" not in columns:
            batch.add_column(sa.Column("external_code", sa.String(255), nullable=True))
        if "group_name" in columns:
            batch.alter_column("group_name", existing_type=sa.String(255), nullable=True)
        if "normal_balance" in columns:
            batch.alter_column("normal_balance", existing_type=sa.String(10), nullable=True)

    unique_names = {constraint.get("name") for constraint in sa.inspect(op.get_bind()).get_unique_constraints("ledger_accounts")}
    if "uq_ledger_account_entity_external_code" not in unique_names:
        with op.batch_alter_table("ledger_accounts") as batch:
            batch.create_unique_constraint("uq_ledger_account_entity_external_code", ["entity_id", "external_code"])
    _ensure_indexes("ledger_accounts", {"ix_ledger_accounts_external_code": ["external_code"]})


def _upgrade_scrutiny_runs() -> None:
    _add_columns(
        "scrutiny_runs",
        {
            "dataset_fingerprint": sa.Column("dataset_fingerprint", sa.String(64), nullable=True),
            "source_batch_ids": sa.Column("source_batch_ids", sa.JSON(), nullable=True),
        },
    )


def _ensure_unique(table_name: str, constraint_name: str, columns: list[str]) -> None:
    names = {constraint.get("name") for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)}
    if constraint_name not in names:
        with op.batch_alter_table(table_name) as batch:
            batch.create_unique_constraint(constraint_name, columns)


def _ensure_foreign_keys(
    table_name: str,
    foreign_keys: dict[str, tuple[str, list[str], list[str], str]],
) -> None:
    existing = sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    missing = []
    for constraint_name, (referred_table, local_columns, remote_columns, ondelete) in foreign_keys.items():
        present = any(
            foreign_key["constrained_columns"] == local_columns
            and foreign_key["referred_table"] == referred_table
            for foreign_key in existing
        )
        if not present:
            missing.append((constraint_name, referred_table, local_columns, remote_columns, ondelete))

    if not missing:
        return
    with op.batch_alter_table(table_name) as batch:
        for constraint_name, referred_table, local_columns, remote_columns, ondelete in missing:
            batch.create_foreign_key(
                constraint_name,
                referred_table,
                local_columns,
                remote_columns,
                ondelete=ondelete,
            )


def _ensure_indexes(table_name: str, indexes: dict[str, list[str]]) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    for index_name, columns in indexes.items():
        if index_name not in existing:
            op.create_index(index_name, table_name, columns)
