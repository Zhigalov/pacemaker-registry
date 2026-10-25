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
    parts = list(map(int, value.split(":")))
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError("Unsupported elapsed time format")


def time_difference_seconds(target_time: str, chip_time: str) -> int:
    return elapsed_seconds(chip_time) - target_seconds(target_time)
