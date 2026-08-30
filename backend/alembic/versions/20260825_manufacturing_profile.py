"""Add GST-guided sector profile fields.

Revision ID: 20260825_manufacturing_profile
Revises: 20260822_audit_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "20260825_manufacturing_profile"
down_revision = "20260822_audit_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("entities")}
    with op.batch_alter_table("entities") as batch:
        if "gstin" not in columns:
            batch.add_column(sa.Column("gstin", sa.String(15), nullable=True))
        if "sector" not in columns:
            batch.add_column(sa.Column("sector", sa.String(50), nullable=True))
        if "rule_pack" not in columns:
            batch.add_column(sa.Column("rule_pack", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("entities") as batch:
        batch.drop_column("rule_pack")
        batch.drop_column("sector")
        batch.drop_column("gstin")
