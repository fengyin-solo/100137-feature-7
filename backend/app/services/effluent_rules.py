"""出水达标判定规则：排放标准字典、厂站口径映射与版本化判定。

判定口径（对应需求）：
1. COD出水值、氨氮出水值、总磷出水值三项分别与所属厂站适用的排放限值比对，
   任一超标即整体"超标"，全部不超限才"达标"；超标给出每项的倍数区间。
2. 排放流量独立校验（按厂站正常区间），异常只做单独标记，不参与达标判定。
3. 三项指标缺失或格式不对时不生成结论，逐条说明原因。
4. 厂站适用标准按 STATION_RULES 区分；未配置口径的厂站不出结论。
5. 规则变更会提升版本号；既有记录默认保留录入时的规则快照，不会被新规则悄悄改写，
   需要时通过"重新判定"显式重跑。
6. 每条判定随记录保存规则快照（限值、区间、版本、输入值与哈希），
   复核时用快照原样重算并比对哈希，保证复核人与录入时看到的依据完全一致。
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 参与限值判定的三项指标：记录字段 -> (快照键名, 中文名称)
INDICATORS: tuple[tuple[str, str, str], ...] = (
    ("COD出水值", "cod", "COD"),
    ("氨氮出水值", "nh3n", "氨氮"),
    ("总磷出水值", "tp", "总磷"),
)

# 超标倍数区间的分桶边界（含下界、不含上界），最后一桶无上限。
MULTIPLE_BUCKETS: tuple[tuple[float, float | None], ...] = (
    (1.0, 2.0),
    (2.0, 3.0),
    (3.0, 5.0),
    (5.0, 10.0),
    (10.0, None),
)

# 排放标准字典：限值单位 mg/L。一级A氨氮按水温>12℃的常用口径 5.0。
# 数值调整即构成规则变更（版本号随之提升）。
DISCHARGE_STANDARDS: dict[str, dict[str, float]] = {
    "一级A": {"cod": 50.0, "nh3n": 5.0, "tp": 0.5},
    "一级B": {"cod": 60.0, "nh3n": 8.0, "tp": 1.0},
    "二级": {"cod": 100.0, "nh3n": 25.0, "tp": 3.0},
    "三级": {"cod": 120.0, "nh3n": 35.0, "tp": 5.0},
}

# 厂站判定口径：厂站名 -> 适用标准 + 排放流量正常区间（m³/h，闭区间）。
STATION_RULES: dict[str, dict[str, Any]] = {
    "城东净水厂": {"standard": "一级A", "flow_min": 200.0, "flow_max": 900.0},
    "城西污水处理厂": {"standard": "一级B", "flow_min": 150.0, "flow_max": 700.0},
    "南港工业区污水厂": {"standard": "二级", "flow_min": 100.0, "flow_max": 600.0},
    "北岛生态站": {"standard": "三级", "flow_min": 30.0, "flow_max": 200.0},
}


def _bucket_label(ratio: float) -> str:
    """把超标倍数（>=1）落到固定的倍数区间描述上。"""
    for low, high in MULTIPLE_BUCKETS:
        if ratio >= low and (high is None or ratio < high):
            return f"{low:g}~{high:g}倍" if high is not None else f">{low:g}倍"
    return ">10倍"


def parse_number(raw: Any) -> tuple[float | None, str | None]:
    """解析监测数值：空值/非数字/负数都视为不可用并给出原因。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "指标值缺失"
    if isinstance(raw, bool):
        return None, f"数值格式不对：{raw!r} 不是有效数字"
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip().replace("mg/L", "").replace("mg/l", "").strip()
        try:
            value = float(text)
        except ValueError:
            return None, f"数值格式不对：「{raw}」不是有效数字"
    else:
        return None, f"数值格式不对：{raw!r} 不是有效数字"
    if value < 0:
        return None, f"数值不能为负：{value:g}"
    return value, None


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    """对规则快照做哈希，复核时据此判断依据是否被改动过。"""
    body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


@dataclass
class RuleBook:
    """内存中的规则册：带版本号，限值/口径调整后版本递增，历史快照不受影响。"""

    version: int = 1
    standards: dict[str, dict[str, float]] = field(
        default_factory=lambda: deepcopy(DISCHARGE_STANDARDS)
    )
    stations: dict[str, dict[str, Any]] = field(
        default_factory=lambda: deepcopy(STATION_RULES)
    )
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.updated_at:
            self.updated_at = _now()

    def bump(self) -> None:
        self.version += 1
        self.updated_at = _now()

    def station_standard(self, station: str) -> str | None:
        rule = self.stations.get(station)
        return str(rule["standard"]) if rule else None

    def build_snapshot(self, station: str) -> tuple[dict[str, Any] | None, str | None]:
        """取某厂站当前适用的限值快照；厂站未配置口径时返回原因。"""
        rule = self.stations.get(station)
        if rule is None:
            return None, f"厂站「{station}」尚未配置排放标准判定口径"
        standard = str(rule["standard"])
        limits = self.standards.get(standard)
        if limits is None:
            return None, f"排放标准「{standard}」缺少限值定义"
        snapshot = {
            "rule_version": self.version,
            "standard": standard,
            "limits": dict(limits),
            "flow_min": rule.get("flow_min"),
            "flow_max": rule.get("flow_max"),
            "multiple_buckets": [[low, high] for low, high in MULTIPLE_BUCKETS],
        }
        return snapshot, None

    def update_standard_limits(self, standard: str, limits: dict[str, float]) -> None:
        if standard not in self.standards:
            raise KeyError(standard)
        merged = dict(self.standards[standard])
        for key, value in limits.items():
            if key not in merged:
                raise KeyError(f"{standard}.{key}")
            merged[key] = float(value)
        if merged != self.standards[standard]:
            self.standards[standard] = merged
            self.bump()

    def upsert_station(
        self,
        station: str,
        *,
        standard: str,
        flow_min: float | None,
        flow_max: float | None,
    ) -> None:
        if standard not in self.standards:
            raise KeyError(standard)
        rule = {
            "standard": standard,
            "flow_min": flow_min,
            "flow_max": flow_max,
        }
        if self.stations.get(station) != rule:
            self.stations[station] = rule
            self.bump()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def evaluate(values: dict[str, Any], rulebook: RuleBook) -> dict[str, Any]:
    """对一条出水记录执行判定，返回结构化结论（不直接改记录）。

    返回键：
      conclusion: 达标 / 超标 / None（不出结论）
      status:     给列表用的展示状态（待判定/达标/超标）
      reasons:    不出结论或需提示的原因列表
      flow_abnormal / flow_message / flow_range: 流量独立标记
      items:      三项指标各自的实测值、限值、倍数、倍数区间
      basis:      规则快照（版本、标准、限值、区间）
      inputs:     参与判定的原始输入
      basis_hash: 快照+输入联合哈希，供复核比对
      evaluated_at / rule_version / standard
    """
    station = str(values.get("所属厂站") or "").strip()
    snapshot, basis_reason = (
        rulebook.build_snapshot(station) if station else (None, "所属厂站缺失")
    )

    parsed: dict[str, float | None] = {}
    invalid: list[str] = []
    items: list[dict[str, Any]] = []
    for field_name, key, label in INDICATORS:
        value, reason = parse_number(values.get(field_name))
        parsed[key] = value
        if reason:
            invalid.append(f"{label}（{field_name}）：{reason}")

    if snapshot is None:
        reasons = [basis_reason or "缺少判定口径"]
        if not station:
            reasons = ["所属厂站缺失，无法匹配排放标准"]
        reasons.extend(invalid)
        return _blank_result(reasons, station, snapshot)

    # 流量独立判定：缺失/格式错/越界都只做单独标记，不阻断达标结论。
    flow_value, flow_reason = parse_number(values.get("排放流量"))
    flow_min = snapshot["flow_min"]
    flow_max = snapshot["flow_max"]
    flow_abnormal = False
    flow_message = ""
    if flow_reason:
        flow_abnormal = True
        flow_detail = "未填报" if flow_reason == "指标值缺失" else flow_reason
        flow_message = f"排放流量{flow_detail}，已跳过流量区间校验"
    elif (flow_min is not None and flow_value < flow_min) or (
        flow_max is not None and flow_value > flow_max
    ):
        flow_abnormal = True
        flow_message = (
            f"排放流量 {flow_value:g} m³/h 超出正常区间 "
            f"{flow_min:g}~{flow_max:g} m³/h"
        )

    if invalid:
        reasons = [
            f"以下监测指标不可用，未生成达标结论（共 {len(invalid)} 项）："
            + "；".join(invalid),
        ]
        if flow_message:
            reasons.append(flow_message)
        return _blank_result(reasons, station, snapshot, parsed, flow_abnormal,
                             flow_message, (flow_min, flow_max), flow_value)

    over_items: list[dict[str, Any]] = []
    for field_name, key, label in INDICATORS:
        value = parsed[key]
        limit = float(snapshot["limits"][key])
        ratio = value / limit if limit > 0 else float("inf")
        item = {
            "indicator": label,
            "field": field_name,
            "value": value,
            "limit": limit,
            "ratio": round(ratio, 3),
            "exceeded": value > limit,
            "ratio_bucket": _bucket_label(ratio) if value > limit else None,
        }
        items.append(item)
        if item["exceeded"]:
            over_items.append(item)

    if over_items:
        worst = max(over_items, key=lambda item: item["ratio"])
        detail = "；".join(
            f"{it['indicator']}实测 {it['value']:g} / 限值 {it['limit']:g} mg/L，"
            f"超限 {it['ratio']:g} 倍（{it['ratio_bucket']}）"
            for it in over_items
        )
        conclusion = "超标"
        reasons = [f"按{station}适用的{snapshot['standard']}标准判定：{detail}"]
        if flow_message:
            reasons.append(flow_message)
        over_labels = "、".join(it["indicator"] for it in over_items)
        conclusion_text = (
            f"超标：{over_labels}超限，"
            f"最大超标倍数 {worst['ratio']:g} 倍（{worst['ratio_bucket']}）"
        )
    else:
        conclusion = "达标"
        reasons = [
            f"按{station}适用的{snapshot['standard']}标准判定："
            "COD、氨氮、总磷三项实测值均未超过排放限值"
        ]
        if flow_message:
            reasons.append(flow_message)
        conclusion_text = "达标：三项指标均未超过排放限值"

    inputs = {"station": station, **{key: parsed[key] for _, key, _ in INDICATORS},
              "flow": flow_value}
    basis_hash = _join_hash(snapshot, inputs)
    return {
        "conclusion": conclusion,
        "status": conclusion,
        "conclusion_text": conclusion_text,
        "reasons": reasons,
        "flow_abnormal": flow_abnormal,
        "flow_message": flow_message,
        "flow_range": {"min": flow_min, "max": flow_max},
        "items": items,
        "basis": snapshot,
        "inputs": inputs,
        "basis_hash": basis_hash,
        "rule_version": snapshot["rule_version"],
        "standard": snapshot["standard"],
        "evaluated_at": _now(),
    }


def _blank_result(
    reasons: list[str],
    station: str,
    snapshot: dict[str, Any] | None,
    parsed: dict[str, float | None] | None = None,
    flow_abnormal: bool = False,
    flow_message: str = "",
    flow_range: tuple[Any, Any] = (None, None),
    flow_value: float | None = None,
) -> dict[str, Any]:
    inputs = {"station": station or None}
    if parsed is not None:
        inputs.update(parsed)
    if flow_value is not None:
        inputs["flow"] = flow_value
    basis_hash = _join_hash(snapshot, inputs) if snapshot else None
    return {
        "conclusion": None,
        "status": "待判定",
        "conclusion_text": "待判定：" + "；".join(reasons),
        "reasons": reasons,
        "flow_abnormal": flow_abnormal,
        "flow_message": flow_message,
        "flow_range": {"min": flow_range[0], "max": flow_range[1]},
        "items": [],
        "basis": snapshot,
        "inputs": inputs,
        "basis_hash": basis_hash,
        "rule_version": snapshot["rule_version"] if snapshot else None,
        "standard": snapshot["standard"] if snapshot else None,
        "evaluated_at": _now(),
    }


def _join_hash(snapshot: dict[str, Any], inputs: dict[str, Any]) -> str:
    return _snapshot_hash({"basis": snapshot, "inputs": inputs})


def verify_basis(judgment: dict[str, Any]) -> dict[str, Any]:
    """复核：用记录上保存的规则快照与输入原样重算，比对哈希是否一致。

    返回 consistent=True 表示复核人看到的判定依据与录入时那份完全相同，
    没有被后续规则改动或人工改写影响。
    """
    snapshot = judgment.get("basis")
    inputs = judgment.get("inputs") or {}
    saved_hash = judgment.get("basis_hash")
    if not snapshot or not saved_hash:
        return {
            "consistent": False,
            "reason": "记录缺少判定规则快照，无法核对原始依据",
        }
    actual_hash = _join_hash(snapshot, inputs)
    if actual_hash != saved_hash:
        return {
            "consistent": False,
            "reason": "判定依据快照与录入时不一致，限值或输入值可能被改动",
            "saved_hash": saved_hash,
            "actual_hash": actual_hash,
        }

    # 用快照里的限值（而非当前规则册）重算一遍，验证结论本身仍成立。
    recomputed_items: list[dict[str, Any]] = []
    mismatch: list[str] = []
    for field_name, key, label in INDICATORS:
        value = inputs.get(key)
        if value is None:
            mismatch.append(f"{label}输入缺失")
            continue
        limit = float(snapshot["limits"][key])
        ratio = float(value) / limit
        old = next((it for it in judgment.get("items", []) if it.get("field") == field_name), None)
        if old is None:
            mismatch.append(f"{label}缺少原判定明细")
            continue
        if bool(old.get("exceeded")) != (float(value) > limit):
            mismatch.append(f"{label}复核结论与原结论不符")
        recomputed_items.append({"indicator": label, "ratio": round(ratio, 3)})

    flow_min = snapshot.get("flow_min")
    flow_max = snapshot.get("flow_max")
    flow = inputs.get("flow")
    flow_abnormal_now = judgment.get("flow_abnormal", False)
    if isinstance(flow, (int, float)):
        out_of_range = (flow_min is not None and flow < flow_min) or (
            flow_max is not None and flow > flow_max
        )
        if out_of_range != bool(flow_abnormal_now):
            mismatch.append("排放流量标记与快照区间复核结果不符")

    if mismatch:
        return {
            "consistent": False,
            "reason": "；".join(mismatch),
            "saved_hash": saved_hash,
            "actual_hash": actual_hash,
        }
    return {
        "consistent": True,
        "reason": "判定依据与录入时一致（规则版本 v{}，{}标准）".format(
            snapshot.get("rule_version"), snapshot.get("standard")
        ),
        "rule_version": snapshot.get("rule_version"),
        "standard": snapshot.get("standard"),
        "limits": snapshot.get("limits"),
        "items": recomputed_items,
        "saved_hash": saved_hash,
    }


# 进程内单例规则册
rulebook = RuleBook()
