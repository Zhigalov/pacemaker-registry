import pytest

from pacemaker_registry.runc import parse_result_html, parse_result_url
from pacemaker_registry.russiarunning import Checkpoint, InvalidResultUrl


RESULT_URL = (
    "https://results.runc.run/event/moscow_marathon_10_km2026/result/27050/"
)
RESULT_HTML = """
<h1 class="header-results-race__heading">СберПрайм Московский Марафон 10 км</h1>
<div class="results-race-detail__top-racer-name">Дыскин&nbsp;Артём</div>
<div class="results-indicator">
  <div class="results-indicator__label">Результат от личного старта</div>
  <div class="results-indicator__value">0:39:00</div>
</div>
<div class="results-indicator">
  <div class="results-indicator__label">Результат от общего старта</div>
  <div class="results-indicator__value">0:40:07</div>
</div>
<div class="results-indicator">
  <div class="results-indicator__label">Средний темп</div>
  <div class="results-indicator__value">3:54 <span>мин/км</span></div>
</div>
<div class="results-indicator">
  <div class="results-indicator__label">Дистанция (км)</div>
  <div class="results-indicator__value">10</div>
</div>
<div class="results-race-detail__body-time-points">
  <div class="results-table">
    <div class="results-table__values">
      <div class="results-table__values-item"><span>5KM</span></div>
      <div class="results-table__values-item"><span>0:19:32</span></div>
      <div class="results-table__values-item"></div>
      <div class="results-table__values-item"><span>1534</span></div>
    </div>
  </div>
</div>
"""


def test_parse_result_url() -> None:
    link = parse_result_url(RESULT_URL)

    assert link.event_code == "moscow_marathon_10_km2026"
    assert link.result_id == "27050"


def test_parse_result_url_accepts_comma_in_runc_event_code() -> None:
    link = parse_result_url(
        "https://results.runc.run/event/moscow_marathon_42,2km_2026/result/27944/"
    )

    assert link.event_code == "moscow_marathon_42,2km_2026"


@pytest.mark.parametrize(
    "url",
    [
        "http://results.runc.run/event/event/result/27050/",
        "https://example.com/event/event/result/27050/",
        "https://results.runc.run/event/event/result/not-a-number/",
        "https://results.runc.run/event/event/overview/",
    ],
)
def test_parse_result_url_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(InvalidResultUrl):
        parse_result_url(url)


def test_parse_result_html_calculates_split_paces_and_finish() -> None:
    result = parse_result_html(parse_result_url(RESULT_URL), RESULT_HTML)

    assert result.athlete_name == "Дыскин Артём"
    assert result.source_event_id == "runc:moscow_marathon_10_km2026"
    assert result.source_participant_id == "705d9a43-cf7b-5669-9028-bbabdbe77a8c"
    assert result.event_name == "СберПрайм Московский Марафон 10 км"
    assert result.distance_km == "10"
    assert result.chip_time == "00:39:00"
    assert result.target_time == "00:39"
    assert result.pace == "03:54 /км"
    assert result.checkpoints == (
        Checkpoint(
            distance_km="5,00",
            segment_distance_km="5,00",
            time="19:32",
            pace_per_km="03:54",
        ),
        Checkpoint(
            distance_km="10,00",
            segment_distance_km="5,00",
            time="39:00",
            pace_per_km="03:54",
        ),
    )


def test_parse_result_html_works_without_intermediate_points() -> None:
    html_without_points = RESULT_HTML.replace(
        '<div class="results-race-detail__body-time-points">',
        '<div class="other-section">',
    )

    result = parse_result_html(parse_result_url(RESULT_URL), html_without_points)

    assert result.checkpoints == (
        Checkpoint(
            distance_km="10,00",
            segment_distance_km="10,00",
            time="39:00",
            pace_per_km="03:54",
        ),
    )
