"""Scene input for external demonstrations in an isolated team inference process."""
import ast
import hashlib
from pathlib import Path
import re
from unittest.mock import patch


def resolve_demo_scene(core, card):
    region = card.get("key", "")
    if not isinstance(region, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", region):
        raise ValueError("시연 지도 이름을 확인할 수 없습니다.")
    expected = Path("dashboard_execution", "maps", region, "scene.local.json")
    filename = card.get("path")
    if not isinstance(filename, str) or Path(filename) != expected or card.get("joint_split") != "external_demonstration":
        raise ValueError(f"선택한 시연 지도의 출처 또는 경로가 다릅니다: {region}")
    root = Path(core).resolve()
    path = (root / expected).resolve()
    if path != root / expected or not path.is_file():
        raise ValueError(f"선택한 시연 지도 파일이 없거나 외부 경로입니다: {region}")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != card.get("sha256"):
            raise ValueError(f"선택한 시연 지도의 파일 식별값이 다릅니다: {region}")
    return path


def survey_contract(source):
    """Read the public-input admission rule without importing the research environment."""
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "survey_for"]
    if len(functions) != 1:
        return None, None
    method = functions[0]
    for stmt in method.body:
        if not isinstance(stmt, ast.If) or len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.Raise):
            continue
        cmp = stmt.test
        if (isinstance(cmp, ast.Compare) and len(cmp.ops) == 1 and isinstance(cmp.ops[0], ast.NotIn)
                and isinstance(cmp.left, ast.Subscript) and isinstance(cmp.left.slice, ast.Constant)
                and cmp.left.slice.value == "study_role" and isinstance(cmp.comparators[0], (ast.Tuple, ast.List, ast.Set))
                and all(isinstance(i, ast.Constant) and isinstance(i.value, str) for i in cmp.comparators[0].elts)):
            body = ast.Module(body=[node for node in method.body if node is not stmt], type_ignores=[])
            roles = frozenset(i.value for i in cmp.comparators[0].elts)
            return roles, hashlib.sha256(ast.unparse(body).encode("utf-8")).hexdigest()
    return None, None


def study_roles(source):
    return survey_contract(source)[0]


def demo_survey_supported(source):
    # 5a6a775 fast_halfsec/context.py::survey_for. A new preparation recipe needs
    # an explicit parity check. Changing the native algorithms called below does
    # not invalidate this boundary; they are loaded from the selected team commit.
    return survey_contract(source)[1] == "c662659b8531765060288eecd2a9a88b62ce564ddeda7de0f658cbef6fb72ffc"


def needs_demo(entry, roles):
    if entry is None:
        return True
    if roles is None or entry.get("study_role") in roles:
        return False
    if entry.get("study_role") == "legacy_protected":
        return True
    raise ValueError(f"이 팀 지도는 시연 입력으로 사용할 수 없습니다: {entry.get('study_role')}")


def demo_survey(context, card, scene, region, direction):
    """Run the verified native survey recipe with an explicit scene as its only new input."""
    if region != card["key"]:
        raise ValueError("비행의 시연 지역과 지도 입력이 다릅니다.")
    context.initialize()
    scenario = context.Scenario(direction=direction, motion_dt_s=3.).validate()
    city = context.prepare_city_without_unused_radio_cache(scene, scenario, context.ARTIFACTS / "cache/city")
    city.radio.enable_cache()

    # Native fleet selection reads public geometry via these two imported aliases.
    # Supply only this exact scene, only for this inference. Study registries and
    # their train/validation/test permissions are never rewritten.
    def selected_entry(key):
        if key != region:
            raise ValueError("시연 중 요청한 지도와 선택한 지도가 다릅니다.")
        return {**card, "path": str(scene)}

    with (patch.object(context.legacy, "entry_for", selected_entry),
          patch.object(context.map_features, "entry_for", selected_entry)):
        fleet = context.legacy.deployment_for(dict(region=region, direction=direction))
    plan = context.ensure_nominal(city, fleet, scenario)
    with context.np.load(plan, allow_pickle=False) as archive:
        survey = context.make_survey(city, fleet.ns, archive["nodes"])
    anchor = context.ensure_nominal(city, context.contracts.DEPLOYMENT_BY_KEY["v8_s3_r5_40"], scenario)
    with context.np.load(anchor, allow_pickle=False) as archive:
        anchor_s = (len(archive["nodes"]) - 1) * 3.
    return city, scenario, fleet, survey, anchor_s
