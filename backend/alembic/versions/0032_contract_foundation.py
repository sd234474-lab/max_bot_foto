"""Q1-01: additive contract foundation, tenant databases only.

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa
revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(100), nullable=False, unique=True),
        sa.Column("customer_name", sa.String(250), nullable=False),
        sa.Column("contractor_name", sa.String(250), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Numeric(18,2)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_contract_dates"))
    op.create_table("contract_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("source_ref", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("contract_id", "revision_no"),
        sa.CheckConstraint("revision_no > 0"),
        sa.CheckConstraint("status IN ('DRAFT','APPROVED','ARCHIVED')", name="ck_contract_revision_status"))
    op.create_table("contract_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("contract_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_code", sa.String(80), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.UniqueConstraint("revision_id","stage_code"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_contract_stage_dates"))
    op.create_table("contract_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("contract_stages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_code", sa.String(80), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.UniqueConstraint("stage_id","period_code"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_contract_period_dates"))
    op.create_table("contract_work_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("contract_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("work_type_id", sa.Integer(), sa.ForeignKey("work_types.id", ondelete="RESTRICT")),
        sa.Column("planning_rule", sa.String(80)),
        sa.UniqueConstraint("revision_id","source_code"))
    op.create_table("contract_territories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("contract_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.UniqueConstraint("revision_id","source_code"))
    op.create_table("contract_territory_districts",
        sa.Column("territory_id", sa.Integer(), sa.ForeignKey("contract_territories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("district_id", sa.Integer(), sa.ForeignKey("districts.id", ondelete="RESTRICT"), primary_key=True))
    op.create_table("contract_work_applicability",
        sa.Column("work_line_id", sa.Integer(), sa.ForeignKey("contract_work_lines.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("territory_id", sa.Integer(), sa.ForeignKey("contract_territories.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("contract_indicator_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False))
    op.create_table("contract_physical_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_line_id", sa.Integer(), sa.ForeignKey("contract_work_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("territory_id", sa.Integer(), sa.ForeignKey("contract_territories.id", ondelete="CASCADE")),
        sa.Column("indicator_type_id", sa.Integer(), sa.ForeignKey("contract_indicator_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("value", sa.Numeric(20,6)),
        sa.Column("source_ref", sa.Text()),
        sa.CheckConstraint("value >= 0", name="ck_contract_physical_nonnegative"))
    op.create_table("contract_price_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("contract_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_line_code", sa.String(120), nullable=False),
        sa.Column("work_line_id", sa.Integer(), sa.ForeignKey("contract_work_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("contract_stages.id", ondelete="RESTRICT")),
        sa.Column("period_id", sa.Integer(), sa.ForeignKey("contract_periods.id", ondelete="RESTRICT")),
        sa.Column("quantity", sa.Numeric(20,6)),
        sa.Column("unit_price", sa.Numeric(18,6)),
        sa.Column("line_amount", sa.Numeric(18,2)),
        sa.Column("source_ref", sa.Text()),
        sa.UniqueConstraint("revision_id","source_line_code"),
        sa.CheckConstraint("quantity >= 0", name="ck_contract_price_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_contract_unit_price"))

def downgrade():
    for table in ("contract_price_lines", "contract_physical_values", "contract_indicator_types",
                  "contract_work_applicability", "contract_territory_districts", "contract_territories",
                  "contract_work_lines", "contract_periods", "contract_stages",
                  "contract_revisions", "contracts"):
        op.drop_table(table)
