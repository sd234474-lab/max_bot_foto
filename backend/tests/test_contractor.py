"""NSI behavior tests use synthetic data only; no customer artifacts in Git."""

import hashlib
import io
import zipfile
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import current_user
from app.contractor import plate_valid, render_approval_pdf, router
from app.contractor_import import (
    assert_local_test_url,
    import_questionnaire,
    match_declared_attachment,
)
from app.contractor_models import (
    CATALOGUES,
    Contract,
    Counterparty,
    Document,
    DocumentVehicleLink,
    History,
    ImportEvent,
    QuestionnaireImport,
    Vehicle,
    VehicleApprovalDecision,
    VehicleApprovalRequest,
    VehicleRelation,
    VehicleType,
    WorkCategory,
)
from app.db import Base, get_db


def test_questionnaire_document_match_uses_declared_filename_not_plate_search():
    attachments = [
        {"id": "one", "original_name": "Справка ХОВО М 903 КУ186.pdf"},
        {"id": "two", "original_name": "Справка  М399ЕХ186.pdf"},
        {"id": "three", "original_name": "СТС HOWO Е 679 РН 186.pdf"},
    ]

    assert match_declared_attachment("Справка Хово М903КУ186", attachments)["id"] == "one"
    assert match_declared_attachment("Справка Хово М399ЕХ186", attachments)["id"] == "two"
    assert match_declared_attachment("СТС HOWO Е310УА186", attachments) is None


@pytest.fixture
def nsi():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE work_types (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE districts (id INTEGER PRIMARY KEY)"))
    tables = {model.__tablename__ for model, _ in CATALOGUES.values()} | {
        History.__tablename__,
        ImportEvent.__tablename__,
        QuestionnaireImport.__tablename__,
        VehicleApprovalRequest.__tablename__,
        VehicleApprovalDecision.__tablename__,
    }
    for table in Base.metadata.sorted_tables:
        if table.name in tables:
            table.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


@pytest.fixture
def nsi_client(nsi):
    app = FastAPI()
    app.include_router(router)
    actor = SimpleNamespace(id=1, name="Тестовый оператор", role="ADMIN_OPERATOR")
    app.dependency_overrides[get_db] = lambda: nsi
    app.dependency_overrides[current_user] = lambda: actor
    with TestClient(app) as client:
        yield client, actor


def create(client, kind, **values):
    return client.post("/api/contractor/" + kind, json={"values": values})


def test_catalogues_require_login():
    app = FastAPI()
    app.include_router(router)
    # Authorization is an explicit dependency, not just a hidden UI button.
    from fastapi import HTTPException

    def denied():
        raise HTTPException(401, "login")

    app.dependency_overrides[current_user] = denied
    with TestClient(app) as client:
        assert client.get("/api/contractor/catalogues").status_code == 401


def test_operator_read_only(nsi_client):
    client, actor = nsi_client
    actor.role = "OPERATOR"
    assert client.get("/api/contractor/vehicles").status_code == 200
    assert create(client, "areas", id="a", name="A").status_code == 403
    assert client.patch("/api/contractor/areas/a", json={"values": {"name": "B"}}).status_code == 403


def test_vehicle_list_is_sorted_by_short_name_and_enriched(nsi_client, nsi):
    client, actor = nsi_client
    party = Counterparty(id="party", name="Подрядчик", status="REVIEW", details={})
    vehicles = [
        Vehicle(id="b", name="333Шакман", plate="Е333РА186", vehicle_type="Самосвал", status="REVIEW", details={}),
        Vehicle(id="a", name="227КАМАЗ", plate="К227СА186", vehicle_type="Самосвал", status="REVIEW", details={}, wialon_unit_id="42"),
        Vehicle(id="missing", name="М777ТР86 · имя не задано", plate="М777ТР86", vehicle_type="Самосвал", status="INCOMPLETE", details={}),
    ]
    nsi.add_all([party, *vehicles])
    nsi.add_all([
        VehicleRelation(id="owner", name="owner", vehicle_id="a", counterparty_id="party", role="OPERATIONAL", status="CONFIRMED", details={}, approved_at=datetime.now(timezone.utc)),
        VehicleRelation(id="candidate", name="candidate", vehicle_id="missing", counterparty_id="party", role="CANDIDATE", status="REVIEW", details={}),
    ])
    nsi.commit()

    rows = client.get("/api/contractor/vehicles").json()["items"]
    assert [row["id"] for row in rows] == ["a", "b", "missing"]
    assert rows[0]["operational_owner"] == "Подрядчик"
    assert rows[0]["wialon_control"] == "MATCHED"
    assert rows[2]["short_name"] is None
    assert rows[2]["owner_candidate"] == "Подрядчик"
    assert [row["id"] for row in client.get("/api/contractor/counterparties/party/vehicles").json()] == ["a", "missing"]


def test_fk_duplicate_history_and_concurrent_edit(nsi_client):
    client, _ = nsi_client
    assert (
        create(client, "territories", id="t", name="T", area_id="missing", contract_id="missing").status_code
        == 422
    )
    assert create(client, "areas", id="a", name="Area").status_code == 201
    assert create(client, "areas", id="a", name="duplicate").status_code == 409
    assert (
        client.patch("/api/contractor/areas/a", json={"version": 1, "values": {"name": "New"}}).status_code
        == 200
    )
    assert (
        client.patch("/api/contractor/areas/a", json={"version": 1, "values": {"name": "Lost"}}).status_code
        == 409
    )
    detail = client.get("/api/contractor/areas/a").json()
    assert detail["record"]["name"] == "New"
    assert len(detail["history"]) == 2
    assert (
        client.patch("/api/contractor/areas/a", json={"version": 2, "values": {"id": "other"}}).status_code
        == 422
    )


def test_revision_immutable_and_links(nsi_client):
    client, _ = nsi_client
    assert create(client, "contracts", id="c", name="Contract", number="TEST-001").status_code == 201
    assert (
        create(
            client,
            "revisions",
            id="r1",
            name="Original",
            contract_id="c",
            revision=1,
            status="CONFIRMED",
            signed_on="2026-10-01",
            effective_from="2026-10-16",
            basis="Synthetic signed document",
            party_snapshots=[
                {"name": "Test customer", "role": "CUSTOMER"},
                {"name": "Test contractor", "role": "CONTRACTOR"},
            ],
        ).status_code
        == 201
    )
    assert (
        client.patch(
            "/api/contractor/revisions/r1", json={"version": 1, "values": {"name": "overwrite"}}
        ).status_code
        == 409
    )
    assert (
        create(client, "revisions", id="r2", name="Amendment", contract_id="c", revision=2).status_code == 201
    )
    links = client.get("/api/contractor/contracts/c").json()["links"]
    assert len(next(group for group in links if group["kind"] == "revisions")["items"]) == 2


@pytest.mark.parametrize(
    "plate,valid",
    [
        ("0406УХ86", True),
        ("Е679РН186", True),
        ("E500OX186", False),
        ("5939EE86", False),
        ("Е 679 РН 186", False),
        ("", False),
    ],
)
def test_plate_never_guesses(plate, valid):
    assert plate_valid(plate) == valid


def questionnaire(path):
    book = Workbook()
    passport = book.active
    passport.title = "Паспорт контрагента"
    for cell, value in {
        "B3": "ARM_IMPORT_V2",
        "B5": "ИП",
        "B8": "Тестовый",
        "B9": "Контрагент",
        "B13": "123456789012",
        "A25": "Расчётный счёт",
        "B25": 4.0802810038419997e19,
    }.items():
        passport[cell] = value
    tech = book.create_sheet("Техника")
    for row, plate in [(5, "1234УХ86"), (6, "E500OX186"), (7, "5678УХ86")]:
        tech[f"H{row}"] = "Погрузчик"
        tech[f"I{row}"] = plate
        tech[f"K{row}"] = "TEST"
    data = io.BytesIO()
    book.save(data)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ARM_anketa.xlsx", data.getvalue())
        archive.writestr("card.txt", "synthetic test document")


def test_import_partial_idempotent_no_overwrite(nsi, tmp_path):
    path = tmp_path / "synthetic.zip"
    questionnaire(path)
    original = path.read_bytes()
    import_questionnaire(nsi, path)
    nsi.commit()
    assert len(nsi.scalars(select(Vehicle)).all()) == 3
    assert nsi.get(Vehicle, "E500OX186").status == "INCOMPLETE"
    vehicle = nsi.get(Vehicle, "1234УХ86")
    assert vehicle.status == "INCOMPLETE"
    assert "имя не задано" in vehicle.name
    vehicle.name = "Approved later"
    vehicle.status = "CONFIRMED"
    vehicle.version += 1
    nsi.commit()
    import_questionnaire(nsi, path)
    nsi.commit()
    assert len(nsi.scalars(select(Vehicle)).all()) == 3
    assert len(nsi.scalars(select(Counterparty)).all()) == 1
    assert len(nsi.scalars(select(VehicleRelation)).all()) == 3
    assert vehicle.name == "Approved later"
    assert nsi.scalar(select(Counterparty)).details["Расчётный счёт"] is None
    assert any(e.outcome == "REVIEW" for e in nsi.scalars(select(ImportEvent)))
    assert path.read_bytes() == original


def test_questionnaire_upload_keeps_journal_and_extracted_files_not_zip(
    nsi_client, tmp_path, monkeypatch
):
    client, _ = nsi_client
    source = tmp_path / "incoming.zip"
    questionnaire(source)
    data_dir = tmp_path / "workspace"
    monkeypatch.setattr(
        "app.contractor.current_organization", lambda: SimpleNamespace(data_dir=data_dir)
    )

    response = client.post(
        "/api/contractor/questionnaire-imports",
        files={"file": ("incoming.zip", source.read_bytes(), "application/zip")},
    )

    assert response.status_code == 201
    assert response.json()["file_name"] == "incoming.zip"
    assert not list(data_dir.rglob("*.zip"))
    assert list((data_dir / "contractor-documents").iterdir())
    assert client.get("/api/contractor/questionnaire-imports").json()[0]["file_name"] == "incoming.zip"


def test_questionnaire_v2_stages_objects_and_applies_only_prepared_rows(
    nsi_client, nsi, tmp_path, monkeypatch
):
    client, _ = nsi_client
    source = tmp_path / "staged.zip"
    questionnaire(source)
    data_dir = tmp_path / "workspace"
    monkeypatch.setattr(
        "app.contractor.current_organization", lambda: SimpleNamespace(data_dir=data_dir)
    )

    uploaded = client.post(
        "/api/contractor/questionnaire-imports",
        files={"file": ("staged.zip", source.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 201
    batch = uploaded.json()
    assert batch["summary"]["schema_version"] == 2
    assert batch["summary"]["counts"]["source_rows"] == 3
    assert not nsi.scalars(select(Counterparty)).all()
    assert not nsi.scalars(select(Vehicle)).all()

    party = batch["summary"]["counterparty"]
    vehicle = batch["summary"]["vehicles"][0]
    assert client.patch(
        f"/api/contractor/questionnaire-imports/{batch['id']}/items/counterparty",
        json={"accepted": party["accepted"]},
    ).status_code == 200
    assert client.patch(
        f"/api/contractor/questionnaire-imports/{batch['id']}/items/{vehicle['key']}",
        json={"accepted": {**vehicle["accepted"], "name": "123 Тест"}},
    ).status_code == 200

    applied = client.post(f"/api/contractor/questionnaire-imports/{batch['id']}/apply")
    assert applied.status_code == 200, applied.text
    assert applied.json()["summary"]["counts"] == {
        "source_rows": 3,
        "prepared": 0,
        "applied": 2,
        "pending": 2,
        "issues": 5,
        "unassigned_documents": 1,
    }
    assert nsi.scalar(select(Counterparty)).inn == "123456789012"
    assert [row.name for row in nsi.scalars(select(Vehicle)).all()] == ["123 Тест"]


def test_questionnaire_resolution_writes_verified_bik_to_counterparty(nsi_client, nsi):
    client, _ = nsi_client
    nsi.add(Counterparty(id="party", name="Test party", details={"БИК": None}))
    nsi.add(
        QuestionnaireImport(
            id="batch",
            file_name="questionnaire.zip",
            sha256="3" * 64,
            counterparty_id="party",
            status="NEEDS_REVIEW",
            summary={
                "accepted": 1,
                "review": 1,
                "events": [
                    {
                        "outcome": "REVIEW",
                        "message": "БИК не представлен точной строкой из девяти цифр",
                        "entity": "counterparties",
                        "record_id": "party",
                    }
                ],
            },
        )
    )
    nsi.commit()

    response = client.patch(
        "/api/contractor/questionnaire-imports/batch/resolve",
        json={"event_index": 0, "resolution": "044525225"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert nsi.get(Counterparty, "party").details["БИК"] == "044525225"


def test_questionnaire_row_links_arbitrarily_named_document(nsi, tmp_path):
    book = Workbook()
    passport = book.active
    passport.title = "Паспорт контрагента"
    passport["B3"], passport["B5"], passport["B8"], passport["B9"], passport["B13"] = (
        "ARM_IMPORT_V2",
        "ИП",
        "Тестовый",
        "Контрагент",
        "123456789012",
    )
    tech = book.create_sheet("Техника")
    tech["B5"] = "5939УУ 86 — 5939EE86"
    tech["C5"] = "ИЗ СПРАВОЧНИКА"
    tech["D5"] = "Погрузчик"
    tech["E5"] = "Е500ОХ186"
    tech["F5"] = "500Шакман"
    tech["G5"] = "TEST LOADER"
    tech["O5"] = "ДА"
    tech["P5"] = "произвольное имя документа"
    tech["V5"] = "Е500ОХ186"
    data = io.BytesIO()
    book.save(data)
    path = tmp_path / "row-link.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ARM_anketa.xlsx", data.getvalue())
        archive.writestr("произвольное имя документа.pdf", "synthetic STS")

    import_questionnaire(nsi, path)
    nsi.commit()

    vehicle = nsi.get(Vehicle, "Е500ОХ186")
    assert vehicle is not None
    assert vehicle.name == "500Шакман"
    link = nsi.scalar(select(DocumentVehicleLink).where(DocumentVehicleLink.vehicle_id == vehicle.id))
    assert link is not None
    assert link.document_kind == "STS"
    assert link.details["match"] == "Имя файла явно указано в строке анкеты"


def test_owner_changes_require_approval_date(nsi_client, nsi):
    client, _ = nsi_client
    for id in ("p1", "p2"):
        assert create(client, "counterparties", id=id, name=id).status_code == 201
    assert (
        create(client, "vehicles", id="v", name="V", plate="1234УХ86", vehicle_type="Погрузчик").status_code
        == 201
    )
    for id, party in [("r1", "p1"), ("r2", "p2")]:
        assert (
            create(
                client,
                "vehicle-relations",
                id=id,
                name=id,
                vehicle_id="v",
                counterparty_id=party,
                role="CANDIDATE",
                source="questionnaire.zip",
            ).status_code
            == 201
        )
        result = client.post(f"/api/contractor/vehicle-relations/{id}/approve", json={})
        assert result.status_code == 200
        assert result.json()["approved_at"]
        assert result.json()["decision_reason"] == "Подтверждено на основании источника: questionnaire.zip"
    assert nsi.get(VehicleRelation, "r1").valid_to == nsi.get(VehicleRelation, "r2").valid_from
    assert nsi.get(VehicleRelation, "r2").valid_to is None


def test_owner_candidate_can_be_rejected_with_audited_reason(nsi_client, nsi):
    client, actor = nsi_client
    assert create(client, "counterparties", id="p", name="Candidate").status_code == 201
    assert create(client, "vehicles", id="v", name="V", plate="1234УХ86", vehicle_type="Погрузчик").status_code == 201
    assert create(client, "vehicle-relations", id="r", name="r", vehicle_id="v", counterparty_id="p", role="CANDIDATE").status_code == 201
    assert client.post("/api/contractor/vehicle-relations/r/reject", json={}).status_code == 422
    assert client.post("/api/contractor/vehicle-relations/r/reject", json={"reason": "Контрагент отозвал технику"}).status_code == 200
    row = nsi.get(VehicleRelation, "r")
    assert (row.decision, row.status, row.decided_by) == ("REJECTED", "INACTIVE", actor.id)
    assert row.decision_reason == "Контрагент отозвал технику"
    assert nsi.scalar(select(History).where(History.entity == "vehicle-relations", History.record_id == "r"))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://x@prod/contractor_max_test",
        "postgresql://x@127.0.0.1:5432/contractor_max_test",
        "postgresql://x@127.0.0.1:55432/general_arm",
    ],
)
def test_import_rejects_other_databases(url):
    with pytest.raises(RuntimeError):
        assert_local_test_url(url)


def test_invalid_values_and_unproven_owner(nsi_client):
    client, _ = nsi_client
    assert create(client, "vehicles", name="V", plate="E500OX186", vehicle_type="Truck").status_code == 422
    assert (
        create(
            client, "vehicles", name="V", plate="1234УХ86", vehicle_type="Loader", status="UNKNOWN"
        ).status_code
        == 422
    )
    assert create(client, "contracts", id="c", name="Contract", number="TEST-001").status_code == 201
    assert (
        create(
            client, "revisions", name="Unsigned", contract_id="c", revision=1, status="CONFIRMED"
        ).status_code
        == 422
    )
    assert create(client, "works", id="parent", name="Parent", code="P").status_code == 201
    assert create(client, "works", id="child", name="Child", code="C", parent_id="parent").status_code == 201
    assert (
        client.patch(
            "/api/contractor/works/parent", json={"version": 1, "values": {"parent_id": "child"}}
        ).status_code
        == 422
    )


def test_document_path_cannot_escape_sources(nsi_client, nsi):
    from app.contractor_models import Document

    client, _ = nsi_client
    nsi.add(Document(id="private", name="Forbidden", sha256="0" * 64, location="C:/Windows/win.ini!x"))
    nsi.commit()
    assert client.get("/api/contractor/documents/private/content").status_code == 422


def test_contract_parties_keep_legal_snapshot_and_reverse_links(nsi_client):
    client, _ = nsi_client
    assert create(client, "contracts", id="c", name="Test", number="Test").status_code == 201
    assert (
        create(client, "revisions", id="r", name="Test revision", contract_id="c", revision=1).status_code
        == 201
    )
    for key in ("a", "b"):
        assert create(client, "counterparties", id=key, name="Original " + key).status_code == 201
        assert (
            create(
                client,
                "contract-parties",
                id=key,
                name="Side " + key,
                revision_id="r",
                counterparty_id=key,
                role=key,
            ).status_code
            == 201
        )
    assert (
        client.patch(
            "/api/contractor/counterparties/a", json={"version": 1, "values": {"name": "Changed passport"}}
        ).status_code
        == 200
    )
    assert (
        client.get("/api/contractor/contract-parties/a").json()["record"]["snapshot"]["name"] == "Original a"
    )
    confirmed = client.patch(
        "/api/contractor/revisions/r",
        json={
            "version": 1,
            "values": {
                "status": "CONFIRMED",
                "signed_on": "2026-10-01",
                "effective_from": "2026-10-16",
                "basis": "Synthetic signed agreement",
            },
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["party_snapshots"][0]["name"] == "Original a"
    assert (
        client.patch(
            "/api/contractor/contract-parties/a", json={"version": 1, "values": {"role": "changed"}}
        ).status_code
        == 409
    )
    assert any(
        g["kind"] == "contract-parties"
        for g in client.get("/api/contractor/counterparties/a").json()["links"]
    )


def test_organization_documents_have_profile_link(nsi_client, nsi):
    from app.contractor_models import LegalProfile

    nsi.add(LegalProfile(id="profile", name="Our profile", organization_id="workspace"))
    nsi.commit()
    client, _ = nsi_client
    assert (
        create(
            client,
            "documents",
            id="org-doc",
            name="Synthetic organization document",
            sha256="1" * 64,
            location="external-reference",
            legal_profile_id="profile",
        ).status_code
        == 201
    )
    assert any(
        g["kind"] == "documents" for g in client.get("/api/contractor/organization/profile").json()["links"]
    )


def test_vehicle_type_accepts_multiple_work_categories(nsi_client, nsi):
    nsi.add(VehicleType(id="dump", name="Самосвал", code="DUMP_TRUCK"))
    nsi.add_all(
        [
            WorkCategory(id="haul", name="Вывоз", code="HAUL"),
            WorkCategory(id="load", name="Погрузка", code="LOAD"),
        ]
    )
    nsi.commit()
    client, _ = nsi_client

    response = client.put(
        "/api/contractor/vehicle-types/dump/capabilities",
        json={"work_category_ids": ["haul", "load"]},
    )
    assert response.status_code == 200
    capabilities = client.get("/api/contractor/vehicle-types/dump/capabilities").json()
    assert {item["id"] for item in capabilities if item["selected"]} == {"haul", "load"}


def test_vehicle_document_queue_and_controlled_assignment(nsi_client, nsi):
    nsi.add(Vehicle(id="vehicle", name="167Шакман", plate="М167КВ186", vehicle_type="Самосвал"))
    nsi.add(
        Document(
            id="sts",
            name="М 167 КВ 186 СТС.pdf",
            sha256="2" * 64,
            location="synthetic-reference",
            document_scope="VEHICLE",
            document_kind="STS",
            link_state="NEEDS_LINK",
        )
    )
    nsi.commit()
    client, _ = nsi_client

    assert [item["id"] for item in client.get("/api/contractor/vehicle-documents/queue").json()] == ["sts"]
    assigned = client.post(
        "/api/contractor/documents/sts/assign-vehicle",
        json={"vehicle_id": "vehicle", "document_kind": "STS"},
    )
    assert assigned.status_code == 201
    assert client.get("/api/contractor/vehicle-documents/queue").json() == []
    documents = client.get("/api/contractor/vehicles/vehicle/documents").json()
    assert [(item["id"], item["document_kind"]) for item in documents] == [("sts", "STS")]


def test_calibration_confirmation_is_explicit_atomic_and_idempotent(
    nsi_client, nsi, tmp_path, monkeypatch
):
    content = b"%PDF-1.4 synthetic calibration"
    source = tmp_path / "questionnaire.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("calibration.pdf", content)
    monkeypatch.setattr("app.contractor.questionnaire_source_root", lambda: tmp_path.resolve())
    vehicle = Vehicle(
        id="dump",
        name="679HOWO",
        plate="Е679РН186",
        vehicle_type="Самосвал",
        status="CONFIRMED",
        calibration_m3=25,
        calibration_on=date(2020, 1, 1),
    )
    document = Document(
        id="calibration",
        name="Справка Хово Е679РН186.pdf",
        sha256=hashlib.sha256(content).hexdigest(),
        location=f"{source}!calibration.pdf",
        status="REVIEW",
        document_scope="VEHICLE",
        document_kind="CALIBRATION",
        link_state="LINKED",
    )
    link = DocumentVehicleLink(
        id="calibration-link",
        name=document.name,
        status="CONFIRMED",
        document_id=document.id,
        vehicle_id=vehicle.id,
        document_kind="CALIBRATION",
    )
    nsi.add_all([vehicle, document])
    nsi.commit()
    nsi.add(link)
    nsi.commit()
    client, actor = nsi_client

    before = client.get("/api/contractor/vehicle-approvals/candidates").json()[0]
    assert before["calibration"]["ready"] is False
    assert [item["code"] for item in before["calibration"]["reasons"]] == [
        "DOCUMENT_NOT_CONFIRMED"
    ]
    assert client.post("/api/contractor/documents/calibration/confirm").status_code == 422
    actor.role = "OPERATOR"
    assert (
        client.post(
            "/api/contractor/vehicles/dump/calibrations/calibration/confirm",
            json={"volume_m3": "25.000"},
        ).status_code
        == 403
    )
    assert document.status == "REVIEW"
    actor.role = "ADMIN_OPERATOR"

    confirmed = client.post(
        "/api/contractor/vehicles/dump/calibrations/calibration/confirm",
        json={"volume_m3": "25.000"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["calibration"]["ready"] is True
    assert document.status == "CONFIRMED"
    assert document.details["calibration_verified_by_name"] == "Тестовый оператор"
    assert document.details["calibration_confirmed_volume_m3"] == "25.000"
    assert vehicle.calibration_m3 == 25
    assert vehicle.calibration_on != date(2020, 1, 1)
    history_count = len(nsi.scalars(select(History)).all())
    versions = document.version, vehicle.version

    repeated = client.post(
        "/api/contractor/vehicles/dump/calibrations/calibration/confirm",
        json={"volume_m3": "25.000"},
    )
    assert repeated.status_code == 200
    assert len(nsi.scalars(select(History)).all()) == history_count
    assert (document.version, vehicle.version) == versions


def test_calibration_readiness_prefers_confirmed_document_over_newer_review(
    nsi_client, nsi, tmp_path
):
    confirmed_file = tmp_path / "confirmed.pdf"
    review_file = tmp_path / "review.pdf"
    confirmed_file.write_bytes(b"confirmed")
    review_file.write_bytes(b"review")
    vehicle = Vehicle(
        id="dump",
        name="Test dump",
        plate="Е100РН186",
        vehicle_type="Самосвал",
        calibration_m3=20,
    )
    confirmed = Document(
        id="confirmed",
        name="Confirmed.pdf",
        sha256=hashlib.sha256(confirmed_file.read_bytes()).hexdigest(),
        location=str(confirmed_file),
        status="CONFIRMED",
        document_kind="CALIBRATION",
        details={"calibration_verified_at": "2026-11-01T10:00:00+00:00"},
    )
    review = Document(
        id="review",
        name="Review.pdf",
        sha256=hashlib.sha256(review_file.read_bytes()).hexdigest(),
        location=str(review_file),
        status="REVIEW",
        document_kind="CALIBRATION",
    )
    nsi.add_all([vehicle, confirmed, review])
    nsi.commit()
    nsi.add_all(
        [
            DocumentVehicleLink(
                id="confirmed-link",
                name="Confirmed",
                document_id="confirmed",
                vehicle_id="dump",
                document_kind="CALIBRATION",
            ),
            DocumentVehicleLink(
                id="review-link",
                name="Review",
                document_id="review",
                vehicle_id="dump",
                document_kind="CALIBRATION",
            ),
        ]
    )
    nsi.commit()
    client, _ = nsi_client

    row = client.get("/api/contractor/vehicle-approvals/candidates").json()[0]
    assert row["calibration"]["ready"] is True
    assert row["calibration"]["document_id"] == "confirmed"


def test_calibration_readiness_reports_missing_volume_and_file(nsi_client, nsi):
    vehicle = Vehicle(
        id="dump",
        name="Test dump",
        plate="Е101РН186",
        vehicle_type="Самосвал",
    )
    document = Document(
        id="missing-file",
        name="Missing.pdf",
        sha256="7" * 64,
        location="missing-file.pdf",
        status="CONFIRMED",
        document_kind="CALIBRATION",
    )
    nsi.add_all([vehicle, document])
    nsi.commit()
    nsi.add_all(
        [
            DocumentVehicleLink(
                id="missing-link",
                name="Missing",
                document_id=document.id,
                vehicle_id=vehicle.id,
                document_kind="CALIBRATION",
            ),
        ]
    )
    nsi.commit()
    client, _ = nsi_client

    row = client.get("/api/contractor/vehicle-approvals/candidates").json()[0]
    assert {item["code"] for item in row["calibration"]["reasons"]} == {
        "VOLUME_MISSING",
        "FILE_UNAVAILABLE",
    }


def test_approval_letter_pdf_uses_selected_business_values(tmp_path):
    from PIL import Image

    signature = tmp_path / "signature.png"
    Image.new("RGBA", (120, 60), (255, 255, 255, 0)).save(signature)
    body = SimpleNamespace(
        sender_lines=["Тестовый отправитель", "Адрес", "Подписант"],
        letter_number="TEST-7",
        letter_date=date(2026, 11, 1),
        recipient="Тестовый адресат",
        body_text="В рамках исполнения контракта [выбранные номера] сообщаю о технике:",
        monitoring_url="https://monitoring.example.test",
        monitoring_login="shared-login",
        monitoring_password="shared-password",
    )
    pdf = render_approval_pdf(
        body,
        [SimpleNamespace(number="58"), SimpleNamespace(number="60")],
        [
            {
                "vehicle_type": "Самосвал",
                "calibration_m3": "20.000",
                "model": "TEST",
                "plate": "1234УХ86",
                "attachment_name": "123 Тест тарировка 20 м3 2026-11-01.pdf",
            }
        ],
        signature,
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000


def test_approval_letter_zip_uses_same_readiness_rule_and_registers_request(
    nsi_client, nsi, tmp_path, monkeypatch
):
    calibration_file = tmp_path / "calibration.pdf"
    calibration_file.write_bytes(b"%PDF-1.4 confirmed calibration")
    signature = tmp_path / "signature.png"
    signature.write_bytes(b"synthetic signature asset")
    vehicle_type = VehicleType(
        id="dump-type", name="Самосвал", code="DUMP_TRUCK", requires_calibration=True
    )
    vehicle = Vehicle(
        id="dump",
        name="679HOWO",
        plate="Е679РН186",
        vehicle_type="DUMP_TRUCK",
        vehicle_type_id=vehicle_type.id,
        model="HOWO",
        calibration_m3=25,
        calibration_on=date(2026, 11, 5),
    )
    document = Document(
        id="calibration",
        name="Справка Хово Е679РН186.pdf",
        sha256=hashlib.sha256(calibration_file.read_bytes()).hexdigest(),
        location=str(calibration_file),
        status="CONFIRMED",
        document_kind="CALIBRATION",
        details={"calibration_verified_at": "2026-11-05T08:00:00+00:00"},
    )
    nsi.add(vehicle_type)
    nsi.commit()
    nsi.add_all(
        [
            Contract(id="58", name="Контракт 58", number="ЭА.2026.00058"),
            Contract(id="60", name="Контракт 60", number="ЭА.2026.00060"),
            vehicle,
            document,
        ]
    )
    nsi.commit()
    nsi.add(
            DocumentVehicleLink(
                id="calibration-link",
                name=document.name,
                document_id=document.id,
                vehicle_id=vehicle.id,
                document_kind="CALIBRATION",
            )
    )
    nsi.commit()
    monkeypatch.setattr("app.contractor.approval_signature_path", lambda: signature)
    monkeypatch.setattr(
        "app.contractor.render_approval_pdf", lambda *args: b"%PDF-1.4 synthetic letter"
    )
    monkeypatch.setattr(
        "app.contractor.current_organization",
        lambda: SimpleNamespace(data_dir=tmp_path / "workspace"),
    )
    client, _ = nsi_client

    response = client.post(
        "/api/contractor/vehicle-approvals/letter",
        json={
            "contract_ids": ["58", "60"],
            "vehicle_ids": ["dump"],
            "letter_number": "TEST-58-60",
            "letter_date": "2026-11-05",
            "sender_lines": ["Отправитель", "Адрес", "Подписант"],
            "recipient": "Заказчику",
            "body_text": "По контрактам [выбранные номера] направляем технику.",
            "monitoring_url": "https://monitoring.example.test",
            "monitoring_login": "test-login",
            "monitoring_password": "test-password",
        },
    )

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert any(name.endswith(".pdf") and "Письмо" in name for name in names)
        assert any("679HOWO" in name and "25" in name for name in names)
        assert archive.read(next(name for name in names if "Письмо" in name)).startswith(
            b"%PDF"
        )
    request = nsi.scalar(select(VehicleApprovalRequest))
    decision = nsi.scalar(select(VehicleApprovalDecision))
    assert request.contract_ids == ["58", "60"]
    assert decision.vehicle_id == "dump"
    assert decision.calibration_document_id == "calibration"
