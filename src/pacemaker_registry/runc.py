import html
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

from pacemaker_registry.russiarunning import (
    Checkpoint,
    InvalidResultUrl,
    RaceResult,
    RussiaRunningError,
    SLUG_PATTERN,
    guess_target_time,
)


RUNC_ORIGIN = "https://results.runc.run"
RESULT_ID_PATTERN = re.compile(r"^[0-9]+$")
TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class RuncResultLink:
    event_code: str
    result_id: str
    source_url: str


def parse_result_url(value: str) -> RuncResultLink:
    source_url = value.strip()
    parsed = urlsplit(source_url)

    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "results.runc.run"
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidResultUrl("Нужна HTTPS-ссылка на результат с results.runc.run.")

    parts = parsed.path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "event" or parts[2] != "result":
        raise InvalidResultUrl("Ссылка должна вести на результат участника RUNC.")

    _, event_code, _, result_id = parts
    if not SLUG_PATTERN.fullmatch(event_code):
        raise InvalidResultUrl("В ссылке некорректный код мероприятия.")
    if not RESULT_ID_PATTERN.fullmatch(result_id):
        raise InvalidResultUrl("В ссылке некорректный идентификатор результата.")

    return RuncResultLink(
        event_code=event_code,
        result_id=result_id,
        source_url=source_url,
    )


def parse_result_html(link: RuncResultLink, document: str) -> RaceResult:
    event_name = _text_by_class(document, "header-results-race__heading")
    athlete_name = _text_by_class(document, "results-race-detail__top-racer-name")
    indicators = _parse_indicators(document)

    chip_time = indicators.get("Результат от личного старта")
    pace = indicators.get("Средний темп")
    distance_text = indicators.get("Дистанция (км)")
    if not all((event_name, athlete_name, chip_time, pace, distance_text)):
        raise RussiaRunningError("В ответе RUNC не хватает обязательных полей.")

    try:
        distance = _parse_decimal(distance_text)
        chip_seconds = _time_to_seconds(chip_time)
    except (ArithmeticError, ValueError) as error:
        raise RussiaRunningError("RUNC вернул некорректное время или дистанцию.") from error
    if distance <= 0:
        raise RussiaRunningError("RUNC вернул некорректную дистанцию.")

    checkpoint_values = _parse_checkpoint_values(document)
    checkpoints: list[Checkpoint] = []
    previous_distance = Decimal("0")
    previous_time = 0
    for distance_value, elapsed_time in checkpoint_values:
        if distance_value <= previous_distance:
            continue
        elapsed_seconds = _time_to_seconds(elapsed_time)
        if elapsed_seconds <= previous_time:
            continue
        checkpoints.append(
            _make_checkpoint(
                distance_value,
                elapsed_seconds,
                previous_distance,
                previous_time,
            )
        )
        previous_distance = distance_value
        previous_time = elapsed_seconds

    if distance > previous_distance and chip_seconds > previous_time:
        checkpoints.append(
            _make_checkpoint(
                distance,
                chip_seconds,
                previous_distance,
                previous_time,
            )
        )

    normalized_chip_time = _format_finish_time(chip_seconds)
    source_participant_id = str(
        uuid5(NAMESPACE_URL, f"runc:{link.event_code}:{link.result_id}")
    )
    return RaceResult(
        athlete_name=athlete_name,
        source_event_id=f"runc:{link.event_code}",
        source_participant_id=source_participant_id,
        event_name=event_name,
        distance_km=_format_distance(distance),
        chip_time=normalized_chip_time,
        target_time=guess_target_time(normalized_chip_time),
        pace=f"{_pace_value(pace)} /км",
        checkpoints=tuple(checkpoints),
        source_url=link.source_url,
    )


async def load_race_result(value: str) -> RaceResult:
    link = parse_result_url(value)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "pacemaker-registry/0.1",
    }
    try:
        async with httpx.AsyncClient(
            base_url=RUNC_ORIGIN,
            headers=headers,
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"/event/{link.event_code}/result/{link.result_id}/"
            )
            response.raise_for_status()
            document = response.text
    except httpx.HTTPError as error:
        raise RussiaRunningError(
            "Не удалось получить данные от RUNC. Попробуйте ещё раз."
        ) from error

    return parse_result_html(link, document)


def _text_by_class(document: str, class_name: str) -> str:
    match = re.search(
        rf'<[^>]+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</[^>]+>',
        document,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return _clean_html(match.group(1)) if match else ""


def _parse_indicators(document: str) -> dict[str, str]:
    pairs = re.findall(
        r'<div class="results-indicator__label">(.*?)</div>\s*'
        r'<div class="results-indicator__value">(.*?)</div>',
        document,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return {_clean_html(label): _clean_html(value) for label, value in pairs}


def _parse_checkpoint_values(document: str) -> list[tuple[Decimal, str]]:
    if "results-race-detail__body-time-points" not in document:
        return []

    rows = re.findall(
        r'<div class="results-table__values">\s*'
        r'<div class="results-table__values-item">(.*?)</div>\s*'
        r'<div class="results-table__values-item">(.*?)</div>',
        document,
        flags=re.DOTALL | re.IGNORECASE,
    )
    parsed: list[tuple[Decimal, str]] = []
    for distance_html, elapsed_html in rows:
        distance_match = re.search(
            r"[0-9]+(?:[.,][0-9]+)?", _clean_html(distance_html)
        )
        elapsed_time = _clean_html(elapsed_html)
        if not distance_match or not elapsed_time:
            continue
        parsed.append((_parse_decimal(distance_match.group()), elapsed_time))
    return parsed


def _make_checkpoint(
    distance: Decimal,
    elapsed_seconds: int,
    previous_distance: Decimal,
    previous_time: int,
) -> Checkpoint:
    segment_distance = distance - previous_distance
    segment_seconds = elapsed_seconds - previous_time
    return Checkpoint(
        distance_km=_format_distance(distance, fixed=True),
        segment_distance_km=_format_distance(segment_distance, fixed=True),
        time=_format_elapsed_time(elapsed_seconds),
        pace_per_km=_format_pace(segment_seconds, segment_distance),
    )


def _clean_html(value: str) -> str:
    without_tags = TAG_PATTERN.sub(" ", value)
    return " ".join(html.unescape(without_tags).split())


def _parse_decimal(value: str) -> Decimal:
    return Decimal(value.strip().replace(",", "."))


def _time_to_seconds(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = map(int, parts)
    elif len(parts) == 3:
        hours, minutes, seconds = map(int, parts)
    else:
        raise ValueError("unsupported time")
    if hours < 0 or minutes < 0 or seconds < 0 or minutes >= 60 or seconds >= 60:
        raise ValueError("invalid time")
    return hours * 3600 + minutes * 60 + seconds


def _format_finish_time(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_elapsed_time(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_pace(segment_seconds: int, segment_distance: Decimal) -> str:
    seconds_per_km = int(
        (Decimal(segment_seconds) / segment_distance).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    minutes, seconds = divmod(seconds_per_km, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _format_distance(value: Decimal, *, fixed: bool = False) -> str:
    if fixed:
        return f"{value:.2f}".replace(".", ",")
    return format(value.normalize(), "f").replace(".", ",")


def _pace_value(value: str) -> str:
    match = re.search(r"[0-9]+:[0-5][0-9]", value)
    if not match:
        raise RussiaRunningError("RUNC вернул некорректный средний темп.")
    minutes, seconds = map(int, match.group().split(":"))
    return f"{minutes:02d}:{seconds:02d}"
