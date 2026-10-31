"""Explicit contract parties and documents of the existing organization profile."""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "nsi_documents",
        sa.Column("legal_profile_id", sa.String(100), sa.ForeignKey("nsi_legal_profiles.id"), nullable=True),
    )
    op.create_table(
        "nsi_contract_parties",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revision_id", sa.String(100), sa.ForeignKey("nsi_contract_revisions.id"), nullable=False),
        sa.Column("counterparty_id", sa.String(100), sa.ForeignKey("nsi_counterparties.id"), nullable=True),
        sa.Column("legal_profile_id", sa.String(100), sa.ForeignKey("nsi_legal_profiles.id"), nullable=True),
        sa.Column("role", sa.String(60), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "(counterparty_id IS NOT NULL AND legal_profile_id IS NULL) OR (counterparty_id IS NULL AND legal_profile_id IS NOT NULL)"
        ),
        sa.UniqueConstraint("revision_id", "counterparty_id", "role"),
        sa.UniqueConstraint("revision_id", "legal_profile_id", "role"),
    )


def downgrade():
    raise RuntimeError("Historical contract parties must be retained")
