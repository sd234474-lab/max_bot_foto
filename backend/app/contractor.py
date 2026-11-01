"""Workspace-scoped NSI routes, using MAX authentication, CSRF and database scope."""

import copy
import hashlib
import io
import mimetypes
import re
import tempfile
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Date, DateTime, Integer, Numeric, String, case, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth import AdminUser, CurrentUser, Db
from app.contractor_models import (
    CATALOGUES,
    Contract,
    ContractLine,
    ContractParty,
    ContractRevision,
    Counterparty,
    Document,
    DocumentVehicleLink,
    History,
    LegalProfile,
    QuestionnaireImport,
    Vehicle,
    VehicleApprovalDecision,
    VehicleApprovalRequest,
    VehicleRelation,
    VehicleType,
    VehicleTypeWorkLink,
    WorkCategory,
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


@router.get("/questionnaire-imports")
def questionnaire_imports(db: Db, user: CurrentUser):
    rows = db.scalars(
        select(QuestionnaireImport).order_by(QuestionnaireImport.received_at.desc()).limit(100)
    ).all()
    return [questionnaire_result(row) for row in rows]


@router.get("/questionnaire-imports/{id}")
def questionnaire_import(id: str, db: Db, user: CurrentUser):
    row = db.get(QuestionnaireImport, id)
    if row is None:
        raise HTTPException(404, "Загрузка анкеты не найдена")
    result = questionnaire_result(row)
    summary = dict(result.get("summary") or {})
    events = [dict(item) for item in summary.get("events", [])]
    account_ordinal = 0
    for item in events:
        message = item.get("message", "")
        if item.get("entity") == "counterparties" and item.get("record_id"):
            counterparty = db.get(Counterparty, item["record_id"])
            details = dict(counterparty.details or {}) if counterparty else {}
            if "БИК" in message:
                key = next((key for key in details if "бик" in key.casefold()), "БИК")
                item.update(
                    {"field_kind": "BIK", "field_label": "БИК", "current_value": details.get(key) or ""}
                )
            elif "Счёт" in message:
                keys = [
                    key
                    for key in details
                    if "счёт" in key.casefold() or "счет" in key.casefold()
                ]
                key = keys[min(account_ordinal, len(keys) - 1)] if keys else "Банковский счёт"
                account_ordinal += 1
                item.update(
                    {
                        "field_kind": "ACCOUNT",
                        "field_label": key,
                        "current_value": details.get(key) or "",
                    }
                )
        elif (
            item.get("entity") == "vehicles"
            and item.get("record_id")
            and "рабочее имя" in message.casefold()
        ):
            vehicle = db.get(Vehicle, item["record_id"])
            item.update(
                {
                    "field_kind": "VEHICLE_NAME",
                    "field_label": "Рабочее имя",
                    "current_value": vehicle.name if vehicle else "",
                }
            )
    summary["events"] = events
    result["summary"] = summary
    return result


@router.get("/questionnaire-imports/{id}/documents/{document_id}")
def questionnaire_document(id: str, document_id: str, db: Db, user: CurrentUser):
    row = db.get(QuestionnaireImport, id)
    if row is None:
        raise HTTPException(404, "Загрузка анкеты не найдена")
    summary = copy.deepcopy(row.summary or {})
    candidates = list(summary.get("unassigned_documents", []))
    for vehicle in summary.get("vehicles", []):
        candidates.extend(vehicle.get("documents", []))
    metadata = next((item for item in candidates if item.get("id") == document_id), None)
    if metadata is None or not metadata.get("location"):
        raise HTTPException(404, "Документ анкеты не найден")
    repository = Path(__file__).resolve().parents[2]
    scope = current_organization()
    root = (
        scope.data_dir / "contractor-documents"
        if scope
        else repository / ".local" / "contractor-documents"
    ).resolve()
    path = Path(metadata["location"]).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 52_428_800:
        raise HTTPException(422, "Файл недоступен для безопасного просмотра")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != metadata.get("sha256"):
        raise HTTPException(409, "Сохранённый файл изменился")
    mime = mimetypes.guess_type(metadata.get("original_name") or path.name)[0] or "application/octet-stream"
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


class QuestionnaireResolution(BaseModel):
    event_index: int = Field(ge=0)
    resolution: str = Field(min_length=2, max_length=1000)


class QuestionnaireItemDraft(BaseModel):
    accepted: dict[str, Any] = Field(default_factory=dict)
    vehicle_key: str | None = None
    document_kind: str | None = None


def questionnaire_counts(summary):
    items = [summary.get("counterparty")] + list(summary.get("vehicles", []))
    items = [item for item in items if item]
    return {
        "source_rows": len(summary.get("vehicles", [])),
        "prepared": sum(item.get("status") == "STAGED" for item in items),
        "applied": sum(item.get("status") == "APPLIED" for item in items),
        "pending": sum(item.get("status") == "PENDING" for item in items),
        "issues": sum(len(item.get("issues", [])) for item in items),
        "unassigned_documents": sum(
            item.get("status") != "APPLIED" for item in summary.get("unassigned_documents", [])
        ),
    }


def questionnaire_result(row):
    result = serialize(row)
    summary = dict(result.get("summary") or {})
    if summary.get("schema_version") == 2:
        summary["counts"] = questionnaire_counts(summary)
    result["summary"] = summary
    return result


def questionnaire_item(summary, key):
    if key == "counterparty":
        return summary.get("counterparty")
    return next(
        (
            item
            for group in (summary.get("vehicles", []), summary.get("unassigned_documents", []))
            for item in group
            if item.get("key") == key
        ),
        None,
    )


@router.patch("/questionnaire-imports/{id}/items/{item_key}")
def prepare_questionnaire_item(
    id: str, item_key: str, body: QuestionnaireItemDraft, db: Db, user: AdminUser
):
    row = db.scalar(select(QuestionnaireImport).where(QuestionnaireImport.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, "Загрузка анкеты не найдена")
    summary = copy.deepcopy(row.summary or {})
    if summary.get("schema_version") != 2:
        raise HTTPException(409, "Эта загрузка создана прежней версией обработчика")
    item = questionnaire_item(summary, item_key)
    if item is None:
        raise HTTPException(404, "Строка анкеты не найдена")
    if item.get("status") == "APPLIED":
        raise HTTPException(409, "Строка уже применена к НСИ")
    if item.get("section") in {"counterparty", "vehicles"}:
        accepted = {**dict(item.get("accepted") or item.get("proposed") or {}), **body.accepted}
        if item.get("section") == "vehicles" and item.get("operation") != "RETIRE":
            if not accepted.get("plate") or not accepted.get("vehicle_type"):
                raise HTTPException(422, "Для строки техники обязательны госномер и тип")
        item["accepted"] = accepted
    else:
        if not body.vehicle_key or not body.document_kind:
            raise HTTPException(422, "Выберите машину и вид документа")
        if not any(vehicle.get("key") == body.vehicle_key for vehicle in summary.get("vehicles", [])):
            raise HTTPException(422, "Выбранная машина отсутствует в этой анкете")
        item.update(vehicle_key=body.vehicle_key, kind=body.document_kind)
    item.update(
        status="STAGED",
        prepared_by=user.id,
        prepared_at=datetime.now(timezone.utc).isoformat(),
    )
    row.summary = summary
    row.status = "READY_TO_APPLY"
    commit(db)
    return questionnaire_result(row)


def safe_document_name(vehicle, kind, original_name, calibration_m3=None):
    label = "СТС" if kind == "STS" else "тарировка" if kind == "CALIBRATION" else "документ"
    volume = f" {calibration_m3} м3" if kind == "CALIBRATION" and calibration_m3 else ""
    stem = re.sub(r'[^0-9A-Za-zА-Яа-яЁё _.-]+', "_", f"{vehicle.name} {label}{volume} {date.today().isoformat()}")
    return stem.strip(" .") + Path(original_name).suffix.lower()


def apply_staged_document(db, user, source, metadata, vehicle):
    if metadata.get("state") == "MISSING" or not metadata.get("sha256"):
        return None
    document = db.scalar(select(Document).where(Document.sha256 == metadata["sha256"]))
    kind = metadata.get("kind") or "OTHER"
    if document is None:
        document = Document(
            id=metadata["sha256"],
            name=safe_document_name(vehicle, kind, metadata["original_name"], vehicle.calibration_m3),
            sha256=metadata["sha256"],
            location=metadata["location"],
            counterparty_id=None,
            vehicle_id=vehicle.id,
            document_scope="VEHICLE",
            document_kind=kind,
            link_state="LINKED",
            status="REVIEW",
            source=source,
            details={"original_name": metadata["original_name"], "verification": "Требует проверки оператором"},
        )
        db.add(document)
    link = db.scalar(
        select(DocumentVehicleLink).where(
            DocumentVehicleLink.document_id == document.id,
            DocumentVehicleLink.vehicle_id == vehicle.id,
            DocumentVehicleLink.document_kind == kind,
        )
    )
    if link is None:
        link = DocumentVehicleLink(
            name=document.name,
            status="CONFIRMED",
            source=source,
            details={"original_name": metadata["original_name"]},
            document_id=document.id,
            vehicle_id=vehicle.id,
            document_kind=kind,
            linked_by=user.id,
        )
        db.add(link)
    return document


@router.post("/questionnaire-imports/{id}/apply")
def apply_questionnaire(id: str, db: Db, user: AdminUser):
    from app.contractor_import import ensure_vehicle_type

    row = db.scalar(select(QuestionnaireImport).where(QuestionnaireImport.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, "Загрузка анкеты не найдена")
    summary = copy.deepcopy(row.summary or {})
    if summary.get("schema_version") != 2:
        raise HTTPException(409, "Эта загрузка создана прежней версией обработчика")
    source = summary.get("source") or row.file_name
    party_item = summary.get("counterparty") or {}
    party = db.get(Counterparty, row.counterparty_id) if row.counterparty_id else None
    if party_item.get("status") == "STAGED":
        values = dict(party_item.get("accepted") or {})
        before = serialize(party) if party else None
        if party is None:
            party = Counterparty(
                id="inn:" + values["inn"],
                name=values["name"],
                status="REVIEW",
                version=1,
                source=source,
                details={},
            )
            db.add(party)
        for key in ("name", "inn", "legal_form", "details"):
            if key in values:
                setattr(party, key, values[key])
        if before:
            party.version += 1
        history(db, "counterparties", party, user, before)
        row.counterparty_id = party.id
        party_item.update(status="APPLIED", applied_by=user.id, applied_at=datetime.now(timezone.utc).isoformat())
    if party is None:
        raise HTTPException(409, "Сначала подготовьте и примените данные контрагента")

    vehicles_by_key = {}
    for item in summary.get("vehicles", []):
        if item.get("status") != "STAGED":
            continue
        values = dict(item.get("accepted") or {})
        vehicle = db.get(Vehicle, item.get("record_id")) if item.get("record_id") else None
        if item.get("operation") == "RETIRE":
            if vehicle:
                for relation in db.scalars(
                    select(VehicleRelation).where(
                        VehicleRelation.vehicle_id == vehicle.id,
                        VehicleRelation.counterparty_id == party.id,
                        VehicleRelation.valid_to.is_(None),
                    )
                ):
                    before = serialize(relation)
                    relation.valid_to = date.today()
                    relation.status = "INACTIVE"
                    relation.decision_reason = "Выбыла по подтверждённой анкете"
                    relation.version += 1
                    history(db, "vehicle-relations", relation, user, before)
            item.update(status="APPLIED", applied_by=user.id, applied_at=datetime.now(timezone.utc).isoformat())
            continue
        vehicle_type = ensure_vehicle_type(db, values["vehicle_type"])
        if vehicle is None:
            vehicle = db.scalar(select(Vehicle).where(Vehicle.plate == values["plate"]))
        before = serialize(vehicle) if vehicle else None
        if vehicle is None:
            vehicle = Vehicle(id=values["plate"], status="REVIEW", version=1, source=source, details={})
            db.add(vehicle)
        for key in ("name", "plate", "vehicle_type", "model", "calibration_m3"):
            if key in values:
                setattr(vehicle, key, values[key] or None)
        vehicle.vehicle_type_id = vehicle_type.id if vehicle_type else vehicle.vehicle_type_id
        if before:
            vehicle.version += 1
        vehicle.details = {**(vehicle.details or {}), "last_questionnaire": row.id, "questionnaire_row": item.get("source_row")}
        history(db, "vehicles", vehicle, user, before)
        relation_id = hashlib.sha256((party.id + ":" + vehicle.id).encode()).hexdigest()
        relation = db.get(VehicleRelation, relation_id)
        if relation is None:
            relation = VehicleRelation(
                id=relation_id,
                name=party.name + " ↔ " + vehicle.plate,
                vehicle_id=vehicle.id,
                counterparty_id=party.id,
                role="CANDIDATE",
                decision="PENDING",
                status="REVIEW",
                source=source,
                details={"questionnaire_row": item.get("source_row")},
            )
            db.add(relation)
        for document in item.get("documents", []):
            apply_staged_document(db, user, source, document, vehicle)
        vehicles_by_key[item["key"]] = vehicle
        item.update(status="APPLIED", record_id=vehicle.id, applied_by=user.id, applied_at=datetime.now(timezone.utc).isoformat())

    for item in summary.get("unassigned_documents", []):
        if item.get("status") != "STAGED":
            continue
        vehicle_item = next((candidate for candidate in summary.get("vehicles", []) if candidate.get("key") == item.get("vehicle_key")), None)
        vehicle = vehicles_by_key.get(item.get("vehicle_key"))
        if vehicle is None and vehicle_item and vehicle_item.get("record_id"):
            vehicle = db.get(Vehicle, vehicle_item["record_id"])
        if vehicle is None:
            continue
        apply_staged_document(db, user, source, {**item, "state": "MATCHED"}, vehicle)
        item.update(status="APPLIED", applied_by=user.id, applied_at=datetime.now(timezone.utc).isoformat())
    counts = questionnaire_counts(summary)
    row.summary = summary
    row.status = "COMPLETED" if counts["pending"] == 0 and counts["unassigned_documents"] == 0 else "NEEDS_REVIEW"
    commit(db)
    return questionnaire_result(row)


@router.patch("/questionnaire-imports/{id}/resolve")
def resolve_questionnaire_issue(
    id: str, body: QuestionnaireResolution, db: Db, user: AdminUser
):
    row = db.scalar(select(QuestionnaireImport).where(QuestionnaireImport.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, "Загрузка анкеты не найдена")
    summary = dict(row.summary or {})
    events = [dict(item) for item in summary.get("events", [])]
    if body.event_index >= len(events) or events[body.event_index].get("outcome") not in {
        "REVIEW",
        "REJECTED",
    }:
        raise HTTPException(422, "Вопрос анкеты не найден")
    issue = events[body.event_index]
    applied = None
    if issue.get("entity") == "counterparties" and issue.get("record_id"):
        counterparty = db.get(Counterparty, issue["record_id"])
        if counterparty:
            details = dict(counterparty.details or {})
            if "БИК" in issue.get("message", ""):
                if not re.fullmatch(r"\d{9}", body.resolution.strip()):
                    raise HTTPException(422, "БИК должен состоять ровно из девяти цифр")
                key = next((key for key in details if "бик" in key.casefold()), "БИК")
                before = serialize(counterparty)
                details[key] = body.resolution.strip()
                counterparty.details = details
                counterparty.version += 1
                history(db, "counterparties", counterparty, user, before)
                applied = {key: body.resolution.strip()}
            elif "Счёт" in issue.get("message", ""):
                if not re.fullmatch(r"\d{20}", body.resolution.strip()):
                    raise HTTPException(422, "Банковский счёт должен состоять ровно из двадцати цифр")
                account_keys = [
                    key
                    for key in details
                    if "счёт" in key.casefold() or "счет" in key.casefold()
                ]
                prior = sum(
                    item.get("entity") == "counterparties"
                    and "Счёт" in item.get("message", "")
                    for item in events[: body.event_index]
                )
                key = account_keys[min(prior, len(account_keys) - 1)] if account_keys else "Расчётный счёт"
                before = serialize(counterparty)
                details[key] = body.resolution.strip()
                counterparty.details = details
                counterparty.version += 1
                history(db, "counterparties", counterparty, user, before)
                applied = {key: body.resolution.strip()}
    elif (
        issue.get("entity") == "vehicles"
        and issue.get("record_id")
        and "рабочее имя" in issue.get("message", "").casefold()
    ):
        vehicle = db.get(Vehicle, issue["record_id"])
        if vehicle:
            before = serialize(vehicle)
            vehicle.name = body.resolution.strip()
            vehicle.version += 1
            history(db, "vehicles", vehicle, user, before)
            applied = {"name": vehicle.name}
    events[body.event_index].update(
        {
            "resolved": True,
            "resolution": body.resolution.strip(),
            "resolved_by": user.id,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "applied_values": applied,
        }
    )
    unresolved = sum(
        item.get("outcome") in {"REVIEW", "REJECTED"} and not item.get("resolved")
        for item in events
    )
    summary.update({"events": events, "review": unresolved})
    row.summary = summary
    row.status = "COMPLETED" if unresolved == 0 else "NEEDS_REVIEW"
    commit(db)
    return serialize(row)


@router.post("/questionnaire-imports", status_code=201)
async def upload_questionnaire(request: Request, db: Db, user: AdminUser):
    from starlette.datastructures import UploadFile

    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise HTTPException(422, "Выберите ZIP-архив анкеты")
    if not upload.filename.lower().endswith(".zip"):
        raise HTTPException(422, "Загрузчик принимает только ZIP-архив анкеты")
    content = await upload.read(104_857_601)
    if len(content) > 104_857_600:
        raise HTTPException(413, "Архив превышает предел 100 МБ")
    fingerprint = hashlib.sha256(content).hexdigest()
    from app.contractor_import import QUESTIONNAIRE_ANALYZER_VERSION

    batch_id = hashlib.sha256(
        f"{fingerprint}:object-review-v2:{QUESTIONNAIRE_ANALYZER_VERSION}".encode()
    ).hexdigest()
    existing = db.get(QuestionnaireImport, batch_id)
    if existing:
        return questionnaire_result(existing)
    scope = current_organization()
    repository = Path(__file__).resolve().parents[2]
    document_root = (
        scope.data_dir / "contractor-documents"
        if scope
        else repository / ".local" / "contractor-documents"
    )
    temporary_file = tempfile.NamedTemporaryFile(
        prefix="contractor-questionnaire-", suffix=".zip", delete=False
    )
    temporary = Path(temporary_file.name)
    temporary_file.close()
    try:
        temporary.write_bytes(content)
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise HTTPException(422, f"Архив повреждён: {bad}")
        from app.contractor_import import analyze_questionnaire

        summary = analyze_questionnaire(
            db, temporary, document_root=document_root, original_name=upload.filename
        )
        party = summary.get("counterparty") or {}
        counterparty_id = party.get("record_id")
        counts = questionnaire_counts(summary)
        batch = QuestionnaireImport(
            id=batch_id,
            file_name=upload.filename,
            sha256=fingerprint,
            counterparty_id=counterparty_id,
            status="NEEDS_REVIEW" if counts["pending"] else "COMPLETED",
            summary=summary,
            received_by=user.id,
        )
        db.add(batch)
        commit(db)
        return questionnaire_result(batch)
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from None
    except zipfile.BadZipFile:
        db.rollback()
        raise HTTPException(422, "Файл не является исправным ZIP-архивом") from None
    finally:
        temporary.unlink(missing_ok=True)


def questionnaire_document_bytes(document):
    path = Path(document.location)
    if "!" in document.location or not path.is_file() or path.stat().st_size > 52_428_800:
        return None
    content = path.read_bytes()
    return content if hashlib.sha256(content).hexdigest() == document.sha256 else None


@router.get("/counterparties/{id}/questionnaire-export")
def export_questionnaire(id: str, db: Db, user: CurrentUser):
    from openpyxl import Workbook

    party = get_record(db, "counterparties", id)
    relations = db.scalars(
        select(VehicleRelation).where(
            VehicleRelation.counterparty_id == id,
            VehicleRelation.role == "OPERATIONAL",
            VehicleRelation.status == "CONFIRMED",
            VehicleRelation.valid_to.is_(None),
        )
    ).all()
    vehicles = {
        vehicle.id: vehicle
        for vehicle in db.scalars(
            select(Vehicle).where(Vehicle.id.in_({relation.vehicle_id for relation in relations}))
        ).all()
    }
    links = db.scalars(
        select(DocumentVehicleLink).where(DocumentVehicleLink.vehicle_id.in_(vehicles))
    ).all() if vehicles else []
    documents = {
        document.id: document
        for document in db.scalars(
            select(Document).where(Document.id.in_({link.document_id for link in links}))
        ).all()
    }
    links_by_vehicle = {}
    for link in links:
        document = documents.get(link.document_id)
        if document:
            links_by_vehicle.setdefault(link.vehicle_id, {})[link.document_kind] = document

    workbook = Workbook()
    passport = workbook.active
    passport.title = "Паспорт контрагента"
    passport["A3"], passport["B3"] = "Версия", "ARM_IMPORT_V2"
    passport["A5"], passport["B5"] = "Организационная форма", party.legal_form
    passport["A8"], passport["B8"] = "Наименование", party.name
    passport["A13"], passport["B13"] = "ИНН", party.inn
    details = dict(party.details or {})
    used_rows = {3, 5, 8, 13}
    for preferred, tokens in ((24, ("бик",)), (25, ("расч", "сч")), (26, ("корр", "сч"))):
        found = next(((key, value) for key, value in details.items() if all(token in key.casefold() for token in tokens)), None)
        if found:
            passport[f"A{preferred}"], passport[f"B{preferred}"] = found
            used_rows.add(preferred)
    free_rows = iter(row for row in range(4, 31) if row not in used_rows)
    written_labels = {str(passport[f"A{row}"].value or "") for row in used_rows}
    for key, value in details.items():
        if key in written_labels:
            continue
        try:
            target_row = next(free_rows)
        except StopIteration:
            break
        passport[f"A{target_row}"], passport[f"B{target_row}"] = key, value

    sheet = workbook.create_sheet("Техника")
    headers = {"B": "Запись", "C": "Статус", "D": "Тип", "E": "Госномер", "F": "Рабочее имя", "G": "Марка / модель", "L": "Тарировка", "N": "Файл тарировки", "P": "Файл СТС", "V": "Технический ID"}
    for column, label in headers.items():
        sheet[f"{column}4"] = label
    archive_files = {}
    manifest = []
    for row_number, vehicle in enumerate(sorted(vehicles.values(), key=lambda item: item.name.casefold()), 5):
        sheet[f"B{row_number}"] = vehicle.name
        sheet[f"C{row_number}"] = "ИЗ СПРАВОЧНИКА"
        sheet[f"D{row_number}"] = vehicle.vehicle_type
        sheet[f"E{row_number}"] = vehicle.plate
        sheet[f"F{row_number}"] = vehicle.name
        sheet[f"G{row_number}"] = vehicle.model
        sheet[f"L{row_number}"] = float(vehicle.calibration_m3) if vehicle.calibration_m3 is not None else None
        sheet[f"V{row_number}"] = vehicle.plate
        for kind, column in (("CALIBRATION", "N"), ("STS", "P")):
            document = links_by_vehicle.get(vehicle.id, {}).get(kind)
            if not document:
                sheet[f"{column}{row_number}"] = "ОТСУТСТВУЕТ"
                continue
            content = questionnaire_document_bytes(document)
            if content is None:
                sheet[f"{column}{row_number}"] = "ФАЙЛ НЕДОСТУПЕН"
                continue
            name = safe_document_name(vehicle, kind, document.name, vehicle.calibration_m3)
            base, suffix = Path(name).stem, Path(name).suffix
            candidate, ordinal = name, 2
            while candidate.casefold() in {item.casefold() for item in archive_files}:
                candidate = f"{base} ({ordinal}){suffix}"
                ordinal += 1
            archive_files[candidate] = content
            sheet[f"{column}{row_number}"] = candidate
            manifest.append({"vehicle_id": vehicle.id, "document_id": document.id, "kind": kind, "name": candidate})

    xlsx = io.BytesIO()
    workbook.save(xlsx)
    workbook.close()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Анкета.xlsx", xlsx.getvalue())
        for name, content in archive_files.items():
            archive.writestr(name, content)
    export_id = hashlib.sha256((id + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()
    db.add(
        History(
            entity="questionnaire-exports",
            record_id=export_id,
            actor_id=user.id,
            before=None,
            after={"counterparty_id": id, "vehicles": list(vehicles), "documents": manifest},
        )
    )
    commit(db)
    filename = re.sub(r'[^0-9A-Za-zА-Яа-яЁё_.-]+', "_", f"Анкета_{party.name}_{date.today().isoformat()}.zip")
    return Response(
        result.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="questionnaire.zip"; filename*=UTF-8\'\'{quote(filename)}'},
    )


def vehicle_rows(db, vehicles):
    """Add the operator-facing ownership and monitoring state to vehicle rows."""
    vehicle_ids = [vehicle.id for vehicle in vehicles]
    if not vehicle_ids:
        return []
    from app.config import settings

    today = datetime.now(ZoneInfo(settings().app_timezone)).date()
    relations = db.scalars(
        select(VehicleRelation).where(
            VehicleRelation.vehicle_id.in_(vehicle_ids),
            or_(VehicleRelation.valid_from.is_(None), VehicleRelation.valid_from <= today),
            or_(VehicleRelation.valid_to.is_(None), VehicleRelation.valid_to >= today),
            VehicleRelation.role.in_(("OPERATIONAL", "CANDIDATE")),
        )
    ).all()
    parties = {
        party.id: party.name
        for party in db.scalars(
            select(Counterparty).where(
                Counterparty.id.in_({relation.counterparty_id for relation in relations})
            )
        ).all()
    }
    by_vehicle = {}
    for relation in relations:
        values = by_vehicle.setdefault(relation.vehicle_id, {})
        if relation.role == "OPERATIONAL" and relation.status == "CONFIRMED":
            values["operational_owner"] = parties.get(relation.counterparty_id)
        elif relation.role == "CANDIDATE" and relation.decision == "PENDING":
            values.setdefault("owner_candidates", []).append(
                {"id": relation.id, "name": parties.get(relation.counterparty_id)}
            )
    type_ids = {vehicle.vehicle_type_id for vehicle in vehicles if vehicle.vehicle_type_id}
    type_names = {item.id: item.name for item in db.scalars(select(VehicleType).where(VehicleType.id.in_(type_ids))).all()}
    result = []
    for vehicle in vehicles:
        item = serialize(vehicle)
        item["vehicle_type"] = type_names.get(vehicle.vehicle_type_id, vehicle.vehicle_type)
        missing = vehicle.name.endswith(" · имя не задано")
        owner = by_vehicle.get(vehicle.id, {}).get("operational_owner")
        candidates = by_vehicle.get(vehicle.id, {}).get("owner_candidates", [])
        item.update(
            short_name=None if missing else vehicle.name,
            short_name_missing=missing,
            operational_owner=owner,
            owner_candidate=candidates[0]["name"] if candidates else None,
            owner_candidates=candidates,
            ownership_control="CONFLICT" if owner and candidates else "PENDING" if candidates else "UNASSIGNED" if not owner else "OK",
            wialon_control="MATCHED" if vehicle.wialon_unit_id else "MISSING",
        )
        result.append(item)
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
        "readonly": column.name in {"version", "created_at", "approved_at", "decision", "decision_reason", "decided_at", "decided_by"},
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
    rows = db.scalars(stmt.order_by(
        case((model.name.ilike("% · имя не задано"), 1), else_=0) if model is Vehicle else model.name,
        model.name,
        model.id,
    ).offset(offset).limit(limit)).all()
    return {
        "total": db.scalar(select(func.count()).select_from(stmt.subquery())),
        "items": vehicle_rows(db, rows) if model is Vehicle else [serialize(row) for row in rows],
    }


@router.get("/counterparties/{id}/vehicles")
def counterparty_vehicles(id: str, db: Db, user: CurrentUser):
    get_record(db, "counterparties", id)
    vehicle_ids = select(VehicleRelation.vehicle_id).where(VehicleRelation.counterparty_id == id)
    vehicles = db.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids))).all()
    rows = vehicle_rows(db, vehicles)
    return sorted(rows, key=lambda row: (row["short_name_missing"], (row["short_name"] or row["plate"]).casefold()))


@router.get("/vehicles/{id}/ownership")
def vehicle_ownership(id: str, db: Db, user: CurrentUser):
    vehicle = get_record(db, "vehicles", id)
    relations = db.scalars(
        select(VehicleRelation).where(VehicleRelation.vehicle_id == vehicle.id).order_by(
            VehicleRelation.created_at.desc(), VehicleRelation.id
        )
    ).all()
    party_ids = {relation.counterparty_id for relation in relations}
    parties = {
        party.id: party.name
        for party in db.scalars(select(Counterparty).where(Counterparty.id.in_(party_ids))).all()
    }
    items = [{**serialize(row), "counterparty_name": parties.get(row.counterparty_id)} for row in relations]
    return {
        "current": next((item for item in items if item["role"] == "OPERATIONAL" and item["status"] == "CONFIRMED" and item["valid_to"] is None), None),
        "candidates": [item for item in items if item["role"] == "CANDIDATE" and item["decision"] == "PENDING"],
        "history": [item for item in items if item["decision"] != "PENDING" or item["role"] == "OPERATIONAL"],
    }


@router.get("/vehicles/{id}/documents")
def vehicle_documents(id: str, db: Db, user: CurrentUser):
    get_record(db, "vehicles", id)
    links = db.scalars(select(DocumentVehicleLink).where(DocumentVehicleLink.vehicle_id == id)).all()
    documents = {item.id: item for item in db.scalars(select(Document).where(Document.id.in_({link.document_id for link in links}))).all()}
    return [{**serialize(documents[link.document_id]), "link": serialize(link)} for link in links if link.document_id in documents]


@router.get("/vehicle-documents/queue")
def vehicle_document_queue(db: Db, user: CurrentUser):
    rows = db.scalars(select(Document).where(Document.document_scope == "VEHICLE", Document.link_state == "NEEDS_LINK").order_by(Document.name)).all()
    return [serialize(row) for row in rows]


class DocumentAssignment(BaseModel):
    vehicle_id: str
    document_kind: str = Field(min_length=2, max_length=40)
    page_reference: str | None = Field(default=None, max_length=100)


class ApprovalLetterRequest(BaseModel):
    contract_ids: list[str] = Field(min_length=1)
    vehicle_ids: list[str] = Field(min_length=1)
    letter_number: str = Field(min_length=1, max_length=100)
    letter_date: date
    sender_lines: list[str] = Field(min_length=1, max_length=12)
    recipient: str = Field(min_length=1, max_length=1000)
    body_text: str = Field(min_length=1, max_length=5000)
    monitoring_url: str = Field(min_length=1, max_length=500)
    monitoring_login: str = Field(min_length=1, max_length=200)
    monitoring_password: str = Field(min_length=1, max_length=200)


def render_approval_pdf(body, contracts, vehicle_rows_for_letter, signature_path):
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1240, 1754
    margin = 92
    regular = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 25)
    small = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 21)
    bold = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 25)
    header_font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 29)
    pages = []

    def new_page():
        page = Image.new("RGB", (width, height), "white")
        pages.append(page)
        return page, ImageDraw.Draw(page), margin

    def wrapped(draw, text, font, max_width):
        words = str(text).split()
        lines, line = [], ""
        for word in words:
            candidate = (line + " " + word).strip()
            if line and draw.textlength(candidate, font=font) > max_width:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        return lines or [""]

    def paragraph(draw, text, x, y, max_width, font=regular, spacing=8):
        for line in wrapped(draw, text, font, max_width):
            draw.text((x, y), line, fill="black", font=font)
            y += font.size + spacing
        return y

    page, draw, y = new_page()
    for index, line in enumerate(body.sender_lines):
        font = header_font if index < 4 else small
        draw.text((margin, y), line, fill="black", font=font)
        y += font.size + 5
    y += 20
    draw.text((margin, y), f"№ {body.letter_number} от {body.letter_date.strftime('%d.%m.%Y')}", fill="black", font=regular)
    y += 64
    recipient_lines = wrapped(draw, body.recipient, regular, 520)
    for line in recipient_lines:
        draw.text((width - margin - 520, y), line, fill="black", font=regular)
        y += regular.size + 7
    y += 55
    contract_numbers = ", ".join(contract.number for contract in contracts)
    body_text = body.body_text.replace("[выбранные номера]", contract_numbers)
    y = paragraph(draw, body_text, margin, y, width - 2 * margin)
    y += 24
    columns = [70, 205, 220, 230, 280]
    headers = ["№ п/п", "Тип ТС", "Тарировка\n(по справке)", "Марка", "ГосНомер"]
    row_height = 66
    x = margin
    for col_width, label in zip(columns, headers, strict=True):
        draw.rectangle((x, y, x + col_width, y + row_height), outline="black", width=2)
        for line_index, line in enumerate(label.split("\n")):
            draw.text((x + 7, y + 8 + line_index * 23), line, fill="black", font=small)
        x += col_width
    y += row_height
    for ordinal, item in enumerate(vehicle_rows_for_letter, 1):
        if y + row_height > height - 420:
            page, draw, y = new_page()
        cells = [str(ordinal), item["vehicle_type"], f'{item["calibration_m3"]} м³', item["model"] or "—", item["plate"]]
        x = margin
        for col_width, value in zip(columns, cells, strict=True):
            draw.rectangle((x, y, x + col_width, y + row_height), outline="black", width=2)
            draw.text((x + 7, y + 18), value, fill="black", font=small)
            x += col_width
        y += row_height
    y += 30
    y = paragraph(draw, "Прошу согласовать заявленный объем вывозимого снега согласно тарировкам.", margin, y, width - 2 * margin)
    y += 20
    y = paragraph(draw, "Доступ для мониторинга в системе ГЛОНАСС:", margin, y, width - 2 * margin, bold)
    for line in (body.monitoring_url, f"логин {body.monitoring_login}", f"пароль {body.monitoring_password}"):
        y = paragraph(draw, line, margin, y, width - 2 * margin, small, 4)
    y += 18
    y = paragraph(draw, "Приложения:", margin, y, width - 2 * margin, bold)
    for item in vehicle_rows_for_letter:
        y = paragraph(draw, item["attachment_name"], margin + 20, y, width - 2 * margin - 20, small, 4)
    signature = Image.open(signature_path).convert("RGBA")
    signature.thumbnail((440, 300))
    if y + signature.height + 80 > height - margin:
        page, draw, y = new_page()
    page.paste(signature, (margin + 470, y + 20), signature)
    draw.text((margin, y + 100), body.sender_lines[2] if len(body.sender_lines) > 2 else "Подпись", fill="black", font=regular)
    output = io.BytesIO()
    pages[0].save(output, format="PDF", resolution=150, save_all=True, append_images=pages[1:])
    return output.getvalue()


@router.post("/vehicle-approvals/letter")
def create_approval_letter(body: ApprovalLetterRequest, db: Db, user: AdminUser):
    contracts = db.scalars(select(Contract).where(Contract.id.in_(set(body.contract_ids)))).all()
    if len(contracts) != len(set(body.contract_ids)):
        raise HTTPException(422, "Один из выбранных договоров не найден")
    vehicles = db.scalars(select(Vehicle).where(Vehicle.id.in_(set(body.vehicle_ids)))).all()
    if len(vehicles) != len(set(body.vehicle_ids)):
        raise HTTPException(422, "Одна из выбранных машин не найдена")
    links = db.scalars(
        select(DocumentVehicleLink).where(
            DocumentVehicleLink.vehicle_id.in_(set(body.vehicle_ids)),
            DocumentVehicleLink.document_kind == "CALIBRATION",
        )
    ).all()
    documents = {
        document.id: document
        for document in db.scalars(
            select(Document).where(
                Document.id.in_({link.document_id for link in links}),
                Document.status == "CONFIRMED",
            )
        ).all()
    }
    calibration_by_vehicle = {
        link.vehicle_id: documents[link.document_id]
        for link in links
        if link.document_id in documents
    }
    missing = [vehicle.name for vehicle in vehicles if vehicle.id not in calibration_by_vehicle or vehicle.calibration_m3 is None]
    if missing:
        raise HTTPException(422, "Нет проверенной тарировки: " + ", ".join(missing))
    signature_path = Path(__file__).resolve().parents[2] / ".local" / "sources" / "approval-letter-template" / "word" / "media" / "image1.png"
    if not signature_path.is_file():
        raise HTTPException(409, "Локальный эталон факсимиле не подготовлен")
    rows_for_letter, attachments = [], {}
    for vehicle in sorted(vehicles, key=lambda item: item.name.casefold()):
        document = calibration_by_vehicle[vehicle.id]
        content = questionnaire_document_bytes(document)
        if content is None:
            raise HTTPException(422, f"Файл тарировки недоступен: {vehicle.name}")
        attachment_name = safe_document_name(vehicle, "CALIBRATION", document.name, vehicle.calibration_m3)
        attachments[attachment_name] = content
        rows_for_letter.append(
            {
                "vehicle_type": vehicle.vehicle_type,
                "calibration_m3": vehicle.calibration_m3,
                "model": vehicle.model,
                "plate": vehicle.plate,
                "attachment_name": attachment_name,
            }
        )
    pdf = render_approval_pdf(body, contracts, rows_for_letter, signature_path)
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Письмо на согласование техники.pdf", pdf)
        for name, content in attachments.items():
            archive.writestr(name, content)
    export_id = hashlib.sha256((body.letter_number + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()
    scope = current_organization()
    repository = Path(__file__).resolve().parents[2]
    document_root = (
        scope.data_dir / "contractor-documents"
        if scope
        else repository / ".local" / "contractor-documents"
    )
    document_root.mkdir(parents=True, exist_ok=True)
    pdf_hash = hashlib.sha256(pdf).hexdigest()
    pdf_path = document_root / (pdf_hash + ".pdf")
    if not pdf_path.exists():
        pdf_path.write_bytes(pdf)
    request_document = db.get(Document, pdf_hash)
    if request_document is None:
        request_document = Document(
            id=pdf_hash,
            name=f"Письмо {body.letter_number} от {body.letter_date.strftime('%d.%m.%Y')}.pdf",
            sha256=pdf_hash,
            location=str(pdf_path.resolve()),
            status="CONFIRMED",
            source="Сформировано модулем подрядчика",
            details={"letter_number": body.letter_number, "letter_date": body.letter_date.isoformat()},
            document_scope="CONTRACT",
            document_kind="VEHICLE_APPROVAL_REQUEST",
            link_state="NOT_REQUIRED",
        )
        db.add(request_document)
    approval_request = VehicleApprovalRequest(
        id=export_id,
        name=f"Письмо {body.letter_number} от {body.letter_date.strftime('%d.%m.%Y')}",
        status="REVIEW",
        source="Решение оператора",
        details={"vehicle_count": len(vehicles)},
        letter_number=body.letter_number,
        letter_date=body.letter_date,
        contract_ids=body.contract_ids,
        request_document_id=request_document.id,
    )
    db.add(approval_request)
    for vehicle in vehicles:
        calibration = calibration_by_vehicle[vehicle.id]
        db.add(
            VehicleApprovalDecision(
                id=hashlib.sha256((export_id + ":" + vehicle.id).encode()).hexdigest(),
                name=vehicle.name,
                status="REVIEW",
                source="Письмо " + body.letter_number,
                details={},
                request_id=export_id,
                vehicle_id=vehicle.id,
                calibration_document_id=calibration.id,
                decision="PENDING",
            )
        )
    db.add(
        History(
            entity="vehicle-approval-letters",
            record_id=export_id,
            actor_id=user.id,
            before=None,
            after={
                "letter_number": body.letter_number,
                "letter_date": body.letter_date.isoformat(),
                "contract_ids": body.contract_ids,
                "vehicle_ids": body.vehicle_ids,
                "attachments": list(attachments),
            },
        )
    )
    commit(db)
    return Response(
        package.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=vehicle-approval-letter.zip"},
    )


@router.get("/vehicle-approvals/requests")
def approval_requests(db: Db, user: CurrentUser):
    requests = db.scalars(
        select(VehicleApprovalRequest).order_by(VehicleApprovalRequest.created_at.desc())
    ).all()
    result = []
    for request in requests:
        decisions = db.scalars(
            select(VehicleApprovalDecision).where(VehicleApprovalDecision.request_id == request.id)
        ).all()
        vehicles = {
            vehicle.id: vehicle
            for vehicle in db.scalars(
                select(Vehicle).where(Vehicle.id.in_({decision.vehicle_id for decision in decisions}))
            ).all()
        }
        result.append(
            {
                **serialize(request),
                "decisions": [
                    {
                        **serialize(decision),
                        "vehicle_name": vehicles.get(decision.vehicle_id).name if vehicles.get(decision.vehicle_id) else decision.vehicle_id,
                        "plate": vehicles.get(decision.vehicle_id).plate if vehicles.get(decision.vehicle_id) else None,
                    }
                    for decision in decisions
                ],
            }
        )
    return result


@router.post("/vehicle-approvals/requests/{id}/response")
async def apply_approval_response(id: str, request: Request, db: Db, user: AdminUser):
    import json

    from starlette.datastructures import UploadFile

    approval = db.scalar(
        select(VehicleApprovalRequest).where(VehicleApprovalRequest.id == id).with_for_update()
    )
    if approval is None:
        raise HTTPException(404, "Запрос на согласование не найден")
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise HTTPException(422, "Приложите ответ заказчика")
    try:
        decisions = json.loads(str(form.get("decisions") or "{}"))
    except json.JSONDecodeError:
        raise HTTPException(422, "Не удалось прочитать решения по технике") from None
    if not isinstance(decisions, dict) or any(value not in {"APPROVED", "REJECTED"} for value in decisions.values()):
        raise HTTPException(422, "Для каждой разобранной машины выберите «Согласовано» или «Отказано»")
    content = await upload.read(52_428_801)
    if not content or len(content) > 52_428_800:
        raise HTTPException(413, "Файл ответа пуст или превышает 50 МБ")
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise HTTPException(422, "Ответ заказчика принимается в PDF, JPG или PNG")
    scope = current_organization()
    repository = Path(__file__).resolve().parents[2]
    document_root = (
        scope.data_dir / "contractor-documents"
        if scope
        else repository / ".local" / "contractor-documents"
    )
    document_root.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(content).hexdigest()
    target = document_root / (content_hash + suffix)
    if not target.exists():
        target.write_bytes(content)
    document = db.get(Document, content_hash)
    if document is None:
        document = Document(
            id=content_hash,
            name=f"Ответ заказчика на письмо {approval.letter_number}{suffix}",
            sha256=content_hash,
            location=str(target.resolve()),
            status="CONFIRMED",
            source="Ответ заказчика",
            details={"original_name": upload.filename},
            document_scope="CONTRACT",
            document_kind="VEHICLE_APPROVAL_RESPONSE",
            link_state="NOT_REQUIRED",
        )
        db.add(document)
    rows = db.scalars(
        select(VehicleApprovalDecision).where(VehicleApprovalDecision.request_id == id)
    ).all()
    for row in rows:
        decision = decisions.get(row.vehicle_id)
        if not decision or row.decision != "PENDING":
            continue
        row.decision = decision
        row.status = "CONFIRMED" if decision == "APPROVED" else "INACTIVE"
        row.decided_at = datetime.now(timezone.utc)
        row.decided_by = user.id
        row.version += 1
        if decision == "APPROVED":
            existing = db.scalar(
                select(DocumentVehicleLink).where(
                    DocumentVehicleLink.document_id == document.id,
                    DocumentVehicleLink.vehicle_id == row.vehicle_id,
                    DocumentVehicleLink.document_kind == "CUSTOMER_APPROVAL",
                )
            )
            if existing is None:
                db.add(
                    DocumentVehicleLink(
                        name=document.name,
                        status="CONFIRMED",
                        source="Решение заказчика",
                        details={"request_id": id},
                        document_id=document.id,
                        vehicle_id=row.vehicle_id,
                        document_kind="CUSTOMER_APPROVAL",
                        linked_by=user.id,
                    )
                )
    approval.response_document_id = document.id
    if all(row.decision != "PENDING" for row in rows):
        approval.status = "CONFIRMED"
        approval.closed_at = datetime.now(timezone.utc)
    approval.version += 1
    commit(db)
    return next(item for item in approval_requests(db, user) if item["id"] == id)


@router.post("/documents/{id}/assign-vehicle", status_code=201)
def assign_vehicle_document(id: str, body: DocumentAssignment, db: Db, user: AdminUser):
    document = get_record(db, "documents", id)
    vehicle = get_record(db, "vehicles", body.vehicle_id)
    existing = db.scalar(select(DocumentVehicleLink).where(DocumentVehicleLink.document_id == id, DocumentVehicleLink.vehicle_id == vehicle.id, DocumentVehicleLink.document_kind == body.document_kind))
    if existing:
        return serialize(existing)
    link = DocumentVehicleLink(name=document.name, status="CONFIRMED", source="Решение оператора", details={}, document_id=id, vehicle_id=vehicle.id, document_kind=body.document_kind, page_reference=body.page_reference, linked_by=user.id)
    db.add(link)
    document.document_scope, document.document_kind, document.link_state = "VEHICLE", body.document_kind, "LINKED"
    history(db, "document-vehicle-links", link, user, None)
    commit(db)
    return serialize(link)


@router.post("/documents/{id}/confirm")
def confirm_document(id: str, db: Db, user: AdminUser):
    from app.config import settings

    document = db.scalar(select(Document).where(Document.id == id).with_for_update())
    if document is None:
        raise HTTPException(404, "Документ не найден")
    before = serialize(document)
    document.status = "CONFIRMED"
    document.version += 1
    document.details = {**(document.details or {}), "verification": "Подтверждено оператором"}
    history(db, "documents", document, user, before)
    links = db.scalars(select(DocumentVehicleLink).where(DocumentVehicleLink.document_id == id)).all()
    if document.document_kind == "CALIBRATION":
        today = datetime.now(ZoneInfo(settings().app_timezone)).date()
        for link in links:
            vehicle = db.get(Vehicle, link.vehicle_id)
            if vehicle and vehicle.calibration_on is None:
                vehicle_before = serialize(vehicle)
                vehicle.calibration_on = today
                vehicle.version += 1
                history(db, "vehicles", vehicle, user, vehicle_before)
    commit(db)
    return serialize(document)


@router.get("/vehicle-types/{id}/capabilities")
def vehicle_type_capabilities(id: str, db: Db, user: CurrentUser):
    get_record(db, "vehicle-types", id)
    selected = set(db.scalars(select(VehicleTypeWorkLink.work_category_id).where(VehicleTypeWorkLink.vehicle_type_id == id)))
    return [{**serialize(item), "selected": item.id in selected} for item in db.scalars(select(WorkCategory).order_by(WorkCategory.name)).all()]


class Capabilities(BaseModel):
    work_category_ids: list[str]


@router.put("/vehicle-types/{id}/capabilities")
def update_vehicle_type_capabilities(id: str, body: Capabilities, db: Db, user: AdminUser):
    vehicle_type = get_record(db, "vehicle-types", id)
    categories = db.scalars(select(WorkCategory).where(WorkCategory.id.in_(set(body.work_category_ids)))).all()
    if len(categories) != len(set(body.work_category_ids)):
        raise HTTPException(422, "Неизвестная рабочая категория")
    current = {row.work_category_id: row for row in db.scalars(select(VehicleTypeWorkLink).where(VehicleTypeWorkLink.vehicle_type_id == id)).all()}
    for category_id, row in current.items():
        if category_id not in body.work_category_ids:
            db.delete(row)
    for category in categories:
        if category.id not in current:
            db.add(VehicleTypeWorkLink(name=vehicle_type.name + " · " + category.name, status="CONFIRMED", source="Решение оператора", details={}, vehicle_type_id=id, work_category_id=category.id))
    commit(db)
    return {"work_category_ids": body.work_category_ids}


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
    scope = current_organization()
    document_root = (
        scope.data_dir / "contractor-documents"
        if scope
        else repository / ".local" / "contractor-documents"
    ).resolve()
    stored_document = len(parts) == 1 and document_root in path.parents
    local_source = (
        len(parts) == 1
        and (repository / ".local/sources").resolve() in path.parents
        and path.suffix.lower() == ".xlsx"
    )
    if not stored_document and not local_source and (
        root.resolve() not in path.parents or len(parts) not in {2, 3}
    ):
        raise HTTPException(
            422, "Для этого источника доступна исходная ссылка, локальный просмотр отсутствует"
        )
    try:
        if local_source or stored_document:
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


class OwnerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=1000)


def validate_values(db, model, values, record=None):
    columns = {c.name: c for c in model.__table__.columns}
    if set(values) - set(columns) or set(values) & {"created_at", "version", "approved_at", "decision", "decision_reason", "decided_at", "decided_by"}:
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
def approve_owner(id: str, body: OwnerDecision, db: Db, user: AdminUser):
    row = get_record(db, "vehicle-relations", id)
    # Lock the shared vehicle to serialize competing approvals, including when
    # there is no previous owner relation to lock.
    from app.contractor_models import Vehicle

    db.scalar(select(Vehicle).where(Vehicle.id == row.vehicle_id).with_for_update())
    db.refresh(row)
    if row.status == "CONFIRMED" and row.decision == "APPROVED":
        return serialize(row)
    if row.role != "CANDIDATE" or row.decision != "PENDING":
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
    decided = datetime.now(timezone.utc)
    row.role, row.status, row.valid_from = "OPERATIONAL", "CONFIRMED", today
    row.approved_at = row.decided_at = decided
    supplied_reason = body.reason.strip() if body.reason else ""
    row.decision = "APPROVED"
    row.decision_reason = supplied_reason or (
        "Подтверждено на основании источника: " + row.source
        if row.source
        else "Подтверждено решением оператора"
    )
    row.decided_by = user.id
    row.version += 1
    history(db, "vehicle-relations", row, user, before)
    commit(db)
    return serialize(row)


@router.post("/vehicle-relations/{id}/reject")
def reject_owner(id: str, body: OwnerDecision, db: Db, user: AdminUser):
    row = db.scalar(select(VehicleRelation).where(VehicleRelation.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, "Кандидат не найден")
    if row.role != "CANDIDATE" or row.decision != "PENDING":
        raise HTTPException(422, "Решение по этому кандидату уже принято")
    if not body.reason or len(body.reason.strip()) < 3:
        raise HTTPException(422, "При отклонении укажите причину")
    before = serialize(row)
    row.status, row.decision = "INACTIVE", "REJECTED"
    row.decision_reason, row.decided_by = body.reason.strip(), user.id
    row.decided_at = datetime.now(timezone.utc)
    row.version += 1
    history(db, "vehicle-relations", row, user, before)
    commit(db)
    return serialize(row)
