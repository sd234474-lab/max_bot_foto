"""Persist customer vehicle approval requests and per-vehicle decisions."""

import sqlalchemy as sa

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nsi_vehicle_approval_requests",
        sa.Column("letter_number", sa.String(100), nullable=False),
        sa.Column("letter_date", sa.Date(), nullable=False),
        sa.Column("contract_ids", sa.JSON(), nullable=False),
        sa.Column("request_document_id", sa.String(100), sa.ForeignKey("nsi_documents.id"), nullable=False),
        sa.Column("response_document_id", sa.String(100), sa.ForeignKey("nsi_documents.id")),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "nsi_vehicle_approval_decisions",
        sa.Column("request_id", sa.String(100), sa.ForeignKey("nsi_vehicle_approval_requests.id"), nullable=False),
        sa.Column("vehicle_id", sa.String(100), sa.ForeignKey("nsi_vehicles.id"), nullable=False),
        sa.Column("calibration_document_id", sa.String(100), sa.ForeignKey("nsi_documents.id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.Integer()),
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("request_id", "vehicle_id"),
        sa.CheckConstraint("decision IN ('PENDING','APPROVED','REJECTED')"),
    )


def downgrade():
    raise RuntimeError("Vehicle approval history downgrade is intentionally unsupported")
