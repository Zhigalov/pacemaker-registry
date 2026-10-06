import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg import Error as PsycopgError

from pacemaker_registry.db import (
    RATING_MODE_AUTOMATIC,
    RATING_MODE_CUSTOM,
    RATING_MODE_STRICT,
    RATING_MODE_SYMMETRIC,
    CustomRatingConfig,
    Registry,
    calculate_rating_for_difference,
    check_database,
    initialize_database,
    load_registry,
    parse_custom_rating_config,
    save_race_result,
    serialize_custom_rating_points,
)
from pacemaker_registry.russiarunning import (
    InvalidResultUrl,
    RussiaRunningError,
    load_race_result,
)


PACKAGE_DIR = Path(__file__).resolve().parent
TARGET_TIME_PATTERN = re.compile(r"^[0-9]{1,2}:[0-5][0-9]$")


def _rating_chart_points(
    rating_mode: str,
    custom_rating: CustomRatingConfig = CustomRatingConfig(),
) -> str:
    points = []
    for difference in range(-75, 76, 3):
        rating = calculate_rating_for_difference(
            difference,
            rating_mode,
            custom_rating,
        )
        x = 48 + (difference + 75) / 150 * 564
        y = 18 + (10 - rating) / 10 * 184
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


RATING_CHARTS = {
    RATING_MODE_STRICT: _rating_chart_points(RATING_MODE_STRICT),
    RATING_MODE_SYMMETRIC: _rating_chart_points(RATING_MODE_SYMMETRIC),
}


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
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    event_id: int | None = None,
    rating_mode: str = RATING_MODE_AUTOMATIC,
    include_splits: bool = True,
    custom_points: str | None = None,
    custom_left_start: str | None = None,
    custom_right_start: str | None = None,
    custom_left_decay: str | None = None,
    custom_right_decay: str | None = None,
    custom_start: str | None = None,
    custom_decay: str | None = None,
) -> HTMLResponse:
    registry_error = None
    custom_rating = parse_custom_rating_config(
        custom_points,
        custom_left_start,
        custom_right_start,
        custom_left_decay,
        custom_right_decay,
        custom_start,
        custom_decay,
    )
    try:
        registry = load_registry(
            event_id if event_id and event_id > 0 else None,
            rating_mode,
            custom_rating,
            include_splits,
        )
    except (PsycopgError, RuntimeError):
        registry = Registry(
            pacemakers=(),
            event_count=0,
            result_count=0,
            rating_mode=rating_mode,
            custom_rating=custom_rating,
            include_splits=include_splits,
        )
        registry_error = "Не удалось загрузить реестр. Обновите страницу чуть позже."
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "registry": registry,
            "registry_error": registry_error,
            "rating_charts": RATING_CHARTS,
            "custom_chart": _rating_chart_points(
                RATING_MODE_CUSTOM,
                custom_rating,
            ),
            "custom_points": serialize_custom_rating_points(custom_rating),
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
    status_code = 200

    if not TARGET_TIME_PATTERN.fullmatch(target_time):
        error = "Время на флаге должно быть в формате ЧЧ:ММ."
        status_code = 422
    else:
        try:
            result = await load_race_result(result_url)
            result = replace(result, target_time=target_time)
            created = save_race_result(result, target_time)
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
