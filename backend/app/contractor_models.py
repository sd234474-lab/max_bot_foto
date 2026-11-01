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


class VehicleType(Record, Base):
    __tablename__ = "nsi_vehicle_types"
    code: Mapped[str] = mapped_column(String(60), unique=True)
    requires_calibration: Mapped[bool] = mapped_column(default=False)
    requires_sts: Mapped[bool] = mapped_column(default=True)
    requires_wialon: Mapped[bool] = mapped_column(default=True)


class WorkCategory(Record, Base):
    __tablename__ = "nsi_work_categories"
    code: Mapped[str] = mapped_column(String(60), unique=True)


class VehicleTypeWorkLink(Record, Base):
    __tablename__ = "nsi_vehicle_type_work_links"
    vehicle_type_id: Mapped[str] = mapped_column(ForeignKey("nsi_vehicle_types.id"))
    work_category_id: Mapped[str] = mapped_column(ForeignKey("nsi_work_categories.id"))
    __table_args__ = (UniqueConstraint("vehicle_type_id", "work_category_id"),)


class Vehicle(Record, Base):
    __tablename__ = "nsi_vehicles"
    plate: Mapped[str] = mapped_column(String(20), unique=True)
    vehicle_type: Mapped[str] = mapped_column(String(100))
    vehicle_type_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_vehicle_types.id"))
    model: Mapped[str | None] = mapped_column(String(200))
    calibration_m3: Mapped[float | None] = mapped_column(Numeric(12, 3))
    calibration_on: Mapped[date | None] = mapped_column(Date)
    # External monitoring identity is explicit; the working name remains the
    # human-facing key and may be completed before a Wialon unit is assigned.
    wialon_unit_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    __table_args__ = (CheckConstraint("calibration_m3 IS NULL OR calibration_m3 > 0"),)


class VehicleRelation(Record, Base):
    __tablename__ = "nsi_vehicle_relations"
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("nsi_vehicles.id"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("nsi_counterparties.id"))
    role: Mapped[str] = mapped_column(String(30))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column()
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_documents.id"))
    __table_args__ = (
        CheckConstraint("role IN ('OPERATIONAL','CANDIDATE')"),
        CheckConstraint("decision IN ('PENDING','APPROVED','REJECTED')"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from"),
        CheckConstraint("status != 'CONFIRMED' OR role != 'OPERATIONAL' OR approved_at IS NOT NULL"),
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
    document_scope: Mapped[str] = mapped_column(String(24), default="COUNTERPARTY")
    document_kind: Mapped[str] = mapped_column(String(40), default="OTHER")
    link_state: Mapped[str] = mapped_column(String(24), default="NOT_REQUIRED")


class DocumentVehicleLink(Record, Base):
    __tablename__ = "nsi_document_vehicle_links"
    document_id: Mapped[str] = mapped_column(ForeignKey("nsi_documents.id"))
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("nsi_vehicles.id"))
    document_kind: Mapped[str] = mapped_column(String(40))
    page_reference: Mapped[str | None] = mapped_column(String(100))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    linked_by: Mapped[int | None] = mapped_column()
    __table_args__ = (UniqueConstraint("document_id", "vehicle_id", "document_kind"),)


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


class QuestionnaireImport(Base):
    __tablename__ = "nsi_questionnaire_imports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_counterparties.id"))
    status: Mapped[str] = mapped_column(String(30))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    received_by: Mapped[int | None] = mapped_column()
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VehicleApprovalRequest(Record, Base):
    __tablename__ = "nsi_vehicle_approval_requests"
    letter_number: Mapped[str] = mapped_column(String(100))
    letter_date: Mapped[date] = mapped_column(Date)
    contract_ids: Mapped[list] = mapped_column(JSON, default=list)
    request_document_id: Mapped[str] = mapped_column(ForeignKey("nsi_documents.id"))
    response_document_id: Mapped[str | None] = mapped_column(ForeignKey("nsi_documents.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VehicleApprovalDecision(Record, Base):
    __tablename__ = "nsi_vehicle_approval_decisions"
    request_id: Mapped[str] = mapped_column(ForeignKey("nsi_vehicle_approval_requests.id"))
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("nsi_vehicles.id"))
    calibration_document_id: Mapped[str] = mapped_column(ForeignKey("nsi_documents.id"))
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("request_id", "vehicle_id"),
        CheckConstraint("decision IN ('PENDING','APPROVED','REJECTED')"),
    )


CATALOGUES = {
    "organization": (LegalProfile, "Наша организация"),
    "counterparties": (Counterparty, "Контрагенты"),
    "contracts": (Contract, "Договоры"),
    "revisions": (ContractRevision, "Редакции договоров"),
    "contract-parties": (ContractParty, "Стороны договоров"),
    "units": (Unit, "Единицы измерения"),
    "works": (ContractWork, "Договорные работы"),
    "vehicle-types": (VehicleType, "Типы техники"),
    "work-categories": (WorkCategory, "Рабочие категории"),
    "vehicle-type-work-links": (VehicleTypeWorkLink, "Допуски типов к работам"),
    "areas": (Area, "Территории АРМ"),
    "territories": (Territory, "Договорные территории"),
    "cells": (Cell, "Клетки АРМ"),
    "wialon-links": (WialonLink, "Связи Wialon"),
    "max-zone-links": (MaxZoneLink, "Связи геозон MAX"),
    "vehicles": (Vehicle, "Техника"),
    "vehicle-relations": (VehicleRelation, "Операционные владельцы (системные записи)"),
    "objects": (ContractObject, "Договорные объекты DO"),
    "sites": (Site, "Площадки PL"),
    "maf": (Maf, "МАФ"),
    "documents": (Document, "Документы"),
    "document-vehicle-links": (DocumentVehicleLink, "Связи документов с техникой"),
    "contract-lines": (ContractLine, "Строки ОП / РЦ"),
}
