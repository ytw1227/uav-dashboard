"""Read-only parent summaries for a separate performance and training view.

Catalog aggregates remain authoritative if the original source is missing or changed.
No checkpoint or research modules are loaded by this stdlib-only module.
"""
import json
from pathlib import Path, PurePosixPath
from .common import digest, metadata
from .final_summary import final_v1_summary

PARENT_KINDS = {"evaluation_parent", "training_progress", "bootstrap_parent"}
AGGREGATE_KEYS = ("communication", "prediction", "alarms", "cases", "updates",
                  "committed_updates", "training_episodes", "last", "train_episodes",
                  "validation_episodes")
MODE_LABELS = {"joint": "공동학습", "fixed": "예측 고정", "physics": "물리 기준"}
SPLIT_LABELS = {"validation": "검증", "test": "시험", "train": "훈련",
                "demonstration": "시연"}
REPRESENTATIVE_POLICY = ("같은 제어 방식·데이터 분할·설정·연구 코드·평가 사례 집합에서 "
                         "기록된 업데이트 수가 가장 큰 평가를 기본 표시합니다. 동률은 모두 유지하며, "
                         "최고 성능이나 최종 완료 시각을 뜻하지 않습니다. 파일 수정 시각은 사용하지 않습니다.")


def _verified_source(project, manifest):
    refs = manifest.get("source_refs", [])
    for ref in refs:
        if ref.get("hash_kind") != "canonical_json":
            continue
        try:
            relative = PurePosixPath(ref["path"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
                continue
            path = (project / Path(*relative.parts)).resolve()
            base = (project / "results" / "transformer").resolve()
            if not path.is_relative_to(base) or path.stat().st_size > 32 * 1024 * 1024:
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and digest(raw) == ref.get("sha256"):
                return raw, "source_hash_verified"
            return {}, "catalog_only_source_changed"
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return {}, "catalog_only_source_unavailable"


def _source_name(record):
    refs = record.get("source_refs", [])
    return PurePosixPath(refs[0].get("path", "")) if refs else PurePosixPath()


def _nonnegative_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _label(record):
    meta = record["metadata"]
    mode = MODE_LABELS.get(meta.get("mode"), "제어 방식 미기록")
    kind = record["artifact_kind"]
    if kind == "evaluation_parent":
        split = SPLIT_LABELS.get(meta.get("split"), "분할 미기록")
        updates = meta.get("updates")
        stage = f" · 업데이트 {updates}" if updates is not None else ""
        series = meta.get("training_series") or ""
        purpose = " · 점검 실행" if "smoke" in series or "probe" in series else ""
        return f"{mode} · {split} 성능{stage}{purpose}"
    if kind == "bootstrap_parent":
        return "초기 학습·검증 데이터 준비"
    series = meta.get("training_series")
    if mode == "제어 방식 미기록":
        mode = {"interrupt_probe": "중단 점검", "smoke_joint": "학습 점검"}.get(series, series or "모델")
    suffix = " · 점검 기록" if series and ("smoke" in series or "probe" in series) else ""
    return f"{mode} · 학습 진행{suffix}"


def _record(project, manifest):
    raw, status = _verified_source(project, manifest)
    aggregates = dict(manifest.get("stored_aggregates", {}))
    # A legacy catalog omitted these fields. Only a hash-matched source may enrich it.
    aggregates.update({key: raw[key] for key in AGGREGATE_KEYS if key in raw})
    meta = metadata(manifest)
    saved = manifest.get("summary_metadata", {})
    for key in ("mode", "split", "checkpoint"):
        value = raw.get(key, saved.get(key))
        if value is not None:
            meta[key] = value
    updates = raw.get("updates", saved.get("updates", aggregates.get("updates")))
    meta["updates"] = updates if _nonnegative_number(updates) else None
    checkpoint = meta.get("checkpoint")
    checkpoint_parts = PurePosixPath(checkpoint).parts if isinstance(checkpoint, str) else ()
    meta["training_series"] = checkpoint_parts[-3] if len(checkpoint_parts) >= 3 and checkpoint_parts[-2] == "checkpoints" else None
    if manifest["artifact_kind"] == "training_progress":
        meta["training_series"] = meta["training_series"] or _source_name(manifest).parent.name
    children = raw.get("raw_paths", manifest.get("child_source_refs", []))
    case_names = sorted(PurePosixPath(x).name for x in children if isinstance(x, str))
    cohort = digest(case_names) if case_names else manifest.get("source_key", manifest["run_id"])
    research = digest(raw["source_sha256"]) if raw.get("source_sha256") else saved.get("research_fingerprint", manifest.get("research_version"))
    result = {
        "run_id": manifest["run_id"], "bundle_revision": manifest["bundle_revision"],
        "artifact_kind": manifest["artifact_kind"], "metadata": meta,
        "aggregates": aggregates, "source_refs": manifest.get("source_refs", []),
        "duplicate_source_refs": [], "completed_at": manifest.get("completed_at"),
        "completion_time_status": manifest.get("completion_time_status", "unknown"),
        "stage_status": manifest.get("stage_status"), "provenance_status": status,
        "aggregation_scope": "parent; never counted as independent episode",
        "representative": False, "representative_reason": None,
        "cohort_id": cohort, "research_fingerprint": research,
        "prediction_status": "not_applicable" if meta.get("mode") == "physics" else
                             "recorded" if aggregates.get("prediction") else "not_collected",
        "notes": raw.get("notes", saved.get("notes", [])),
    }
    result["label"] = _label(result)
    return result


def list_summaries(registry):
    with registry.connection() as connection:
        generation = int(connection.execute("SELECT value FROM state WHERE key='generation'").fetchone()[0])
        manifests = [json.loads(row[0]) for row in connection.execute("SELECT manifest FROM records")]
    manifests = [m for m in manifests if m.get("artifact_kind") in PARENT_KINDS]
    records = []
    duplicates = {}
    for manifest in sorted(manifests, key=lambda m: m["run_id"]):
        record = _record(registry.project, manifest)
        # Exact same canonical source content is one record even if copied to another path.
        identity = manifest.get("source_identity", {}).get("json_sha256")
        key = (manifest["artifact_kind"], identity or manifest["run_id"])
        if key in duplicates:
            duplicates[key]["duplicate_source_refs"].extend(record["source_refs"])
            continue
        duplicates[key] = record
        records.append(record)
    # Recover training mode only from an evaluation that explicitly records the same checkpoint.
    checkpoint_modes = {}
    for record in records:
        if record["artifact_kind"] == "evaluation_parent" and record["metadata"].get("checkpoint"):
            checkpoint_modes.setdefault(record["metadata"]["checkpoint"], set()).add(record["metadata"].get("mode"))
    for record in records:
        if record["artifact_kind"] == "training_progress" and not record["metadata"].get("mode"):
            modes = checkpoint_modes.get(record["metadata"].get("checkpoint"), set()) - {None}
            if len(modes) == 1:
                record["metadata"]["mode"] = next(iter(modes))
                record["metadata"]["mode_source"] = "evaluation_for_same_checkpoint"
                record["label"] = _label(record)
    groups = {}
    for record in records:
        if record["artifact_kind"] != "evaluation_parent":
            continue
        meta = record["metadata"]
        # Unknown mode/setting is kept separate instead of merging unrelated research.
        key = (meta.get("mode") or record["run_id"], meta.get("split") or record["run_id"],
               meta.get("config_hash") or record["run_id"], record["research_fingerprint"],
               record["cohort_id"])
        groups.setdefault(key, []).append(record)
    for group in groups.values():
        known = [r["metadata"]["updates"] for r in group if r["metadata"]["updates"] is not None]
        maximum = max(known) if known else None
        for record in group:
            value = record["metadata"]["updates"]
            record["representative"] = maximum is None or value == maximum
            record["representative_reason"] = ("largest_recorded_update_in_same_conditions" if record["representative"] and maximum is not None
                                                else "update_unknown_retained" if maximum is None
                                                else "earlier_recorded_update")
    # A tied update can belong to more than one real model. Keep both visibly distinct.
    labels = {}
    for record in records:
        labels.setdefault(record["label"], []).append(record)
    for group in labels.values():
        if len(group) > 1:
            for index, record in enumerate(sorted(group, key=lambda r: (r["metadata"].get("model_id") or "", r["run_id"])), 1):
                record["label"] += f" · 모델 {index}"
    records.sort(key=lambda r: (not r["representative"], r["artifact_kind"], r["label"], r["run_id"]))
    counts = {name: sum(r["artifact_kind"] == kind for r in records) for name, kind in
              (("evaluation", "evaluation_parent"), ("training", "training_progress"), ("bootstrap", "bootstrap_parent"))}
    counts["representative"] = sum(r["representative"] for r in records)
    counts["duplicates_hidden"] = len(manifests) - len(records)
    return {"records": records, "representative_policy": REPRESENTATIVE_POLICY,
            "counts": counts, "generation": generation, "final_v1": final_v1_summary(registry.project)}
