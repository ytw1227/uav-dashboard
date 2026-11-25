"""Explain the selected flight's recorded outages using the pinned team's RF rules."""
from contextlib import closing
import json
import sqlite3
from statistics import median
import zlib


def shape_summary(trace, shape):
    return dict(pattern=shape, margin_at_onset_db=float(trace[-1]) if trace else None,
                margin_before_db=float(trace[-2]) if len(trace) >= 2 else None,
                jump_db=float(trace[-2]-trace[-1]) if len(trace) >= 2 else None,
                earlier_drop_db=float(median(trace[:4])-median(trace[-5:-1])) if len(trace)>=13 else None)


def native_diagnoses(row, arrays):
    import numpy as np
    from forecast_prevention.diagnostics import connected, reachability
    from final_v1.causes import pattern
    from outage_atlas.events import component_groups, preceding_terms
    from outage_atlas.radio import probabilities, radio_graph

    result = {}
    ns = row["search_drones"]
    graphs = arrays["links"]
    for start, end in row["cuts"]:
        k = int(round(2*start))
        trace = arrays["margin_db"][max(0,k-12):k+1].tolist()
        groups = component_groups(graphs[k])
        analysis = dict(**shape_summary(trace, pattern(trace)), factors=[], edges=[],
                        groups=[[f"uav-{i}" for i in group if i < ns] for group in groups if group[0] < ns],
                        isolated=[f"uav-{g[0]}" for g in groups if len(g)==1 and g[0]<ns])
        result[str(start)] = analysis
        if not k:
            analysis["evidence_note"] = "비행 시작부터 단절되어 직전 전파 조건을 대조할 수 없습니다."
            continue
        frames = slice(k-1, k+1)
        loss, extra, noise, shadow = (arrays[key][frames] for key in
            ("radio_loss", "radio_extra", "radio_interference", "radio_shadow"))
        prob = probabilities(loss, noise, shadow)
        graph = radio_graph(prob)
        # Self edges do not participate in any reachability path.
        off_diagonal = ~np.eye(graph.shape[1], dtype=bool)
        if not np.array_equal(graph[:,off_diagonal], graphs[frames][:,off_diagonal]):
            raise ValueError(f"{start}초의 전파 조건과 실제 직접 링크가 다릅니다.")
        analysis["factors"] = preceding_terms(loss, extra, noise, shadow, 1, ns)
        xyz = arrays["positions"][frames]
        if "altitudes" in arrays:
            xyz = np.concatenate((xyz, arrays["altitudes"][frames,:,None]), axis=-1)
        for i, j in zip(*np.where(np.triu(graph[0] & ~graph[1] & ~reachability(graph[1]), 1))):
            restored = graph[1].copy()
            restored[i,j] = restored[j,i] = True
            analysis["edges"].append(dict(pair=[f"uav-{i}",f"uav-{j}"], reconnects_alone=bool(connected(restored,ns)),
                building_db=extra[:,i,j].tolist(), distance_m=np.linalg.norm(xyz[:,i]-xyz[:,j],axis=1).tolist(),
                distance_loss_db=(loss[:,i,j]-extra[:,i,j]).tolist(), shadow_db=shadow[:,i,j].tolist(),
                noise_dbm=(10*np.log10(10**-10.7+noise[:,[i,j]])).tolist(),
                min_hop_probability=np.minimum(prob[:,i,j],prob[:,j,i]).tolist()))
    return result


def read_analysis(root, manifest):
    if (root.name,root.parent.name) != (manifest["bundle_revision"],manifest["run_id"]):
        raise ValueError("분석 자료와 선택한 비행이 다릅니다.")
    def unpack(blob):
        return json.loads(blob if isinstance(blob,str) else zlib.decompress(blob))
    uri = (root/"data.sqlite").resolve().as_uri()+"?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri,uri=True)) as db:
        events = db.execute("SELECT event_id,start_s FROM events WHERE type='persistent_search_outage'").fetchall()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='incident_analysis'").fetchone():
            native = {t:unpack(blob) for t,blob in db.execute("SELECT time_s,payload FROM incident_analysis")}
            return {key:native[t] for key,t in events}
        search = {u["id"] for u in manifest["fleet"] if u["role"]=="search"}
        result = {}
        for key,t in events:
            records = [unpack(r[0]) for r in db.execute("SELECT payload FROM rf_frames WHERE time_s>? AND time_s<=? ORDER BY time_s",(t-6,t+.5))]
            if not records:
                continue
            groups = records[-1].get("components",[])
            trace = [r["metrics"].get("network_margin_db") for r in records]
            trace = trace if all(isinstance(v,(int,float)) for v in trace) else []
            summary = shape_summary(trace,"insufficient_history")
            if summary["earlier_drop_db"] is not None:
                summary["pattern"] = ("other","abrupt","gradual","mixed")[int(summary["jump_db"]>=6)+2*int(summary["earlier_drop_db"]>=3)]
            result[key] = dict(**summary,groups=[[u for u in g if u in search] for g in groups if search.intersection(g)],
                isolated=[g[0] for g in groups if len(g)==1 and g[0] in search],factors=[],edges=[],
                evidence_note="이 비행에는 당시의 건물 손실·간섭 기록이 없어 물리적 원인 판정을 보류합니다.")
    return result
