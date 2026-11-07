"""Time conventions: flags are HH:MM; elapsed times can also be MM:SS."""

import re


def normalize_target_time(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[0-9]{1,2}:[0-5][0-9]", value):
        raise ValueError("Укажите время на флаге в формате ЧЧ:ММ, больше нуля. Например, 01:54.")
    hours, minutes = map(int, value.split(":"))
    if hours == minutes == 0:
        raise ValueError("Укажите время на флаге в формате ЧЧ:ММ, больше нуля. Например, 01:54.")
    return f"{hours:02d}:{minutes:02d}"


def target_seconds(value: str) -> int:
    hours, minutes = map(int, value.split(":"))
    return hours * 3600 + minutes * 60


def elapsed_seconds(value: str) -> int:
    """Read elapsed time at whole-second precision for the rating."""
    match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)(?:\.\d+)?", value.strip())
    if match is None:
        raise ValueError("Unsupported elapsed time format")
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    if seconds >= 60:
        raise ValueError("Unsupported elapsed time format")
    return hours * 3600 + minutes * 60 + seconds


def time_difference_seconds(target_time: str, chip_time: str) -> int:
    return elapsed_seconds(chip_time) - target_seconds(target_time)
