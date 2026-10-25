from urllib.parse import urlsplit

from pacemaker_registry.runc import (
    RUNC_ORIGIN, parse_result_url, load_race_result as load_runc_race_result,
)
from pacemaker_registry.russiarunning import (
    InvalidResultUrl,
    RaceResult,
    RUSSIA_RUNNING_ORIGIN,
    parse_participant_url,
    load_race_result as load_russia_running_race_result,
)


def _hostname(value: str) -> str | None:
    try:
        return urlsplit(value.strip()).hostname
    except ValueError as error:
        raise InvalidResultUrl("Некорректная ссылка на результат.") from error


def result_source_links(value: str) -> tuple[str | None, str | None]:
    """Use the import validators, so accepted URLs and displayed links agree."""
    try:
        if _hostname(value) == "results.russiarunning.com":
            link = parse_participant_url(value)
            return link.source_url, f"{RUSSIA_RUNNING_ORIGIN}/event/{link.event_code}/results/{link.race_code}"
        if _hostname(value) == "results.runc.run":
            link = parse_result_url(value)
            return link.source_url, f"{RUNC_ORIGIN}/event/{link.event_code}/"
    except InvalidResultUrl:
        pass
    return None, None


async def load_race_result(value: str) -> RaceResult:
    hostname = _hostname(value)
    if hostname == "results.russiarunning.com":
        return await load_russia_running_race_result(value)
    if hostname == "results.runc.run":
        return await load_runc_race_result(value)
    raise InvalidResultUrl(
        "Поддерживаются ссылки с results.russiarunning.com и results.runc.run."
    )
