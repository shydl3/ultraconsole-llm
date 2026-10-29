"""Persistent semantic event templates with atomic, revision-checked transactions."""
from __future__ import annotations

import copy
import fcntl
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator

from . import vlm_jobs
from .vlm_jobs import AnalysisParameters, EventDefinition, Identifier, Poi, RunRequest, StrictModel

_lock = threading.RLock()


class Rule(EventDefinition):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name", "description")
    @classmethod
    def text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Text must not be blank")
        return value

    @model_validator(mode="after")
    def template_only(self):
        if self.poiIds:
            raise ValueError("POI bindings belong to the current analysis, not the rule template")
        return self


class Batch(StrictModel):
    revision: int = Field(ge=0)
    upserts: list[Rule] = Field(default_factory=list, max_length=1000)
    deletes: list[Identifier] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        ids = [r.id for r in self.upserts] + self.deletes
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate rule IDs in transaction")
        return self


class Selection(StrictModel):
    id: Identifier
    version: int = Field(ge=1)
    poiIds: list[Identifier] = Field(default_factory=list, max_length=50)


class ManagedRun(AnalysisParameters):
    videoId: Identifier
    pois: list[Poi] = Field(min_length=1, max_length=50)
    selections: list[Selection] = Field(min_length=1, max_length=30)
    revision: int = Field(ge=0)


@contextmanager
def transaction(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with _lock, (root / "event-rules.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load(root: Path):
    path = root / "event-rules.json"
    if not path.exists():
        default = Path(__file__).resolve().parents[2] / "Rules/configs/vlm_events.json"
        now = time.time()
        rules = []
        for event in vlm_jobs.read_json(default)["events"]:
            rule = Rule.model_validate({**event, "poiIds": [], "name": event["description"][:160]})
            rules.append({**rule.model_dump(), "version": 1, "createdAt": now, "updatedAt": now})
        vlm_jobs.write_json(path, {"revision": 1, "rules": rules})
    try:
        value = vlm_jobs.read_json(path)
        if not isinstance(value["revision"], int) or not isinstance(value["rules"], list):
            raise ValueError("Invalid rule library")
        return value
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(500, "Rule library is unreadable; existing file was preserved") from exc


def load(root: Path):
    with transaction(root):
        return _load(root)


def save(root: Path, request: Batch):
    with transaction(root):
        current = _load(root)
        if current["revision"] != request.revision:
            raise HTTPException(409, "规则库已在其他窗口修改，请导出草稿或重新加载后再保存 / Rule library changed; reload before saving")
        rows = {row["id"]: row for row in current["rules"]}
        for identifier in request.deletes:
            if identifier not in rows:
                raise HTTPException(404, f"Rule not found: {identifier}")
            del rows[identifier]
        now = time.time()
        for rule in request.upserts:
            previous = rows.get(rule.id, {})
            rows[rule.id] = {**rule.model_dump(), "version": previous.get("version", 0) + 1,
                             "createdAt": previous.get("createdAt", now), "updatedAt": now}
        if len(rows) > 1000:
            raise HTTPException(422, "Rule library supports up to 1000 definitions")
        result = {"revision": current["revision"] + 1, "rules": list(rows.values())}
        vlm_jobs.write_json(root / "event-rules.json", result)
        return result


def resolve(root: Path, request: ManagedRun):
    with transaction(root):
        library = _load(root)
        if library["revision"] != request.revision:
            raise HTTPException(409, "Rule library changed; reload before starting analysis")
        rows = {row["id"]: row for row in library["rules"]}
        events, snapshots = [], []
        for selection in request.selections:
            row = rows.get(selection.id)
            if not row or row["version"] != selection.version or not row["enabled"]:
                raise HTTPException(409, f"Rule changed, disabled or deleted: {selection.id}")
            event = {key: row[key] for key in EventDefinition.model_fields}
            event["poiIds"] = selection.poiIds
            applicable = [poi for poi in request.pois if not selection.poiIds or poi.id in selection.poiIds]
            if not any(set(event["triggerKinds"]) & ({"line.cross"} if poi.kind == "line" else {"zone.enter", "zone.leave", "initial_inside"}) for poi in applicable):
                raise HTTPException(422, f"Rule has no compatible POI/trigger: {row['name']}")
            events.append(event)
            snapshots.append({"id": row["id"], "name": row["name"], "version": row["version"], "event": copy.deepcopy(event)})
        values = request.model_dump(exclude={"revision", "selections"})
        try:
            run = RunRequest.model_validate({**values, "events": events})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return run, {"libraryRevision": library["revision"], "rules": snapshots}


def snapshot(run_dir: Path):
    path = run_dir / "rules-snapshot.json"
    if path.exists():
        return vlm_jobs.read_json(path)
    # Older and CLI runs already contain the effective event definitions.
    path = run_dir / "request.json"
    if not path.exists():
        return {"libraryRevision": None, "rules": []}
    events = vlm_jobs.read_json(path).get("request", {}).get("events", [])
    return {"libraryRevision": None, "rules": [
        {"id": e["id"], "name": e["description"][:160], "version": None, "event": e} for e in events
    ]}
