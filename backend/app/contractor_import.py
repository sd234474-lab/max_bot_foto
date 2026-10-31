"""Initial local import. Originals are opened read-only and never extracted.

No automatic owner approval, no overwrite of existing cards, no photo mappings
inferred from names. Import diagnostics contain reasons, not bank details.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.contractor import plate_valid, serialize
from app.contractor_models import (
    Area,
    Cell,
    Contract,
    ContractLine,
    ContractObject,
    ContractRevision,
    ContractWork,
    Counterparty,
    Document,
    History,
    ImportEvent,
    Maf,
    Site,
    Territory,
    Unit,
    Vehicle,
    VehicleRelation,
    WialonLink,
)
from app.db import engine


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def event(db, key, outcome, reason, entity=None, record_id=None):
    key = digest(key)
    if not db.scalar(select(ImportEvent.id).where(ImportEvent.source_key == key)):
        db.add(
            ImportEvent(source_key=key, outcome=outcome, reason=reason, entity=entity, record_id=record_id)
        )


def insert(db, model, id, source, /, **values):
    row = db.get(model, id)
    if row:
        # Existing/approved/admin-edited data always wins over an initial source.
        event(
            db,
            source + ":linked:" + model.__tablename__ + ":" + id,
            "LINKED",
            "Существующая запись сохранена",
            model.__tablename__,
            id,
        )
        return row
    row = model(id=id, source=source, **values)
    db.add(row)
    db.flush()
    db.add(
        History(
            entity=next(
                (
                    k
                    for k, (m, _) in __import__(
                        "app.contractor_models", fromlist=["CATALOGUES"]
                    ).CATALOGUES.items()
                    if m is model
                ),
                model.__tablename__,
            ),
            record_id=id,
            actor_id=None,
            before=None,
            after=serialize(row),
        )
    )
    event(
        db,
        source + ":accepted:" + model.__tablename__ + ":" + id,
        "ACCEPTED",
        "Первичная запись; статус источника сохранён",
        model.__tablename__,
        id,
    )
    return row


def source_rows(bundle, key):
    values = bundle[key]["values"]
    header = values[0]
    return [(r + [None] * len(header))[: len(header)] for r in values[1:] if r and r[0]], header


def import_references(db, directory):
    bundle = json.loads((directory / "sheets.json").read_text(encoding="utf-8"))
    for suffix in ("58", "60"):
        number = "ЭА.2026.000" + suffix
        insert(
            db,
            Contract,
            number,
            "CODEX-MAX-001",
            name=number,
            number=number,
            status="REVIEW",
            details={"limitation": "Полные тексты контрактов и другие приложения не предоставлены"},
        )
        insert(
            db,
            ContractRevision,
            number + ":1",
            "CODEX-MAX-001",
            name=number + " · исходная редакция",
            contract_id=number,
            revision=1,
            status="REVIEW",
            details={"limitation": "Даты и юридические снимки требуют подтверждения; не выдуманы"},
        )
    key = "v3:Территории_АРМ"
    rows, header = source_rows(bundle, key)
    for r in rows:
        insert(db, Area, r[0], key, name=r[1], status="REVIEW", details=dict(zip(header, r, strict=False)))
    key = "v3:Связи_договор_АРМ"
    rows, header = source_rows(bundle, key)
    for r in rows:
        insert(
            db,
            Territory,
            r[0],
            key,
            name=r[1],
            area_id=r[3],
            contract_id=r[2],
            status="REVIEW",
            details=dict(zip(header, r, strict=False)),
        )
    key = "v3:Клетки_ARM"
    rows, header = source_rows(bundle, key)
    for r in rows:
        territory = db.get(Territory, r[2])
        if not territory or territory.area_id != r[19]:
            event(db, key + r[0], "REVIEW", "Несоответствие area_id цепочке territory_id", "cells", r[0])
            continue
        insert(
            db,
            Cell,
            r[0],
            key,
            name=r[1],
            territory_id=r[2],
            status="REVIEW",
            details=dict(zip(header, r, strict=False)),
        )
    key = "v3:Клетка_Wialon"
    rows, header = source_rows(bundle, key)
    for r in rows:
        if db.get(Cell, r[0]) and r[2] and r[3]:
            insert(
                db,
                WialonLink,
                r[0] + ":wialon",
                key,
                name=r[4],
                cell_id=r[0],
                resource_id=str(r[2]),
                zone_id=str(r[3]),
                status="REVIEW",
                details=dict(zip(header, r, strict=False)),
            )
    key = "works:Все виды и подпроцессы"
    rows, header = source_rows(bundle, key)
    # Codes recur across contracts; contract+code is the stable identity.
    for r in rows:
        if r[6]:
            insert(db, Unit, "unit:" + digest(r[6])[:16], key, name=r[6], status="REVIEW")
    pending = list(rows)
    while pending:
        progressed = False
        for r in pending[:]:
            id = r[0] + ":" + r[1]
            parent = r[0] + ":" + r[4] if r[4] else None
            if parent and not db.get(ContractWork, parent):
                continue
            insert(
                db,
                ContractWork,
                id,
                key,
                name=r[2],
                code=id,
                parent_id=parent,
                unit_id="unit:" + digest(r[6])[:16] if r[6] else None,
                status="REVIEW",
                details=dict(zip(header, r, strict=False)),
            )
            pending.remove(r)
            progressed = True
        if not progressed:
            for r in pending:
                event(db, key + str(r[:2]), "REVIEW", "Родитель работы не найден или цикл", "works", r[1])
            break
    for key, model, id_col, name_col, fk_col, fk_field in [
        ("maf:ДОГОВОРНЫЕ_ОБЪЕКТЫ", ContractObject, 0, 3, 1, "contract_id"),
        ("maf:ПЛОЩАДКИ", Site, 1, 4, 0, "object_id"),
        ("maf:МАФ", Maf, 0, 6, 1, "site_id"),
    ]:
        rows, header = source_rows(bundle, key)
        for r in rows:
            insert(
                db,
                model,
                r[id_col],
                key,
                name=r[name_col],
                status="REVIEW",
                details=dict(zip(header, r, strict=False)),
                **{fk_field: r[fk_col]},
            )
    rows, header = source_rows(bundle, "shashko:Техника")
    for r in rows:
        if r[0] in {"0406УХ86", "0766УС86"}:
            insert(
                db,
                Vehicle,
                r[0],
                "shashko:Техника",
                name=r[1],
                plate=r[2],
                vehicle_type=r[3],
                model=r[5],
                status="REVIEW",
                details={
                    "candidate_operator": "Шашко",
                    "legal_owner": None,
                    "source_row": dict(zip(header, r, strict=False)),
                },
            )


def import_contracts(db, directory):
    bundle = json.loads((directory / "contracts.json").read_text(encoding="utf-8"))
    for key, source in bundle.items():
        suffix, kind = key.split("-")
        revision = "ЭА.2026.000" + suffix + ":1"
        original = directory / (source["id"] + ".xlsx")
        if original.exists():
            insert(
                db,
                Document,
                source["id"] + ":original",
                source["url"],
                name=key + " · оригинал XLSX",
                sha256=digest(original.read_bytes()),
                location=str(original.resolve()),
                revision_id=revision,
                status="REVIEW",
                details={"fingerprint_kind": "original_bytes", "source_url": source["url"]},
            )
        fingerprint = digest(source["content"])
        doc = insert(
            db,
            Document,
            source["id"],
            source["url"],
            name=key + " · исходное приложение",
            sha256=fingerprint,
            location=source["url"],
            revision_id=revision,
            status="REVIEW",
            details={"fingerprint_kind": "readable_text_not_original_bytes", "original_format": "xlsx"},
        )
        rows = list(csv.reader(io.StringIO(source["content"])))
        for index, row in enumerate(rows):
            insert(
                db,
                ContractLine,
                source["id"] + ":" + str(index),
                source["url"],
                name=f"{key} · строка {index + 1}",
                revision_id=revision,
                document_id=doc.id,
                kind=kind,
                source_row=row,
                status="REVIEW",
                details={
                    "source_line": index + 1,
                    "interpretation": "Точное текстовое представление, без пересчёта; семантические связи требуют сверки",
                },
            )
        import_contract_values(db, key, rows, doc, source["url"])


def territory_key(name):
    return re.sub(r"[\s.]", "", name).casefold()


def import_contract_values(db, key, rows, doc, source):
    suffix, kind = key.split("-")
    contract_id = "ЭА.2026.000" + suffix
    revision = contract_id + ":1"
    if kind == "OP":
        header = next(r for r in rows if r[0] == "5")
        territories = {
            territory_key(t.name): t.id
            for t in db.scalars(select(Territory).where(Territory.contract_id == contract_id))
        }
        for row in rows:
            # Only numbered physical indicators. Formula/contract totals remain
            # separate rows and never become territorial financial limits.
            if len(row) < 4 or not row[1].isdigit() or not row[2] or row[2].isdigit():
                continue
            for col in range(3, len(row)):
                if not row[col]:
                    continue
                territory = territories.get(territory_key(header[col])) if col < len(header) - 1 else None
                if col < len(header) - 1 and not territory:
                    event(
                        db,
                        source + f":op:{row[0]}:{col}",
                        "REVIEW",
                        "Заголовок ОП не сопоставлен с договорной территорией",
                    )
                    continue
                insert(
                    db,
                    ContractLine,
                    doc.id + f":op:{row[0]}:{col}",
                    source,
                    name=row[2] + " · " + (header[col] if territory else "Всего по контракту"),
                    kind="OP",
                    revision_id=revision,
                    document_id=doc.id,
                    territory_id=territory,
                    indicator=row[2],
                    quantity_text=row[col],
                    source_row=row,
                    status="REVIEW",
                    details={
                        "source_column": col,
                        "source_row": row[0],
                        "scope": "territory" if territory else "contract_total",
                    },
                )
    else:
        # Explicit correspondence for the supplied RC wording. Unmatched words
        # stay unlinked and are never mapped onto photo work_types.
        codes = [
            ("Распределение противогололедных", "WIN-ANTIICE"),
            ("Уборка снега вручную", "WIN-MAN-SNOW"),
            ("Механизированная уборка снега", "WIN-MECH-SNOW"),
            ("Погрузка и вывоз", "WIN-HAUL"),
            ("Очистка урн", "BIN-CLEAN"),
            ("Уборка газонов", "LAWN-LITTER"),
            ("Очистка дождеприемных", "DRAIN-CLEAN"),
            ("Резка арматуры", "REBAR-CUT"),
            ("Содержание детских", "PLAYGROUND"),
            ("Уборка от случайного", "SUM-RANDOM"),
            ("Подметание территории", "SUM-SWEEP"),
            ("Мойка брусчатки", "SUM-PAVER-WASH"),
            ("Промывка урн", "BIN-WASH"),
            ("Уборка зеленых зон, газонов под грабли", "GREEN-RAKE"),
            ("Уборка зеленых зон, газонов  (", "GREEN-CLEAN"),
            ("Выкашивание травы", "GREEN-MOW"),
        ]
        stage, start, end = None, None, None
        for row in rows:
            if len(row) < 8:
                continue
            if "Расчет цены на 2026" in row[1]:
                stage, start, end = "2026 · зима", date(2026, 10, 16), date(2026, 11, 30)
            elif "Расчет цены на 2027" in row[1]:
                stage, start, end = "2027 · зима", date(2026, 12, 1), date(2027, 4, 20)
            elif row[1].startswith("Летнее содержание"):
                stage, start, end = "2027 · лето", date(2027, 4, 21), date(2027, 10, 14)
            elif row[1] == "Круглогодично":
                # RC provides no explicit dates for these sections; do not
                # borrow seasonal dates or invent annual applicability dates.
                stage, start, end = (
                    ("2026" if stage and stage.startswith("2026") else "2027") + " · круглогодично",
                    None,
                    None,
                )
            if not row[1].isdigit() or not row[2] or row[2].isdigit():
                continue
            code = next((code for prefix, code in codes if row[2].startswith(prefix)), None)
            work = contract_id + ":" + code if code else None
            if work and not db.get(ContractWork, work):
                work = None
            insert(
                db,
                ContractLine,
                doc.id + ":rc:" + row[0],
                source,
                name=row[2].strip(),
                kind="RC",
                revision_id=revision,
                document_id=doc.id,
                work_id=work,
                stage=stage,
                period_from=start,
                period_to=end,
                quantity_text=row[3],
                rate_text=row[6],
                total_text=row[7],
                source_row=row,
                status="REVIEW",
                details={
                    "cycles_or_days": row[4],
                    "frequency": row[5],
                    "scope": "contract",
                    "source_row": row[0],
                },
            )


def compact(value):
    return re.sub(r"[\s_.-]", "", str(value or "")).upper()


def import_questionnaire(db, path):
    fingerprint = digest(path.read_bytes())
    source = path.name + ":" + fingerprint
    with zipfile.ZipFile(path) as archive:
        books = [n for n in archive.namelist() if n.lower().endswith(".xlsx")]
        if len(books) != 1:
            event(db, source, "REJECTED", "Ожидалась ровно одна анкета XLSX")
            return
        workbook = load_workbook(io.BytesIO(archive.read(books[0])), data_only=True)
        passport = workbook["Паспорт контрагента"]
        if passport["B3"].value != "ARM_IMPORT_V2":
            event(db, source, "REJECTED", "Неподдерживаемая версия шаблона")
            return
        inn = str(passport["B13"].value or "").strip()
        if not re.fullmatch(r"\d{10}|\d{12}", inn):
            event(db, source, "REJECTED", "Неоднозначный ИНН контрагента")
            return
        name = " ".join(str(passport[f"B{i}"].value or "").strip() for i in (8, 9, 10)).strip()
        details = {}
        for r in range(4, 31):
            label, value = passport[f"A{r}"].value, passport[f"B{r}"].value
            if r == 24 and value is not None and not re.fullmatch(r"\d{9}", str(value).strip()):
                event(
                    db,
                    source + ":bik",
                    "REVIEW",
                    "БИК не представлен точной строкой из девяти цифр; требуется сверка карточки ИП",
                    "counterparties",
                    "inn:" + inn,
                )
            if r in (25, 26) and isinstance(value, (int, float)):
                details[str(label)] = None
                event(
                    db,
                    source + f":bank:{r}",
                    "REVIEW",
                    "Счёт сохранён числом; требуется точная строка из карточки ИП",
                    "counterparties",
                    "inn:" + inn,
                )
            elif value is not None:
                details[str(label)] = value.isoformat() if hasattr(value, "isoformat") else str(value).strip()
        party = insert(
            db,
            Counterparty,
            "inn:" + inn,
            source,
            name=name,
            inn=inn,
            legal_form=str(passport["B5"].value or ""),
            status="REVIEW",
            details=details,
        )
        # Attach originals to the counterparty, without asserting that the scan
        # proves a particular vehicle's identity or legal ownership.
        for info in archive.infolist():
            if info.is_dir():
                continue
            content_hash = digest(archive.read(info))
            if not db.scalar(select(Document.id).where(Document.sha256 == content_hash)):
                insert(
                    db,
                    Document,
                    content_hash,
                    source,
                    name=info.filename,
                    sha256=content_hash,
                    location=str(path.resolve()) + "!" + info.filename,
                    counterparty_id=party.id,
                    status="REVIEW",
                    details={"verification": "Визуальная достоверность и привязка к машине не подтверждены"},
                )
            if info.filename.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(archive.read(info))) as nested:
                    for inner in nested.infolist():
                        if inner.is_dir():
                            continue
                        inner_hash = digest(nested.read(inner))
                        if not db.scalar(select(Document.id).where(Document.sha256 == inner_hash)):
                            insert(
                                db,
                                Document,
                                inner_hash,
                                source,
                                name=info.filename + " / " + inner.filename,
                                sha256=inner_hash,
                                location=str(path.resolve()) + "!" + info.filename + "!" + inner.filename,
                                counterparty_id=party.id,
                                status="REVIEW",
                                details={"verification": "Вложенный скан; требуется визуальная сверка"},
                            )
        sheet = workbook["Техника"]
        seen = set()
        for r in range(5, 55):
            vals = {c: sheet[f"{c}{r}"].value for c in "BCDEFGHIJKLMNOPQRSTUV"}
            if not any(vals[c] for c in "BCHIK"):
                continue
            key = source + f":vehicle:{r}"
            plate = str(vals["I"] or vals["E"] or "").strip()
            if not plate_valid(plate):
                event(
                    db,
                    key,
                    "REVIEW",
                    "Госномер неоднозначен или содержит латиницу; строка не принята",
                    "questionnaire-row",
                    str(r),
                )
                continue
            # Conflicts between the stable existing ID and entered new plate
            # are not repaired by guessing a visually similar registration.
            if vals["V"] and str(vals["V"]).strip() != plate:
                event(db, key, "REVIEW", "Конфликт госномера с постоянным ID", "questionnaire-row", str(r))
                continue
            if plate in seen:
                event(db, key, "REVIEW", "Повтор номера внутри анкеты", "questionnaire-row", str(r))
                continue
            seen.add(plate)
            vehicle_type = str(vals["H"] or vals["D"] or "").strip()
            model = str(vals["K"] or vals["G"] or "").strip()
            old = db.scalar(select(Vehicle).where(Vehicle.plate == plate))
            if old and (
                (old.model and model and compact(old.model) != compact(model))
                or old.vehicle_type != vehicle_type
            ):
                event(
                    db,
                    key,
                    "REVIEW",
                    "Номер существует, но тип/модель расходятся; карточка сохранена",
                    "vehicles",
                    old.id,
                )
                continue
            # New rows may have no working name. A temporary label is explicit.
            working_name = str(
                vals["J"] or (vals["F"] if vals["C"] == "ИЗ СПРАВОЧНИКА" else "") or ""
            ).strip()
            if not vehicle_type:
                event(db, key, "REVIEW", "Не указан тип техники", "questionnaire-row", str(r))
                continue
            vehicle = old or insert(
                db,
                Vehicle,
                plate,
                source,
                name=working_name or plate + " · имя не задано",
                plate=plate,
                vehicle_type=vehicle_type,
                model=model or None,
                status="REVIEW" if working_name else "INCOMPLETE",
                details={
                    "questionnaire_row": r,
                    "raw_values": {k: str(v) if v is not None else None for k, v in vals.items()},
                    "declared_calibration_m3": str(vals["L"]) if vals["L"] is not None else None,
                    "calibration_verification": "Требует сверки справки",
                    "type_verification": "Требует сверки СТС",
                },
            )
            insert(
                db,
                VehicleRelation,
                digest(party.id + ":" + vehicle.id),
                source,
                name=party.name + " ↔ " + vehicle.plate,
                vehicle_id=vehicle.id,
                counterparty_id=party.id,
                role="CANDIDATE",
                status="REVIEW",
                details={"approval_required": True, "questionnaire_row": r},
            )
            event(
                db,
                key,
                "LINKED" if old else "ACCEPTED",
                "Кандидат операционного владельца; юридический собственник не установлен",
                "vehicles",
                vehicle.id,
            )
            if not working_name:
                event(
                    db,
                    key + ":name",
                    "REVIEW",
                    "Временная подпись; рабочее имя требует утверждения",
                    "vehicles",
                    vehicle.id,
                )
        workbook.close()


def assert_local_test_url(url):
    target = make_url(url)
    if (
        target.host not in {"127.0.0.1", "localhost"}
        or target.database != "contractor_max_test"
        or target.port != 55432
    ):
        raise RuntimeError("Импорт разрешён только в 127.0.0.1:55432/contractor_max_test")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--questionnaires", type=Path, required=True)
    args = parser.parse_args()
    from app.config import settings

    assert_local_test_url(settings().database_url)
    with Session(engine()) as db:
        # One transaction/advisory lock prevents concurrent initial import runs.
        from sqlalchemy import text

        db.execute(text("SELECT pg_advisory_xact_lock(7001001)"))
        import_references(db, args.sources)
        import_contracts(db, args.sources)
        for path in sorted(args.questionnaires.glob("*.zip")):
            import_questionnaire(db, path)
        db.commit()
        print(json.dumps(dict(Counter(db.scalars(select(ImportEvent.outcome)))), ensure_ascii=False))


if __name__ == "__main__":
    main()
