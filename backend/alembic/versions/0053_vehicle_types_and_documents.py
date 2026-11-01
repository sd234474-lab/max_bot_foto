"""Vehicle type capabilities and controlled document-to-vehicle links."""

import sqlalchemy as sa

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def record_columns():
    return [
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="CONFIRMED"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("source", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table("nsi_vehicle_types", *record_columns(), sa.Column("code", sa.String(60), nullable=False, unique=True), sa.Column("requires_calibration", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("requires_sts", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("requires_wialon", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("nsi_work_categories", *record_columns(), sa.Column("code", sa.String(60), nullable=False, unique=True))
    op.create_table("nsi_vehicle_type_work_links", *record_columns(), sa.Column("vehicle_type_id", sa.String(100), sa.ForeignKey("nsi_vehicle_types.id"), nullable=False), sa.Column("work_category_id", sa.String(100), sa.ForeignKey("nsi_work_categories.id"), nullable=False), sa.UniqueConstraint("vehicle_type_id", "work_category_id"))
    op.add_column("nsi_vehicles", sa.Column("vehicle_type_id", sa.String(100), sa.ForeignKey("nsi_vehicle_types.id")))
    types = [("DUMP_TRUCK","Самосвал",True),("LOADER","Погрузчик",False),("MINI_LOADER","Минипогрузчик",False),("TRACTOR","Трактор",False),("GRADER","Грейдер",False),("BULLDOZER","Бульдозер",False)]
    for code, name, calibration in types:
        op.execute(sa.text("INSERT INTO nsi_vehicle_types(id,name,code,requires_calibration) VALUES (:id,:name,:code,:calibration)").bindparams(id=code,name=name,code=code,calibration=calibration))
    mappings = {"Самосвал":"DUMP_TRUCK","Погрузчик":"LOADER","МПогрузчик":"MINI_LOADER","Минипогрузчик":"MINI_LOADER","Трактор":"TRACTOR","Грейдер":"GRADER","Бульдозер":"BULLDOZER"}
    for raw, target in mappings.items():
        op.execute(sa.text("UPDATE nsi_vehicles SET vehicle_type_id=:target WHERE lower(vehicle_type)=lower(:raw)").bindparams(target=target,raw=raw))
    for code,name in [("HAUL","Вывоз"),("LOAD","Погрузка"),("PILE","Сгребание"),("MECH_CLEAN","Механизированная уборка"),("GRADE","Планировка"),("SPREAD","Посыпка"),("MOW","Покос")]:
        op.execute(sa.text("INSERT INTO nsi_work_categories(id,name,code) VALUES (:id,:name,:code)").bindparams(id=code,name=name,code=code))
    op.add_column("nsi_documents", sa.Column("document_scope", sa.String(24), nullable=False, server_default="COUNTERPARTY"))
    op.add_column("nsi_documents", sa.Column("document_kind", sa.String(40), nullable=False, server_default="OTHER"))
    op.add_column("nsi_documents", sa.Column("link_state", sa.String(24), nullable=False, server_default="NOT_REQUIRED"))
    op.create_table("nsi_document_vehicle_links", *record_columns(), sa.Column("document_id", sa.String(100), sa.ForeignKey("nsi_documents.id"), nullable=False), sa.Column("vehicle_id", sa.String(100), sa.ForeignKey("nsi_vehicles.id"), nullable=False), sa.Column("document_kind", sa.String(40), nullable=False), sa.Column("page_reference", sa.String(100)), sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("linked_by", sa.Integer()), sa.UniqueConstraint("document_id", "vehicle_id", "document_kind"))
    op.execute("UPDATE nsi_documents SET document_scope='VEHICLE', document_kind=CASE WHEN lower(name) LIKE '%стс%' THEN 'STS' WHEN lower(name) LIKE '%замер%' OR lower(name) LIKE '%тарир%' THEN 'CALIBRATION' WHEN lower(name) LIKE '%глонасс%' THEN 'GLONASS' ELSE 'OTHER' END, link_state='NEEDS_LINK' WHERE lower(name) LIKE '%стс%' OR lower(name) LIKE '%замер%' OR lower(name) LIKE '%тарир%' OR lower(name) LIKE '%глонасс%'")
    op.execute("""INSERT INTO nsi_document_vehicle_links(id,name,status,version,details,document_id,vehicle_id,document_kind)
      SELECT md5(d.id||':'||v.id), d.name, 'REVIEW', 1, '{}'::json, d.id, v.id, d.document_kind
      FROM nsi_documents d JOIN nsi_vehicles v ON regexp_replace(upper(d.name),'[^0-9А-Я]','','g') LIKE '%'||regexp_replace(upper(v.plate),'[^0-9А-Я]','','g')||'%'
      WHERE d.document_scope='VEHICLE'""")
    op.execute("UPDATE nsi_documents d SET link_state='LINKED', vehicle_id=l.vehicle_id FROM nsi_document_vehicle_links l WHERE l.document_id=d.id")


def downgrade():
    raise RuntimeError("Vehicle type and document-link history must be retained")
