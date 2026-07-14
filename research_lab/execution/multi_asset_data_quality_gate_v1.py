from __future__ import annotations

import hashlib
import json
import math
import copy
from datetime import datetime
from typing import Any

REQUEST_VERSION = "multi_asset_data_quality_gate_request_v1"
RESULT_VERSION = "multi_asset_data_quality_gate_result_v1"
CONTRACT_VERSION = "multi_asset_data_quality_gate_v1"


def build_multi_asset_data_quality_gate(request: dict[str, object]) -> dict[str, object]:
    value = _validate(request)
    blocking: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    assets = _bound_assets(value, blocking)
    universe = value["universe_result"]["validated_universe"]
    instruments = {item["instrument_id"]: item for item in universe["instruments"]}
    snapshot_ids = {item["instrument_id"] for item in assets}
    if snapshot_ids != set(instruments) or len(snapshot_ids) != len(assets) or len(instruments) != len(universe["instruments"]): _finding(blocking, "UNIVERSE_SNAPSHOT_MEMBERSHIP_MISMATCH")
    calendars = {_child_instrument_id(item, "calendar"): _child_payload(item) for item in value["calendar_results"] if _child_instrument_id(item, "calendar")}
    actions = {_child_instrument_id(item, "corporate_action"): _child_payload(item) for item in value["corporate_action_results"] if _child_instrument_id(item, "corporate_action")}
    _child_membership(blocking, set(instruments), value["calendar_results"], "calendar")
    _child_membership(blocking, set(instruments), value["corporate_action_results"], "corporate_action")
    _child_hashes(blocking, value, calendars, actions)
    metrics: dict[str, Any] = {}
    for asset in assets:
        iid = asset["instrument_id"]
        instrument = instruments.get(iid)
        if instrument is None: continue
        if any(asset.get(key) != instrument.get(key) for key in ("provider_symbol", "currency", "calendar_id", "corporate_action_policy_id")):
            _finding(blocking, "INSTRUMENT_IDENTITY_MISMATCH", iid)
        _asset_lineage(blocking, asset, value["multi_asset_snapshot_result"], iid)
        bars = asset.get("bars")
        findings = _bars(bars, iid, value["thresholds"])
        blocking.extend(findings)
        cal = calendars.get(iid)
        if cal is None or cal.get("calendar_id") != asset.get("calendar_id") or cal.get("validation_status") != "COMPLETE" or cal.get("blocking_findings"): _finding(blocking, "CALENDAR_IDENTITY_OR_STATUS_FAILURE", iid)
        elif cal:
            summary = cal.get("validation_summary", {})
            missing=summary.get("missing_sessions", [])
            missing_ratio=summary.get("missing_session_ratio", 0.0)
            missing_exceeds=len(missing) > value["thresholds"]["maximum_missing_session_count"] or missing_ratio > value["thresholds"]["maximum_missing_session_ratio"]
            if missing: _finding(blocking if missing_exceeds and value["policies"]["missing_session_severity"] == "FAIL" else review, "MISSING_SESSIONS", iid, severity="BLOCKING" if missing_exceeds and value["policies"]["missing_session_severity"] == "FAIL" else "REVIEW", observed_value=len(missing), threshold=value["thresholds"]["maximum_missing_session_count"])
            if summary.get("unexpected_sessions", []): _finding(blocking if value["policies"]["unexpected_session_severity"] == "FAIL" else review, "UNEXPECTED_SESSIONS", iid, severity="BLOCKING" if value["policies"]["unexpected_session_severity"] == "FAIL" else "REVIEW")
            for key in ("duplicate_observed_sessions", "bars_on_closed_sessions", "bars_before_open", "bars_after_close", "bars_outside_calendar_coverage"):
                if summary.get(key, []): _finding(blocking, "CALENDAR_" + key.upper(), iid)
        action = actions.get(iid)
        action_identity=action.get("instrument_identity", {}) if isinstance(action,dict) else {}
        if action is None or action.get("contract_status") == "FAILED_VALIDATION" or action.get("blocking_findings") or (action_identity and (action_identity.get("instrument_id") != iid or action_identity.get("provider_symbol") != asset.get("provider_symbol"))) or action.get("price_series_compatibility_status") not in {None,"COMPATIBLE"}: _finding(blocking, "CORPORATE_ACTION_FAILURE", iid)
        elif action.get("adjustment_policy") != asset.get("corporate_action_policy_id"): _finding(blocking, "ADJUSTMENT_POLICY_MISMATCH", iid)
        status = instrument.get("point_in_time_membership_status")
        if status == "EXPLICIT_STATIC_RESEARCH_UNIVERSE" and value["policies"]["static_universe_requires_review"]: _finding(review, "STATIC_UNIVERSE_SURVIVORSHIP_WARNING", iid, severity="REVIEW")
        elif status == "CURRENT_MEMBERSHIP_ONLY": _finding(blocking if not value["policies"]["allow_unsafe_current_membership"] else review, "CURRENT_MEMBERSHIP_UNSAFE", iid, severity="BLOCKING" if not value["policies"]["allow_unsafe_current_membership"] else "REVIEW")
        elif status == "NOT_POINT_IN_TIME_SAFE": _finding(blocking if value["policies"]["not_point_in_time_safe_requires_failure"] else review, "NOT_POINT_IN_TIME_SAFE", iid, severity="BLOCKING" if value["policies"]["not_point_in_time_safe_requires_failure"] else "REVIEW")
        if bars:
            timestamps = [bar["timestamp"] for bar in bars if isinstance(bar, dict) and "timestamp" in bar]
            metrics[iid] = {"row_count": len(bars), "first_timestamp": min(timestamps) if timestamps else None, "last_timestamp": max(timestamps) if timestamps else None}
            _returns(bars, iid, value["thresholds"], review)
    _overlap(metrics, value["thresholds"], blocking)
    _fx(value, assets, blocking)
    blocking.sort(key=_key); review.sort(key=_key)
    overall = "FAILED_VALIDATION" if blocking else "REVIEW_REQUIRED" if review else "PASS"
    per_status = {item["instrument_id"]: "FAILED_VALIDATION" if any(f.get("instrument_id") == item["instrument_id"] for f in blocking) else "REVIEW_REQUIRED" if any(f.get("instrument_id") == item["instrument_id"] for f in review) else "PASS" for item in sorted(assets, key=lambda x: x["instrument_id"])}
    result: dict[str, Any] = {"version": RESULT_VERSION, "contract_version": CONTRACT_VERSION, "quality_gate_id": value["quality_gate_id"], "overall_status": overall, "per_asset_status": per_status, "per_asset_metrics": {key: metrics[key] for key in sorted(metrics)}, "blocking_findings": blocking, "review_findings": review, "informational_findings": [], "bar_quality_summary": {"asset_count": len(assets)}, "calendar_summary": {"calendar_count": len(calendars)}, "overlap_summary": _overlap_summary(metrics), "discontinuity_summary": {"threshold": value["thresholds"]["maximum_single_period_return_abs"]}, "corporate_action_summary": {"asset_count": len(actions)}, "currency_and_fx_summary": {"base_currency": universe["base_currency"]}, "universe_and_survivorship_summary": {"statuses": {key: sorted(item["instrument_id"] for item in universe["instruments"] if item.get("point_in_time_membership_status") == key) for key in sorted({item.get("point_in_time_membership_status") for item in universe["instruments"]})}}, "exact_child_lineage": value["expected_child_hashes"], "input_sha256": _hash(value), "provider_calls_used": 0, "network_used": False, "filesystem_reads_performed": False, "filesystem_writes_performed": False, "data_mutation_performed": False, "registry_write_performed": False, "broker_actions_used": 0, "paper_trading_performed": False, "deployment_performed": False, "promotion_performed": False, "generated_code_executed": False, "production_runtime_supported": False, "provenance": value["provenance"]}
    result["output_payload_sha256"] = _hash(result); return result

def _validate(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ValueError("request must be an object.")
    allowed = {"version","quality_gate_id","universe_result","multi_asset_snapshot_result","asset_bar_bindings","calendar_results","corporate_action_results","fx_result","thresholds","policies","expected_child_hashes","as_of_timestamp","provenance"}
    unknown = set(raw) - allowed
    if unknown: raise ValueError("unknown field")
    if raw.get("version") != REQUEST_VERSION: raise ValueError("version")
    value = copy.deepcopy(raw)
    if not isinstance(value.get("quality_gate_id"), str) or not value["quality_gate_id"]: raise ValueError("quality_gate_id")
    _timestamp(value.get("as_of_timestamp")); _mapping(value.get("universe_result")); _mapping(value.get("multi_asset_snapshot_result")); _list(value.get("asset_bar_bindings")); _list(value.get("calendar_results")); _list(value.get("corporate_action_results")); _mapping(value.get("expected_child_hashes")); _mapping(value.get("provenance"))
    thresholds = _mapping(value.get("thresholds")); policies = _mapping(value.get("policies"))
    for key in ("minimum_rows_per_asset","minimum_common_overlap_days","maximum_end_staleness_days","maximum_start_delay_days","maximum_missing_session_count","maximum_missing_session_ratio","maximum_unexpected_session_count","maximum_single_period_return_abs","split_candidate_tolerance"):
        if not isinstance(thresholds.get(key), (int,float)) or isinstance(thresholds[key], bool) or not math.isfinite(float(thresholds[key])) or thresholds[key] < 0: raise ValueError("malformed threshold")
    if not isinstance(thresholds.get("volume_integrality_required"), bool): raise ValueError("malformed threshold")
    for key in ("static_universe_requires_review","allow_unsafe_current_membership","not_point_in_time_safe_requires_failure"):
        if not isinstance(policies.get(key), bool): raise ValueError("policies")
    if policies.get("missing_session_severity") not in {"FAIL","REVIEW"} or policies.get("unexpected_session_severity") not in {"FAIL","REVIEW"}: raise ValueError("policies")
    return value

def _bars(bars: Any, iid: str, thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    findings=[]
    if not isinstance(bars, list) or not bars: _finding(findings,"ZERO_ROWS",iid); return findings
    if len(bars) < thresholds["minimum_rows_per_asset"]: _finding(findings,"INSUFFICIENT_ROWS",iid)
    previous=None; seen=set()
    for bar in bars:
        if not isinstance(bar, dict): _finding(findings,"MALFORMED_BAR",iid); continue
        try: timestamp=_timestamp(bar.get("timestamp"))
        except ValueError: _finding(findings,"MALFORMED_TIMESTAMP",iid); continue
        if timestamp in seen: _finding(findings,"DUPLICATE_TIMESTAMP",iid)
        if previous and timestamp < previous: _finding(findings,"UNORDERED_TIMESTAMP",iid)
        seen.add(timestamp); previous=timestamp
        values={key: bar.get(key) for key in ("open","high","low","close","volume")}
        if any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(float(x)) for x in values.values()): _finding(findings,"NONFINITE_OHLCV",iid); continue
        if any(values[key] <= 0 for key in ("open","high","low","close")) or values["volume"] < 0: _finding(findings,"INVALID_PRICE_OR_VOLUME",iid)
        if thresholds["volume_integrality_required"] and float(values["volume"]).is_integer() is False: _finding(findings,"NONINTEGRAL_VOLUME",iid)
        if values["high"] < max(values["open"],values["close"],values["low"]) or values["low"] > min(values["open"],values["close"]): _finding(findings,"INVALID_OHLC_RELATIONSHIP",iid)
    return findings

def _bound_assets(value, blocking):
    """Create inspection views solely from snapshot metadata and exact adapters."""
    universe=value["universe_result"].get("validated_universe", {})
    instruments={item.get("instrument_id"): item for item in universe.get("instruments", []) if isinstance(item,dict)}
    snapshots={item.get("instrument_id"): item for item in value["multi_asset_snapshot_result"].get("validated_asset_series", []) if isinstance(item,dict)}
    bindings=value["asset_bar_bindings"]
    ids=[item.get("instrument_id") for item in bindings if isinstance(item,dict)]
    required=set(instruments)
    if set(ids) != required or len(ids) != len(set(ids)):
        _finding(blocking,"ASSET_BAR_BINDING_MEMBERSHIP_MISMATCH")
    bound=[]
    for binding in bindings:
        if not isinstance(binding,dict) or set(binding) - {"instrument_id","adapter_result","adapter_result_sha256","provenance"}:
            _finding(blocking,"MALFORMED_ASSET_BAR_BINDING"); continue
        iid=binding.get("instrument_id"); adapter=binding.get("adapter_result")
        snapshot=snapshots.get(iid); instrument=instruments.get(iid)
        if not isinstance(iid,str) or not isinstance(adapter,dict) or not snapshot or not instrument:
            _finding(blocking,"ASSET_BAR_BINDING_IDENTITY_MISMATCH",iid); continue
        adapter_hash=_hash(adapter)
        if binding.get("adapter_result_sha256") != adapter_hash or snapshot.get("adapter_result_sha256") != adapter_hash:
            _finding(blocking,"ADAPTER_RESULT_HASH_MISMATCH",iid)
        expected={"version":"local_ohlcv_file_input_adapter_result_v1","adapter_version":"local_ohlcv_file_input_adapter_v1","status":"SUCCESS"}
        if any(adapter.get(key) != item for key,item in expected.items()) or adapter.get("network_used") is not False or adapter.get("provider_calls_used") != 0 or adapter.get("production_runtime_supported") is not False:
            _finding(blocking,"ADAPTER_RESULT_SAFETY_OR_STATUS_FAILURE",iid)
        for adapter_key,snapshot_key,code in (("source_sha256","source_artifact_sha256","SOURCE_FILE_HASH_MISMATCH"),("normalized_rows_hash","normalized_bars_sha256","NORMALIZED_BARS_HASH_MISMATCH"),("row_count","row_count","ROW_COUNT_MISMATCH"),("first_timestamp","first_timestamp","FIRST_TIMESTAMP_MISMATCH"),("last_timestamp","last_timestamp","LAST_TIMESTAMP_MISMATCH")):
            if adapter.get(adapter_key) != snapshot.get(snapshot_key): _finding(blocking,code,iid)
        downstream=adapter.get("downstream_adapter_result")
        bars=downstream.get("synthetic_bars") if isinstance(downstream,dict) else None
        if not isinstance(bars,list): _finding(blocking,"MISSING_ADAPTER_SYNTHETIC_BARS",iid); bars=[]
        bound.append({**snapshot,"bars":bars,"currency":instrument.get("currency"),"calendar_id":instrument.get("calendar_id"),"corporate_action_policy_id":instrument.get("corporate_action_policy_id"),"provider_symbol":instrument.get("provider_symbol")})
    return bound

def _returns(bars, iid, thresholds, review):
    for prior,current in zip(bars,bars[1:]):
        if isinstance(prior,dict) and isinstance(current,dict) and isinstance(prior.get("close"),(int,float)) and prior["close"] > 0 and isinstance(current.get("close"),(int,float)):
            ret=abs(current["close"]/prior["close"]-1)
            if ret > thresholds["maximum_single_period_return_abs"]: _finding(review,"UNEXPLAINED_DISCONTINUITY",iid,threshold=thresholds["maximum_single_period_return_abs"],observed_value=ret,severity="REVIEW")

def _overlap(metrics, thresholds, blocking):
    if not metrics:return
    starts=[m["first_timestamp"] for m in metrics.values() if m["first_timestamp"]]; ends=[m["last_timestamp"] for m in metrics.values() if m["last_timestamp"]]
    if not starts or max(starts)>min(ends): _finding(blocking,"NO_COMMON_OVERLAP"); return
    days=(datetime.fromisoformat(min(ends).replace("Z","+00:00"))-datetime.fromisoformat(max(starts).replace("Z","+00:00"))).days
    if days < thresholds["minimum_common_overlap_days"]: _finding(blocking,"INSUFFICIENT_COMMON_OVERLAP")
    latest=max(ends); oldest=min(ends)
    if (datetime.fromisoformat(latest.replace("Z","+00:00"))-datetime.fromisoformat(oldest.replace("Z","+00:00"))).days > thresholds["maximum_end_staleness_days"]: _finding(blocking,"STALE_ENDING_ASSET")
    earliest_start=min(starts); latest_start=max(starts)
    if (datetime.fromisoformat(latest_start.replace("Z","+00:00"))-datetime.fromisoformat(earliest_start.replace("Z","+00:00"))).days > thresholds["maximum_start_delay_days"]: _finding(blocking,"LATE_STARTING_ASSET")
def _overlap_summary(metrics):
    if not metrics:return {"common_overlap_days":0}
    starts=[m["first_timestamp"] for m in metrics.values() if m["first_timestamp"]]; ends=[m["last_timestamp"] for m in metrics.values() if m["last_timestamp"]]
    if not starts or max(starts)>min(ends): return {"common_overlap_days":0}
    return {"common_overlap_start":max(starts),"common_overlap_end":min(ends),"common_overlap_days":(datetime.fromisoformat(min(ends).replace("Z","+00:00"))-datetime.fromisoformat(max(starts).replace("Z","+00:00"))).days}
def _fx(value, assets, blocking):
    currencies={a.get("currency") for a in assets}; base=value["universe_result"]["validated_universe"].get("base_currency")
    fx=value.get("fx_result")
    if currencies != {base} and not fx:
        _finding(blocking,"MISSING_REQUIRED_FX"); return
    if fx is None: return
    if fx.get("conversion_status") != "SUCCESS" or fx.get("base_currency") != base:
        _finding(blocking,"FX_CHILD_FAILURE"); return
    expected=value["expected_child_hashes"].get("fx")
    if expected is None or fx.get("output_payload_sha256") != expected:
        _finding(blocking,"CHILD_OUTPUT_HASH_MISMATCH", child_identity="fx")
    conversions={item.get("instrument_id"): item for item in fx.get("converted_values", []) if isinstance(item,dict)}
    for asset in assets:
        if asset.get("currency") == base: continue
        conversion=conversions.get(asset.get("instrument_id"))
        if not conversion:
            _finding(blocking,"MISSING_REQUIRED_FX",asset.get("instrument_id")); continue
        if conversion.get("source_currency") != asset.get("currency") or conversion.get("target_currency") != base:
            _finding(blocking,"FX_CURRENCY_MISMATCH",asset.get("instrument_id"))
        if conversion.get("decision_timestamp") and conversion["decision_timestamp"] > value["as_of_timestamp"]:
            _finding(blocking,"FUTURE_FX_CONVERSION",asset.get("instrument_id"))

def _child_membership(blocking, expected, children, name):
    ids=[_child_instrument_id(item, name) for item in children]
    if set(ids) != expected or len(ids) != len(set(ids)):
        _finding(blocking,"CHILD_INSTRUMENT_MEMBERSHIP_MISMATCH",child_identity=name)

def _child_hashes(blocking, value, calendars, actions):
    expected=value["expected_child_hashes"]
    pairs=(("universe",value["universe_result"]),("snapshot",value["multi_asset_snapshot_result"]))
    for name, child in pairs:
        actual=child.get("output_payload_sha256")
        if expected.get(name) is None or actual != expected.get(name):
            _finding(blocking,"CHILD_OUTPUT_HASH_MISMATCH",child_identity=name,expected_value=expected.get(name),observed_value=actual)
    for name, children in (("calendars",calendars),("corporate_actions",actions)):
        mapped=expected.get(name)
        for iid, child in children.items():
            actual=child.get("output_payload_sha256")
            wanted=mapped.get(iid) if isinstance(mapped,dict) else None
            if wanted is None or actual != wanted:
                _finding(blocking,"CHILD_OUTPUT_HASH_MISMATCH",iid,child_identity=name,expected_value=wanted,observed_value=actual)

def _child_payload(item):
    if not isinstance(item,dict): return {}
    payload=item.get("result", item.get("calendar_result", item.get("corporate_action_result", item)))
    return payload if isinstance(payload,dict) else {}

def _child_instrument_id(item, kind):
    if not isinstance(item,dict): return None
    payload=_child_payload(item)
    if isinstance(item.get("instrument_id"),str): return item["instrument_id"]
    if isinstance(payload.get("instrument_id"),str): return payload["instrument_id"]
    if kind == "corporate_action":
        identity=payload.get("instrument_identity")
        if isinstance(identity,dict) and isinstance(identity.get("instrument_id"),str): return identity["instrument_id"]
    return None

def _asset_lineage(blocking, asset, snapshot, iid):
    """Bind per-asset hashes to the snapshot's immutable manifest when supplied."""
    maps=(
        ("source_artifact_sha256", snapshot.get("per_asset_source_hashes"), "SOURCE_FILE_HASH_MISMATCH"),
        ("normalized_bars_sha256", snapshot.get("per_asset_normalized_hashes"), "NORMALIZED_BARS_HASH_MISMATCH"),
    )
    for field, mapping, code in maps:
        actual=asset.get(field)
        if actual is not None and not _sha(actual):
            _finding(blocking,"MALFORMED_SHA256",iid,observed_value=actual)
        if isinstance(mapping,dict) and mapping.get(iid) != actual:
            _finding(blocking,code,iid,expected_value=mapping.get(iid),observed_value=actual)
    adapters=snapshot.get("per_asset_adapter_hashes")
    if isinstance(adapters,dict) and adapters.get(iid) != asset.get("adapter_result_sha256"):
        _finding(blocking,"ADAPTER_RESULT_HASH_MISMATCH",iid,expected_value=adapters.get(iid),observed_value=asset.get("adapter_result_sha256"))

def _sha(value):
    return isinstance(value,str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())

def _finding(target, code, iid=None, severity="BLOCKING", **extra):
    target.append({"finding_code":code,"severity":severity,"instrument_id":iid,**extra})
def _key(item): return (item.get("severity",""),item.get("instrument_id") or "",item["finding_code"])
def _hash(value): return hashlib.sha256(json.dumps(_hashable(value),sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
def _hashable(value):
    if isinstance(value,float) and not math.isfinite(value): return "NONFINITE"
    if isinstance(value,dict): return {key:_hashable(item) for key,item in value.items()}
    if isinstance(value,list): return [_hashable(item) for item in value]
    return value
def _mapping(value):
    if not isinstance(value,dict): raise ValueError("object required")
    return value
def _list(value):
    if not isinstance(value,list): raise ValueError("list required")
    return value
def _timestamp(value):
    if not isinstance(value,str) or not value.endswith("Z"): raise ValueError("timestamp")
    return datetime.fromisoformat(value[:-1]+"+00:00").isoformat().replace("+00:00","Z")
