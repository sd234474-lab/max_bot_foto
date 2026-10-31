"""Workspace-scoped NSI routes, using MAX authentication, CSRF and database scope."""

import io
import mimetypes
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Date, DateTime, Integer, Numeric, String, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth import AdminUser, CurrentUser, Db
from app.contractor_models import (
    CATALOGUES,
    ContractLine,
    ContractParty,
    ContractRevision,
    Counterparty,
    Document,
    History,
    LegalProfile,
    VehicleRelation,
)
from app.organization_context import current_organization

router = APIRouter(prefix="/api/contractor", tags=["contractor"])
STATUSES = {"REVIEW", "INCOMPLETE", "CONFIRMED", "INACTIVE"}
PLATE = re.compile(r"(?:[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}|\d{4}[А-Я]{2}\d{2,3})\Z")


def plate_valid(value):
    return isinstance(value, str) and bool(PLATE.fullmatch(value))


def serialize(record):
    result = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        result[column.name] = (
            value.isoformat()
            if isinstance(value, (date, datetime))
            else str(value)
            if isinstance(value, Decimal)
            else value
        )
    return result


def model_for(kind):
    if kind not in CATALOGUES:
        raise HTTPException(404, "Справочник не найден")
    return CATALOGUES[kind][0]


def field_info(column):
    target = next(iter(column.foreign_keys), None)
    catalogue = next(
        (
            key
            for key, (model, _) in CATALOGUES.items()
            if target and model.__tablename__ == target.column.table.name
        ),
        None,
    )
    type_name = (
        "json"
        if isinstance(column.type, JSON)
        else "datetime"
        if isinstance(column.type, DateTime)
        else "date"
        if isinstance(column.type, Date)
        else "number"
        if isinstance(column.type, (Integer, Numeric))
        else "text"
    )
    return {
        "key": column.name,
        "type": type_name,
        "required": not column.nullable and column.default is None and column.server_default is None,
        "catalogue": catalogue,
        "readonly": column.name in {"version", "created_at", "approved_at"},
    }


@router.get("/catalogues")
def catalogues(user: CurrentUser):
    return [
        {"key": key, "name": label, "fields": [field_info(c) for c in model.__table__.columns]}
        for key, (model, label) in CATALOGUES.items()
    ]


@router.get("/{kind}")
def records(
    kind: str,
    db: Db,
    user: CurrentUser,
    q: str = "",
    status: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    model = model_for(kind)
    stmt = select(model)
    if model is ContractLine:
        # Raw text rows are retained as import evidence, while the business
        # list shows the parsed physical indicators and contract work lines.
        stmt = stmt.where(model.details["interpretation"].as_string().is_(None))
    if q:
        search_columns = [model.name, model.id]
        search_columns.extend(
            getattr(model, key) for key in ("plate", "number", "code", "inn") if hasattr(model, key)
        )
        stmt = stmt.where(or_(*(column.ilike("%" + q + "%") for column in search_columns)))
    if status:
        stmt = stmt.where(model.status == status)
    return {
        "total": db.scalar(select(func.count()).select_from(stmt.subquery())),
        "items": [
            serialize(row)
            for row in db.scalars(stmt.order_by(model.name, model.id).offset(offset).limit(limit))
        ],
    }


def get_record(db, kind, id):
    row = db.get(model_for(kind), id)
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    return row


@router.get("/{kind}/{id}")
def detail(kind: str, id: str, db: Db, user: CurrentUser):
    record = get_record(db, kind, id)
    links = []
    for key, (model, label) in CATALOGUES.items():
        for column in model.__table__.columns:
            if any(fk.target_fullname == record.__tablename__ + ".id" for fk in column.foreign_keys):
                related = select(model).where(column == id)
                if model is ContractLine:
                    related = related.where(model.details["interpretation"].as_string().is_(None))
                rows = db.scalars(related.order_by(model.id).limit(500)).all()
                if rows:
                    links.append({"kind": key, "name": label, "items": [serialize(row) for row in rows]})
    return {
        "record": serialize(record),
        "links": links,
        "history": [
            serialize(h)
            for h in db.scalars(
                select(History).where(History.entity == kind, History.record_id == id).order_by(History.id)
            )
        ],
    }


@router.get("/documents/{id}/content")
def document_content(id: str, db: Db, user: CurrentUser):
    document = db.get(Document, id)
    if document is None:
        raise HTTPException(404, "Документ не найден")
    parts = document.location.split("!")
    repository = Path(__file__).resolve().parents[2]
    root = repository.parent / "questionnaires"
    path = Path(parts[0]).resolve()
    local_source = (
        len(parts) == 1
        and (repository / ".local/sources").resolve() in path.parents
        and path.suffix.lower() == ".xlsx"
    )
    if not local_source and (root.resolve() not in path.parents or len(parts) not in {2, 3}):
        raise HTTPException(
            422, "Для этого источника доступна исходная ссылка, локальный просмотр отсутствует"
        )
    try:
        if local_source:
            if path.stat().st_size > 52_428_800:
                raise HTTPException(413, "Документ превышает предел просмотра")
            content = path.read_bytes()
        else:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo(parts[1])
                if info.file_size > 52_428_800:
                    raise HTTPException(413, "Документ превышает предел просмотра")
                content = archive.read(info)
        if len(parts) == 3:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                info = archive.getinfo(parts[2])
                if info.file_size > 52_428_800:
                    raise HTTPException(413, "Документ превышает предел просмотра")
                content = archive.read(info)
        import hashlib

        if hashlib.sha256(content).hexdigest() != document.sha256:
            raise HTTPException(409, "Оригинал изменился; просмотр остановлен")
    except (OSError, KeyError, zipfile.BadZipFile):
        raise HTTPException(404, "Исходное вложение недоступно") from None
    mime = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
    inline = mime in {"application/pdf", "image/jpeg", "image/png", "image/webp"}
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": "inline" if inline else "attachment",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class Write(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=0, ge=0)
    values: dict[str, Any]


def validate_values(db, model, values, record=None):
    columns = {c.name: c for c in model.__table__.columns}
    if set(values) - set(columns) or set(values) & {"created_at", "version", "approved_at"}:
        raise HTTPException(422, "Неизвестное или служебное поле")
    if record and "id" in values and values["id"] != record.id:
        raise HTTPException(422, "Постоянный ID менять нельзя")
    for key, value in list(values.items()):
        c = columns[key]
        if value is None:
            if not c.nullable:
                raise HTTPException(422, f"Поле {key} обязательно")
            continue
        try:
            if isinstance(c.type, DateTime):
                values[key] = datetime.fromisoformat(value)
            elif isinstance(c.type, Date):
                values[key] = date.fromisoformat(value)
            elif isinstance(c.type, Integer):
                if isinstance(value, bool) or int(value) != float(value):
                    raise ValueError()
                values[key] = int(value)
            elif isinstance(c.type, Numeric):
                values[key] = Decimal(str(value))
                if not values[key].is_finite():
                    raise ValueError()
            elif isinstance(c.type, String):
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or (c.type.length and len(value) > c.type.length)
                ):
                    raise ValueError()
            elif isinstance(c.type, JSON):
                expected = list if key in {"party_snapshots", "source_row"} else dict
                if not isinstance(value, expected):
                    raise ValueError()
        except (ValueError, TypeError, ArithmeticError):
            raise HTTPException(422, f"Неверное значение поля {key}") from None
        for fk in c.foreign_keys:
            target = fk.column.table
            if not db.scalar(select(target.c.id).where(target.c.id == values[key])):
                raise HTTPException(422, f"Связанная запись {key} не существует")
    merged = (serialize(record) if record else {}) | values
    if merged.get("status", "REVIEW") not in STATUSES:
        raise HTTPException(422, "Недопустимый статус")
    if (
        model.__tablename__ in {"nsi_counterparties", "nsi_legal_profiles"}
        and merged.get("status") == "CONFIRMED"
    ):
        if not re.fullmatch(r"\d{10}|\d{12}", merged.get("inn") or ""):
            raise HTTPException(422, "Для подтверждения укажите точный ИНН")

        def validate_bank(data):
            if isinstance(data, list):
                for item in data:
                    validate_bank(item)
            elif isinstance(data, dict):
                for key, value in data.items():
                    if value and key.casefold() == "бик" and not re.fullmatch(r"\d{9}", str(value)):
                        raise HTTPException(422, "БИК должен быть точной строкой из девяти цифр")
                    if (
                        value
                        and re.search(r"сч[её]т$", key, re.I)
                        and not re.fullmatch(r"\d{20}", str(value))
                    ):
                        raise HTTPException(
                            422, "Банковский счёт должен быть точной строкой из двадцати цифр"
                        )
                    if isinstance(value, (list, dict)):
                        validate_bank(value)

        validate_bank(merged.get("details", {}))
    if not record:
        for c in columns.values():
            if not c.nullable and c.default is None and c.server_default is None and c.name not in merged:
                raise HTTPException(422, f"Поле {c.name} обязательно")
    if "plate" in merged and not plate_valid(merged["plate"]):
        raise HTTPException(
            422, "Госномер требует проверки: используйте полный номер кириллицей без пробелов"
        )
    if model is LegalProfile:
        scope = current_organization()
        if not scope or merged.get("organization_id") != scope.id:
            raise HTTPException(422, "Профиль должен принадлежать выбранной Organization MAX")
    if merged.get("parent_id"):
        seen = {merged.get("id")}
        parent = merged["parent_id"]
        while parent:
            if parent in seen:
                raise HTTPException(422, "Цикл в иерархии работ")
            seen.add(parent)
            parent = db.get(model, parent).parent_id
    if (
        model is VehicleRelation
        and merged.get("role") == "OPERATIONAL"
        and merged.get("status") == "CONFIRMED"
    ):
        raise HTTPException(422, "Подтвердите смену операционного владельца отдельным действием")
    if record and model is ContractRevision and record.status == "CONFIRMED":
        raise HTTPException(409, "Подтверждённая редакция неизменяема; создайте новую редакцию")
    if model is ContractRevision and merged.get("status") == "CONFIRMED":
        parties = db.scalars(select(ContractParty).where(ContractParty.revision_id == merged.get("id"))).all()
        if parties:
            values["party_snapshots"] = [{**p.snapshot, "role": p.role} for p in parties]
            merged["party_snapshots"] = values["party_snapshots"]
        snapshots = merged.get("party_snapshots", [])
        if (
            not merged.get("signed_on")
            or not merged.get("effective_from")
            or not merged.get("basis")
            or len(snapshots) < 2
        ):
            raise HTTPException(
                422, "Для подтверждения нужны даты, основание и юридические снимки обеих сторон"
            )
        if any(not isinstance(p, dict) or not p.get("name") or not p.get("role") for p in snapshots):
            raise HTTPException(422, "В снимке стороны обязательны наименование и договорная роль")
    if model is ContractParty:
        if bool(merged.get("counterparty_id")) == bool(merged.get("legal_profile_id")):
            raise HTTPException(422, "Выберите одну сторону: внешний контрагент или наша организация")
        identity = (
            db.get(Counterparty, merged["counterparty_id"])
            if merged.get("counterparty_id")
            else db.get(LegalProfile, merged["legal_profile_id"])
        )
        values["snapshot"] = serialize(identity)
    if model in {ContractLine, ContractParty}:
        revision_ids = {merged.get("revision_id"), getattr(record, "revision_id", None)} - {None}
        for revision_id in revision_ids:
            if db.get(ContractRevision, revision_id).status == "CONFIRMED":
                raise HTTPException(
                    409, "Строки подтверждённой редакции неизменяемы; создайте новую редакцию"
                )
    return values


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Нарушена уникальность или связь; проверьте ID, номера и даты") from None


def history(db, kind, row, user, before):
    db.flush()
    db.add(History(entity=kind, record_id=row.id, actor_id=user.id, before=before, after=serialize(row)))


@router.post("/{kind}", status_code=201)
def create(kind: str, body: Write, db: Db, user: AdminUser):
    model = model_for(kind)
    row = model(**validate_values(db, model, dict(body.values)))
    db.add(row)
    try:
        history(db, kind, row, user, None)
        commit(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Запись с таким ключом уже существует или нарушена связь") from None
    return serialize(row)


@router.patch("/{kind}/{id}")
def edit(kind: str, id: str, body: Write, db: Db, user: AdminUser):
    row = db.scalar(select(model_for(kind)).where(model_for(kind).id == id).with_for_update())
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    if row.version != body.version:
        raise HTTPException(409, "Запись уже изменена; обновите карточку")
    before = serialize(row)
    for key, value in validate_values(db, type(row), dict(body.values), row).items():
        setattr(row, key, value)
    row.version += 1
    history(db, kind, row, user, before)
    commit(db)
    return serialize(row)


@router.post("/vehicle-relations/{id}/approve")
def approve_owner(id: str, db: Db, user: AdminUser):
    row = get_record(db, "vehicle-relations", id)
    # Lock the shared vehicle to serialize competing approvals, including when
    # there is no previous owner relation to lock.
    from app.contractor_models import Vehicle

    db.scalar(select(Vehicle).where(Vehicle.id == row.vehicle_id).with_for_update())
    db.refresh(row)
    if row.status == "CONFIRMED":
        return serialize(row)
    if row.role not in {"CANDIDATE", "OPERATIONAL"}:
        raise HTTPException(422, "Эта связь не является кандидатом операционного владельца")
    from app.config import settings

    today = datetime.now(ZoneInfo(settings().app_timezone)).date()
    for old in db.scalars(
        select(VehicleRelation).where(
            VehicleRelation.vehicle_id == row.vehicle_id,
            VehicleRelation.role == "OPERATIONAL",
            VehicleRelation.status == "CONFIRMED",
            VehicleRelation.valid_to.is_(None),
        )
    ):
        before = serialize(old)
        old.valid_to = today
        old.version += 1
        history(db, "vehicle-relations", old, user, before)
    before = serialize(row)
    row.role, row.status, row.valid_from = "OPERATIONAL", "CONFIRMED", today
    row.approved_at = datetime.now(timezone.utc)
    row.version += 1
    history(db, "vehicle-relations", row, user, before)
    commit(db)
    return serialize(row)
