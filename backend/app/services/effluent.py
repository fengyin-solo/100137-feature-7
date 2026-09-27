"""出水监测业务规则：记录维护、限值自动判定、重新判定与复核。

达标/超标结论全部由 effluent_rules 按厂站排放标准自动给出，化验员不再手工填写：
- create/update 时固化判定（含规则快照与哈希）写入记录；
- 规则改动后既有记录保留旧快照，通过 rejudge_all/rejudge_entry 显式重判；
- review_entry 用保存的快照复核，保证复核依据与录入时一致。
"""
from __future__ import annotations

from typing import Any

from app.services.effluent_rules import evaluate, rulebook, verify_basis
from app.store import store

MODULE = "effluent"
REQUIRED_FIELDS = ["记录编号", "所属厂站", "监测时间"]
# 可由登记/修改接口写入的业务字段
VALUE_FIELDS = [
    "记录编号", "所属厂站", "监测时间",
    "COD出水值", "氨氮出水值", "总磷出水值", "排放流量",
]
# 运维动作沿用历史状态流转，只影响处置状态，不改写自动判定结论
ACTION_RULES = {"预警通知": "接近限值", "关阀截流": "已关阀", "恢复排放": "达标"}


class EffluentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        flow_abnormal: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if flow_abnormal is not None:
            rows = [
                row for row in rows
                if bool((row.get("判定") or {}).get("flow_abnormal")) == flow_abnormal
            ]
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
        entry.update({field: values.get(field) for field in VALUE_FIELDS})
        self._apply_judgment(entry)
        entry["处置状态"] = None
        rows.append(entry)
        return entry, []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        for field in VALUE_FIELDS:
            if field in values:
                entry[field] = values.get(field)
        missing = [f for f in REQUIRED_FIELDS if not str(entry.get(f) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        # 录入值改动后按当前规则重新判定，快照同步刷新
        self._apply_judgment(entry)
        return entry, "出水记录已更新并重新判定"

    def rejudge_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        old_version = (entry.get("判定") or {}).get("rule_version")
        self._apply_judgment(entry)
        new_version = (entry.get("判定") or {}).get("rule_version")
        return entry, self._rejudge_message(old_version, new_version)

    def rejudge_all(self) -> dict[str, Any]:
        """规则改动后把既有记录全部按当前规则重新判一遍，并汇总变化。"""
        rows = store.rows(MODULE)
        basis_changed = 0
        conclusion_changed = 0
        to_compliant = 0
        to_exceeded = 0
        to_pending = 0
        for entry in rows:
            judgment = entry.get("判定") or {}
            before = judgment.get("conclusion")
            before_hash = judgment.get("basis_hash")
            self._apply_judgment(entry)
            after_judgment = entry.get("判定") or {}
            after = after_judgment.get("conclusion")
            if after_judgment.get("basis_hash") != before_hash:
                basis_changed += 1
            if before != after:
                conclusion_changed += 1
                if after == "达标":
                    to_compliant += 1
                elif after == "超标":
                    to_exceeded += 1
                elif after is None:
                    to_pending += 1
        return {
            "total": len(rows),
            "changed": conclusion_changed,
            "basis_changed": basis_changed,
            "to_compliant": to_compliant,
            "to_exceeded": to_exceeded,
            "to_pending": to_pending,
            "rule_version": rulebook.version,
        }

    def review_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """复核单条记录：核对保存的规则快照与录入时是否一致。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        judgment = entry.get("判定")
        if not judgment:
            return None, "该记录尚未生成判定，没有可复核的依据"
        report = verify_basis(judgment)
        return {"entry_id": entry_id, "review": report, "judgment": judgment}, ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于出水监测可执行范围"
        # 运维动作只改处置状态；自动判定结论（达标/超标/待判定）不受人工动作影响
        entry["处置状态"] = ACTION_RULES[action]
        return entry, f"出水记录已{action}（处置状态已更新，自动判定结论不变）"

    # ------------------------------------------------------------------
    def _apply_judgment(self, entry: dict[str, Any]) -> None:
        judgment = evaluate(entry, rulebook)
        entry["判定"] = judgment
        # status 与自动结论保持一致，供列表状态过滤与看板统计使用
        entry["status"] = judgment["status"]
        entry["出水状态"] = judgment["conclusion"] or "待判定"
        entry["pending"] = judgment["conclusion"] is None
        entry["abnormal"] = bool(judgment["flow_abnormal"]) or judgment["conclusion"] == "超标"

    @staticmethod
    def _rejudge_message(old_version: Any, new_version: int) -> str:
        if old_version == new_version:
            return f"已按当前规则（v{new_version}）重新判定，规则版本未变化"
        return f"已按最新规则 v{new_version} 重新判定（原判定基于 v{old_version}）"
