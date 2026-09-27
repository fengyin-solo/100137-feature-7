"""出水监测接口：记录维护、限值自动判定、规则版本管理、重新判定与复核。

达标/超标结论不接受前端直接提交，一律由后端按厂站适用标准计算。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.effluent import EffluentService
from app.services.effluent_rules import rulebook

router = APIRouter(prefix="/api/effluent", tags=["出水监测"])

service = EffluentService()

LIST_FIELDS = ["记录编号", "所属厂站", "监测时间", "COD出水值", "氨氮出水值", "总磷出水值", "排放流量", "出水状态"]
JUDGE_STATUSES = ["达标", "超标", "待判定"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="达标、超标、待判定"),
    flow_abnormal: bool | None = Query(default=None, description="true 仅看排放流量异常记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号、判定状态与流量标记过滤出水监测列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, flow_abnormal=flow_abnormal, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/rules")
def get_rules() -> dict[str, Any]:
    """读取当前判定规则：排放标准限值、各厂站适用口径与版本号。"""
    return {
        "version": rulebook.version,
        "updated_at": rulebook.updated_at,
        "standards": rulebook.standards,
        "stations": rulebook.stations,
    }


@router.put("/rules/standards/{standard}", response_model=ActionResult)
def update_standard(standard: str, payload: EntryPayload) -> ActionResult:
    """调整某档排放标准的 COD/氨氮/总磷限值；调整后规则版本递增，历史记录不自动改写。"""
    try:
        rulebook.update_standard_limits(
            standard,
            {key: float(value) for key, value in payload.values.items() if key in {"cod", "nh3n", "tp"}},
        )
    except KeyError:
        return ActionResult(ok=False, message=f"排放标准「{standard}」或指标不存在")
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="限值必须是数字，规则未改动")
    return ActionResult(
        ok=True,
        message=f"「{standard}」限值已更新，规则升级到 v{rulebook.version}；既有记录需执行重新判定后才会套用新口径",
    )


@router.put("/rules/stations/{station}", response_model=ActionResult)
def upsert_station(station: str, payload: EntryPayload) -> ActionResult:
    """配置/调整某厂站适用的排放标准与排放流量正常区间。"""
    values = payload.values
    standard = str(values.get("standard") or "").strip()
    try:
        flow_min = values.get("flow_min")
        flow_max = values.get("flow_max")
        rulebook.upsert_station(
            station,
            standard=standard,
            flow_min=None if flow_min in (None, "") else float(flow_min),
            flow_max=None if flow_max in (None, "") else float(flow_max),
        )
    except KeyError:
        return ActionResult(ok=False, message=f"排放标准「{standard}」不存在，厂站口径未改动")
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="流量区间必须是数字，厂站口径未改动")
    return ActionResult(
        ok=True,
        message=f"厂站「{station}」判定口径已保存（{standard}），规则版本 v{rulebook.version}",
    )


@router.post("/rejudge-all", response_model=ActionResult)
def rejudge_all() -> ActionResult:
    """规则改动后把既有出水监测记录全部按当前规则重新判一遍。"""
    report = service.rejudge_all()
    message = (
        f"已按当前规则（v{report['rule_version']}）重新判定 {report['total']} 条记录："
        f"{report['basis_changed']} 条判定依据更新，"
        f"其中结论变化 {report['changed']} 条"
        f"（转为达标 {report['to_compliant']} 条、转为超标 {report['to_exceeded']} 条、"
        f"待判定 {report['to_pending']} 条）"
    )
    return ActionResult(ok=True, message=message, entry=report)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出出水监测清单：返回当前全量数据及其判定依据快照。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "effluent", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条出水记录明细（含判定依据快照）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"出水记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条出水记录，缺必填字段时说明原因；指标缺失/格式不对会保留记录但不生成结论。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    judgment = entry["判定"]
    if judgment["conclusion"] is None:
        return ActionResult(
            ok=True,
            message="出水记录已登记，但未生成达标结论：" + "；".join(judgment["reasons"]),
            entry=entry,
        )
    message = f"出水记录已登记，自动判定：{judgment['conclusion_text']}"
    if judgment["flow_abnormal"]:
        message += f"；{judgment['flow_message']}"
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改监测值后自动重新判定；达标结论不接受手工填写。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    judgment = entry["判定"]
    tail = (
        "；".join(judgment["reasons"])
        if judgment["conclusion"] is None
        else judgment["conclusion_text"]
    )
    return ActionResult(ok=True, message=f"{message}：{tail}", entry=entry)


@router.post("/{entry_id}/rejudge", response_model=ActionResult)
def rejudge_entry(entry_id: int) -> ActionResult:
    """按当前规则对单条既有记录重新判定。"""
    entry, message = service.rejudge_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/review")
def review_entry(entry_id: int) -> dict[str, Any]:
    """复核判定依据：用录入时保存的规则快照原样重算并比对哈希。"""
    report, message = service.review_entry(entry_id)
    if report is None:
        raise HTTPException(status_code=404, detail=message)
    return report


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条出水记录执行预警通知、关阀截流、恢复排放；只影响处置状态，不改写自动判定。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
