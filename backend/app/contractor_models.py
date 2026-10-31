"""Contractor reference data. Each database already belongs to one MAX workspace.

No photo catalogue or platform organization is duplicated here. Stable source IDs
are retained, while unresolved source facts remain explicitly unconfirmed.
"""

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Record:
    id: Mapped[str] = mapped_column(String(100), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default="REVIEW")
    version: Mapped[int] = mapped_column(default=1)
    source: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LegalProfile(Record, Base):
    __tablename__ = "nsi_legal_profiles"
    # Identity comes from the platform registry, which is in a different database
    # for non-legacy workspaces; a cross-database SQL FK would be incorrect.
    organization_id: Mapped[str] = mapped_column(String(36), unique=True)
    inn: Mapped[str | None] = mapped_column(String(12))


class Counterparty(Record, Base):
    __tablename__ = "nsi_counterparties"
    inn: Mapped[str | None] = mapped_column(String(12), unique=True)
    legal_form: Mapped[str | None] = mapped_column(String(40))


class Contract(Record, Base):
    __tablename__ = "nsi_contracts"
    number: Mapped[str] = mapped_column(String(100), unique=True)


class ContractRevision(Record, Base):
    __tablename__ = "nsi_contract_revisions"
    contract_id: Mapped[str] = mapped_column(ForeignKey("nsi_contracts.id"))
    revision: Mapped[int] = mapped_column()
    signed_on: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    basis: Mapped[str | None] = mapped_column(Text)
    party_snapshots: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (
        UniqueConstraint("contract_id", "revision"),
        CheckConstraint("revision > 0"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from"),
    )


class Unit(Record, Base):
    __tablename__ = "nsi_units"


class ContractWork(Record, Base):
    __tablename__ = "nsi_contract_works"
    code: Mapped[str] = mapped_column(String(100), unique=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_contract_works.id"))
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_units.id"))
    photo_work_type_id: Mapped[int | None] = mapped_column(ForeignKey("work_types.id"))


class Area(Record, Base):
    __tablename__ = "nsi_areas"


class Territory(Record, Base):
    __tablename__ = "nsi_territories"
    area_id: Mapped[str] = mapped_column(ForeignKey("nsi_areas.id"))
    contract_id: Mapped[str] = mapped_column(ForeignKey("nsi_contracts.id"))


class Cell(Record, Base):
    __tablename__ = "nsi_cells"
    territory_id: Mapped[str] = mapped_column(ForeignKey("nsi_territories.id"))
    replaced_by_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_cells.id"))


class WialonLink(Record, Base):
    __tablename__ = "nsi_wialon_links"
    cell_id: Mapped[str] = mapped_column(ForeignKey("nsi_cells.id"))
    resource_id: Mapped[str] = mapped_column(String(100))
    zone_id: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("resource_id", "zone_id"), UniqueConstraint("cell_id"))


class MaxZoneLink(Record, Base):
    __tablename__ = "nsi_max_zone_links"
    area_id: Mapped[str] = mapped_column(ForeignKey("nsi_areas.id"))
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"))
    __table_args__ = (UniqueConstraint("area_id", "district_id"),)


class Vehicle(Record, Base):
    __tablename__ = "nsi_vehicles"
    plate: Mapped[str] = mapped_column(String(20), unique=True)
    vehicle_type: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    calibration_m3: Mapped[float | None] = mapped_column(Numeric(12, 3))
    calibration_on: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint("calibration_m3 IS NULL OR calibration_m3 > 0"),)


class VehicleRelation(Record, Base):
    __tablename__ = "nsi_vehicle_relations"
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("nsi_vehicles.id"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("nsi_counterparties.id"))
    contract_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_contracts.id"))
    role: Mapped[str] = mapped_column(String(30))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_documents.id"))
    __table_args__ = (
        CheckConstraint("role IN ('OPERATIONAL','LEGAL_OWNER','CONTRACT','ACTUAL','CANDIDATE')"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from"),
        CheckConstraint("status != 'CONFIRMED' OR role != 'OPERATIONAL' OR approved_at IS NOT NULL"),
        CheckConstraint("status != 'CONFIRMED' OR role != 'LEGAL_OWNER' OR evidence_id IS NOT NULL"),
    )


class ContractObject(Record, Base):
    __tablename__ = "nsi_contract_objects"
    contract_id: Mapped[str] = mapped_column(ForeignKey("nsi_contracts.id"))
    territory_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_territories.id"))


class Site(Record, Base):
    __tablename__ = "nsi_sites"
    object_id: Mapped[str] = mapped_column(ForeignKey("nsi_contract_objects.id"))


class Maf(Record, Base):
    __tablename__ = "nsi_maf"
    site_id: Mapped[str] = mapped_column(ForeignKey("nsi_sites.id"))


class Document(Record, Base):
    __tablename__ = "nsi_documents"
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    # An immutable external reference, never a public download URL or a copy in git.
    location: Mapped[str] = mapped_column(Text)
    legal_profile_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_legal_profiles.id"))
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_counterparties.id"))
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_vehicles.id"))
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_contract_revisions.id"))


class ContractLine(Record, Base):
    __tablename__ = "nsi_contract_lines"
    revision_id: Mapped[str] = mapped_column(ForeignKey("nsi_contract_revisions.id"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_documents.id"))
    kind: Mapped[str] = mapped_column(String(4))
    territory_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_territories.id"))
    work_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_contract_works.id"))
    stage: Mapped[str | None] = mapped_column(String(100))
    indicator: Mapped[str | None] = mapped_column(String(500))
    quantity_text: Mapped[str | None] = mapped_column(String(100))
    rate_text: Mapped[str | None] = mapped_column(String(100))
    total_text: Mapped[str | None] = mapped_column(String(100))
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    # Exact source row, decimals and wording retained without recalculation.
    source_row: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (CheckConstraint("kind IN ('OP','RC')"),)


class ContractParty(Record, Base):
    __tablename__ = "nsi_contract_parties"
    revision_id: Mapped[str] = mapped_column(ForeignKey("nsi_contract_revisions.id"))
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_counterparties.id"))
    legal_profile_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_legal_profiles.id"))
    role: Mapped[str] = mapped_column(String(60))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (
        CheckConstraint(
            "(counterparty_id IS NOT NULL AND legal_profile_id IS NULL) OR (counterparty_id IS NULL AND legal_profile_id IS NOT NULL)"
        ),
        UniqueConstraint("revision_id", "counterparty_id", "role"),
        UniqueConstraint("revision_id", "legal_profile_id", "role"),
    )


class History(Base):
    __tablename__ = "nsi_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(100))
    record_id: Mapped[str] = mapped_column(String(100), index=True)
    actor_id: Mapped[int | None] = mapped_column()
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportEvent(Base):
    __tablename__ = "nsi_import_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), unique=True)
    outcome: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(500))
    entity: Mapped[str | None] = mapped_column(String(100))
    record_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


CATALOGUES = {
    "organization": (LegalProfile, "Наша организация"),
    "counterparties": (Counterparty, "Контрагенты"),
    "contracts": (Contract, "Договоры"),
    "revisions": (ContractRevision, "Редакции договоров"),
    "contract-parties": (ContractParty, "Стороны договоров"),
    "units": (Unit, "Единицы измерения"),
    "works": (ContractWork, "Договорные работы"),
    "areas": (Area, "Территории АРМ"),
    "territories": (Territory, "Договорные территории"),
    "cells": (Cell, "Клетки АРМ"),
    "wialon-links": (WialonLink, "Связи Wialon"),
    "max-zone-links": (MaxZoneLink, "Связи геозон MAX"),
    "vehicles": (Vehicle, "Техника"),
    "vehicle-relations": (VehicleRelation, "Связи техники"),
    "objects": (ContractObject, "Договорные объекты DO"),
    "sites": (Site, "Площадки PL"),
    "maf": (Maf, "МАФ"),
    "documents": (Document, "Документы"),
    "contract-lines": (ContractLine, "Строки ОП / РЦ"),
}
