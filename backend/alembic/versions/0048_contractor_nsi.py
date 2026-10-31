"""Contractor NSI schema snapshot; no production data or photo table changes."""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
CREATE TABLE nsi_areas (
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)

""",
    """
CREATE TABLE nsi_contracts (
	number VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (number)
)

""",
    """
CREATE TABLE nsi_counterparties (
	inn VARCHAR(12),
	legal_form VARCHAR(40),
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (inn)
)

""",
    """
CREATE TABLE nsi_history (
	id SERIAL NOT NULL,
	entity VARCHAR(100) NOT NULL,
	record_id VARCHAR(100) NOT NULL,
	actor_id INTEGER,
	before JSON,
	after JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)

""",
    """CREATE INDEX ix_nsi_history_record_id ON nsi_history (record_id)""",
    """
CREATE TABLE nsi_import_events (
	id SERIAL NOT NULL,
	source_key VARCHAR(64) NOT NULL,
	outcome VARCHAR(20) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	entity VARCHAR(100),
	record_id VARCHAR(100),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (source_key)
)

""",
    """
CREATE TABLE nsi_legal_profiles (
	organization_id VARCHAR(36) NOT NULL,
	inn VARCHAR(12),
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id)
)

""",
    """
CREATE TABLE nsi_units (
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)

""",
    """
CREATE TABLE nsi_vehicles (
	plate VARCHAR(20) NOT NULL,
	vehicle_type VARCHAR(100) NOT NULL,
	model VARCHAR(200),
	calibration_m3 NUMERIC(12, 3),
	calibration_on DATE,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (plate),
	CHECK (calibration_m3 IS NULL OR calibration_m3 > 0)
)

""",
    """
CREATE TABLE nsi_contract_revisions (
	contract_id VARCHAR(100) NOT NULL,
	revision INTEGER NOT NULL,
	signed_on DATE,
	effective_from DATE,
	effective_to DATE,
	basis TEXT,
	party_snapshots JSON NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (effective_to IS NULL OR effective_to >= effective_from),
	FOREIGN KEY(contract_id) REFERENCES nsi_contracts (id),
	UNIQUE (contract_id, revision),
	CHECK (revision > 0)
)

""",
    """
CREATE TABLE nsi_contract_works (
	code VARCHAR(100) NOT NULL,
	parent_id VARCHAR(100),
	unit_id VARCHAR(100),
	photo_work_type_id INTEGER,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code),
	FOREIGN KEY(unit_id) REFERENCES nsi_units (id),
	FOREIGN KEY(photo_work_type_id) REFERENCES work_types (id),
	FOREIGN KEY(parent_id) REFERENCES nsi_contract_works (id)
)

""",
    """
CREATE TABLE nsi_max_zone_links (
	area_id VARCHAR(100) NOT NULL,
	district_id INTEGER NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(area_id) REFERENCES nsi_areas (id),
	UNIQUE (area_id, district_id),
	FOREIGN KEY(district_id) REFERENCES districts (id)
)

""",
    """
CREATE TABLE nsi_territories (
	area_id VARCHAR(100) NOT NULL,
	contract_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(contract_id) REFERENCES nsi_contracts (id),
	FOREIGN KEY(area_id) REFERENCES nsi_areas (id)
)

""",
    """
CREATE TABLE nsi_cells (
	territory_id VARCHAR(100) NOT NULL,
	replaced_by_id VARCHAR(100),
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(territory_id) REFERENCES nsi_territories (id),
	FOREIGN KEY(replaced_by_id) REFERENCES nsi_cells (id)
)

""",
    """
CREATE TABLE nsi_contract_objects (
	contract_id VARCHAR(100) NOT NULL,
	territory_id VARCHAR(100),
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(territory_id) REFERENCES nsi_territories (id),
	FOREIGN KEY(contract_id) REFERENCES nsi_contracts (id)
)

""",
    """
CREATE TABLE nsi_documents (
	sha256 VARCHAR(64) NOT NULL,
	location TEXT NOT NULL,
	counterparty_id VARCHAR(100),
	vehicle_id VARCHAR(100),
	revision_id VARCHAR(100),
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (sha256),
	FOREIGN KEY(vehicle_id) REFERENCES nsi_vehicles (id),
	FOREIGN KEY(revision_id) REFERENCES nsi_contract_revisions (id),
	FOREIGN KEY(counterparty_id) REFERENCES nsi_counterparties (id)
)

""",
    """
CREATE TABLE nsi_contract_lines (
	revision_id VARCHAR(100) NOT NULL,
	document_id VARCHAR(100),
	kind VARCHAR(4) NOT NULL,
	territory_id VARCHAR(100),
	work_id VARCHAR(100),
	stage VARCHAR(100),
	period_from DATE,
	period_to DATE,
	source_row JSON NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(work_id) REFERENCES nsi_contract_works (id),
	FOREIGN KEY(document_id) REFERENCES nsi_documents (id),
	FOREIGN KEY(revision_id) REFERENCES nsi_contract_revisions (id),
	FOREIGN KEY(territory_id) REFERENCES nsi_territories (id),
	CHECK (kind IN ('OP','RC'))
)

""",
    """
CREATE TABLE nsi_sites (
	object_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(object_id) REFERENCES nsi_contract_objects (id)
)

""",
    """
CREATE TABLE nsi_vehicle_relations (
	vehicle_id VARCHAR(100) NOT NULL,
	counterparty_id VARCHAR(100) NOT NULL,
	contract_id VARCHAR(100),
	role VARCHAR(30) NOT NULL,
	valid_from DATE,
	valid_to DATE,
	approved_at TIMESTAMP WITH TIME ZONE,
	evidence_id VARCHAR(100),
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (status != 'CONFIRMED' OR role != 'LEGAL_OWNER' OR evidence_id IS NOT NULL),
	CHECK (valid_to IS NULL OR valid_to >= valid_from),
	FOREIGN KEY(contract_id) REFERENCES nsi_contracts (id),
	FOREIGN KEY(vehicle_id) REFERENCES nsi_vehicles (id),
	CHECK (role IN ('OPERATIONAL','LEGAL_OWNER','CONTRACT','ACTUAL','CANDIDATE')),
	CHECK (status != 'CONFIRMED' OR role != 'OPERATIONAL' OR approved_at IS NOT NULL),
	FOREIGN KEY(evidence_id) REFERENCES nsi_documents (id),
	FOREIGN KEY(counterparty_id) REFERENCES nsi_counterparties (id)
)

""",
    """
CREATE TABLE nsi_wialon_links (
	cell_id VARCHAR(100) NOT NULL,
	resource_id VARCHAR(100) NOT NULL,
	zone_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (cell_id),
	FOREIGN KEY(cell_id) REFERENCES nsi_cells (id),
	UNIQUE (resource_id, zone_id)
)

""",
    """
CREATE TABLE nsi_maf (
	site_id VARCHAR(100) NOT NULL,
	id VARCHAR(100) NOT NULL,
	name VARCHAR(500) NOT NULL,
	status VARCHAR(24) NOT NULL,
	version INTEGER NOT NULL,
	source TEXT,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(site_id) REFERENCES nsi_sites (id)
)

""",
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    raise RuntimeError("NSI contains legal history; removal requires explicit approval")
