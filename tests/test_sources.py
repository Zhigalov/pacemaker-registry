import pytest

from pacemaker_registry.russiarunning import InvalidResultUrl
from pacemaker_registry.sources import load_race_result


@pytest.mark.anyio
async def test_source_router_selects_russia_running(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_loader(value: str) -> str:
        calls.append(value)
        return "russia-running"

    monkeypatch.setattr(
        "pacemaker_registry.sources.load_russia_running_race_result", fake_loader
    )
    url = "https://results.russiarunning.com/participant/event/race/id"

    result = await load_race_result(url)

    assert result == "russia-running"
    assert calls == [url]


@pytest.mark.anyio
async def test_source_router_selects_runc(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_loader(value: str) -> str:
        calls.append(value)
        return "runc"

    monkeypatch.setattr("pacemaker_registry.sources.load_runc_race_result", fake_loader)
    url = "https://results.runc.run/event/event/result/27050/"

    result = await load_race_result(url)

    assert result == "runc"
    assert calls == [url]


@pytest.mark.anyio
async def test_source_router_rejects_unknown_hosts() -> None:
    with pytest.raises(InvalidResultUrl):
        await load_race_result("https://example.com/result/1")
