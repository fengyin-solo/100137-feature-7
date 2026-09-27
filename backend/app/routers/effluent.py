"""出水监测接口：维护出水记录，自动判定覆盖预警通知、关阀截流、恢复排放等处置动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.effluent import EffluentService

router = APIRouter(prefix="/api/effluent", tags=["出水监测"])

service = EffluentService()

LIST_FIELDS = [
    "记录编号", "所属厂站", "监测时间",
    "COD出水值", "氨氮出水值", "总磷出水值", "排放流量",
    "判定结论", "超标倍数", "流量异常", "判定规则版本", "处置状态",
]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="判定结论：达标、超标、无法判定"),
    station: str | None = Query(default=None, description="按所属厂站名称过滤"),
    flow_abnormal: bool | None = Query(default=None, description="true 只看排放流量异常记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号、判定结论、厂站与流量异常过滤出水记录；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        station=station,
        flow_abnormal=flow_abnormal,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    status: str | None = None,
    station: str | None = None,
    flow_abnormal: bool | None = None,
) -> dict[str, Any]:
    """导出出水监测清单：返回当前过滤条件下的全量数据（含判定依据快照）。"""
    items, total = service.list_entries(
        status=status, station=station, flow_abnormal=flow_abnormal, page=1, size=10000
    )
    return {"module": "effluent", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条出水记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"出水记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条出水记录并按当前口径自动判定；缺主档字段说明原因，指标缺失只影响结论不影响登记。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    message = f"出水记录已登记，自动判定：{entry['判定结论']}（口径 {entry['判定规则版本']}）"
    if entry["判定问题"]:
        message += f"；未生成结论原因：{entry['判定问题']}"
    if entry["流量异常"]:
        message += f"；{entry['流量异常说明']}"
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/rejudge", response_model=ActionResult)
def rejudge_entry(entry_id: int) -> ActionResult:
    """按当前生效口径重判单条记录；录入时的判定依据保持不变。"""
    entry, message = service.rejudge_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条出水记录执行预警通知、关阀截流、恢复排放；处置动作不改写自动判定结论。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
