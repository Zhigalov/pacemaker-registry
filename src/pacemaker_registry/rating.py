"""The single finish-rating model, configured independently for each event."""

from dataclasses import asdict, dataclass
from math import exp, isfinite


@dataclass(frozen=True, slots=True)
class EventRatingConfig:
    left_bad: float = -60
    left_good: float = -15
    right_good: float = 0
    right_bad: float = 0
    left_decay: float = 30
    right_decay: float = 30
    left_curve: float = 1
    right_curve: float = 1
    left_score: float = 8
    right_score: float = 8

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
               for v in values.values()):
            raise ValueError("Все параметры должны быть конечными числами.")
        if not (-600 <= self.left_bad <= self.left_good <= 0
                <= self.right_good <= self.right_bad <= 600):
            raise ValueError("Границы должны идти по порядку: красная слева ≤ начало допуска ≤ 0 ≤ конец допуска ≤ красная справа (от −600 до +600 с).")
        if not all(1 <= v <= 300 for v in (self.left_decay, self.right_decay)):
            raise ValueError("Масштаб экспоненты должен быть от 1 до 300 секунд.")
        if not all(0.5 <= v <= 3 for v in (self.left_curve, self.right_curve)):
            raise ValueError("Кривизна должна быть от 0,5 до 3.")
        if not all(0.1 <= v <= 10 for v in (self.left_score, self.right_score)):
            raise ValueError("Оценка на границе должна быть от 0,1 до 10 баллов.")


def default_event_rating(target_time_type: str | None) -> EventRatingConfig:
    if target_time_type == "sub_minute":
        return EventRatingConfig(left_bad=-45, left_good=-10, right_good=10, right_bad=45)
    return EventRatingConfig()


def parse_event_rating(values: dict, *, allow_legacy: bool = False) -> EventRatingConfig:
    keys = set(asdict(EventRatingConfig()))
    if allow_legacy and isinstance(values, dict) and set(values) == keys - {"left_score", "right_score"}:
        values = values | {"left_score": 8, "right_score": 8}
    if not isinstance(values, dict) or set(values) != keys:
        raise ValueError("Передайте все десять параметров формулы.")
    try:
        if any(isinstance(v, bool) for v in values.values()):
            raise ValueError
        return EventRatingConfig(**{key: float(value) for key, value in values.items()})
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(str(error) or "Некорректные параметры формулы.") from error


def calculate_rating_for_difference(difference: float, config: EventRatingConfig) -> float:
    if config.left_good <= difference <= config.right_good:
        return 10.0
    if difference < config.left_good:
        width = config.left_good - config.left_bad
        if difference >= config.left_bad and width > 0:
            return config.left_score + (10 - config.left_score) * (difference - config.left_bad) / width
        distance = (config.left_bad - difference) / config.left_decay
        edge_score = config.left_score if width > 0 else 10
        curve = config.left_curve
    else:
        width = config.right_bad - config.right_good
        if difference <= config.right_bad and width > 0:
            return 10 - (10 - config.right_score) * (difference - config.right_good) / width
        distance = (difference - config.right_bad) / config.right_decay
        edge_score = config.right_score if width > 0 else 10
        curve = config.right_curve
    # Prevent numerical underflow: even a very distant finish stays above zero.
    return edge_score * exp(-min(700, distance ** curve))
