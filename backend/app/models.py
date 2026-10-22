from datetime import date, datetime, time

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.permissions import DEFAULT_OPERATOR_PERMISSIONS


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Organization(Timestamps, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), default="PROVISIONING")
    database_name: Mapped[str | None] = mapped_column(String(63), unique=True)
    database_role: Mapped[str | None] = mapped_column(String(63), unique=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(80))
    legacy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    disk_account_id: Mapped[str | None] = mapped_column(ForeignKey("disk_accounts.id"))


class DiskAccount(Timestamps, Base):
    __tablename__ = "disk_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    yandex_uid: Mapped[str] = mapped_column(String(100), unique=True)
    login: Mapped[str] = mapped_column(String(150))


class OrganizationLogin(Base):
    __tablename__ = "organization_logins"
    login: Mapped[str] = mapped_column(String(100), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)


class WorkspaceSession(Base):
    __tablename__ = "workspace_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginChoice(Base):
    __tablename__ = "login_choices"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    ip_hash: Mapped[str] = mapped_column(String(64))
    candidates: Mapped[list] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PlatformOwner(Base):
    __tablename__ = "platform_owners"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class OrganizationChat(Timestamps, Base):
    __tablename__ = "organization_chats"
    max_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))


class OrganizationEvent(Base):
    __tablename__ = "organization_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(String(80))


class WorkType(Timestamps, Base):
    __tablename__ = "work_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Kept immutable when the display name changes, so existing disk paths stay valid.
    filename_label: Mapped[str] = mapped_column(String(100))
    model_index: Mapped[int] = mapped_column(Integer, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (
        CheckConstraint("model_index >= 0 AND sort_order >= 1 AND version >= 1"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 100"),
        Index("ix_work_types_name_unique", func.lower(name), unique=True),
    )


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(Text)
    avatar_image: Mapped[bytes | None] = mapped_column(LargeBinary)
    sidebar_order: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    # NULL preserves unrestricted access; an empty array grants access to no chats.
    allowed_chat_ids: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True))
    role: Mapped[str] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[list[str]] = mapped_column(
        JSONB,
        default=lambda: list(DEFAULT_OPERATOR_PERMISSIONS),
        server_default=text(
            '\'["reports_view","photos_view","originals_view","photos_edit","spam_view","reports_export","previews_remove","districts_view"]\'::jsonb'
        ),
    )
    __table_args__ = (
        CheckConstraint("role IN ('ADMIN_OPERATOR', 'OPERATOR')"),
        CheckConstraint("jsonb_typeof(sidebar_order)='array'", name="user_sidebar_order_array"),
        CheckConstraint("jsonb_typeof(permissions)='array'", name="user_permissions_array"),
        CheckConstraint(
            "allowed_chat_ids IS NULL OR jsonb_typeof(allowed_chat_ids)='array'",
            name="user_allowed_chats_array",
        ),
    )


class OperatorMessage(Base):
    __tablename__ = "operator_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[str] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id", name="fk_operator_messages_photo"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("sender_id <> recipient_id", name="ck_operator_message_peer"),
        CheckConstraint("length(body) <= 4000 AND (length(body) > 0 OR photo_id IS NOT NULL)",
                        name="ck_operator_message_body"),
        UniqueConstraint("sender_id", "client_id", name="uq_operator_message_client"),
        Index("ix_operator_messages_pair", "sender_id", "recipient_id", "id"),
        Index("ix_operator_messages_unread", "recipient_id", "read_at", "sender_id"),
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    id: Mapped[str] = mapped_column(String(36), server_default=text("gen_random_uuid()::text"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    __table_args__ = (UniqueConstraint("id", name="uq_login_sessions_id"),)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chat(Timestamps, Base):
    __tablename__ = "chats"
    id: Mapped[int] = mapped_column(primary_key=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(150))
    active: Mapped[bool] = mapped_column(default=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cv_work_type_enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    fixed_work_type_code: Mapped[str | None] = mapped_column(String(60))
    work_rule_version: Mapped[int] = mapped_column(default=1, server_default="1")


class MaxSender(Timestamps, Base):
    """One current label per MAX user, shared by every chat in this installation."""

    __tablename__ = "max_senders"
    sender_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(150))
    __table_args__ = (
        CheckConstraint("sender_id > 0", name="ck_max_sender_id"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 150", name="ck_max_sender_name"),
    )


class ChatSender(Timestamps, Base):
    # Manual registration in a chat. The legacy name is retained for migration recovery;
    # MaxSender.name is the sole authority for the current label.
    __tablename__ = "chat_senders"
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    sender_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(150))
    __table_args__ = (
        CheckConstraint("sender_id > 0", name="ck_chat_sender_id"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 150", name="ck_chat_sender_name"),
    )


class DiscoveredChat(Base):
    __tablename__ = "discovered_chats"
    max_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_preview: Mapped[str | None] = mapped_column(String(200))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class District(Timestamps, Base):
    __tablename__ = "districts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    geometry: Mapped[object] = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))


class Inbox(Base):
    __tablename__ = "inbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reply_text: Mapped[str | None] = mapped_column(Text)
    reply_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("max_chat_id", "message_id"),)


class Photo(Timestamps, Base):
    __tablename__ = "photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    inbox_id: Mapped[int] = mapped_column(ForeignKey("inbox.id"), index=True)
    attachment_index: Mapped[int] = mapped_column(Integer)
    attachment: Mapped[dict] = mapped_column(JSONB, default=dict)
    sha256: Mapped[str | None] = mapped_column(String(64))
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("photos.id"))
    original_filename: Mapped[str | None] = mapped_column(String(500))
    stored_filename: Mapped[str | None] = mapped_column(String(255))
    extension: Mapped[str | None] = mapped_column(String(10))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_name: Mapped[str] = mapped_column(String(150))
    max_message_id: Mapped[str] = mapped_column(String(200))
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    is_forwarded: Mapped[bool] = mapped_column(default=False)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    photo_date: Mapped[date | None] = mapped_column(Date, index=True)
    photo_time: Mapped[time | None] = mapped_column(Time)
    ocr_raw_text: Mapped[str | None] = mapped_column(Text)
    ocr_date_raw: Mapped[str | None] = mapped_column(Text)
    ocr_coordinates_raw: Mapped[str | None] = mapped_column(Text)
    coordinate_format: Mapped[str | None] = mapped_column(String(30))
    coordinate_parse_confidence: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    ocr_address_raw: Mapped[str | None] = mapped_column(Text)
    detected_address: Mapped[str | None] = mapped_column(Text)
    detected_city: Mapped[str | None] = mapped_column(String(300))
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), index=True)
    district_source: Mapped[str | None] = mapped_column(String(20))
    ai_result: Mapped[dict | None] = mapped_column(JSONB)
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_alternatives: Mapped[list] = mapped_column(JSONB, default=list)
    work_type: Mapped[str | None] = mapped_column(String(60), index=True)
    work_type_source: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="RECEIVED", index=True)
    review_reason: Mapped[list] = mapped_column(JSONB, default=list)
    yandex_disk_path: Mapped[str | None] = mapped_column(Text)
    storage_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    processing_error: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)
    cohort: Mapped[str] = mapped_column(String(20), default="test", server_default="test")
    archive_layout: Mapped[str] = mapped_column(
        String(20), default="district_v1", server_default="district_v1"
    )
    classification_mode: Mapped[str] = mapped_column(
        String(30), default="LEGACY_UNKNOWN", server_default="LEGACY_UNKNOWN"
    )
    fixed_work_type_snapshot: Mapped[str | None] = mapped_column(String(60))
    work_rule_version: Mapped[int | None] = mapped_column(Integer)
    district_name_snapshot: Mapped[str | None] = mapped_column(String(120))
    preview_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("inbox_id", "attachment_index"),
        Index(
            "uq_photos_active_cohort_sha256",
            "cohort",
            "sha256",
            unique=True,
            postgresql_where=text("preview_removed_at IS NULL"),
        ),
        CheckConstraint("cohort IN ('test','production')", name="ck_photos_cohort"),
        CheckConstraint("archive_layout IN ('legacy','district_v1')", name="ck_photos_archive_layout"),
        CheckConstraint("latitude BETWEEN -90 AND 90"),
        CheckConstraint("longitude BETWEEN -180 AND 180"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)"),
        CheckConstraint(
            "district_source IS NULL OR district_source IN ('COORDINATES','OPERATOR')",
            name="ck_photos_district_source",
        ),
        CheckConstraint(
            "district_source IS DISTINCT FROM 'OPERATOR' OR district_id IS NOT NULL",
            name="ck_photos_manual_district",
        ),
        CheckConstraint(
            "status IN ('RECEIVED','PROCESSING','ACCEPTED','NEEDS_REVIEW','REJECTED','DUPLICATE','FORWARDED','ERROR','SPAM')",
            name="ck_photos_status",
        ),
        CheckConstraint(
            "work_type_source IS NULL OR work_type_source IN ('AI','OPERATOR','CHAT_FIXED')",
            name="ck_photos_work_type_source",
        ),
        Index("ix_photos_review_order", "status", "received_at", "id"),
    )


class DayClosure(Base):
    __tablename__ = "day_closures"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    photo_count: Mapped[int] = mapped_column(Integer)
    # NULL cohort identifies legacy closures whose scope is known only from receipts.
    cohort: Mapped[str | None] = mapped_column(String(20))
    all_chats: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class DayClosureChat(Base):
    __tablename__ = "day_closure_chats"
    closure_id: Mapped[int] = mapped_column(ForeignKey("day_closures.id"), primary_key=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class DayPhotoReview(Base):
    __tablename__ = "day_photo_reviews"
    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id"), primary_key=True)
    closure_id: Mapped[int] = mapped_column(ForeignKey("day_closures.id"), index=True)
    # A content snapshot, independent of storage sync/version and ML training labels.
    photo_date: Mapped[date] = mapped_column(Date, index=True)
    sha256: Mapped[str | None] = mapped_column(String(64))
    photo_time: Mapped[time | None] = mapped_column(Time)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    district_id: Mapped[int | None] = mapped_column(Integer)
    work_type: Mapped[str | None] = mapped_column(String(60))
    is_defect: Mapped[bool] = mapped_column(Boolean)
    cohort: Mapped[str] = mapped_column(String(20))


class Audit(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    action: Mapped[str] = mapped_column(String(100))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[int] = mapped_column(Integer)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id"), index=True)
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(default=0)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_jobs_ready", "status", "next_run_at"),)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    sample_count: Mapped[int] = mapped_column(Integer)
    labels_snapshot: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    supported_classes: Mapped[list] = mapped_column(JSONB, default=list)
    model_path: Mapped[str | None] = mapped_column(Text)
    model_sha256: Mapped[str | None] = mapped_column(String(64))
    weights_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_one_active_model", "status", unique=True, postgresql_where=text("status = 'ACTIVE'")),
    )


class ReceiverStateFields:
    id: Mapped[int] = mapped_column(primary_key=True)
    token_fingerprint: Mapped[str | None] = mapped_column(String(64))
    marker: Mapped[int | None] = mapped_column(BigInteger)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(100))
    events_received: Mapped[int] = mapped_column(BigInteger, default=0)
    inbox_received: Mapped[int] = mapped_column(BigInteger, default=0)


class MaxReceiverState(ReceiverStateFields, Base):
    __tablename__ = "max_receiver_state"


class PlatformMaxReceiverState(ReceiverStateFields, Base):
    __tablename__ = "platform_max_receiver_state"


class DiskFolderSettings(Base):
    __tablename__ = "disk_folder_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    folder_sets: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    __table_args__ = (CheckConstraint("id=1"),)


class ExchangeState(Base):
    __tablename__ = "exchange_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    source_timezone: Mapped[str] = mapped_column(String(60))
    intake_cohort: Mapped[str] = mapped_column(String(20), default="test")
    production_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_publication_id: Mapped[int | None] = mapped_column(Integer)
    last_production_publication_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("id=1"),
        CheckConstraint("intake_cohort IN ('test','production')"),
    )


class Publication(Base):
    __tablename__ = "publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    cohort: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    index_path: Mapped[str | None] = mapped_column(Text)
    photo_count: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(100))


class ExchangeFile(Base):
    """Immutable data files can outlive, and be shared by, their publishing indexes."""

    __tablename__ = "exchange_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"))
    root: Mapped[str] = mapped_column(Text)
    file_ref: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    __table_args__ = (
        UniqueConstraint("root", "file_ref"),
        CheckConstraint("status IN ('PENDING','AVAILABLE','DELETING','DELETED')"),
        Index("ix_exchange_files_section", "root", "section", "publication_id"),
    )


# Q1-01 additive tenant contract tables; imported to register Base.metadata.
from app.contract_models import (  # noqa: F401
    Contract, ContractRevision, ContractStage, ContractPeriod, ContractWorkLine,
    ContractTerritory, ContractTerritoryDistrict, ContractWorkApplicability,
    PhysicalIndicatorType, ContractPhysicalValue, ContractPriceLine,
)
