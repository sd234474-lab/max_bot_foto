"""Q1-01 tenant-local contract foundation. No runtime routes or productive data changes."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(100), unique=True)
    customer_name: Mapped[str] = mapped_column(String(250))
    contractor_name: Mapped[str] = mapped_column(String(250))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("ends_on >= starts_on", name="ck_contract_dates"),)


class ContractRevision(Base):
    __tablename__ = "contract_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"))
    revision_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default="DRAFT")
    source_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("contract_id", "revision_no"), CheckConstraint("revision_no > 0"), CheckConstraint("status IN ('DRAFT','APPROVED','ARCHIVED')", name="ck_contract_revision_status"))


class ContractStage(Base):
    __tablename__ = "contract_stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("contract_revisions.id", ondelete="CASCADE"))
    stage_code: Mapped[str] = mapped_column(String(80))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("revision_id", "stage_code"), CheckConstraint("ends_on >= starts_on", name="ck_contract_stage_dates"))


class ContractPeriod(Base):
    __tablename__ = "contract_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("contract_stages.id", ondelete="CASCADE"))
    period_code: Mapped[str] = mapped_column(String(80))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("stage_id", "period_code"), CheckConstraint("ends_on >= starts_on", name="ck_contract_period_dates"))


class ContractWorkLine(Base):
    __tablename__ = "contract_work_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("contract_revisions.id", ondelete="CASCADE"))
    source_code: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40))
    work_type_id: Mapped[int | None] = mapped_column(ForeignKey("work_types.id", ondelete="RESTRICT"))
    planning_rule: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("revision_id", "source_code"),)


class ContractTerritory(Base):
    __tablename__ = "contract_territories"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("contract_revisions.id", ondelete="CASCADE"))
    source_code: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("revision_id", "source_code"),)


class ContractTerritoryDistrict(Base):
    __tablename__ = "contract_territory_districts"
    territory_id: Mapped[int] = mapped_column(ForeignKey("contract_territories.id", ondelete="CASCADE"), primary_key=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id", ondelete="RESTRICT"), primary_key=True)


class ContractWorkApplicability(Base):
    __tablename__ = "contract_work_applicability"
    work_line_id: Mapped[int] = mapped_column(ForeignKey("contract_work_lines.id", ondelete="CASCADE"), primary_key=True)
    territory_id: Mapped[int] = mapped_column(ForeignKey("contract_territories.id", ondelete="CASCADE"), primary_key=True)


class PhysicalIndicatorType(Base):
    __tablename__ = "contract_indicator_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40))


class ContractPhysicalValue(Base):
    __tablename__ = "contract_physical_values"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_line_id: Mapped[int] = mapped_column(ForeignKey("contract_work_lines.id", ondelete="CASCADE"))
    territory_id: Mapped[int | None] = mapped_column(ForeignKey("contract_territories.id", ondelete="CASCADE"))
    indicator_type_id: Mapped[int] = mapped_column(ForeignKey("contract_indicator_types.id", ondelete="RESTRICT"))
    value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    source_ref: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("value >= 0", name="ck_contract_physical_nonnegative"),)


class ContractPriceLine(Base):
    __tablename__ = "contract_price_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("contract_revisions.id", ondelete="CASCADE"))
    source_line_code: Mapped[str] = mapped_column(String(120))
    work_line_id: Mapped[int] = mapped_column(ForeignKey("contract_work_lines.id", ondelete="RESTRICT"))
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("contract_stages.id", ondelete="RESTRICT"))
    period_id: Mapped[int | None] = mapped_column(ForeignKey("contract_periods.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    line_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    source_ref: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("revision_id", "source_line_code"), CheckConstraint("quantity >= 0", name="ck_contract_price_quantity"), CheckConstraint("unit_price >= 0", name="ck_contract_unit_price"))
