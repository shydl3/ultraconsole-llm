from __future__ import annotations

from ultra_rules.vlm.runner import count_candidates, phase_timeout


def config(duration: float):
    return {"video": {"durationSec": duration}}


def test_phase_timeouts_scale_for_a_24h_recording(tmp_path):
    day = 24 * 3600
    assert phase_timeout("segment", config(day), tmp_path) == day
    assert phase_timeout("tracking", config(day), tmp_path) == 4 * day
    assert phase_timeout("cues", config(day), tmp_path) == day
    assert phase_timeout("understanding", config(day), tmp_path) == 4 * day


def test_understanding_timeout_counts_candidates(tmp_path):
    (tmp_path / "candidates.jsonl").write_text("{}\n" * 20000)
    assert count_candidates(tmp_path) == 20000
    assert phase_timeout("understanding", config(24 * 3600), tmp_path) == 20000 * 30 + 3600


def test_short_clips_keep_the_generous_floor(tmp_path):
    assert phase_timeout("segment", config(90), tmp_path) == 3600
    assert phase_timeout("tracking", config(90), tmp_path) == 86400
