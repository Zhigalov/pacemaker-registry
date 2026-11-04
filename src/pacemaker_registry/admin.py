"""Manual assignment of administrator and event captain permissions."""
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg import Error as PsycopgError

from pacemaker_registry import auth, db


def _require_admin(account):
    if not account.is_admin:
        raise HTTPException(403, "Управление правами доступно только администраторам.")


def _set_permissions(actor_id, account_id, event_id, make_admin):
    with db.connect_database() as connection:
        # Serialize role changes so two administrators cannot revoke each other concurrently.
        connection.execute("SELECT pg_advisory_xact_lock(528077313)")
        actor = connection.execute("SELECT is_admin FROM accounts WHERE id = %s", (actor_id,)).fetchone()
        if not actor or not actor[0]:
            raise HTTPException(403, "Управление правами доступно только администраторам.")
        if account_id is not None and not connection.execute(
            "SELECT id FROM accounts WHERE id = %s", (account_id,)
        ).fetchone():
            raise HTTPException(404, "Аккаунт не найден.")

        if event_id is not None:
            updated = connection.execute(
                "UPDATE events SET captain_account_id = %s WHERE id = %s RETURNING id",
                (account_id, event_id),
            ).fetchone()
            if updated is None:
                raise HTTPException(404, "Соревнование не найдено.")
        else:
            if not make_admin and not connection.execute(
                "SELECT 1 FROM accounts WHERE is_admin AND id <> %s LIMIT 1", (account_id,)
            ).fetchone():
                raise HTTPException(409, "В реестре должен остаться хотя бы один администратор.")
            connection.execute("UPDATE accounts SET is_admin = %s WHERE id = %s", (make_admin, account_id))


def create_admin_router(templates):
    router = APIRouter(prefix="/admin")

    @router.get("/roles")
    def roles(request: Request, saved: bool = False):
        _require_admin(auth.require_account(request))
        try:
            with db.connect_database() as connection:
                accounts = connection.execute(
                    """SELECT id, yandex_login, is_admin FROM accounts
                       ORDER BY lower(yandex_login) NULLS LAST, id"""
                ).fetchall()
                events = connection.execute(
                    "SELECT id, name, captain_account_id FROM events ORDER BY lower(name), id"
                ).fetchall()
        except (PsycopgError, RuntimeError):
            raise HTTPException(503, "Не удалось загрузить список ролей.") from None
        return templates.TemplateResponse(
            request=request, name="admin_roles.html",
            context={"accounts": accounts, "events": events, "saved": saved},
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/roles")
    def save_roles(
        request: Request,
        account_id: Annotated[int, Form(ge=0)],
        event_id: Annotated[int | None, Form(gt=0)] = None,
        make_admin: Annotated[bool | None, Form()] = None,
    ):
        actor = auth.require_write(request)
        _require_admin(actor)
        if event_id is None and (not account_id or make_admin is None):
            raise HTTPException(422, "Выберите аккаунт.")
        try:
            _set_permissions(actor.id, account_id or None, event_id, make_admin)
        except (PsycopgError, RuntimeError):
            raise HTTPException(503, "Не удалось сохранить права. Попробуйте ещё раз.") from None
        return RedirectResponse("/admin/roles?saved=true", status_code=303)

    return router
