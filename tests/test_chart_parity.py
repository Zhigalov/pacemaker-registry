"""Run the actual browser scoring functions against the server implementation."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from pacemaker_registry.rating import (
    EventRatingConfig, PaceRatingConfig, default_event_rating,
    calculate_rating_for_difference, calculate_split_rating,
)


def test_javascript_scores_match_python():
    node = os.getenv('NODE_BINARY') or shutil.which('node')
    assert node, 'Node.js is required for chart parity tests (or set NODE_BINARY).'
    rng = random.Random(42)
    finish = [EventRatingConfig(), default_event_rating('sub_minute'),
              EventRatingConfig(left_bad=-10, left_good=-10, right_good=15, right_bad=15),
              EventRatingConfig(left_score=2.1, right_score=6.3, left_curve=.5, right_curve=3)]
    pace = [PaceRatingConfig(), PaceRatingConfig(tolerance=0, checkpoint=1, bad=2, decay=1, curve=3),
            PaceRatingConfig(tolerance=15, checkpoint=30, bad=300, checkpoint_score=8, bad_score=.1, curve=.5)]
    cases, expected = [], []
    for kind, configs, evaluate in [('finish', finish, calculate_rating_for_difference),
                                     ('pace', pace, calculate_split_rating)]:
        for config in configs:
            anchors = ([config.left_bad, config.left_good, 0, config.right_good, config.right_bad]
                       if kind == 'finish' else [0, config.tolerance, config.checkpoint, config.bad])
            seconds = [value + epsilon for value in anchors for epsilon in [-.001, 0, .001]]
            seconds += [-1e9, 1e9] + [rng.uniform(-1200, 1200) for _ in range(250)]
            for value in seconds:
                cases.append({'kind': kind, 'seconds': value, 'config': asdict(config)})
                expected.append(evaluate(value, config))
    result = subprocess.run([node, str(Path(__file__).with_name('rating-parity.cjs'))],
                            input=json.dumps(cases), text=True, capture_output=True, check=True, timeout=15)
    actual = json.loads(result.stdout)
    assert len(actual) == len(expected)
    for case, js_score, python_score in zip(cases, actual, expected):
        assert js_score == pytest.approx(python_score, rel=1e-12, abs=1e-300), case
