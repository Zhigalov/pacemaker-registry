from pathlib import Path

from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg import Error as PsycopgError

from pacemaker_registry.db import check_database
from pacemaker_registry.russiarunning import (
    InvalidResultUrl,
    RussiaRunningError,
    load_race_result,
)


PACKAGE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Pacemaker Registry",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/add", response_class=HTMLResponse)
def add_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="add.html",
        context={"result": None, "error": None, "result_url": ""},
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
        context={"result": result, "error": error, "result_url": result_url},
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
