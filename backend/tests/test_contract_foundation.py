"""Q1-01 schema smoke tests (no production data)."""
import importlib.util
from pathlib import Path

from sqlalchemy import UniqueConstraint

from app import models  # noqa: F401
from app.db import Base


EXPECTED = {
    "contracts", "contract_revisions", "contract_stages", "contract_periods",
    "contract_work_lines", "contract_territories", "contract_territory_districts",
    "contract_work_applicability", "contract_indicator_types",
    "contract_physical_values", "contract_price_lines",
}


def test_contract_foundation_metadata_is_additive():
    assert EXPECTED <= set(Base.metadata.tables)
    assert "users" in Base.metadata.tables
    assert "work_types" in Base.metadata.tables
    assert "districts" in Base.metadata.tables


def test_contracts_are_versioned_and_preserve_source_lines():
    assert {"contract_id", "revision_no", "status"} <= set(Base.metadata.tables["contract_revisions"].columns.keys())
    assert {"source_line_code", "work_line_id", "quantity", "unit_price", "line_amount"} <= set(Base.metadata.tables["contract_price_lines"].columns.keys())
    for name in EXPECTED:
        assert any(col.primary_key for col in Base.metadata.tables[name].columns)


def test_referential_integrity_and_uniqueness():
    assert any(isinstance(c, UniqueConstraint) and {x.name for x in c.columns} == {"revision_id", "source_line_code"}
               for c in Base.metadata.tables["contract_price_lines"].constraints)
    work_line = Base.metadata.tables["contract_work_lines"]
    assert any(fk.target_fullname == "work_types.id" for fk in work_line.c.work_type_id.foreign_keys)
    territory_mapping = Base.metadata.tables["contract_territory_districts"]
    assert any(fk.target_fullname == "districts.id" for fk in territory_mapping.c.district_id.foreign_keys)
    assert len([c for c in territory_mapping.columns if c.primary_key]) == 2


def test_new_migration_is_reversible_without_touching_existing_tables():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0032_contract_foundation.py"
    spec = importlib.util.spec_from_file_location("q101_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0032"
    assert module.down_revision == "0031"
    text = path.read_text(encoding="utf-8")
    for name in EXPECTED:
        assert f'"{name}"' in text
    assert 'op.drop_table(table)' in text
