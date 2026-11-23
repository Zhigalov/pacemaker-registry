from decimal import Decimal

import pytest

from pacemaker_registry.registry import score_result
from pacemaker_registry.models import RegistryEvent
from pacemaker_registry.times import (
    elapsed_seconds, format_duration, format_elapsed_time, format_pace,
    format_distance, normalize_target_time, parse_distance, target_seconds,
)


@pytest.mark.parametrize('value,seconds', [('27:30', 1650), ('36:15', 2175),
    ('01:12:30', 4350), ('65:07', 3907), ('01:05:07.99', 3907)])
def test_duration_roundtrip(value, seconds):
    assert elapsed_seconds(value) == seconds
    assert elapsed_seconds(format_duration(seconds)) == seconds
    assert elapsed_seconds(format_elapsed_time(seconds)) == seconds


def test_legacy_flag_and_new_form_remain_unambiguous():
    assert target_seconds('01:05') == 3900
    assert normalize_target_time('01:05') == '01:05'
    assert normalize_target_time('01:05', minutes_seconds=True) == '00:01:05'
    assert normalize_target_time('27:30', minutes_seconds=True) == '00:27:30'


def test_distance_roundtrip_and_pace_rounding():
    assert parse_distance('21,100') == parse_distance('21.1') == Decimal('21.1')
    assert format_distance(Decimal('21.100')) == '21,1'
    assert format_distance(Decimal('5'), fixed=True) == '5,00'
    assert format_pace(601, Decimal('2')) == '05:01'


def test_scored_result_exposes_seconds_without_changing_original_times():
    result = score_result(1, RegistryEvent(1, 'Test'), Decimal('5'),
                          '00:27:30', '27:29.8', '05:30', (), details=True)
    assert (result.target_seconds, result.chip_seconds, result.time_deviation) == (1650, 1649, -1)
    assert result.card().chip_time == '27:29.8'
    assert result.details().target_pace == '05:30 /км'
