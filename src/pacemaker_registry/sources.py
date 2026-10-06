from urllib.parse import urlsplit

from pacemaker_registry.runc import load_race_result as load_runc_race_result
from pacemaker_registry.russiarunning import (
    InvalidResultUrl,
    RaceResult,
    load_race_result as load_russia_running_race_result,
)


async def load_race_result(value: str) -> RaceResult:
    hostname = urlsplit(value.strip()).hostname
    if hostname == "results.russiarunning.com":
        return await load_russia_running_race_result(value)
    if hostname == "results.runc.run":
        return await load_runc_race_result(value)
    raise InvalidResultUrl(
        "Поддерживаются ссылки с results.russiarunning.com и results.runc.run."
    )
