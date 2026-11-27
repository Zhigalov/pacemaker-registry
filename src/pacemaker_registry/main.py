import os
import re
from functools import lru_cache
from hashlib import sha256
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from psycopg import Error as PsycopgError

from pacemaker_registry import auth
from pacemaker_registry.filters import Filters, RegistryFilters
from pacemaker_registry.db import (
    Registry, REGISTRY_PAGE_SIZE,
    list_events,
    get_event,
    save_event_rating,
    save_event_pace_rating,
    get_result_details,
    update_result_target_time,
    check_database,
    get_event_by_source,
    get_draft_results,
    save_event_draft,
    EventAccessError,
    load_registry,
    save_race_result,
)
from pacemaker_registry.rating import (
    default_event_rating, parse_event_rating, rating_color_style,
    PaceRatingConfig, parse_pace_rating, RANKING_FORMULAS,
)
from pacemaker_registry.imports import InvalidResultUrl, ResultImportError, guess_target_time
from pacemaker_registry.models import EventStatus
from pacemaker_registry.sources import load_race_result, result_source_links as _result_source_links
from pacemaker_registry.times import format_target_time, normalize_target_time
from pacemaker_registry.avatars import avatar_url


PACKAGE_DIR = Path(__file__).resolve().parent


@lru_cache
def asset_url(name: str) -> str:
    version = sha256((PACKAGE_DIR / 'static' / name).read_bytes()).hexdigest()[:12]
    return f'/static/{name}?v={version}'


app = FastAPI(
    title="Pacemaker Registry",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")


@app.middleware("http")
async def account_session(request: Request, call_next):
    enabled = auth.oauth_settings() is not None
    has_session = enabled and auth.SESSION_COOKIE in request.cookies
    if has_session and not request.url.path.startswith(("/static/", "/auth/")):
        try:
            request.state.account = await run_in_threadpool(auth.current_account, request)
        except (PsycopgError, RuntimeError):
            request.state.account = None
    response = await call_next(request)
    if enabled and response.headers.get("content-type", "").startswith("text/html"):
        response.headers.add_vary_header("Cookie")
        if has_session:
            response.headers["Cache-Control"] = "private, no-store"
    return response


def analytics_context(request: Request) -> dict:
    counter_id = os.getenv("YANDEX_METRICA_ID", "")
    return {"metrica_id": counter_id if re.fullmatch(r"[1-9][0-9]{0,14}", counter_id) else None}


templates = Jinja2Templates(
    directory=PACKAGE_DIR / "templates", context_processors=[analytics_context, auth.auth_context],
)
templates.env.globals["asset_url"] = asset_url
templates.env.globals["default_filters"] = RegistryFilters()
templates.env.filters["rating_color"] = rating_color_style
templates.env.filters["avatar_url"] = avatar_url
templates.env.filters["flag_time"] = format_target_time

from pacemaker_registry.admin import create_admin_router
from pacemaker_registry.profile_routes import create_profile_router
from pacemaker_registry.profile_linking import create_profile_link_router
app.include_router(create_profile_router(templates))
app.include_router(create_profile_link_router(templates))
from pacemaker_registry.reviews import router as reviews_router
app.include_router(reviews_router)
app.include_router(auth.router)
app.include_router(create_admin_router(templates))


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    filters: Filters,
    page: Annotated[int, Query(ge=1)] = 1,
    fragment: bool = False,
) -> HTMLResponse:
    registry_error = None
    try:
        registry = load_registry(
            filters.event_id or None, min_events=filters.min_events,
            distance_km=filters.distance_km, search=filters.q, page=page,
            rating_formula=RANKING_FORMULAS[filters.formula][1],
        )
    except (PsycopgError, RuntimeError):
        if fragment:
            raise HTTPException(503, "Не удалось загрузить следующую страницу.")
        registry = Registry((), 0, 0, min_events=filters.min_events, search=filters.q)
        registry_error = "Не удалось загрузить реестр. Обновите страницу чуть позже."
    page, page_count = registry.page, registry.page_count
    filters = replace(filters, event_id=registry.selected_event_id or 0)
    return templates.TemplateResponse(
        request=request, name="registry_fragment.html" if fragment else "index.html",
        context={"registry": registry, "registry_error": registry_error,
                 "visible_pacemakers": registry.pacemakers, "filters": filters,
                 "ranking_formulas": RANKING_FORMULAS,
                 "rank_offset": (page - 1) * REGISTRY_PAGE_SIZE,
                 "all_events_url": replace(filters, event_id=0).url(),
                 "page": page, "page_count": page_count,
                 "previous_page_url": filters.url(page=page - 1),
                 "next_page_url": filters.url(page=page + 1),
                 "clear_search_url": filters.without_search().url()},
    )


def _load_event_or_404(request: Request, event_id: int):
    try:
        event = get_event(event_id)
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить соревнование. Попробуйте позже.") from error
    if event is None or (event.is_draft and not _can_edit_event(request, event)):
        raise HTTPException(404, "Соревнование не найдено.")
    return event


def _can_edit_event(request, event):
    account = auth.request_account(request)
    return account is not None and account.can_edit_event(event)


def _require_event_editor(account, event):
    if not account.can_edit_event(event):
        raise HTTPException(403, "Настройки доступны капитану этого соревнования или администратору.")


def _event_response(request, event, *, values=None, pace_values=None, splits_enabled=None,
                    error=None, saved=False, notice=None, status_code=200, result=None, result_url="",
                    target_time_type=None, draft_settings=False):
    try:
        draft_results = get_draft_results(event) if event.is_draft and not draft_settings else ()
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить результаты черновика.") from error
    return templates.TemplateResponse(
        request=request, name="event.html",
        context={
            "event": event, "can_edit_event": _can_edit_event(request, event), "draft_results": draft_results,
            "values": values if values is not None else asdict(event.formula),
            "defaults": asdict(default_event_rating(event.target_time_type)),
            "pace_values": pace_values if pace_values is not None else asdict(event.pace_formula),
            "pace_defaults": asdict(PaceRatingConfig()),
            "splits_enabled": event.splits_enabled if splits_enabled is None else splits_enabled,
            "error": error, "saved": saved, "notice": notice, "result": result, "result_url": result_url,
            "target_time_type": target_time_type or event.target_time_type,
            "draft_settings": draft_settings,
            "draft_defaults": {kind: asdict(default_event_rating(kind)) for kind in ("sub_minute", "round")}
                              if draft_settings else None,
        },
        status_code=status_code,
    )

@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    examples = ((10, 10, 10, 9), (10, 10))
    performances = [tuple(SimpleNamespace(rating=score) for score in scores) for scores in examples]
    formulas = {
        key: (label, *(calculate(results) for results in performances))
        for key, (label, calculate) in RANKING_FORMULAS.items()
    }
    return templates.TemplateResponse(
        request=request, name="about.html", context={"examples": examples, "formulas": formulas},
    )


@app.get("/events", response_class=HTMLResponse)
def events_page(request: Request, status: EventStatus | Literal["all"] = "all"):
    account = auth.request_account(request)
    try:
        events = list_events(account, None if status == "all" else status)
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить соревнования. Попробуйте позже.") from error
    events = sorted(events, key=lambda event: round(event.team_rating, 1) if event.team_rating is not None else -1,
                    reverse=True)
    return templates.TemplateResponse(
        request=request, name="events.html",
        context={"events": events, "status": status, "show_status_filter": account is not None},
    )


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_page(request: Request, event_id: int, saved: bool = False,
               notice: Literal["added", "duplicate", "draft", "published"] | None = None):
    return _event_response(request, _load_event_or_404(request, event_id), saved=saved, notice=notice)


@app.get("/events/{event_id}/draft", response_class=HTMLResponse)
def draft_settings_page(request: Request, event_id: int, notice: Literal["draft"] | None = None):
    event = _load_event_or_404(request, event_id)
    if not event.is_draft:
        return RedirectResponse(f"/events/{event_id}", status_code=303)
    return _event_response(request, event, notice=notice, draft_settings=True)


@app.post("/events/{event_id}/draft")
async def update_draft(
    request: Request, event_id: int,
    action: Annotated[Literal["save", "publish"], Form()],
    target_time_type: Annotated[Literal["sub_minute", "round"] | None, Form()] = None,
):
    account = auth.require_write(request)
    event = await run_in_threadpool(_load_event_or_404, request, event_id)
    _require_event_editor(account, event)
    if not event.is_draft:
        raise HTTPException(409, "Соревнование уже опубликовано.")
    fields = await request.form()
    values = {key: fields.get(key, "") for key in asdict(event.formula)}
    enabled = fields.get("splits_enabled") == "true"
    pace_values = {key: fields.get(key, "") for key in asdict(event.pace_formula)} if enabled else None
    error = None
    publish = action == "publish"
    try:
        if publish and target_time_type is None:
            raise ValueError("Выберите тип времени на флаге перед публикацией.")
        config = parse_event_rating(values)
        # Keep a default formula dynamic when only the flag type changed.
        if config == default_event_rating(target_time_type) or (event.rating_config is None and config == event.formula):
            config = None
        pace_config = parse_pace_rating(pace_values) if enabled else None
        updated = await run_in_threadpool(
            save_event_draft, event.id, account, target_time_type,
            publish=publish, config=config, pace_config=pace_config, splits_enabled=enabled,
        )
    except ValueError as exception:
        error, status_code = str(exception), 422
    except (PsycopgError, RuntimeError):
        error, status_code = "Не удалось сохранить соревнование.", 503
    if error:
        return await run_in_threadpool(_event_response, request, event, values=values, pace_values=pace_values,
                                       splits_enabled=enabled, target_time_type=target_time_type, error=error,
                                       draft_settings=True, status_code=status_code)
    if not updated:
        raise HTTPException(409, "Черновик уже изменён. Обновите страницу.")
    location = f"/events/{event_id}?notice=published" if publish else f"/events/{event_id}/draft?notice=draft"
    return RedirectResponse(location, status_code=303)


@app.post("/events/{event_id}/rating", response_class=HTMLResponse)
async def update_event_rating(request: Request, event_id: int):
    account = auth.require_write(request)
    event = await run_in_threadpool(_load_event_or_404, request, event_id)
    _require_event_editor(account, event)
    if event.is_draft:
        raise HTTPException(409, "Сохраните формулу на странице черновика.")
    form = await request.form()
    values = dict(form)
    try:
        config = parse_event_rating(values)
    except ValueError as error:
        return _event_response(request, event, values=values, error=str(error), status_code=422)
    try:
        updated = await run_in_threadpool(save_event_rating, event_id, config)
    except (PsycopgError, RuntimeError):
        return _event_response(
            request, event, values=values,
            error="Не удалось сохранить формулу. Настройки остались в форме — попробуйте ещё раз.",
            status_code=503,
        )
    if not updated:
        raise HTTPException(404, "Соревнование не найдено.")
    return RedirectResponse(f"/events/{event_id}?saved=true", status_code=303)


@app.post("/events/{event_id}/pace-rating", response_class=HTMLResponse)
async def update_event_pace_rating(request: Request, event_id: int):
    account = auth.require_write(request)
    event = await run_in_threadpool(_load_event_or_404, request, event_id)
    _require_event_editor(account, event)
    if event.is_draft:
        raise HTTPException(409, "Сохраните настройки на странице черновика.")
    values = dict(await request.form())
    submitted = values.pop("splits_enabled", None)
    if submitted not in (None, "true", "false"):
        raise HTTPException(422, "Некорректный режим учёта отсечек.")
    enabled = None if submitted is None else submitted == "true"
    try:
        config = parse_pace_rating(values) if enabled is None or (enabled and values) else None
    except ValueError as error:
        return _event_response(request, event, pace_values=values, splits_enabled=enabled, error=str(error), status_code=422)
    try:
        updated = await run_in_threadpool(save_event_pace_rating, event_id, config, splits_enabled=enabled)
    except (PsycopgError, RuntimeError):
        return _event_response(
            request, event, pace_values=values or None, splits_enabled=enabled,
            error="Не удалось сохранить оценку темпа. Настройки остались в форме — попробуйте ещё раз.",
            status_code=503,
        )
    if not updated:
        raise HTTPException(404, "Соревнование не найдено.")
    return RedirectResponse(f"/events/{event_id}?saved=true#pace-rating", status_code=303)


def _load_result_or_404(request: Request, result_id: int):
    try:
        details = get_result_details(result_id)
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить результат. Попробуйте позже.") from error
    if details is None or (details.event.is_draft and not _can_edit_event(request, details.event)):
        raise HTTPException(404, "Результат не найден.")
    return details


def _result_response(request, details, filters, *,
                     target_time=None, error=None, saved=False, status_code=200):
    source_url, source_event_url = _result_source_links(details.source_url)
    return templates.TemplateResponse(
        request=request, name="result.html",
        context={
            "details": details, "result": details.result, "event": details.event,
            "can_edit_event": _can_edit_event(request, details.event),
            "source_url": source_url, "source_event_url": source_event_url,
            "finish_config": asdict(details.event.formula),
            "pace_config": asdict(details.event.pace_formula),
            "back_url": f"/events/{details.event.id}#team" if details.event.is_draft else filters.url(), "filters": filters,
            "edit_url": filters.url(f"/results/{details.result.id}/target-time"),
            "target_time_value": details.result.target_time_display if target_time is None else target_time,
            "error": error, "saved": saved,
        },
        status_code=status_code,
    )


@app.get("/results/{result_id}", response_class=HTMLResponse)
def result_page(
    request: Request, result_id: int,
    filters: Filters,
    saved: bool = False,
):
    return _result_response(request, _load_result_or_404(request, result_id),
                            filters, saved=saved)


@app.post("/results/{result_id}/target-time", response_class=HTMLResponse)
def edit_result_target_time(
    request: Request, result_id: int,
    target_time: Annotated[str, Form(max_length=20)],
    previous_target_time: Annotated[str, Form(max_length=20)],
    filters: Filters,
    target_time_format: Annotated[Literal["duration"] | None, Form()] = None,
):
    account = auth.require_write(request)
    details = _load_result_or_404(request, result_id)
    _require_event_editor(account, details.event)
    target_time = target_time.strip()
    try:
        target_time = normalize_target_time(target_time, minutes_seconds=target_time_format == "duration")
    except ValueError as error:
        return _result_response(
            request, details, filters, target_time=target_time,
            error=str(error), status_code=422,
        )
    try:
        updated = update_result_target_time(result_id, target_time, previous_target_time)
    except (PsycopgError, RuntimeError):
        return _result_response(
            request, details, filters, target_time=format_target_time(target_time),
            error="Не удалось сохранить время. Проверьте текущее значение и попробуйте ещё раз.", status_code=503,
        )
    if not updated:
        details = _load_result_or_404(request, result_id)
        return _result_response(
            request, details, filters, target_time=format_target_time(target_time),
            error="Время уже изменено в другой вкладке. Сравните с текущей целью и подтвердите сохранение ещё раз.",
            status_code=409,
        )
    return RedirectResponse(filters.url(f"/results/{result_id}", saved='true'), status_code=303)


@app.get("/add", response_class=HTMLResponse)
async def add_form(
    request: Request,
    result_url: Annotated[str | None, Query(max_length=500)] = None,
) -> HTMLResponse:
    return await _preview_result(request, result_url or "")


def _add_response(request, *, result_url="", result=None, event=None, error=None, status_code=200):
    return templates.TemplateResponse(
        request=request, name="add.html", status_code=status_code,
        context={"result": result, "error": error, "result_url": result_url, "event": event},
    )


async def _preview_result(request: Request, result_url: str, *, draft=None) -> HTMLResponse:
    result = event = error = None
    status_code = 200
    if result_url:
        try:
            result = await load_race_result(result_url)
            if draft is not None and result.source_event_id != draft.source_event_id:
                raise ValueError("Результат относится к другому соревнованию.")
            event = draft or await run_in_threadpool(get_event_by_source, result.source_event_id)
            if event and not _can_edit_event(request, event):
                message = "Соревнование недоступно для добавления." if event.is_draft else "Добавить результат может капитан или администратор."
                return _add_response(request, result_url=result_url, error=message,
                                     event=None if event.is_draft else event, status_code=403)
            if event and event.target_time_type:
                result = replace(result, target_time=guess_target_time(result.chip_time, event.target_time_type))
        except (InvalidResultUrl, ValueError) as exception:
            error, status_code = str(exception), 422
        except ResultImportError as exception:
            error, status_code = str(exception), 502
        except (PsycopgError, RuntimeError):
            error, status_code = "Не удалось загрузить соревнование. Попробуйте ещё раз.", 503
    if error:
        result = None
    if draft is not None:
        return await run_in_threadpool(_event_response, request, draft, result=result,
                                       result_url=result_url, error=error, status_code=status_code)
    return _add_response(request, result_url=result_url, result=result, event=event,
                         error=error, status_code=status_code)


@app.post("/add", response_class=HTMLResponse)
async def add_result(
    request: Request,
    result_url: Annotated[str, Form(min_length=1, max_length=500)],
) -> HTMLResponse:
    return await _preview_result(request, result_url)


@app.post("/events/{event_id}/preview-result", response_class=HTMLResponse)
async def preview_draft_result(
    request: Request, event_id: int,
    result_url: Annotated[str, Form(min_length=1, max_length=500)],
):
    account = auth.require_write(request)
    event = await run_in_threadpool(_load_event_or_404, request, event_id)
    _require_event_editor(account, event)
    if not event.is_draft:
        raise HTTPException(409, "Соревнование уже опубликовано. Добавьте результат через форму импорта.")
    return await _preview_result(request, result_url, draft=event)


@app.post("/results", response_class=HTMLResponse)
async def save_result(
    request: Request,
    result_url: Annotated[str, Form(min_length=1, max_length=500)],
    target_time: Annotated[str, Form(max_length=20)],
    target_time_format: Annotated[Literal["duration"] | None, Form()] = None,
    event_id: Annotated[int | None, Form(gt=0)] = None,
) -> HTMLResponse:
    account = auth.require_write(request)
    event = None
    if event_id is not None:
        event = await run_in_threadpool(_load_event_or_404, request, event_id)
        _require_event_editor(account, event)
    try:
        target_time = normalize_target_time(target_time, minutes_seconds=target_time_format == "duration")
        result = await load_race_result(result_url)
        saved = await run_in_threadpool(save_race_result, result, target_time, account, event_id=event_id)
    except EventAccessError as exception:
        if exception.event.is_draft:
            raise HTTPException(404, "Соревнование не найдено.") from None
        event = exception.event
        error, status_code = "Добавить результат может капитан или администратор.", 403
    except (InvalidResultUrl, ValueError) as exception:
        error, status_code = str(exception), 422
    except ResultImportError as exception:
        error, status_code = str(exception), 502
    except (PsycopgError, RuntimeError):
        error, status_code = "Не удалось сохранить результат. Попробуйте ещё раз.", 503
    else:
        notice = "added" if saved.created else "duplicate"
        anchor = "#team" if saved.event.is_draft and event_id is not None else ""
        return RedirectResponse(f"/events/{saved.event.id}?notice={notice}{anchor}", status_code=303)
    if event and event.is_draft:
        return await run_in_threadpool(_event_response, request, event, error=error,
                                       result_url=result_url, status_code=status_code)
    return _add_response(request, result_url=result_url, event=event, error=error, status_code=status_code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    try:
        check_database()
    except (PsycopgError, RuntimeError):
        return JSONResponse(status_code=503, content={"status": "unavailable"})

    return JSONResponse(content={"status": "ok"})
