"""Questionnaire upload journal without retaining source ZIP archives."""

import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nsi_questionnaire_imports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("counterparty_id", sa.String(), sa.ForeignKey("nsi_counterparties.id")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("received_by", sa.Integer()),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_nsi_questionnaire_imports_sha256", "nsi_questionnaire_imports", ["sha256"])


def downgrade():
    raise RuntimeError("Questionnaire import journal downgrade is intentionally unsupported")
