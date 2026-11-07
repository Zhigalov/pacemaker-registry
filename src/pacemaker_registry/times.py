"""Stored flag times are HH:MM or HH:MM:SS."""

import re


def normalize_target_time(value: str, *, minutes_seconds: bool = False) -> str:
    value = value.strip()
    # Only the new forms interpret two components as minutes and seconds.
    if minutes_seconds and value.count(":") == 1:
        value = "00:" + value.zfill(5)
    if not re.fullmatch(r"[0-9]{1,2}:[0-5][0-9](?::[0-5][0-9])?", value) or not target_seconds(value):
        raise ValueError("Укажите время на флаге больше нуля: ММ:СС (27:30) или ЧЧ:ММ:СС (1:12:30).")
    hours, minutes, seconds = format_duration(target_seconds(value)).split(":")
    return f"{hours}:{minutes}:{seconds}" if seconds != "00" else f"{hours}:{minutes}"


def target_seconds(value: str) -> int:
    hours, minutes, *seconds = map(int, value.split(":"))
    return hours * 3600 + minutes * 60 + (seconds[0] if seconds else 0)


def format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_target_time(value: str) -> str:
    return format_duration(target_seconds(value))


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
