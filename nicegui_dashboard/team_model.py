"""Read model documentation and evaluation from the exact team Git revision used for a flight."""
from functools import lru_cache
from copy import deepcopy
import hashlib
import json
import math
import re
import subprocess

from .team_runtime import _git_bytes
from .team_source import source_path


RELEASE = "final_v1/halfsec_main/"
FORECAST_REPORT = "reports/halfsec-joint-results.json"
CONTROL_KEYS = ("starts_per_h", "cut_seconds_per_h", "timely_pdr", "relay_m")
FORECAST_KEYS = ("recall", "precision", "false_per_h", "average_precision")


def identity(flight):
    return dict(commit=flight.get("team_commit"), controller=flight.get("controller"),
                checkpoint=flight.get("model_sha256"), interval=flight.get("decision_interval_s"))


def measured(values, keys):
    return {key: value for key in keys if type(value := values.get(key)) in (int, float)
            and math.isfinite(value) and value >= 0
            and (key not in ("recall", "precision", "average_precision", "timely_pdr") or value <= 1)}


def operating_scores(report):
    values = measured(report.get("forecast_operating", {}).get("joint", {}),
                      (*FORECAST_KEYS, "events", "hits", "alerts"))
    if all(key in values for key in FORECAST_KEYS):
        return {**values, "completed_at": report.get("completed_at")}
    return {}


@lru_cache(maxsize=32)
def release_at(source, commit, controller, checkpoint, interval):
    # ls-tree and cat-file read only immutable local objects, even if the checkout advances mid-flight.
    _git_bytes(source, "rev-parse", "--verify", "--quiet", commit + "^{commit}")
    listing = _git_bytes(source, "ls-tree", "-rz", commit, "--", RELEASE, FORECAST_REPORT)
    blobs = {}
    for entry in listing.split(b"\0"):
        if entry:
            header, path = entry.split(b"\t", 1)
            mode, kind, oid = header.split()
            if kind == b"blob" and mode in {b"100644", b"100755"}:
                blobs[path.decode()] = oid.decode()
    refs = {}

    def read(path):
        raw = _git_bytes(source, "cat-file", "blob", blobs[path])
        refs[path] = hashlib.sha256(raw).hexdigest()
        return raw

    meta = json.loads(read(RELEASE + "checkpoint.json"))
    planned = controller == "halfsec_planned_eight" and RELEASE + "submission.py" in blobs
    checkpoint_controller = meta.get("controller") if planned else controller
    if planned:
        import ast
        submission = ast.parse(read(RELEASE + "submission.py").decode("utf-8-sig"))
        uses_release = any(isinstance(node, ast.ImportFrom) and node.module == "release" and node.level == 1
                           and any(name.name == "load_model" for name in node.names) for node in submission.body)
        if not uses_release or checkpoint_controller != "v21_transformer_halfsec":
            raise ValueError("배치 제어기가 사용한 가중치 출처를 확인할 수 없습니다.")
    if (meta.get("sha256"), meta.get("controller"), meta.get("decision_interval_s")) != (checkpoint, checkpoint_controller, interval):
        raise ValueError("이 비행의 모델 파일·제어기·판단 주기와 팀 배포 정보가 일치하지 않습니다.")
    details = []
    release_note = ""
    if RELEASE + "README.md" in blobs:
        notes = read(RELEASE + "README.md").decode("utf-8-sig")
        intro = re.search(r"^# [^\n]+\r?\n\s*\n([^#\s][^\n]+)", notes, re.M)
        if intro:
            release_note = intro[1].strip().replace("`", "")
        section = re.search(r"^## 예측과 재배치[ \t]*\r?\n(.*?)(?=^## |\Z)", notes, re.M | re.S)
        if section:
            details = [line[2:] for line in section[1].splitlines() if line.startswith("- ")]
        if planned:
            planning = re.search(r"^## 비행 전 배치와 구간 통과[ \t]*\r?\n(.*?)(?=^## |\Z)", notes, re.M | re.S)
            if planning:
                details = [line[2:] for line in planning[1].splitlines() if line.startswith("- ")] + details
    profile = dict(identity=dict(commit=commit, controller=controller, checkpoint=checkpoint, interval=interval),
                   model=measured(meta, ("step", "threshold")), release_note=release_note, notes=details, source_refs=refs,
                   controls={}, forecast={}, status="verified")
    report_path = RELEASE + "validation_summary.json"
    if report_path in blobs:
        summary = json.loads(read(report_path))
        checks = summary.get("source_checksums", {})
        valid = (summary.get("controller") == controller and summary.get("checkpoint") == meta
                 and set(("mission.py", "release.py", "validation.py")) <= checks.keys())
        for name, checksum in checks.items():
            path = RELEASE + name
            if not re.fullmatch(r"[\w.-]+\.py", name) or path not in blobs:
                valid = False
            else:
                read(path)
                valid = valid and refs[path] == checksum
        if valid:
            for name in ("separate_maps", "known_stalls"):
                group = summary.get(name, {})
                values = measured(group.get("after", {}), (*CONTROL_KEYS, "flights", "completed"))
                if all(k in values for k in ("flights", "completed")):
                    profile["controls"][name] = {**values, **measured(group, ("maps",))}
            profile["completed_at"] = summary.get("completed_at")
            profile["protocol_sha256"] = summary.get("protocol_sha256")
            profile["forecast"] = operating_scores(summary)
        else:
            profile["control_reason"] = "배포 평가가 사용한 모델 또는 제어 코드가 선택한 비행과 다릅니다."
    if not profile["forecast"] and FORECAST_REPORT in blobs:
        candidate = json.loads(read(FORECAST_REPORT))
        selected = candidate.get("selected", {})
        if selected.get("sha256") == checkpoint and selected.get("step") == meta.get("step"):
            profile["forecast"] = operating_scores(candidate)
        else:
            profile["forecast_reason"] = "배포본에 포함된 별도 선행 탐지 평가의 모델 가중치가 이 비행과 다릅니다."
            steps = (selected.get("step"), meta.get("step"))
            if all(type(step) is int for step in steps) and steps[0] != steps[1]:
                profile["forecast_reason"] += f" 평가 {steps[0]}회차 · 적용 {steps[1]}회차."
    return profile


def verified_profile(flight):
    pin = identity(flight)
    if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"] or "") or not re.fullmatch(r"[0-9a-f]{64}", pin["checkpoint"] or ""):
        return None
    try:
        profile = release_at(source_path(), **pin)
        threshold = flight.get("model_threshold")
        if threshold is not None and threshold != profile["model"].get("threshold"):
            return None
        return profile
    except (OSError, KeyError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return None


def model_for_flight(manifest, bundle=None):
    pin = identity(manifest)
    profile = manifest.get("team_model_profile")
    if (not isinstance(profile, dict) or profile.get("identity") != pin or profile.get("status") != "verified"
            or (manifest.get("model_threshold") is not None
                and manifest["model_threshold"] != profile.get("model", {}).get("threshold"))):
        profile = verified_profile(manifest)
    if bundle is not None and manifest.get("mode") != "nominal" and manifest["run_id"].startswith("team-"):
        from .team_model_metrics import regional_metrics
        # The cached release profile belongs to many flights. Never attach a
        # region or revision to that shared object in place.
        profile = deepcopy(profile) if profile is not None else dict(identity=pin, status="unverified")
        try:
            profile["regional"] = regional_metrics(bundle, manifest)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            import logging
            logging.getLogger(__name__).warning("Regional model metrics rejected for %s: %s", manifest["run_id"], exc)
            profile["regional"] = dict(status="error", message="선택한 비행의 지역 평가 기록을 확인할 수 없습니다.")
    return dict(run_id=manifest["run_id"], revision=manifest["bundle_revision"], identity=pin, profile=profile)
