import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from psycopg import Error as PsycopgError

from pacemaker_registry.db import (
    Registry,
    list_events,
    get_event,
    save_event_rating,
    save_event_pace_rating,
    get_result_details,
    check_database,
    get_event_target_time_type,
    initialize_database,
    load_registry,
    save_race_result,
)
from pacemaker_registry.rating import (
    default_event_rating, parse_event_rating, rating_color_style,
    PaceRatingConfig, parse_pace_rating,
)
from pacemaker_registry.russiarunning import (
    InvalidResultUrl,
    RaceResult,
    RussiaRunningError,
    guess_target_time,
)
from pacemaker_registry.sources import load_race_result


PACKAGE_DIR = Path(__file__).resolve().parent
TARGET_TIME_PATTERN = re.compile(r"^[0-9]{1,2}:[0-5][0-9]$")


def _apply_event_target_time_type(result: RaceResult) -> RaceResult:
    try:
        target_time_type = get_event_target_time_type(result.source_event_id)
    except (PsycopgError, RuntimeError):
        return result
    if not target_time_type:
        return result
    return replace(
        result,
        target_time=guess_target_time(result.chip_time, target_time_type),
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield

app = FastAPI(
    title="Pacemaker Registry",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
def analytics_context(request: Request) -> dict:
    counter_id = os.getenv("YANDEX_METRICA_ID", "")
    return {"metrica_id": counter_id if re.fullmatch(r"[1-9][0-9]{0,14}", counter_id) else None}


templates = Jinja2Templates(
    directory=PACKAGE_DIR / "templates", context_processors=[analytics_context],
)
templates.env.filters["rating_color"] = rating_color_style


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    event_id: int | None = None,
    include_splits: bool = True,
    min_events: Annotated[int, Query(ge=1, le=10000)] = 1,
) -> HTMLResponse:
    registry_error = None
    try:
        registry = load_registry(
            event_id if event_id and event_id > 0 else None,
            include_splits=include_splits,
            min_events=min_events,
        )
    except (PsycopgError, RuntimeError):
        registry = Registry((), 0, 0, include_splits=include_splits, min_events=min_events)
        registry_error = "Не удалось загрузить реестр. Обновите страницу чуть позже."
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"registry": registry, "registry_error": registry_error},
    )


def _load_event_or_404(event_id: int):
    try:
        event = get_event(event_id)
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить соревнование. Попробуйте позже.") from error
    if event is None:
        raise HTTPException(404, "Соревнование не найдено.")
    return event


def _event_response(request, event, *, values=None, pace_values=None, error=None, saved=False, status_code=200):
    return templates.TemplateResponse(
        request=request, name="event.html",
        context={
            "event": event,
            "values": values if values is not None else asdict(event.formula),
            "defaults": asdict(default_event_rating(event.target_time_type)),
            "pace_values": pace_values if pace_values is not None else asdict(event.pace_formula),
            "pace_defaults": asdict(PaceRatingConfig()),
            "error": error, "saved": saved,
        },
        status_code=status_code,
    )


@app.get("/events", response_class=HTMLResponse)
def events_page(request: Request):
    try:
        events = list_events()
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить соревнования. Попробуйте позже.") from error
    return templates.TemplateResponse(
        request=request, name="events.html",
        context={"events": events, "configs": {event.id: asdict(event.formula) for event in events}},
    )


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_page(request: Request, event_id: int, saved: bool = False):
    return _event_response(request, _load_event_or_404(event_id), saved=saved)


@app.post("/events/{event_id}/rating", response_class=HTMLResponse)
async def update_event_rating(request: Request, event_id: int):
    event = await run_in_threadpool(_load_event_or_404, event_id)
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
    event = await run_in_threadpool(_load_event_or_404, event_id)
    values = dict(await request.form())
    try:
        config = parse_pace_rating(values)
    except ValueError as error:
        return _event_response(request, event, pace_values=values, error=str(error), status_code=422)
    try:
        updated = await run_in_threadpool(save_event_pace_rating, event_id, config)
    except (PsycopgError, RuntimeError):
        return _event_response(
            request, event, pace_values=values,
            error="Не удалось сохранить оценку темпа. Настройки остались в форме — попробуйте ещё раз.",
            status_code=503,
        )
    if not updated:
        raise HTTPException(404, "Соревнование не найдено.")
    return RedirectResponse(f"/events/{event_id}?saved=true#pace-rating", status_code=303)


def _result_source_links(source_url: str) -> tuple[str | None, str | None]:
    """Only turn supported public result URLs into clickable external links."""
    try:
        url = urlsplit(source_url)
    except ValueError:
        return None, None
    if url.scheme != "https":
        return None, None
    base = f"https://{url.netloc}"
    if url.netloc == "results.russiarunning.com":
        match = re.fullmatch(r"/participant/([\w-]+)/([\w-]+)/([\w-]+)/?", url.path)
        if match:
            return base + url.path, f"{base}/event/{match[1]}/results/{match[2]}"
    elif url.netloc == "results.runc.run":
        match = re.fullmatch(r"/event/([\w-]+)/result/(\d+)/?", url.path)
        if match:
            return base + url.path, f"{base}/event/{match[1]}/"
    return None, None


@app.get("/results/{result_id}", response_class=HTMLResponse)
def result_page(
    request: Request, result_id: int, include_splits: bool = True,
    event_id: Annotated[int, Query(ge=0)] = 0,
    min_events: Annotated[int, Query(ge=1, le=10000)] = 1,
):
    try:
        details = get_result_details(result_id, include_splits=include_splits)
    except (PsycopgError, RuntimeError) as error:
        raise HTTPException(503, "Не удалось загрузить результат. Попробуйте позже.") from error
    if details is None:
        raise HTTPException(404, "Результат не найден.")
    source_url, source_event_url = _result_source_links(details.source_url)
    return templates.TemplateResponse(
        request=request, name="result.html",
        context={
            "details": details, "result": details.result, "event": details.event,
            "source_url": source_url, "source_event_url": source_event_url,
            "finish_config": asdict(details.event.formula),
            "pace_config": asdict(details.event.pace_formula), "include_splits": include_splits,
            "back_url": "/?" + urlencode({"event_id": event_id, "include_splits": str(include_splits).lower(), "min_events": min_events}),
        },
    )


@app.get("/add", response_class=HTMLResponse)
async def add_form(
    request: Request,
    result_url: Annotated[str | None, Query(max_length=500)] = None,
) -> HTMLResponse:
    result = None
    error = None
    status_code = 200
    submitted_url = result_url or ""

    if result_url:
        try:
            result = await load_race_result(result_url)
            result = _apply_event_target_time_type(result)
        except InvalidResultUrl as exception:
            error = str(exception)
            status_code = 422
        except RussiaRunningError as exception:
            error = str(exception)
            status_code = 502

    return templates.TemplateResponse(
        request=request,
        name="add.html",
        context={
            "result": result,
            "error": error,
            "result_url": submitted_url,
            "saved_message": None,
        },
        status_code=status_code,
    )


@app.post("/add", response_class=HTMLResponse)
async def add_result(
    request: Request,
    result_url: Annotated[str, Form(min_length=1, max_length=500)],
) -> HTMLResponse:
    result = None
    error = None
    status_code = 200
    try:
        result = await load_race_result(result_url)
        result = _apply_event_target_time_type(result)
    except InvalidResultUrl as exception:
        error = str(exception)
        status_code = 422
    except RussiaRunningError as exception:
        error = str(exception)
        status_code = 502

    return templates.TemplateResponse(
        request=request,
        name="add.html",
        context={
            "result": result,
            "error": error,
            "result_url": result_url,
            "saved_message": None,
        },
        status_code=status_code,
    )


@app.post("/results", response_class=HTMLResponse)
async def save_result(
    request: Request,
    result_url: Annotated[str, Form(min_length=1, max_length=500)],
    target_time: Annotated[str, Form(min_length=4, max_length=5)],
) -> HTMLResponse:
    result = None
    error = None
    saved_message = None
    result_created = False
    status_code = 200

    if not TARGET_TIME_PATTERN.fullmatch(target_time):
        error = "Время на флаге должно быть в формате ЧЧ:ММ."
        status_code = 422
    else:
        try:
            result = await load_race_result(result_url)
            result = replace(result, target_time=target_time)
            created = save_race_result(result, target_time)
            result_created = created
            saved_message = (
                "Результат сохранён в реестр."
                if created
                else "Этот результат уже был сохранён — дубликат не добавлен."
            )
            result = None
            result_url = ""
        except InvalidResultUrl as exception:
            error = str(exception)
            status_code = 422
        except RussiaRunningError as exception:
            error = str(exception)
            status_code = 502
        except (PsycopgError, RuntimeError):
            error = "Не удалось сохранить результат. Попробуйте ещё раз."
            status_code = 503

    return templates.TemplateResponse(
        request=request,
        name="add.html",
        context={
            "result": result,
            "error": error,
            "result_url": result_url,
            "saved_message": saved_message,
            "result_created": result_created,
        },
        status_code=status_code,
    )


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
