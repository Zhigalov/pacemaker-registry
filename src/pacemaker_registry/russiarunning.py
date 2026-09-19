import asyncio
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx


RUSSIA_RUNNING_ORIGIN = "https://results.russiarunning.com"
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidResultUrl(ValueError):
    """The submitted URL is not a supported RussiaRunning result URL."""


class RussiaRunningError(RuntimeError):
    """RussiaRunning could not return a usable result."""


@dataclass(frozen=True, slots=True)
class ParticipantLink:
    event_code: str
    race_code: str
    participant_id: str
    source_url: str


@dataclass(frozen=True, slots=True)
class Checkpoint:
    distance_km: str
    segment_distance_km: str
    time: str
    pace_per_km: str


@dataclass(frozen=True, slots=True)
class RaceResult:
    athlete_name: str
    event_name: str
    distance_km: str
    chip_time: str
    target_time: str
    pace: str
    checkpoints: tuple[Checkpoint, ...]
    source_url: str


def parse_participant_url(value: str) -> ParticipantLink:
    source_url = value.strip()
    parsed = urlsplit(source_url)

    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "results.russiarunning.com"
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidResultUrl(
            "Нужна HTTPS-ссылка на результат с results.russiarunning.com."
        )

    parts = parsed.path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "participant":
        raise InvalidResultUrl("Ссылка должна вести на страницу участника.")

    _, event_code, race_code, participant_id = parts
    if not SLUG_PATTERN.fullmatch(event_code) or not SLUG_PATTERN.fullmatch(race_code):
        raise InvalidResultUrl("В ссылке некорректный код мероприятия или дистанции.")

    try:
        participant_id = str(UUID(participant_id))
    except ValueError as error:
        raise InvalidResultUrl("В ссылке некорректный идентификатор участника.") from error

    return ParticipantLink(
        event_code=event_code,
        race_code=race_code,
        participant_id=participant_id,
        source_url=source_url,
    )


def parse_result_payloads(
    link: ParticipantLink,
    event: dict[str, Any],
    options: dict[str, Any],
    profile: dict[str, Any],
) -> RaceResult:
    race = next(
        (item for item in event.get("races", []) if item.get("code") == link.race_code),
        None,
    )
    if race is None:
        raise RussiaRunningError("Дистанция из ссылки не найдена в мероприятии.")

    results = profile.get("result", {}).get("results", [])
    if not results:
        raise RussiaRunningError("RussiaRunning не вернул результат участника.")
    participant = results[0]

    stage_results = {
        item.get("raceStageId"): item
        for item in participant.get("stageResults", [])
        if item.get("raceStageId")
    }
    checkpoints: list[Checkpoint] = []
    finish_distance: Any = None
    previous_checkpoint_distance = Decimal("0")

    for stage in options.get("stagesInfo", []):
        distance = stage.get("distance")
        if distance is None or Decimal(str(distance)) <= 0:
            continue
        if str(stage.get("name", "")).casefold() == "финиш":
            finish_distance = distance

        stage_result = stage_results.get(stage.get("id"))
        if not stage_result:
            continue
        checkpoint_time = stage_result.get("absoluteResult")
        checkpoint_pace = stage_result.get("pace")
        if not checkpoint_time or not checkpoint_pace:
            continue

        checkpoint_distance = Decimal(str(distance))
        segment_distance = checkpoint_distance - previous_checkpoint_distance
        checkpoints.append(
            Checkpoint(
                distance_km=_format_distance(checkpoint_distance, fixed=True),
                segment_distance_km=_format_distance(segment_distance, fixed=True),
                time=checkpoint_time,
                pace_per_km=_pace_value(checkpoint_pace),
            )
        )
        previous_checkpoint_distance = checkpoint_distance

    distance = finish_distance if finish_distance is not None else race.get("distance")
    if distance is None:
        raise RussiaRunningError("Не удалось определить дистанцию.")

    required_fields = {
        "athlete_name": participant.get("fullName"),
        "event_name": event.get("title"),
        "chip_time": participant.get("individualResult"),
        "pace": participant.get("pace"),
    }
    if not all(required_fields.values()):
        raise RussiaRunningError("В ответе RussiaRunning не хватает обязательных полей.")

    return RaceResult(
        athlete_name=required_fields["athlete_name"],
        event_name=required_fields["event_name"],
        distance_km=_format_distance(distance),
        chip_time=required_fields["chip_time"],
        target_time=guess_target_time(required_fields["chip_time"]),
        pace=required_fields["pace"],
        checkpoints=tuple(checkpoints),
        source_url=link.source_url,
    )


async def load_race_result(value: str) -> RaceResult:
    link = parse_participant_url(value)
    headers = {
        "Accept": "application/json",
        "User-Agent": "pacemaker-registry/0.1",
    }

    try:
        async with httpx.AsyncClient(
            base_url=RUSSIA_RUNNING_ORIGIN,
            headers=headers,
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
        ) as client:
            event_response = await client.post(
                "/api/events/get",
                json={"eventCode": link.event_code, "language": "ru"},
            )
            event_response.raise_for_status()
            event = event_response.json()

            race = next(
                (
                    item
                    for item in event.get("races", [])
                    if item.get("code") == link.race_code
                ),
                None,
            )
            if race is None or not race.get("id") or not event.get("id"):
                raise RussiaRunningError("Мероприятие или дистанция не найдены.")

            options_response, profile_response = await asyncio.gather(
                client.get(
                    "/api/results/options/get",
                    params={"RaceId": race["id"], "Language": "ru"},
                ),
                client.post(
                    "/api/results/individual/profile",
                    json={
                        "participantId": link.participant_id,
                        "eventId": event["id"],
                        "language": "ru",
                    },
                ),
            )
            options_response.raise_for_status()
            profile_response.raise_for_status()
            options = options_response.json()
            profile = profile_response.json()
    except RussiaRunningError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise RussiaRunningError(
            "Не удалось получить данные от RussiaRunning. Попробуйте ещё раз."
        ) from error

    return parse_result_payloads(link, event, options, profile)


def _format_distance(value: Any, *, fixed: bool = False) -> str:
    distance = Decimal(str(value))
    if fixed:
        return f"{distance:.2f}".replace(".", ",")
    return format(distance.normalize(), "f").replace(".", ",")


def _pace_value(value: str) -> str:
    return value.split()[0]


def guess_target_time(chip_time: str) -> str:
    """Guess the flag time from the nearest common pacemaker target."""
    parts = chip_time.strip().split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise RussiaRunningError("Не удалось определить время на флаге.")

    if len(parts) == 2:
        hours = 0
        minutes, seconds = map(int, parts)
    else:
        hours, minutes, seconds = map(int, parts)

    if minutes >= 60 or seconds >= 60:
        raise RussiaRunningError("Не удалось определить время на флаге.")

    chip_seconds = hours * 3600 + minutes * 60 + seconds
    center_minute = chip_seconds // 60
    candidate_minutes = range(max(0, center_minute - 5), center_minute + 7)
    candidates = [
        minute * 60 for minute in candidate_minutes if minute % 5 in (0, 4)
    ]
    target_seconds = min(
        candidates,
        key=lambda candidate: (
            abs(candidate - chip_seconds),
            candidate < chip_seconds,
        ),
    )
    target_minutes = target_seconds // 60
    return f"{target_minutes // 60:02d}:{target_minutes % 60:02d}"
