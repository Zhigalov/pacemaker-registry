import pytest

from pacemaker_registry.russiarunning import (
    Checkpoint,
    InvalidResultUrl,
    guess_target_time,
    parse_participant_url,
    parse_result_payloads,
)


RESULT_URL = (
    "https://results.russiarunning.com/participant/"
    "Kogalymskiypolumarafon2026/21km/827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
)


def test_parse_participant_url() -> None:
    link = parse_participant_url(RESULT_URL)

    assert link.event_code == "Kogalymskiypolumarafon2026"
    assert link.race_code == "21km"
    assert link.participant_id == "827f5fcd-eaaf-41c0-93d2-ed4fd58de206"


@pytest.mark.parametrize(
    "url",
    [
        "http://results.russiarunning.com/participant/event/race/827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
        "https://example.com/participant/event/race/827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
        "https://results.russiarunning.com/participant/event/race/not-a-uuid",
    ],
)
def test_parse_participant_url_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(InvalidResultUrl):
        parse_participant_url(url)


def test_parse_result_uses_chip_time_and_absolute_checkpoint_time() -> None:
    link = parse_participant_url(RESULT_URL)
    event = {
        "id": "490b438c-8b18-4162-8382-4f1b480960cd",
        "title": "Международный Когалымский полумарафон",
        "races": [{"id": "race-id", "code": "21km", "distance": 21}],
    }
    options = {
        "stagesInfo": [
            {"id": "start", "name": "Старт", "distance": 0},
            {"id": "five", "name": "5,00 км", "distance": 5},
            {"id": "finish", "name": "Финиш", "distance": 21.1},
        ]
    }
    profile = {
        "result": {
            "results": [
                {
                    "fullName": "Жигалов Сергей",
                    "individualResult": "01:53:53",
                    "absoluteResult": "01:54:16",
                    "pace": "05:23 /км",
                    "stageResults": [
                        {
                            "raceStageId": "five",
                            "individualResult": "27:06",
                            "absoluteResult": "27:29",
                            "pace": "05:25 /км",
                        },
                        {
                            "raceStageId": "finish",
                            "individualResult": "01:53:53",
                            "absoluteResult": "01:54:16",
                            "pace": "05:24 /км",
                        },
                    ],
                }
            ]
        }
    }

    result = parse_result_payloads(link, event, options, profile)

    assert result.athlete_name == "Жигалов Сергей"
    assert result.source_event_id == "490b438c-8b18-4162-8382-4f1b480960cd"
    assert result.source_participant_id == "827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
    assert result.event_name == "Международный Когалымский полумарафон"
    assert result.distance_km == "21,1"
    assert result.chip_time == "01:53:53"
    assert result.target_time == "01:54"
    assert result.pace == "05:23 /км"
    assert result.checkpoints == (
        Checkpoint(
            distance_km="5,00",
            segment_distance_km="5,00",
            time="27:29",
            pace_per_km="05:25",
        ),
        Checkpoint(
            distance_km="21,10",
            segment_distance_km="16,10",
            time="01:54:16",
            pace_per_km="05:24",
        ),
    )


def test_parse_result_falls_back_to_individual_checkpoint_time() -> None:
    link = parse_participant_url(
        "https://results.russiarunning.com/participant/"
        "SiriusAutodrom2026/10_km_f/6ee03f42-97ff-48fe-bfa2-cbd79ffcf0fc"
    )
    event = {
        "id": "68fa5e38-6643-4fd3-9ca3-45a07cff746a",
        "title": "Альфа-Банк Сириус Автодром",
        "races": [{"id": "race-id", "code": "10_km_f", "distance": 10}],
    }
    options = {
        "stagesInfo": [
            {"id": "start", "name": "Старт", "distance": 0},
            {"id": "three", "name": "3,00 км", "distance": 3},
            {"id": "finish", "name": "Финиш", "distance": 10},
        ]
    }
    profile = {
        "result": {
            "results": [
                {
                    "fullName": "Чалов Максим",
                    "individualResult": "54:53",
                    "pace": "05:29 /км",
                    "stageResults": [
                        {
                            "raceStageId": "three",
                            "individualResult": "16:42",
                            "pace": "05:33 /км",
                        },
                        {
                            "raceStageId": "finish",
                            "individualResult": "54:53",
                            "pace": "05:07 /км",
                        },
                    ],
                }
            ]
        }
    }

    result = parse_result_payloads(link, event, options, profile)

    assert result.checkpoints == (
        Checkpoint(
            distance_km="3,00",
            segment_distance_km="3,00",
            time="16:42",
            pace_per_km="05:33",
        ),
        Checkpoint(
            distance_km="10,00",
            segment_distance_km="7,00",
            time="54:53",
            pace_per_km="05:07",
        ),
    )


@pytest.mark.parametrize(
    ("chip_time", "expected"),
    [
        ("01:53:53", "01:54"),
        ("01:54:20", "01:54"),
        ("01:57:30", "01:59"),
        ("01:30:02", "01:30"),
        ("29:45", "00:30"),
    ],
)
def test_guess_target_time_uses_nearest_common_flag_time(
    chip_time: str, expected: str
) -> None:
    assert guess_target_time(chip_time) == expected
