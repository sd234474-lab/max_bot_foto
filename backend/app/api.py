import json
import secrets
from datetime import timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from geoalchemy2.shape import from_shape
from shapely.geometry import mapping
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only

from app import (
    appeal_workflow,  # noqa: F401 - registers the public wall and private task routes
    inspection_chats,  # noqa: F401 - registers the inspection conversation routes
    inspection_profile,  # noqa: F401 - registers inspector personal settings
)
from app.appeal_sharing import router as appeal_sharing_router
from app.appeals import organization_router as appeals_organization_router
from app.appeals import owner_router as appeals_owner_router
from app.appeals import public_router as appeals_public_router
from app.appeals_public import AppealBodyLimit
from app.auth import (
    AdminUser,
    AuditReader,
    ChatManager,
    CurrentUser,
    Db,
    DistrictEditor,
    DistrictReader,
    ExchangeManager,
    JobManager,
    LearningManager,
    OriginalsReader,
    PhotoEditor,
    PhotosReader,
    PreviewRemover,
    ReportsExporter,
    ReportsReader,
    check_permission,
    hasher,
    is_platform_owner,
    user_dict,
)
from app.auth import router as auth_router
from app.catalog import Role, Status, work_type_label
from app.chat_access import (
    allowed_chat_ids,
    can_access_chat,
    chat_condition,
    require_all_chats,
    validate_chat_access,
)
from app.chat_senders import photo_sender_name
from app.chat_senders import router as chat_senders_router
from app.config import settings
from app.daily_reports import router as daily_reports_router
from app.day_reviews import router as day_reviews_router
from app.disk_accounts import router as disk_accounts_router
from app.disk_folders import current_disk_roots
from app.disk_folders import router as disk_folders_router
from app.domain import audit, enqueue, now, reassess
from app.exchange_housekeeping import ARCHIVE_JOB_KEY
from app.filter_counts import photo_filter_counts
from app.inspections import admin_router as inspection_admin_router
from app.inspections import owner_router as inspection_owner_router
from app.inspections import router as inspection_router
from app.intake import ingest
from app.integrations import YandexDisk, safe_url
from app.media import media_path
from app.models import (
    Audit,
    Chat,
    DiscoveredChat,
    District,
    ExchangeState,
    Inbox,
    Job,
    LoginSession,
    Photo,
    Publication,
    User,
)
from app.news import router as news_router
from app.news_editor import router as news_editor_router
from app.operator_chats import router as operator_chats_router
from app.organization_context import current_organization, workspace_url
from app.organization_middleware import OrganizationsMiddleware
from app.organizations import router as organizations_router
from app.permissions import (
    DEFAULT_OPERATOR_PERMISSIONS,
    Permission,
    can_retry_job,
    has_permission,
    permission_catalog,
)
from app.photo_ordering import apply_sort_permission, photo_order, photo_position
from app.photo_promotion import production_duplicate
from app.photo_promotion import router as photo_promotion_router
from app.photo_scope import active_cohort, active_cohort_condition
from app.receiver import connection_status, received
from app.reports import export_excel, filtered, photo_dict, photo_list_position, summarize
from app.schemas import (
    ChatInput,
    DistrictInput,
    ExchangeModeInput,
    PhotoBrowseFilters,
    PhotoEdit,
    PhotoFilters,
    PhotoListFilters,
    PhotoMapFilters,
    PhotoVersionInput,
    UserCreate,
    UserUpdate,
    validate_polygon,
)
from app.security_telemetry import SecurityTelemetry
from app.signin import router as signin_router
from app.work_types import catalog_rows, require_work_type, work_catalog
from app.work_types import router as work_types_router
from app.contractor import router as contractor_router

app = FastAPI(
    title="Метка · операторская панель",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(auth_router)
app.include_router(contractor_router)
app.include_router(inspection_router)
app.include_router(inspection_owner_router)
app.include_router(inspection_admin_router)
app.include_router(appeals_owner_router)
app.include_router(appeals_organization_router)
app.include_router(appeals_public_router)
app.include_router(appeal_sharing_router)
app.include_router(signin_router)
app.include_router(operator_chats_router)
app.include_router(chat_senders_router)
app.include_router(work_types_router)
app.include_router(news_router)
app.include_router(news_editor_router)
app.include_router(disk_folders_router)
app.include_router(day_reviews_router)
app.include_router(daily_reports_router)
app.include_router(organizations_router)
app.include_router(disk_accounts_router)
app.include_router(photo_promotion_router)
app.add_middleware(SecurityTelemetry)
app.add_middleware(OrganizationsMiddleware)
app.add_middleware(AppealBodyLimit)
Filters = Annotated[PhotoFilters, Query()]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(_request, _exc):
    return JSONResponse(
        status_code=409,
        content={"detail": "Запись с такими данными уже существует или связана с другими записями"},
    )


@app.get("/api/health")
def health(db: Db):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@app.post("/api/webhooks/max")
async def webhook(request: Request, db: Db):
    expected = settings().max_webhook_secret
    if len(expected) < 32:
        raise HTTPException(503, "Webhook не настроен")
    if not secrets.compare_digest(request.headers.get("x-max-bot-api-secret", ""), expected):
        raise HTTPException(403, "Неверный секрет webhook")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2_000_000:
            raise HTTPException(413, "Слишком большое событие")
    try:
        update = json.loads(raw)
        if not isinstance(update, dict):
            raise ValueError("object required")
        if settings().organizations_enabled:
            from app.organization_intake import ingest_event
            accepted = ingest_event(db, update)
        else:
            accepted = ingest(db, update)
        received(db, 1, int(accepted is not None))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "Некорректное событие MAX") from exc
    db.commit()
    return {"ok": True}


@app.get("/api/max/status")
def max_status(db: Db, user: ChatManager):
    require_all_chats(user)
    return connection_status(db)


@app.get("/api/meta")
def meta(db: Db, user: CurrentUser):
    browse = has_permission(user, Permission.PHOTOS_VIEW) or has_permission(user, Permission.REPORTS_VIEW)
    return {
        "work_types": [{"code": row.code, "name": row.name, "description": row.description}
                       for row in catalog_rows(db)],
        "statuses": list(Status),
        "timezone": settings().app_timezone,
        "yandex_maps_api_key": settings().yandex_maps_api_key
        if has_permission(user, Permission.DISTRICTS_VIEW)
        else "",
        "permission_catalog": permission_catalog(),
        "today": now().astimezone(ZoneInfo(settings().app_timezone)).date(),
        "intake_cohort": active_cohort(db),
        "organization": {"id": current_organization().id, "name": current_organization().name}
        if current_organization() else None,
        "chats": [
            {
                "id": c.id,
                "max_chat_id": str(c.max_chat_id),
                "name": c.name,
                "active": c.active,
                "cv_work_type_enabled": c.cv_work_type_enabled,
                "fixed_work_type_code": c.fixed_work_type_code,
                "work_rule_version": c.work_rule_version,
            }
            for c in db.scalars(select(Chat).where(chat_condition(user, Chat.max_chat_id)).order_by(Chat.name))
            if browse or has_permission(user, Permission.CHATS_MANAGE)
        ],
        "districts": [
            {"id": d.id, "name": d.name, "active": d.active}
            for d in db.scalars(select(District).options(load_only(District.id, District.name, District.active)).order_by(District.name))
            if browse or has_permission(user, Permission.DISTRICTS_VIEW)
        ],
        "operators": [
            {"id": u.id, "name": u.name}
            for u in db.scalars(select(User).order_by(User.name))
            if browse or has_permission(user, Permission.AUDIT_VIEW) or user.role == Role.ADMIN_OPERATOR
        ],
    }


def dictionaries(db):
    return dict(db.execute(select(District.id, District.name)).all()), dict(
        db.execute(select(User.id, User.name)).all()
    )


@app.get("/api/photos")
def photos(db: Db, user: PhotosReader, filters: Annotated[PhotoListFilters, Query()]):
    filters = apply_sort_permission(filters, user)
    check_spam_filter(user, filters, db)
    stmt = filtered(filters, user)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    anchor = db.get(Photo, filters.anchor_id) if filters.anchor_id else None
    if anchor and (
        anchor.preview_removed_at is not None
        or not can_access_chat(db, user, anchor.max_chat_id)
        or (anchor.is_spam and not has_permission(user, Permission.SPAM_VIEW))
    ):
        anchor = None
    page, focus_index = photo_list_position(db, filters, total, anchor, user)
    rows = list(db.scalars(
        stmt.order_by(*photo_order(filters))
        .offset((page - 1) * filters.page_size)
        .limit(filters.page_size)
    ))
    districts, operators = dictionaries(db)
    catalog = work_catalog(db)
    return {
        "items": [photo_dict(p, districts, operators, catalog=catalog) for p in rows],
        "total": total,
        "page": page,
        "page_size": filters.page_size,
        "focus_id": rows[focus_index].id if focus_index is not None and focus_index < len(rows) else None,
    }


@app.get("/api/photos/filter-counts")
def photo_filter_options(db: Db, user: CurrentUser, filters: Filters):
    if not (has_permission(user, Permission.PHOTOS_VIEW) or has_permission(user, Permission.REPORTS_VIEW)):
        raise HTTPException(403, "Нет доступа к этой функции")
    filters = apply_sort_permission(filters, user)
    check_spam_filter(user, filters, db)
    return photo_filter_counts(db, filters, include_spam=has_permission(user, Permission.SPAM_VIEW), user=user)


@app.get("/api/photos/arrivals")
def photo_arrivals(db: Db, user: PhotosReader):
    """Lightweight arrival snapshot for open panels; updates do not create new arrivals."""
    return {
        "photo_ids": list(
            db.scalars(
                select(Photo.id)
                .where(
                    chat_condition(user),
                    active_cohort_condition(),
                    Photo.received_at >= now() - timedelta(days=1),
                    Photo.preview_removed_at.is_(None),
                    Photo.is_spam.is_(False),
                    Photo.status.not_in([Status.REJECTED, Status.DUPLICATE, Status.FORWARDED, Status.SPAM]),
                )
                .order_by(Photo.id)
            )
        )
    }


@app.get("/api/photos/map")
def photo_map(
    db: Db,
    user: PhotosReader,
    filters: Annotated[PhotoMapFilters, Query()],
):
    """Compact map batches, independent of the photo table's 30-row pagination."""
    filters = apply_sort_permission(filters, user)
    check_spam_filter(user, filters, db)
    after_id, snapshot_id = filters.after_id, filters.snapshot_id
    source = filtered(filters, user).with_only_columns(
        Photo.id, Photo.latitude, Photo.longitude, Photo.photo_date, Photo.photo_time,
        Photo.received_at, Photo.chat_name, Photo.work_type, Photo.is_defect, Photo.status, Photo.sha256,
    ).subquery()
    if snapshot_id is None:
        snapshot_id = db.scalar(select(func.max(source.c.id))) or 0
    valid_coordinates = (
        source.c.latitude.between(-90, 90) & source.c.longitude.between(-180, 180)
    )
    summary = None
    if after_id == 0:
        total, located = db.execute(
            select(func.count(), func.count().filter(valid_coordinates))
            .select_from(source).where(source.c.id <= snapshot_id)
        ).one()
        summary = {"total": total, "located": located, "without_coordinates": total - located}
    rows = db.execute(
        select(source).where(
            source.c.id > after_id, source.c.id <= snapshot_id, valid_coordinates,
        ).order_by(source.c.id).limit(1001)
    ).mappings().all()
    catalog = work_catalog(db)
    items = [
        {
            "id": p["id"], "longitude": p["longitude"], "latitude": p["latitude"],
            "photo_date": p["photo_date"], "photo_time": p["photo_time"],
            "received_at": p["received_at"], "chat_name": p["chat_name"],
            "status": p["status"],
            "work_type_name": work_type_label(p["work_type"], p["is_defect"], catalog),
            "preview_url": workspace_url(f"/api/photos/{p['id']}/preview") if p["sha256"] else None,
        }
        for p in rows[:1000]
    ]
    return {
        "items": items, "summary": summary, "snapshot_id": snapshot_id,
        "next_after_id": items[-1]["id"] if len(rows) > 1000 else None,
    }


def check_photo_access(user, photo):
    check_permission(user, Permission.PHOTOS_VIEW)
    if photo.is_spam:
        check_permission(user, Permission.SPAM_VIEW)


def check_spam_filter(user, filters, db):
    if filters.review_day or filters.day_review or filters.day_ai_only:
        check_permission(user, Permission.DAYS_REVIEW)
    if filters.work_type and filters.work_type != "NONE":
        require_work_type(db, filters.work_type)
    if filters.status == Status.SPAM:
        check_permission(user, Permission.SPAM_VIEW)


def require_photo(db, photo_id, user):
    photo = db.get(Photo, photo_id)
    if not photo or photo.preview_removed_at is not None or not can_access_chat(db, user, photo.max_chat_id):
        raise HTTPException(404, "Фотография не найдена")
    check_photo_access(user, photo)
    if photo.cohort != active_cohort(db) and not is_platform_owner(user):
        raise HTTPException(404, "Фотография недоступна в текущем режиме")
    return photo


@app.get("/api/photos/{photo_id}")
def get_photo(photo_id: int, db: Db, user: PhotosReader):
    photo = require_photo(db, photo_id, user)
    districts, operators = dictionaries(db)
    result = photo_dict(photo, districts, operators, detail=True, catalog=work_catalog(db))
    result["sender_name"] = photo_sender_name(db, photo)
    if is_platform_owner(user) and photo.cohort == "test":
        result["production_duplicate_id"] = production_duplicate(db, photo)
    result["history"] = [
        audit_dict(a)
        for a in db.scalars(
            select(Audit).where(Audit.photo_id == photo_id).order_by(Audit.id.desc()).limit(100)
        )
    ]
    return result


@app.get("/api/photos/{photo_id}/preview")
def preview(photo_id: int, db: Db, user: PhotosReader):
    require_photo(db, photo_id, user)
    path = media_path(photo_id, preview=True)
    if not path.is_file():
        raise HTTPException(404, "Превью пока недоступно")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/photos/{photo_id}/next")
def next_photo(photo_id: int, db: Db, user: PhotosReader, filters: Annotated[PhotoBrowseFilters, Query()]):
    filters = apply_sort_permission(filters, user)
    photo = require_photo(db, photo_id, user)
    check_spam_filter(user, filters, db)
    # Match the selected table order, including partial timestamps and ties,
    # without depending on pagination or whether an edit changed the filters.
    candidate = db.scalar(
        filtered(filters, user).with_only_columns(Photo.id).where(
            photo_position(filters, photo)
        ).order_by(*photo_order(filters)).limit(1)
    )
    if candidate is None and filters.status == Status.NEEDS_REVIEW:
        # Finish the remaining rows before returning to unresolved photos above
        # the cursor. Accepted photos have already left this filtered queue.
        candidate = db.scalar(
            filtered(filters, user).with_only_columns(Photo.id).where(Photo.id != photo.id)
            .order_by(*photo_order(filters)).limit(1)
        )
    return {"id": candidate}


@app.get("/api/photos/{photo_id}/original")
def original(photo_id: int, db: Db, user: OriginalsReader):
    photo = require_photo(db, photo_id, user)
    if photo.processing_error == "ORIGINAL_MISSING":
        raise HTTPException(
            409, "Оригинал не найден на Диске. Восстановите файл и нажмите «Проверить снова»."
        )
    if not photo.yandex_disk_path:
        raise HTTPException(409, "Загрузка оригинала ещё не завершена")
    try:
        response = YandexDisk().request("GET", "/resources/download", params={"path": photo.yandex_disk_path})
        if response.status_code == 404:
            raise HTTPException(
                409, "Оригинал не найден на Яндекс.Диске. Проверьте корзину Диска или обратитесь к оператору."
            )
        response.raise_for_status()
        url = safe_url(response.json()["href"], ("yandex.net", "yandex.ru", "yandex.com"))
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(
            502, "Яндекс.Диск временно недоступен или доступ к файлу ограничен. Повторите позже."
        ) from exc
    return RedirectResponse(url, status_code=302)


@app.patch("/api/photos/{photo_id}")
def edit_photo(photo_id: int, body: PhotoEdit, db: Db, user: PhotoEditor):
    db.execute(text("SELECT pg_advisory_xact_lock(7002, :id)"), {"id": photo_id})
    photo = require_photo(db, photo_id, user)
    if not photo.analyzed_at or photo.status in {Status.REJECTED, Status.DUPLICATE, Status.FORWARDED}:
        raise HTTPException(409, "Эту фотографию пока нельзя редактировать")
    if photo.version != body.version:
        raise HTTPException(409, "Фото изменено другим оператором. Обновите карточку.")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if "work_type" in changes:
        require_work_type(db, changes["work_type"])
    if "district_id" in changes:
        district_id = changes["district_id"]
        if district_id is not None:
            district = db.get(District, district_id)
            unchanged = photo.district_source == "OPERATOR" and photo.district_id == district_id
            if district is None or (not district.active and not unchanged):
                raise HTTPException(422, "Выберите существующий активный микрорайон")
        changes["district_source"] = "OPERATOR" if district_id is not None else None
    if changes.get("is_spam"):
        check_permission(user, Permission.SPAM_VIEW)
        changes["work_type"] = None
        changes["is_defect"] = False
    elif changes.get("work_type"):
        # Selecting a real work category restores a previously archived photo.
        changes["is_spam"] = False
    if changes.get("is_defect") and changes.get("is_spam", photo.is_spam):
        raise HTTPException(422, "Неисправность можно отметить только у рабочей фотографии")
    before = {key: getattr(photo, key) for key in changes}
    before.update(
        district_id=photo.district_id,
        district_source=photo.district_source,
        district_name_snapshot=photo.district_name_snapshot,
        status=photo.status,
        work_type_source=photo.work_type_source,
        is_spam=photo.is_spam,
        is_defect=bool(photo.is_defect),
    )
    for key, value in changes.items():
        setattr(photo, key, value)
    if "work_type" in changes:
        photo.work_type_source = "OPERATOR" if photo.work_type or photo.is_spam else None
    photo.reviewed_by, photo.reviewed_at = user.id, now()
    reassess(db, photo)
    photo.version += 1
    photo.storage_status = "PENDING"
    after = {key: getattr(photo, key) for key in before}
    action = "PHOTO_SPAMMED" if photo.is_spam else "PHOTO_RESTORED" if before["is_spam"] else "PHOTO_REVIEWED"
    audit(db, user.id, action, "photo", photo.id, before, after)
    enqueue(db, "SYNC", photo.id, f"sync:{photo.id}:{photo.version}")
    db.commit()
    db.refresh(photo)
    return {
        "id": photo.id,
        "status": photo.status,
        "is_spam": photo.is_spam,
        "is_defect": bool(photo.is_defect),
        "work_type_name": "Спам" if photo.is_spam else work_type_label(photo.work_type, photo.is_defect, work_catalog(db)),
        "version": photo.version,
        "review_reason": photo.review_reason,
        "storage_status": photo.storage_status,
        "district_id": photo.district_id,
        "district_source": photo.district_source,
    }


@app.post("/api/photos/{photo_id}/remove-missing-preview", status_code=202)
def remove_missing_preview(photo_id: int, body: PhotoVersionInput, db: Db, user: PreviewRemover):
    from app.preview_cleanup import known_original_paths

    db.execute(text("SELECT pg_advisory_xact_lock(7002, :id)"), {"id": photo_id})
    photo = db.get(Photo, photo_id)
    if not photo or not can_access_chat(db, user, photo.max_chat_id):
        raise HTTPException(404, "Фотография не найдена")
    check_photo_access(user, photo)
    if photo.preview_removed_at is not None:
        return {"id": photo.id, "removed": True}
    if photo.version != body.version:
        raise HTTPException(409, "Фото изменилось. Обновите карточку.")
    if photo.processing_error != "ORIGINAL_MISSING" or photo.storage_status != "ERROR":
        raise HTTPException(
            409, "Убрать можно только превью с подтверждённой ошибкой отсутствующего оригинала."
        )
    if media_path(photo.id).is_file():
        raise HTTPException(409, "На сервере ещё есть исходный файл. Сначала повторите синхронизацию.")
    try:
        disk = YandexDisk()
        if any(disk.metadata(path) is not None for path in sorted(known_original_paths(db, photo))):
            raise HTTPException(409, "Файл найден на Диске. Повторите синхронизацию вместо удаления превью.")
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(502, "Не удалось проверить наличие оригинала на Диске. Повторите позже.") from exc
    before = {
        "status": photo.status,
        "storage_status": photo.storage_status,
        "processing_error": photo.processing_error,
        "version": photo.version,
    }
    photo.preview_removed_at = now()
    photo.status, photo.storage_status = Status.REJECTED, "NOT_REQUIRED"
    photo.review_reason, photo.processing_error = ["PREVIEW_REMOVED_ORIGINAL_MISSING"], None
    photo.attachment = {}
    photo.version += 1
    db.execute(
        update(Job)
        .where(
            Job.kind == "SYNC",
            Job.object_id == photo.id,
            Job.status.in_(["PENDING", "RUNNING", "BLOCKED"]),
        )
        .values(status="DONE", last_error="PREVIEW_REMOVED")
    )
    if not db.scalar(
        select(Photo.id)
        .where(
            Photo.inbox_id == photo.inbox_id,
            Photo.preview_removed_at.is_(None),
        )
        .limit(1)
    ):
        db.execute(
            update(Job)
            .where(
                Job.kind.in_(["BATCH", "REPLY"]),
                Job.object_id == photo.inbox_id,
                Job.status.in_(["PENDING", "RUNNING", "BLOCKED"]),
            )
            .values(status="DONE", last_error="PREVIEW_REMOVED")
        )
    audit(
        db,
        user.id,
        "PHOTO_PREVIEW_REMOVED",
        "photo",
        photo.id,
        before,
        {"status": photo.status, "review_reason": photo.review_reason, "version": photo.version},
    )
    enqueue(db, "REMOVE_PREVIEW", photo.id, f"remove-preview:{photo.id}")
    db.commit()
    return {"id": photo.id, "removed": True}


@app.post("/api/photos/{photo_id}/retry")
def retry_photo(photo_id: int, db: Db, user: PhotoEditor):
    db.execute(text("SELECT pg_advisory_xact_lock(7002, :id)"), {"id": photo_id})
    photo = require_photo(db, photo_id, user)
    kind, object_id = ("SYNC", photo.id) if photo.analyzed_at else ("BATCH", photo.inbox_id)
    if photo.status in {Status.REJECTED, Status.DUPLICATE, Status.FORWARDED}:
        raise HTTPException(409, "Повторная обработка не требуется")
    enqueue(db, kind, object_id, f"retry:{photo.id}:{secrets.token_hex(8)}")
    audit(db, user.id, "PHOTO_RETRY", "photo", photo.id, None, {"kind": kind})
    db.commit()
    return {"ok": True}


@app.get("/api/reports")
def reports(db: Db, user: ReportsReader, filters: Filters):
    filters = apply_sort_permission(filters, user)
    check_spam_filter(user, filters, db)
    return summarize(db, filters, user)


@app.get("/api/export.xlsx")
def excel(db: Db, user: ReportsExporter, filters: Filters):
    filters = apply_sort_permission(filters, user)
    check_spam_filter(user, filters, db)
    # One snapshot keeps detailed rows and the summary sheets consistent during concurrent review.
    db.commit()
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    data = export_excel(db, filters, user)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="max-photos.xlsx"'},
    )


@app.get("/api/chats")
def chats(db: Db, user: ChatManager):
    return [
        {
            "id": c.id,
            "max_chat_id": str(c.max_chat_id),
            "name": c.name,
            "active": c.active,
            "cv_work_type_enabled": c.cv_work_type_enabled,
            "fixed_work_type_code": c.fixed_work_type_code,
            "work_rule_version": c.work_rule_version,
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }
        for c in db.scalars(select(Chat).where(chat_condition(user, Chat.max_chat_id)).order_by(Chat.name))
    ]


@app.get("/api/chats/discovered")
def discovered_chats(db: Db, user: ChatManager):
    require_all_chats(user)
    unknown = ~select(Chat.id).where(Chat.max_chat_id == DiscoveredChat.max_chat_id).exists()
    rows = db.scalars(
        select(DiscoveredChat)
        .where(unknown)
        .order_by(
            DiscoveredChat.last_seen_at.desc(),
            DiscoveredChat.max_chat_id,
        )
        .limit(100)
    )
    return [
        {
            "max_chat_id": str(chat.max_chat_id),
            "message_preview": chat.message_preview,
            "last_message_at": chat.last_message_at,
            "first_seen_at": chat.first_seen_at,
            "last_seen_at": chat.last_seen_at,
        }
        for chat in rows
    ]


@app.post("/api/chats", status_code=201)
def create_chat(body: ChatInput, db: Db, user: ChatManager):
    if current_organization():
        from app.organizations import require_chat_binding
        require_chat_binding(body.max_chat_id)
    require_all_chats(user)
    require_work_type(db, body.fixed_work_type_code)
    chat = Chat(**body.model_dump())
    db.add(chat)
    db.flush()
    audit(db, user.id, "CHAT_CREATED", "chat", chat.id, None, body.model_dump())
    db.commit()
    return {"id": chat.id}


@app.put("/api/chats/{chat_id}")
def edit_chat(chat_id: int, body: ChatInput, db: Db, user: ChatManager):
    if current_organization():
        from app.organizations import require_chat_binding
        require_chat_binding(body.max_chat_id)
    chat = db.scalar(select(Chat).where(Chat.id == chat_id).with_for_update())
    if not chat or not can_access_chat(db, user, chat.max_chat_id):
        raise HTTPException(404, "Чат не найден")
    if chat.max_chat_id != body.max_chat_id:
        raise HTTPException(422, "Для другого MAX chat ID создайте новый чат")
    require_work_type(db, body.fixed_work_type_code)
    old = {
        key: getattr(chat, key)
        for key in ("name", "active", "cv_work_type_enabled", "fixed_work_type_code", "work_rule_version")
    }
    chat.name, chat.active = body.name, body.active
    # Old API clients editing only the name must not reset a fixed rule.
    if {"cv_work_type_enabled", "fixed_work_type_code"} & body.model_fields_set:
        if (chat.cv_work_type_enabled, chat.fixed_work_type_code) != (
            body.cv_work_type_enabled,
            body.fixed_work_type_code,
        ):
            chat.work_rule_version += 1
        chat.cv_work_type_enabled = body.cv_work_type_enabled
        chat.fixed_work_type_code = body.fixed_work_type_code
    audit(db, user.id, "CHAT_UPDATED", "chat", chat.id, old, body.model_dump())
    db.commit()
    return {"ok": True}


@app.get("/api/districts")
def districts(db: Db, user: DistrictReader):
    rows = db.execute(
        select(District.id, District.name, District.active, func.ST_AsGeoJSON(District.geometry))
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": did,
                "properties": {"id": did, "name": name, "active": active},
                "geometry": json.loads(geo),
            }
            for did, name, active, geo in rows
        ],
    }


def save_district(db, user, body, district=None):
    # Prevent concurrent boundary writes bypassing the overlap check.
    db.execute(text("SELECT pg_advisory_xact_lock(7003, 1)"))
    try:
        polygon = validate_polygon(body.geometry)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    geometry = from_shape(polygon, srid=4326)
    stmt = select(District.id, District.name).where(
        District.active,
        func.ST_Intersects(District.geometry, geometry),
        func.ST_Area(func.ST_Intersection(District.geometry, geometry)) > 0,
    )
    if district:
        stmt = stmt.where(District.id != district.id)
    overlaps = [{"id": did, "name": name} for did, name in db.execute(stmt)] if body.active else []
    if overlaps and not body.confirm_overlap:
        raise HTTPException(
            409, {"message": "Границы пересекаются. Подтвердите сохранение.", "overlaps": overlaps}
        )
    old = None
    if district:
        old = {
            "name": district.name,
            "active": district.active,
            "geometry": json.loads(
                db.scalar(select(func.ST_AsGeoJSON(District.geometry)).where(District.id == district.id))
            ),
        }
    else:
        district = District()
        db.add(district)
    district.name, district.active, district.geometry = body.name, body.active, geometry
    db.flush()
    audit(
        db,
        user.id,
        "DISTRICT_UPDATED" if old else "DISTRICT_CREATED",
        "district",
        district.id,
        old,
        {
            "name": body.name,
            "active": body.active,
            "geometry": mapping(polygon),
            "overlaps_confirmed": bool(overlaps),
        },
    )
    enqueue(db, "REGEO", district.id, f"regeo:{secrets.token_hex(12)}")
    db.commit()
    return {"id": district.id, "overlaps": overlaps}


@app.post("/api/districts", status_code=201)
def create_district(body: DistrictInput, db: Db, user: DistrictEditor):
    return save_district(db, user, body)


@app.put("/api/districts/{district_id}")
def edit_district(district_id: int, body: DistrictInput, db: Db, user: DistrictEditor):
    district = db.get(District, district_id)
    if not district:
        raise HTTPException(404, "Микрорайон не найден")
    return save_district(db, user, body, district)


@app.get("/api/users")
def users(db: Db, user: AdminUser):
    return [user_dict(u) for u in db.scalars(select(User).order_by(User.id))]


@app.post("/api/users", status_code=201)
def create_user(body: UserCreate, db: Db, user: AdminUser):
    ids = None if body.role == Role.ADMIN_OPERATOR else body.allowed_chat_ids
    validate_chat_access(db, ids)
    record = User(
        login=body.login.lower(),
        name=body.name,
        password_hash=hasher.hash(body.password),
        role=body.role,
        active=body.active,
        permissions=[] if body.role == Role.ADMIN_OPERATOR else body.permissions,
        allowed_chat_ids=ids,
    )
    db.add(record)
    db.flush()
    audit(db, user.id, "USER_CREATED", "user", record.id, None, user_dict(record))
    if current_organization():
        from app.signin_directory import register_user
        register_user(current_organization(), record)
    db.commit()
    return user_dict(record)


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, db: Db, user: AdminUser):
    if current_organization() and user_id == current_organization().owner_user_id:
        raise HTTPException(403, "Аккаунт владельца настраивается в общем кабинете")
    db.execute(text("SELECT pg_advisory_xact_lock(7004, 1)"))
    record = db.get(User, user_id)
    if not record:
        raise HTTPException(404, "Пользователь не найден")
    old = user_dict(record)
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    # Explicit NULL means all chats; omission must preserve an existing restriction.
    if "allowed_chat_ids" in body.model_fields_set:
        updates["allowed_chat_ids"] = body.allowed_chat_ids
    target_role = updates.get("role", record.role)
    if target_role == Role.ADMIN_OPERATOR:
        updates["allowed_chat_ids"] = None
    if "allowed_chat_ids" in updates:
        validate_chat_access(db, updates["allowed_chat_ids"])
    if target_role == Role.ADMIN_OPERATOR and ("role" in updates or "permissions" in updates):
        updates["permissions"] = []
    elif record.role == Role.ADMIN_OPERATOR and target_role == Role.OPERATOR:
        updates.setdefault("permissions", list(DEFAULT_OPERATOR_PERMISSIONS))
    for key, value in updates.items():
        if key == "password":
            record.password_hash = hasher.hash(value)
        else:
            setattr(record, key, value)
    db.flush()
    if not db.scalar(
        select(func.count()).select_from(User).where(User.role == Role.ADMIN_OPERATOR, User.active)
    ):
        raise HTTPException(409, "Нельзя заблокировать или понизить последнего администратора")
    permissions_changed = old["permissions"] != user_dict(record)["permissions"]
    chats_changed = old["allowed_chat_ids"] != user_dict(record)["allowed_chat_ids"]
    if permissions_changed or chats_changed or any(key in updates for key in ("password", "active", "role")):
        db.execute(delete(LoginSession).where(LoginSession.user_id == record.id))
    audit(
        db,
        user.id,
        "USER_UPDATED",
        "user",
        record.id,
        old,
        {**user_dict(record), "password_changed": "password" in updates},
    )
    db.commit()
    return user_dict(record)


def audit_dict(a):
    return {
        "id": a.id,
        "user_id": a.user_id,
        "at": a.at,
        "action": a.action,
        "object_type": a.object_type,
        "object_id": a.object_id,
        "photo_id": a.photo_id,
        "old_value": a.old_value,
        "new_value": a.new_value,
    }


@app.get("/api/audit")
def audits(db: Db, user: AuditReader, page: int = Query(1, ge=1)):
    stmt = select(Audit)
    if allowed_chat_ids(user) is not None:
        stmt = stmt.where(
            Audit.photo_id.in_(select(Photo.id).where(chat_condition(user)))
            | ((Audit.object_type == "chat") & Audit.object_id.in_(allowed_chat_ids(user)))
        )
    rows = db.scalars(stmt.order_by(Audit.id.desc()).offset((page - 1) * 100).limit(100))
    return {"items": [audit_dict(a) for a in rows], "page": page}


def job_chat_condition(user):
    if allowed_chat_ids(user) is None:
        return text("true")
    return (
        Job.kind.in_(["SYNC", "REMOVE_PREVIEW"])
        & select(Photo.id).where(Photo.id == Job.object_id, chat_condition(user)).exists()
    ) | (
        Job.kind.in_(["BATCH", "REPLY"])
        & select(Inbox.id).where(Inbox.id == Job.object_id, chat_condition(user, Inbox.max_chat_id)).exists()
    )


@app.get("/api/jobs")
def jobs(db: Db, user: JobManager):
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "object_id": j.object_id,
            "status": j.status,
            "attempts": j.attempts,
            "last_error": j.last_error,
            "next_run_at": j.next_run_at,
            "can_retry": can_retry_job(user, j.kind),
        }
        for j in db.scalars(select(Job).where(Job.status != "DONE", job_chat_condition(user)).order_by(Job.next_run_at).limit(200))
    ]


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Db, user: JobManager):
    job = db.scalar(select(Job).where(Job.id == job_id, job_chat_condition(user)))
    if not job:
        raise HTTPException(404, "Задача не найдена")
    if not can_retry_job(user, job.kind):
        raise HTTPException(403, "Нет доступа к функции этой задачи")
    if job.kind in {"SYNC", "REMOVE_PREVIEW", "BATCH", "REPLY"}:
        condition = (
            Photo.id == job.object_id
            if job.kind in {"SYNC", "REMOVE_PREVIEW"}
            else Photo.inbox_id == job.object_id
        )
        if db.scalar(select(Photo.id).where(condition, Photo.is_spam).limit(1)):
            check_permission(user, Permission.SPAM_VIEW)
    if job.kind == "SYNC":
        db.execute(text("SELECT pg_advisory_xact_lock(7002, :id)"), {"id": job.object_id})
    db.refresh(job, with_for_update=True)
    if job.status not in {"PENDING", "BLOCKED"}:
        raise HTTPException(409, "Задача уже выполняется или завершена. Обновите список.")
    job.status, job.last_error = "PENDING", None
    job.next_run_at = now() - timedelta(seconds=1)
    audit(db, user.id, "JOB_RETRY", "job", job.id, None, {"kind": job.kind})
    db.commit()
    return {"ok": True}


@app.get("/api/ml")
def ml_status(db: Db, user: LearningManager):
    from app.ml import training_status

    return training_status(db)


@app.post("/api/ml/train")
def ml_train(db: Db, user: LearningManager):
    from app.ml import collect_examples, training_plan

    db.execute(text("SELECT pg_advisory_xact_lock(7006, 1)"))
    if db.scalar(select(Job.id).where(Job.kind == "TRAIN", Job.status != "DONE").limit(1)):
        raise HTTPException(409, "Обучение уже в очереди или выполняется")
    examples, _ = collect_examples(db)
    plan = training_plan(examples, work_catalog(db))
    if not plan["ready"]:
        raise HTTPException(409, "; ".join(plan["reasons"]))
    enqueue(db, "TRAIN", 1, "train:manual:" + secrets.token_hex(16))
    audit(db, user.id, "MODEL_TRAINING_REQUESTED", "model", 0, None, {"sample_count": plan["sample_count"]})
    db.commit()
    return {"ok": True}


@app.get("/api/exchange")
def exchange_status(db: Db, user: ExchangeManager):
    from app.exchange import exchange_root

    state = db.get(ExchangeState, 1)
    cfg = current_disk_roots(db)
    counts = dict(db.execute(select(Photo.cohort, func.count()).group_by(Photo.cohort)).all())
    return {
        "intake_cohort": state.intake_cohort,
        "production_started_at": state.production_started_at,
        "source_timezone": state.source_timezone,
        "roots": {c: exchange_root(state, c, cfg) for c in ("test", "production")},
        "photo_root": f"disk:/{cfg.photo_root}",
        "training_root": f"disk:/{cfg.training_root}",
        "spam_root": f"disk:/{cfg.spam_root}",
        "organizing": db.scalar(select(Job.status).where(Job.dedupe_key == ARCHIVE_JOB_KEY)),
        "counts": {c: counts.get(c, 0) for c in ("test", "production")},
        "publications": [
            {
                key: getattr(p, key)
                for key in (
                    "id",
                    "cohort",
                    "status",
                    "created_at",
                    "as_of",
                    "finished_at",
                    "photo_count",
                    "index_path",
                    "last_error",
                )
            }
            for p in db.scalars(select(Publication).order_by(Publication.id.desc()).limit(30))
        ],
    }


@app.put("/api/exchange/mode")
def exchange_mode(body: ExchangeModeInput, db: Db, user: ExchangeManager):
    state = db.scalar(select(ExchangeState).where(ExchangeState.id == 1).with_for_update())
    before = state.intake_cohort
    state.intake_cohort = body.cohort
    if body.cohort == "production" and state.production_started_at is None:
        state.production_started_at = now()
    audit(
        db, user.id, "EXCHANGE_MODE_CHANGED", "exchange_state", 1, {"cohort": before}, {"cohort": body.cohort}
    )
    db.commit()
    return {"intake_cohort": body.cohort}


@app.post("/api/exchange/publish", status_code=202)
def exchange_publish(body: ExchangeModeInput, db: Db, user: ExchangeManager):
    # Prevent double clicks/concurrent requests from creating duplicate pending jobs.
    db.execute(text("SELECT pg_advisory_xact_lock(7009, 1)"))
    pub = db.scalar(
        select(Publication)
        .where(Publication.cohort == body.cohort, Publication.status.in_(["PENDING", "UPLOADING", "RETRY"]))
        .order_by(Publication.id.desc())
        .limit(1)
    )
    if pub is None:
        pub = Publication(cohort=body.cohort, requested_by=user.id)
        db.add(pub)
        db.flush()
        enqueue(db, "PUBLISH", pub.id, f"publish:{pub.id}")
        audit(
            db, user.id, "EXCHANGE_PUBLICATION_REQUESTED", "publication", pub.id, None, {"cohort": pub.cohort}
        )
    db.commit()
    return {"id": pub.id, "status": pub.status, "cohort": pub.cohort}


@app.post("/api/exchange/organize", status_code=202)
def exchange_organize(db: Db, user: ExchangeManager):
    db.execute(text("SELECT pg_advisory_xact_lock(7009, 1)"))
    job = db.scalar(select(Job).where(Job.dedupe_key == ARCHIVE_JOB_KEY))
    if job is None:
        enqueue(db, "ARCHIVE_ORGANIZE", 1, ARCHIVE_JOB_KEY)
    elif job.status != "RUNNING":
        job.status, job.last_error, job.next_run_at = "PENDING", None, now()
    audit(db, user.id, "ARCHIVE_ORGANIZATION_REQUESTED", "exchange_state", 1, None, {})
    db.commit()
    return {"ok": True}
