"""Time-aware immutable queries and definition-safe descriptive comparisons."""
from collections import defaultdict
from pathlib import Path
import json, math, statistics
from .common import database, decode, metadata, digest, finite_number

def metrics(manifest,bundle):
    if bundle:
        with database(bundle) as c:return [decode(r[0]) for r in c.execute("SELECT payload FROM metrics ORDER BY metric_id")]
    return [{"metric_id":k,"value":v,"definition":manifest.get("metric_definitions",{}).get(k)} for k,v in manifest.get("final_metrics",{}).items()]

def full_manifest(manifest,bundle):
    return dict(manifest,metadata=metadata(manifest),metrics=metrics(manifest,bundle))

def timeline(manifest,bundle,from_s=0,to_s=60,at=0,observer=None,peer=None,channels=None,request_id=None):
    if not bundle:raise ValueError("raw_unavailable: this record has summary only")
    end=float(manifest["simulation_end_s"])
    values=(float(from_s),float(to_s),float(at))
    if not all(math.isfinite(v) for v in values):raise ValueError("nonfinite_time")
    a,b,t=values
    if a<0 or b<a or b>end+1e-6 or t<0 or t>end+1e-6:raise ValueError("time_out_of_range")
    requested=set(channels.split(",")) if isinstance(channels,str) else set(channels or ("positions","rf_frames","decisions","observations"))
    if not requested.issubset({"positions","rf_frames","decisions","observations"}):raise ValueError("unknown_channel")
    ids={x["id"] for x in manifest["fleet"]}
    if (observer and observer not in ids) or (peer and peer not in ids):raise ValueError("unknown_uav")
    if "observations" in requested and not (observer and peer) and b-a>60:
        requested.remove("observations") # latest snapshot stays available; full histories need an explicit pair.
    response={"run_id":manifest["run_id"],"bundle_revision":manifest["bundle_revision"],"request_id":request_id,
              "window":{"from_s":a,"to_s":b},"selected_time_s":t,
              "positions":[],"rf_frames":[],"observations":[],"decisions":[]}
    with database(bundle) as c:
        def one(sql,args=()):
            row=c.execute(sql,args).fetchone();return decode(row[0]) if row else None
        def many(sql,args=()):return [decode(r[0]) for r in c.execute(sql,args)]
        for table in ("positions","decisions"):
            if table not in requested:continue
            response[table]=many(f"SELECT payload FROM {table} WHERE time_s>=? AND time_s<=? ORDER BY time_s",(a,b))
            before=one(f"SELECT payload FROM {table} WHERE time_s<? ORDER BY time_s DESC LIMIT 1",(a,))
            if before:response[table].insert(0,before)
            if table=="positions":
                after=one("SELECT payload FROM positions WHERE time_s>? ORDER BY time_s LIMIT 1",(b,))
                if after:response[table].append(after)
        if "rf_frames" in requested:
            response["rf_frames"]=many("SELECT payload FROM rf_frames WHERE interval_end_s>=? AND interval_start_s<=? ORDER BY time_s",(a,b))
        pair_clause="";pair_args=[]
        if observer:pair_clause+=" AND observer=?";pair_args.append(observer)
        if peer:pair_clause+=" AND peer=?";pair_args.append(peer)
        if "observations" in requested:
            response["observations"]=many("SELECT payload FROM observations WHERE time_s>=? AND time_s<=?"+pair_clause+" ORDER BY time_s,observer,peer",(a,b,*pair_args))
        # max time is queried per pair; older/missing samples keep their recorded age and validity.
        latest=many("SELECT o.payload FROM observations o JOIN (SELECT observer,peer,MAX(time_s) t FROM observations WHERE time_s<=?"+pair_clause+" GROUP BY observer,peer) x ON o.observer=x.observer AND o.peer=x.peer AND o.time_s=x.t",(t,*pair_args))
        response["at"]={
            "position_before":one("SELECT payload FROM positions WHERE time_s<=? ORDER BY time_s DESC LIMIT 1",(t,)),
            "position_after":one("SELECT payload FROM positions WHERE time_s>=? ORDER BY time_s LIMIT 1",(t,)),
            "link_frame":one("SELECT payload FROM rf_frames WHERE interval_start_s<=? AND interval_end_s>? ORDER BY interval_start_s DESC LIMIT 1",(t,t)),
            "packet_frame":one("SELECT payload FROM rf_frames WHERE interval_end_s<=? ORDER BY interval_end_s DESC LIMIT 1",(t,)),
            "decision":one("SELECT payload FROM decisions WHERE time_s<=? ORDER BY time_s DESC LIMIT 1",(t,)),
            "observations":latest
        }
    response["semantics"]={"position":"display interpolation only between bracketing samples",
      "link":"containing RF interval; no extrapolation at simulation end",
      "packet":"last completed RF interval; final summary includes separate drain",
      "observation":"available_at <= selected time; mask and age preserved",
      "decision":"last generated decision <= selected time; posthoc labels remain separate"}
    return response

def events(manifest,bundle,from_s=None,to_s=None,event_type=None,limit=10000,offset=0):
    if not bundle:return {"events":[],"total":0,"reason":"raw_unavailable"}
    limit=min(20000,max(1,int(limit)));offset=max(0,int(offset))
    clauses=[];args=[]
    if from_s is not None:clauses.append("COALESCE(end_s,?)>=?");args.extend([manifest.get("simulation_end_s",float(from_s)),float(from_s)])
    if to_s is not None:clauses.append("start_s<=?");args.append(float(to_s))
    if event_type:clauses.append("type=?");args.append(event_type)
    where=" WHERE "+" AND ".join(clauses) if clauses else ""
    with database(bundle) as c:
        count=c.execute("SELECT COUNT(*) FROM events"+where,args).fetchone()[0]
        data=[decode(row[0]) for row in c.execute("SELECT payload FROM events"+where+" ORDER BY start_s,event_id LIMIT ? OFFSET ?",(*args,limit,offset))]
    return {"events":data,"total":count,"offset":offset,"limit":limit,
            "run_id":manifest["run_id"],"bundle_revision":manifest["bundle_revision"]}

def compare(registry,selections,metric_ids=None):
    if not isinstance(selections,list) or len(selections)>100:raise ValueError("comparison_limit_100")
    output=[];excluded=[];seen=set();grouped=defaultdict(list)
    conflicts={r["run_id"] for r in registry.list()["runs"] if r.get("conflict")}
    uncertain=[]
    episodes=[]
    group_definitions={}
    counts={"selected":len(selections),"included":0,"mission_success":0,"mission_failure":0,
            "process_failure_or_interrupted":0,"data_error":0,"summary_only":0,"duplicate":0,"parent_excluded":0}
    for sel in selections:
        if not isinstance(sel,dict):raise ValueError("invalid_selection_object")
        rid=sel.get("run_id");rev=sel.get("revision",sel.get("bundle_revision"))
        if rid in seen:
            counts["duplicate"]+=1;excluded.append({"run_id":rid,"reason":"same identity/revisions are not independent samples"});continue
        seen.add(rid)
        if rid in conflicts:
            counts["data_error"]+=1;excluded.append({"run_id":rid,"reason":"source_key_conflict: independent occurrence unverified"});continue
        try:m,bundle=registry.get(rid,rev)
        except Exception as exc:
            counts["data_error"]+=1;excluded.append({"run_id":rid,"reason":str(exc)});continue
        if m["artifact_kind"]!="episode":
            key="summary_only" if m["artifact_kind"]=="episode_summary" else "parent_excluded"
            counts[key]+=1;excluded.append({"run_id":rid,"reason":m["artifact_kind"]+": no independent validated episode"});continue
        if m.get("stage_status") not in ("completed","complete","succeeded"):
            counts["process_failure_or_interrupted"]+=1;excluded.append({"run_id":rid,"reason":m.get("stage_status")});continue
        episodes.append((rid,rev,m))
    # A missing result must not change which definitions identify an experiment.
    requested = metric_ids or sorted({
        key for _, _, manifest in episodes for key in manifest.get("metric_definitions", {})
    } | {
        key for _, _, manifest in episodes for key in manifest.get("final_metrics", {})
    })
    requested = list(dict.fromkeys(k.split(".",1)[1] if k.startswith(("v5.","final-v1.")) else k for k in requested))
    for rid,rev,m in episodes:
        meta=metadata(m);final=m.get("final_metrics",{})
        mission=m.get("mission_completed",final.get("mission_completed",meta.get("mission_completed")))
        counts["included"]+=1
        if mission is True:counts["mission_success"]+=1
        elif mission is False:counts["mission_failure"]+=1
        definitions=m.get("metric_definitions",{})
        values={}
        applicable={}
        missing_metrics={}
        for key in requested:
            definition=definitions.get(key)
            if not definition:
                missing_metrics[key]="선택한 비행에 지표 정의가 없습니다."
                continue
            if not definition.get("comparable", True):
                missing_metrics[key]="지표 정의에서 실행 간 비교를 지원하지 않습니다."
                continue
            applicable[key]=definition
            value=int(final[key]) if isinstance(final.get(key),bool) and definition.get("unit")=="boolean" else finite_number(final.get(key))
            if value is None:
                missing_metrics[key]=("최종 지표값이 기록되지 않았습니다." if final.get(key) is None
                                      else "기록된 값이 유효한 수치가 아닙니다.")
                continue
            values[key]={"value":value,"definition":definition}
        conditions={k:meta.get(k) for k in ("map_hash","region","kind","direction","split","mode","model_id","research_version","config_hash","interference","placement","termination_rule","evaluation_window")}
        # Unknown conditions are shown explicitly. Only known same configuration hashes establish a comparable scope.
        conditions["event_definitions"]=m.get("event_definitions",{})
        missing=[key for key in ("map_hash","kind","direction","split","mode","model_id","research_version","config_hash") if not conditions.get(key)]
        if missing:
            conditions["unverified_conditions_run_identity"]=rid
            uncertain.append({"run_id":rid,"missing":missing})
        signature_definitions={k:definitions[k] for k in requested if definitions.get(k)}
        definition_signature=digest({k:d.get("definition_hash",digest(d)) for k,d in signature_definitions.items()})
        signature=digest([conditions,definition_signature])
        group_definitions[signature]=applicable
        row={"run_id":rid,"bundle_revision":rev,"metadata":meta,"metrics":values,
             "missing_metrics":missing_metrics,
             "definition_signature":definition_signature,"condition_signature":signature,"mission_completed":mission,"mission_status":"completed" if mission is True else "not_completed" if mission is False else "not_assessed",
             "packet_counts":{"generated":final.get("generated_packets"),"delivered":final.get("delivered_packets")}}
        output.append(row);grouped[signature].append(row)
    groups=[]
    for signature,rows in grouped.items():
        stats=[]
        for key,definition in sorted(group_definitions[signature].items()):
            vals=[r["metrics"][key]["value"] for r in rows if key in r["metrics"]]
            direction=definition.get("preferred_direction")
            lowest=min(vals,default=None)
            highest=max(vals,default=None)
            worst=lowest if direction in ("higher","maximize","up") else highest if direction in ("lower","minimize","down") else None
            stats.append({"metric_id":key,"label":definition.get("label",key),"unit":definition.get("unit"),
              "selected":len(rows),"n":len(vals),"missing":len(rows)-len(vals),"mean":statistics.fmean(vals) if vals else None,
              "std":statistics.stdev(vals) if len(vals)>1 else None,"min":lowest,"max":highest,
              "median":statistics.median(vals) if vals else None,"worst":worst,"values":vals,"definition":definition})
        packet_rows=[r for r in rows if finite_number(r["packet_counts"]["generated"]) is not None and finite_number(r["packet_counts"]["delivered"]) is not None]
        generated=sum(r["packet_counts"]["generated"] for r in packet_rows)
        delivered=sum(r["packet_counts"]["delivered"] for r in packet_rows)
        groups.append({"condition_signature":signature,"definition_signature":rows[0]["definition_signature"],
          "run_ids":[r["run_id"] for r in rows],"selected":len(rows),"metadata":rows[0]["metadata"],"metrics":stats,
          "pooled_pdr":{"value":delivered/generated if generated else None,"generated":generated,"delivered":delivered,
                        "scope":"pooled_packets_within_same_conditions","zero_denominator":generated==0,"n":len(packet_rows),"missing":len(rows)-len(packet_rows)}})
    warnings=[]
    if uncertain:warnings.append("Unknown comparison conditions: each affected run remains a separate group.")
    if len(groups)>1:warnings.append("지도·조건·모델·지표 정의가 다른 실행을 별도 그룹으로 표시했습니다. 그룹 간 평균을 합치지 않습니다.")
    if excluded:warnings.append("검증되지 않은 상세 데이터·상위 집계·중복 revision을 독립 표본에서 제외했습니다.")
    return {"groups":groups,"runs":output,"warnings":warnings,"counts":counts,"excluded":excluded,"unverified_conditions":uncertain,
      "pooled_pdr":groups[0]["pooled_pdr"] if len(groups)==1 else {"value":None,"reason":"incompatible_groups_or_no_valid_denominator"}}
