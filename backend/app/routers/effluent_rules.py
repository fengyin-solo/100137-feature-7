"""出水排放限值规则接口：查询口径、发布新版并触发既有记录重判。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.discharge_rules import discharge_rules

router = APIRouter(prefix="/api/effluent-rules", tags=["出水判定规则"])


class RulebookPayload(BaseModel):
    """发布新版限值口径：标准限值表 + 厂站口径映射 + 默认标准。"""

    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/current", response_model=dict)
def current_rulebook() -> dict[str, Any]:
    """读取当前生效的判定口径，页面上展示各厂站按哪套标准判定。"""
    return discharge_rules.current()


@router.get("/versions", response_model=list)
def list_versions() -> list[dict[str, Any]]:
    """规则版本清单：版本号、发布时间、规则指纹，供复核人比对依据。"""
    return discharge_rules.versions()


@router.post("/publish", response_model=ActionResult)
def publish_rulebook(payload: RulebookPayload) -> ActionResult:
    """发布新版口径并立即重判全部既有出水记录；口径不合法时给出逐项原因。"""
    try:
        book, summary = discharge_rules.publish(payload.values)
    except ValueError as exc:
        return ActionResult(ok=False, message=f"口径校验未通过：{exc}")
    message = (
        f"口径 {book['version']} 已发布并重判 {summary['total']} 条记录"
        f"（达标 {summary['达标']}、超标 {summary['超标']}、无法判定 {summary['无法判定']}，"
        f"其中 {summary['changed']} 条结论发生变化，{summary['flow_abnormal']} 条排放流量异常）；"
        "各记录「录入判定」保持原样，复核依据不变。"
    )
    return ActionResult(ok=True, message=message, entry={"version": book["version"], "summary": summary})


@router.post("/rejudge", response_model=ActionResult)
def rejudge_all() -> ActionResult:
    """规则没改版也可以手动触发一次全量重判（例如修正了数据后）。"""
    summary = discharge_rules.rejudge_all()
    return ActionResult(
        ok=True,
        message=(
            f"已按当前口径重判 {summary['total']} 条记录：达标 {summary['达标']}、"
            f"超标 {summary['超标']}、无法判定 {summary['无法判定']}，"
            f"流量异常 {summary['flow_abnormal']} 条。"
        ),
        entry=summary,
    )
