"""Add explicit Wialon identity to contractor vehicles."""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nsi_vehicles", sa.Column("wialon_unit_id", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_nsi_vehicles_wialon_unit_id", "nsi_vehicles", ["wialon_unit_id"])


def downgrade():
    raise RuntimeError("Vehicle monitoring identities must be retained")
