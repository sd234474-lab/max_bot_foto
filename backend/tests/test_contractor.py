"""NSI behavior tests use synthetic data only; no customer artifacts in Git."""

import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import current_user
from app.contractor import plate_valid, router
from app.contractor_import import assert_local_test_url, import_questionnaire
from app.contractor_models import CATALOGUES, Counterparty, History, ImportEvent, Vehicle, VehicleRelation
from app.db import Base, get_db


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
    actor = SimpleNamespace(id=1, role="ADMIN_OPERATOR")
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
    assert len(nsi.scalars(select(Vehicle)).all()) == 2
    vehicle = nsi.get(Vehicle, "1234УХ86")
    assert vehicle.status == "INCOMPLETE"
    assert "имя не задано" in vehicle.name
    vehicle.name = "Approved later"
    vehicle.status = "CONFIRMED"
    vehicle.version += 1
    nsi.commit()
    import_questionnaire(nsi, path)
    nsi.commit()
    assert len(nsi.scalars(select(Vehicle)).all()) == 2
    assert len(nsi.scalars(select(Counterparty)).all()) == 1
    assert len(nsi.scalars(select(VehicleRelation)).all()) == 2
    assert vehicle.name == "Approved later"
    assert nsi.scalar(select(Counterparty)).details["Расчётный счёт"] is None
    assert any(e.outcome == "REVIEW" for e in nsi.scalars(select(ImportEvent)))
    assert path.read_bytes() == original


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
            ).status_code
            == 201
        )
        result = client.post(f"/api/contractor/vehicle-relations/{id}/approve")
        assert result.status_code == 200
        assert result.json()["approved_at"]
    assert nsi.get(VehicleRelation, "r1").valid_to == nsi.get(VehicleRelation, "r2").valid_from
    assert nsi.get(VehicleRelation, "r2").valid_to is None


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
