"""Limit vehicle responsibility to operational owners and auditable candidates."""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nsi_vehicle_relations", sa.Column("decision", sa.String(20), nullable=False, server_default="PENDING"))
    op.add_column("nsi_vehicle_relations", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column("nsi_vehicle_relations", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nsi_vehicle_relations", sa.Column("decided_by", sa.Integer(), nullable=True))
    op.execute("UPDATE nsi_vehicle_relations SET decision='APPROVED' WHERE role='OPERATIONAL' AND status='CONFIRMED'")
    op.execute("""
        DO $$ DECLARE item record;
        BEGIN
          FOR item IN SELECT conname FROM pg_constraint
            WHERE conrelid='nsi_vehicle_relations'::regclass AND contype='c'
          LOOP EXECUTE format('ALTER TABLE nsi_vehicle_relations DROP CONSTRAINT %I', item.conname); END LOOP;
        END $$
    """)
    op.drop_column("nsi_vehicle_relations", "contract_id")
    op.create_check_constraint("ck_vehicle_relation_role", "nsi_vehicle_relations", "role IN ('OPERATIONAL','CANDIDATE')")
    op.create_check_constraint("ck_vehicle_relation_decision", "nsi_vehicle_relations", "decision IN ('PENDING','APPROVED','REJECTED')")
    op.create_check_constraint("ck_vehicle_relation_dates", "nsi_vehicle_relations", "valid_to IS NULL OR valid_to >= valid_from")
    op.create_check_constraint("ck_vehicle_operational_approved", "nsi_vehicle_relations", "status != 'CONFIRMED' OR role != 'OPERATIONAL' OR approved_at IS NOT NULL")


def downgrade():
    raise RuntimeError("Operational-owner decisions and history must be retained")
