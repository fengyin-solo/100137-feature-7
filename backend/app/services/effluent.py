"""出水监测业务规则。

达标/超标结论不再由化验员手工填写，统一走 services.discharge_rules 的版本化口径：
- 登记记录时按当前口径自动判定，缺失/格式错误只给原因、不出结论；
- 预警通知、关阀截流、恢复排放是处置动作，落到「处置状态」，不改写自动判定结论；
- 规则改版可对单条或全部既有记录重判，录入时的判定依据保持不变。
"""
from __future__ import annotations

from typing import Any

from app.services.discharge_rules import (
    CONCLUSION_FAIL,
    CONCLUSION_PASS,
    CONCLUSION_UNKNOWN,
    discharge_rules,
)
from app.store import store

MODULE = "effluent"
REQUIRED_FIELDS = ["记录编号", "所属厂站", "监测时间"]
DATA_FIELDS = ["COD出水值", "氨氮出水值", "总磷出水值", "排放流量"]
CONCLUSIONS = [CONCLUSION_PASS, CONCLUSION_FAIL, CONCLUSION_UNKNOWN]
DISPOSAL_ORDER = ["未处置", "预警通知", "已关阀", "已恢复"]
ACTION_RULES = {"预警通知": "预警通知", "关阀截流": "已关阀", "恢复排放": "已恢复"}


def _refresh_pending(entry: dict[str, Any]) -> None:
    """处置完成（恢复排放）后不再占用待处理；否则按非达标或流量异常挂起。"""
    entry["pending"] = (
        entry.get("判定结论") != CONCLUSION_PASS or entry.get("流量异常")
    ) and entry.get("处置状态") != DISPOSAL_ORDER[-1]


class EffluentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        station: str | None = None,
        flow_abnormal: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("判定结论") == status]
        if station:
            rows = [row for row in rows if station in str(row.get("所属厂站", ""))]
        if flow_abnormal is not None:
            rows = [row for row in rows if bool(row.get("流量异常")) is flow_abnormal]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 指标值/流量原样留存；缺不缺、格式对不对由判定引擎给原因。
        entry.update({field: values.get(field) for field in DATA_FIELDS})
        entry["处置状态"] = "未处置"
        rows.append(entry)
        discharge_rules.apply(entry, on_create=True)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于出水监测可执行范围"
        target = ACTION_RULES[action]
        if target not in DISPOSAL_ORDER:
            return None, f"目标状态「{target}」不在允许的处置状态序列里"
        entry["处置状态"] = target
        _refresh_pending(entry)
        # 处置动作只表达处置进度，达标/超标结论始终以判定引擎为准。
        note = f"出水记录已{action}"
        if target == "已恢复":
            note += "，处置闭环（判定结论未改变）"
        return entry, note

    def rejudge_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        result = discharge_rules.apply(entry, on_create=False)
        return entry, f"已按当前口径（{result['规则版本']}）重判，结论：{result['结论']}"
