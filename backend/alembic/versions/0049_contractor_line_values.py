"""Readable OP indicators and RC quantities, preserving source decimal text."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    for name, length in [("indicator", 500), ("quantity_text", 100), ("rate_text", 100), ("total_text", 100)]:
        op.add_column("nsi_contract_lines", sa.Column(name, sa.String(length), nullable=True))


def downgrade():
    raise RuntimeError("Original contract values must be retained")
