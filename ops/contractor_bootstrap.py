"""Populate a newly migrated, explicitly approved local test database only."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.auth import hasher
from app.config import settings
from app.contractor_import import assert_local_test_url
from app.contractor_models import LegalProfile
from app.db import engine
from app.models import User
from app.organization_registry import bootstrap
from sqlalchemy.orm import Session

assert_local_test_url(settings().database_url)
path = Path(__file__).resolve().parents[1] / ".local/config.json"
cfg = json.loads(path.read_text(encoding="utf-8"))
with Session(engine()) as db:
    if db.query(User).count():
        raise RuntimeError("Bootstrap requires an empty test user table")
    db.add(
        User(
            login="contractor-admin",
            name="Администратор тестовой НСИ",
            role="ADMIN_OPERATOR",
            active=True,
            password_hash=hasher.hash(cfg["admin_password"]),
        )
    )
    db.commit()
id = bootstrap(
    "contractor-admin", "ИП Шашко · тестовый модуль подрядчика", "contractor-test"
)
with Session(engine()) as db:
    db.add(
        LegalProfile(
            id="our-legal-profile",
            organization_id=id,
            name="Наша организация",
            status="INCOMPLETE",
            details={
                "limitation": "Подтверждённые реквизиты ИП Шашко не предоставлены"
            },
        )
    )
    db.commit()
cfg["organization_id"] = id
path.write_text(json.dumps(cfg), encoding="utf-8")
