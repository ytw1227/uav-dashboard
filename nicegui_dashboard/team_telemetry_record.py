"""Verified, read-only radio supplements for one immutable team flight."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import zipfile

from result_service.dashboard_contract.validation import MAX_DATABASE, MAX_JSON, canonical_hash, confined, read_json
from .team_baseline_record import pin_fields
from .team_telemetry import PAIR_HEADS


def telemetry_path(bundle):
    bundle = Path(bundle).resolve()
    if (bundle.parent.parent.name != "bundles" or not re.fullmatch(r"team-[0-9a-f]{28}", bundle.parent.name)
            or not re.fullmatch(r"rev-[0-9a-f]{24}", bundle.name)):
        raise ValueError("추가 통신 관측에 사용할 팀 실행·변환본을 확인하세요.")
    return confined(bundle.parents[2] / "flight_telemetry", Path(bundle.parent.name) / (bundle.name + ".zip"), must_exist=False)


def native_telemetry(m):
    flags = m.get("team_telemetry", {})
    return bool(flags.get("exact_margin") and flags.get("shared_series")
                and "ack_success_ratio_in_interval" in m.get("observation_definitions", {})
                and set(PAIR_HEADS).issubset(m.get("head_definitions", {})))


def telemetry_signature(bundle):
    # Nominal package routes share the regional viewer. Only historic applied
    # team flights are eligible for this supplement.
    if not re.fullmatch(r"team-[0-9a-f]{28}", bundle.parent.name) or not re.fullmatch(r"rev-[0-9a-f]{24}", bundle.name):
        return None
    try:
        stat = telemetry_path(bundle).stat()
    except FileNotFoundError:
        return None
    return stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size


def member_digest(blob):
    return hashlib.sha256(blob).hexdigest()


def check_archive(path, expected):
    with zipfile.ZipFile(path) as archive:
        files = archive.infolist()
        if (len(files) != len({f.filename for f in files}) or sum(f.file_size for f in files) > MAX_DATABASE
                or any(f.file_size > MAX_JSON for f in files)):
            raise ValueError("통신 관측 파일의 크기 또는 목록이 올바르지 않습니다.")
        meta = json.loads(archive.read("metadata.json"))
        checksum = meta.pop("metadata_sha256", None)
        if (canonical_hash(meta) != checksum or meta.get("schema_version") != 1 or meta.get("source") != expected
                or set(meta["files"]) != set(archive.namelist()) - {"metadata.json"}
                or not {"timeline.json", "analysis.json"}.issubset(meta["files"])):
            raise ValueError("통신 관측이 선택한 원본 비행과 일치하지 않습니다.")
        for name, digest in meta["files"].items():
            if member_digest(archive.read(name)) != digest:
                raise ValueError("추가 관측 파일의 검사값이 일치하지 않습니다.")
        return meta


@lru_cache(maxsize=24)
def verified_meta(bundle, signature, manifest_hash):
    original = read_json(bundle / "manifest.json")
    expected = pin_fields(bundle, original)
    if (expected["manifest_sha256"] != manifest_hash or read_json(bundle/"READY", 4096).get("manifest_sha256") != manifest_hash
            or (original["run_id"],original["bundle_revision"]) != (bundle.parent.name,bundle.name)):
        raise ValueError("원본 비행의 완료 기록을 확인할 수 없습니다.")
    return check_archive(telemetry_path(bundle), expected)


def telemetry_available(bundle, manifest):
    if native_telemetry(manifest):
        return True
    signature = telemetry_signature(bundle)
    if signature:
        try:
            verified_meta(bundle, signature, read_json(bundle/"READY", 4096)["manifest_sha256"])
            return True
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            pass
    return False


@lru_cache(maxsize=8)
def member(bundle, signature, name):
    # All members were checked against a signed-by-content index. Check the
    # active member again to guard a replacement between validation and read.
    meta = verified_meta(bundle, signature, read_json(bundle/"READY", 4096)["manifest_sha256"])
    if name not in meta["files"]:
        return None
    with zipfile.ZipFile(telemetry_path(bundle)) as archive:
        data = archive.read(name)
    if member_digest(data) != meta["files"][name]:
        raise ValueError("읽는 동안 추가 관측 기록이 변경됐습니다.")
    return json.loads(data)


def radio_member(i, j):
    return f"radio-{i}-{j}.json"


def radio_window(changes, observer, peer, start, stop, cursor):
    latest, rows, at = {}, [], []
    for t, new in changes or ():
        if t > max(stop, cursor):
            break
        latest.update({k:(v,t) for k,v in new.items()})
        if t <= cursor or start <= t <= stop:
            row = dict(observed_at_s=t, available_at_s=t, observer_id=observer, peer_id=peer,
                       values={k:v for k,(v,_) in latest.items()}, valid={k:v is not None for k,(v,_) in latest.items()},
                       ages={k:t-stamp for k,(_,stamp) in latest.items()})
            if t <= cursor:
                at = [row]
            if start <= t <= stop:
                rows.append(row)
    return rows, at
