import json
import sqlite3
from pathlib import Path

from ultra_rules.vlm.contracts import RunRequest
from ultra_rules.vlm.decision import evaluate, red_state, retreat_candidate_ids
from ultra_rules.vlm.storage import write_json


def request(**kwargs):
    return RunRequest(
        videoId="v1",
        pois=[{"id": "gate", "kind": "line", "points": [[0.5, 0.1], [0.5, 0.9]]}],
        cues=[{"id": "red", "points": [[0.4, 0.2], [0.6, 0.2], [0.6, 0.8], [0.4, 0.8]]}],
        events=[{"id": "e1", "description": "异常安检", "triggerKinds": ["line.cross"],
                 "cueIds": ["red"], "preSeconds": 2, "postSeconds": 4}],
        **kwargs,
    )


def candidate(cid, t, seg="1-1", direction="negative_to_positive", kind="line.cross", poi="gate"):
    return {"candidateId": cid, "trackId": 1, "trackSegmentId": seg, "poiId": poi,
            "triggerKind": kind, "direction": direction, "triggerSec": t, "confirmedSec": t + 0.2,
            "identityAmbiguous": False, "eventDefinitionIds": ["e1"]}


def write_observations(run_dir: Path, seg, times):
    db = sqlite3.connect(run_dir / "observations.sqlite")
    db.execute("CREATE TABLE observations (frame_index INTEGER PRIMARY KEY, time REAL NOT NULL, body TEXT NOT NULL)")
    for i, t in enumerate(times):
        body = json.dumps({"persons": [{"trackId": 1, "trackSegmentId": seg, "bbox": [0.4, 0.4, 0.6, 0.6]}]})
        db.execute("INSERT INTO observations VALUES (?, ?, ?)", (i, t, body))
    db.commit()
    db.close()


def test_red_state_on_and_off():
    timelines = {"red": {"intervals": [[10.0, 12.0]]}}
    assert red_state(timelines, ["red"], 11.0, 2, 4) == ("on", 10.0)
    assert red_state(timelines, ["red"], 20.0, 2, 4) == ("off", None)
    assert red_state(timelines, ["red"], 11.0, 2, 4)[0] == "on"


def test_retreat_candidate_ids_skips_only_the_retreat():
    rows = [candidate("c1", 1.0, direction="negative_to_positive"),
            candidate("c2", 2.0, direction="positive_to_negative"),
            candidate("c3", 10.0, direction="negative_to_positive")]
    skipped = retreat_candidate_ids(rows)
    assert skipped == {"c2"}


def test_retreat_candidate_ids_resets_after_pair():
    rows = [candidate("c1", 1.0, direction="negative_to_positive"),
            candidate("c2", 2.0, direction="positive_to_negative"),
            candidate("c3", 8.0, direction="negative_to_positive")]
    skipped = retreat_candidate_ids(rows)
    # c2 is the retreat of c1; c3 starts a new entry (7s later).
    assert skipped == {"c2"}


def test_evaluate_red_off(tmp_path):
    write_json(tmp_path / "cues.json", {"cues": {"red": {"intervals": []}}})
    write_observations(tmp_path, "1-1", [0.0, 0.5, 1.0, 1.5, 2.0])
    result = evaluate(candidate("c1", 1.0), request().events[0], request(), tmp_path, [candidate("c1", 1.0)])
    assert result["decision"] == "no_match"
    assert result["cueEvidence"]["state"] == "off"


def test_evaluate_red_on_retreated(tmp_path):
    write_json(tmp_path / "cues.json", {"cues": {"red": {"intervals": [[0.8, 6.0]]}}})
    write_observations(tmp_path, "1-1", [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0])
    entry = candidate("c1", 1.0, direction="negative_to_positive")
    retreat = candidate("c2", 2.0, direction="positive_to_negative")
    result = evaluate(entry, request().events[0], request(), tmp_path, [entry, retreat])
    assert result["decision"] == "no_match"
    assert result["cueEvidence"]["state"] == "on"


def test_evaluate_red_on_no_retreat_tracked(tmp_path):
    write_json(tmp_path / "cues.json", {"cues": {"red": {"intervals": [[0.8, 6.0]]}}})
    times = [0.0 + i * 0.5 for i in range(14)]  # 0..6.5s, continuous
    write_observations(tmp_path, "1-1", times)
    entry = candidate("c1", 1.0, direction="negative_to_positive")
    result = evaluate(entry, request().events[0], request(), tmp_path, [entry])
    assert result["decision"] == "match"


def test_evaluate_red_on_tracking_missing(tmp_path):
    write_json(tmp_path / "cues.json", {"cues": {"red": {"intervals": [[0.8, 6.0]]}}})
    write_observations(tmp_path, "1-1", [0.0, 0.5, 1.0, 1.5, 2.0])
    entry = candidate("c1", 1.0, direction="negative_to_positive")
    result = evaluate(entry, request().events[0], request(), tmp_path, [entry])
    assert result["decision"] == "uncertain"
    assert result["reviewRequired"] is True
