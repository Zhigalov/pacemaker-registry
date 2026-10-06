import colorsys
import re

import pytest

from pacemaker_registry.rating import rating_color_style


def hue(score):
    return float(re.search(r"hsl\(([\d.]+)", rating_color_style(score))[1])


def test_color_scale_is_continuous_including_seven_and_nine():
    values = [hue(n / 100) for n in range(1001)]
    assert all(0 <= b - a <= 0.76 for a, b in zip(values, values[1:]))
    assert hue(6) < 15  # red
    assert 40 <= hue(7) <= hue(8) <= 60  # amber/yellow
    assert 120 <= hue(9) <= hue(10) <= 165  # green


@pytest.mark.parametrize("score,expected", [(-1, 0), (11, 10), (float('nan'), 0)])
def test_color_bounds(score, expected):
    assert rating_color_style(score) == rating_color_style(expected)


def test_text_contrast_on_tinted_background():
    def luminance(rgb):
        channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * weight for c, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    for n in range(101):
        h = hue(n / 10) / 360
        text = luminance(colorsys.hls_to_rgb(h, 0.28, 0.65))
        background = luminance(colorsys.hls_to_rgb(h, 0.94, 0.70))
        assert (background + 0.05) / (text + 0.05) >= 4.5
