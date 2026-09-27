"""出水达标判定规则集（版本化）。

判定口径在这里固化，化验员不再手工填结论：

- 每个规则版本保存各排放标准下 COD/氨氮/总磷 的限值，以及厂站与排放标准
  （含排放流量上限）的对应关系；不同厂站按各自口径判定，未配置的厂站走默认标准。
- 判定时给记录留存一份不可变快照（规则版本、规则指纹、限值、实测值与逐项结论）。
  记录首次判定时另存「录入判定」；规则改版后对既有记录重判，只更新当前结论并追加
  判定历史，录入时那份依据原样保留，复核人看到的与录入时一致。
- COD/氨氮/总磷任一指标缺失或格式不对，不生成达标/超标结论，逐条写明原因。
- 排放流量异常（为 0、负值、格式错误、超出厂站配置上限）单独标记，不影响水质结论。
"""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime
from hashlib import sha256
from typing import Any

from app.store import store

MODULE = "effluent"

# 出水监测参与限值比对的三项指标，键为记录字段名，值为限值表里的短名。
INDICATORS: tuple[str, ...] = ("COD出水值", "氨氮出水值", "总磷出水值")
LIMIT_KEYS: dict[str, str] = {"COD出水值": "COD", "氨氮出水值": "氨氮", "总磷出水值": "总磷"}
INDICATOR_UNIT = "mg/L"
FLOW_FIELD = "排放流量"
FLOW_UNIT = "m³/d"

CONCLUSION_PASS = "达标"
CONCLUSION_FAIL = "超标"
CONCLUSION_UNKNOWN = "无法判定"

DISPOSAL_FINISHED = "已恢复"

# 初始（v1）口径：标准文本只作展示，真正参与比对的是数值限值。
DEFAULT_RULEBOOK: dict[str, Any] = {
    "note": "初始口径：GB 18918-2002 一级A/一级B，高新区净水厂执行准地表水IV类提标限值",
    "standards": {
        "一级A": {"标准名称": "GB 18918-2002 一级A", "COD": 50.0, "氨氮": 5.0, "总磷": 0.5},
        "一级B": {"标准名称": "GB 18918-2002 一级B", "COD": 60.0, "氨氮": 8.0, "总磷": 1.0},
        "准IV类": {"标准名称": "城镇污水处理厂准地表水IV类提标限值", "COD": 30.0, "氨氮": 1.5, "总磷": 0.3},
    },
    "stations": {
        "城东净水厂": {"标准": "一级A", "排放流量上限": 100000.0},
        "城西污水处理厂": {"标准": "一级B", "排放流量上限": 120000.0},
        "高新区净水厂": {"标准": "准IV类", "排放流量上限": 40000.0},
        "城北站点": {"标准": "一级B", "排放流量上限": 120000.0},
    },
    "default_standard": "一级A",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fingerprint(payload: Any) -> str:
    """对口径内容做稳定摘要，复核时可核对快照是否仍是录入时那份。"""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(body.encode("utf-8")).hexdigest()[:12]


def _parse_indicator(raw: Any) -> tuple[float | None, str | None]:
    """解析指标实测值；返回 (数值, 问题原因)，缺失/格式错误/负值都不给结论。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "缺失"
    if isinstance(raw, bool):
        return None, "格式错误"
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        try:
            value = float(str(raw).strip().replace(",", ""))
        except ValueError:
            return None, "格式错误"
    if math.isnan(value) or math.isinf(value):
        return None, "格式错误"
    if value < 0:
        return None, "不得为负值"
    return value, None


def _parse_flow(raw: Any) -> tuple[float | None, str | None]:
    """排放流量单独解析：0、负值属于业务异常而非格式问题。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "缺失"
    if isinstance(raw, bool):
        return None, "格式错误"
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        try:
            value = float(str(raw).strip().replace(",", ""))
        except ValueError:
            return None, "格式错误"
    if math.isnan(value) or math.isinf(value):
        return None, "格式错误"
    if value < 0:
        return value, "负值"
    return value, None


def excess_bucket(ratio: float) -> str:
    """把超标倍数落到固定区间，便于统计与对外口径一致。"""
    if ratio < 2:
        return "1～2倍"
    if ratio < 5:
        return "2～5倍"
    if ratio < 10:
        return "5～10倍"
    return "10倍以上"


def _fmt(value: float) -> str:
    return f"{value:g}"


class DischargeRulebook:
    """版本化限值口径 + 判定/重判引擎。数据落在内存，随进程启动初始化。"""

    def __init__(self) -> None:
        self._versions: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    # ---- 版本管理 ----------------------------------------------------------

    def bootstrap(self) -> None:
        """进程启动时装入 v1 口径，并对既有出水记录补一遍判定。"""
        if self._versions:
            return
        self._install(DEFAULT_RULEBOOK, version="v1")
        for row in store.rows(MODULE):
            self.apply(row, on_create=True)

    def _ensure_ready(self) -> dict[str, Any]:
        if self._current is None:
            self.bootstrap()
        assert self._current is not None
        return self._current

    def current(self) -> dict[str, Any]:
        return copy.deepcopy(self._ensure_ready())

    def versions(self) -> list[dict[str, Any]]:
        self._ensure_ready()
        return [
            {
                "version": book["version"],
                "published_at": book["published_at"],
                "note": book.get("note", ""),
                "fingerprint": book["fingerprint"],
                "default_standard": book["default_standard"],
                "standards": list(book["standards"].keys()),
            }
            for book in self._versions
        ]

    def publish(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """发布新版口径，并立即用新版重判全部既有出水记录。"""
        version = f"v{len(self._versions) + 1}"
        book = self._install(payload, version=version)
        summary = self.rejudge_all()
        summary["version"] = version
        summary["published_at"] = book["published_at"]
        return copy.deepcopy(book), summary

    def _install(self, payload: dict[str, Any], *, version: str) -> dict[str, Any]:
        normalized = self.validate(payload)
        book = copy.deepcopy(normalized)
        book["version"] = version
        book["published_at"] = now_text()
        book["fingerprint"] = fingerprint(
            {
                "standards": book["standards"],
                "stations": book["stations"],
                "default_standard": book["default_standard"],
            }
        )
        self._versions.append(book)
        self._current = book
        return book

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验新口径，错误集中抛给接口层；返回归一化后的纯数据。"""
        errors: list[str] = []

        raw_standards = payload.get("standards")
        if not isinstance(raw_standards, dict) or not raw_standards:
            raise ValueError("排放标准表不能为空，至少要配置一套 COD/氨氮/总磷 限值")

        standards: dict[str, dict[str, Any]] = {}
        for name, cfg in raw_standards.items():
            if not isinstance(cfg, dict):
                errors.append(f"排放标准「{name}」的配置必须是对象")
                continue
            row: dict[str, Any] = {"标准名称": str(cfg.get("标准名称") or name)}
            for field in ("COD", "氨氮", "总磷"):
                value, ok = self._positive_number(cfg.get(field))
                if not ok:
                    errors.append(f"排放标准「{name}」的{field}限值必须是大于 0 的数字，当前为 {cfg.get(field)!r}")
                else:
                    row[field] = value
            standards[str(name)] = row

        default_standard = str(payload.get("default_standard") or "").strip()
        if not default_standard:
            errors.append("必须指定默认排放标准 default_standard（厂站未配置时兜底）")
        elif default_standard not in standards:
            errors.append(f"默认排放标准「{default_standard}」不在排放标准表里")

        stations: dict[str, dict[str, Any]] = {}
        raw_stations = payload.get("stations") or {}
        if not isinstance(raw_stations, dict):
            errors.append("厂站口径映射 stations 必须是 {厂站名称: {标准, 排放流量上限}} 的对象")
            raw_stations = {}
        for name, cfg in raw_stations.items():
            if not isinstance(cfg, dict):
                errors.append(f"厂站「{name}」的配置必须是对象")
                continue
            std = str(cfg.get("标准") or "").strip()
            if std not in standards:
                errors.append(f"厂站「{name}」绑定的排放标准「{std}」不在排放标准表里")
            upper = None
            if cfg.get("排放流量上限") not in (None, ""):
                upper, ok = self._positive_number(cfg.get("排放流量上限"))
                if not ok:
                    errors.append(f"厂站「{name}」的排放流量上限必须是大于 0 的数字，当前为 {cfg.get('排放流量上限')!r}")
            stations[str(name)] = {"标准": std, "排放流量上限": upper}

        if errors:
            raise ValueError("；".join(errors))
        return {
            "note": str(payload.get("note") or "").strip(),
            "standards": standards,
            "stations": stations,
            "default_standard": default_standard,
        }

    @staticmethod
    def _positive_number(raw: Any) -> tuple[float | None, bool]:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            return None, False
        try:
            value = float(str(raw).strip())
        except (ValueError, AttributeError):
            return None, False
        if math.isnan(value) or math.isinf(value) or value <= 0:
            return None, False
        return value, True

    # ---- 判定 --------------------------------------------------------------

    def resolve_standard(
        self, station: str, book: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, Any] | None, bool]:
        """返回 (标准键, 标准配置, 厂站配置, 是否走了默认兜底)。"""
        station_cfg = book["stations"].get(station)
        if station_cfg and station_cfg.get("标准") in book["standards"]:
            key = station_cfg["标准"]
            return key, book["standards"][key], station_cfg, False
        key = book["default_standard"]
        return key, book["standards"][key], station_cfg, True

    def evaluate(self, entry: dict[str, Any], book: dict[str, Any] | None = None) -> dict[str, Any]:
        """纯判定：不写记录，只返回结论、逐项比对、流量核查与依据文本。"""
        book = book or self._ensure_ready()
        station = str(entry.get("所属厂站") or "").strip()
        std_key, standard, station_cfg, used_default = self.resolve_standard(station, book)
        limits = {LIMIT_KEYS[name]: float(standard[LIMIT_KEYS[name]]) for name in INDICATORS}

        problems: list[str] = []
        items: list[dict[str, Any]] = []
        for name in INDICATORS:
            value, error = _parse_indicator(entry.get(name))
            item: dict[str, Any] = {
                "指标": name,
                "实测值": value,
                "限值": limits[LIMIT_KEYS[name]],
                "单位": INDICATOR_UNIT,
                "达标": None,
            }
            if error is not None:
                item["问题"] = error
                problems.append(f"{name}{self._problem_phrase(error)}")
            else:
                ratio = value / limits[LIMIT_KEYS[name]]
                item["倍数"] = round(ratio, 2)
                item["达标"] = ratio <= 1
                if ratio > 1:
                    item["倍数区间"] = excess_bucket(ratio)
            items.append(item)

        flow = self._check_flow(entry, station_cfg)
        if problems:
            conclusion = CONCLUSION_UNKNOWN
        elif any(item["达标"] is False for item in items):
            conclusion = CONCLUSION_FAIL
        else:
            conclusion = CONCLUSION_PASS

        basis_text = self._build_text(
            book=book,
            std_key=std_key,
            standard=standard,
            used_default=used_default,
            station=station,
            items=items,
            problems=problems,
            flow=flow,
            conclusion=conclusion,
        )

        snapshot_core = {
            "规则版本": book["version"],
            "规则指纹": book["fingerprint"],
            "排放标准": std_key,
            "标准名称": standard["标准名称"],
            "厂站": station,
            "走默认标准": used_default,
            "限值": limits,
            "指标判定": copy.deepcopy(items),
            "流量核查": copy.deepcopy(flow),
            "结论": conclusion,
        }
        snapshot = copy.deepcopy(snapshot_core)
        snapshot["判定时间"] = now_text()
        snapshot["依据指纹"] = fingerprint(snapshot_core)

        return {
            "结论": conclusion,
            "问题": problems,
            "指标判定": items,
            "流量核查": flow,
            "依据说明": basis_text,
            "快照": snapshot,
            "排放标准": std_key,
            "规则版本": book["version"],
            "规则指纹": book["fingerprint"],
            "判定时间": snapshot["判定时间"],
        }

    @staticmethod
    def _problem_phrase(error: str) -> str:
        return {
            "缺失": "缺失，无法比对限值",
            "格式错误": "格式不是有效数值，无法比对限值",
            "不得为负值": "为负值，数据不合理，无法比对限值",
        }.get(error, error)

    def _check_flow(
        self, entry: dict[str, Any], station_cfg: dict[str, Any] | None
    ) -> dict[str, Any]:
        """排放流量异常单独核查，不参与达标/超标结论。"""
        raw = entry.get(FLOW_FIELD)
        upper = station_cfg.get("排放流量上限") if station_cfg else None
        result: dict[str, Any] = {
            "字段": FLOW_FIELD,
            "实测值": None,
            "上限": upper,
            "单位": FLOW_UNIT,
            "异常": False,
            "说明": "",
        }
        value, error = _parse_flow(raw)
        result["实测值"] = value
        if error == "缺失":
            result["说明"] = "排放流量缺失，未做流量核查（不影响水质结论）"
            return result
        if error == "格式错误":
            result["异常"] = True
            result["说明"] = f"排放流量格式不正确（原值：{raw}），单独标记，请核对仪表或录入"
            return result
        if error == "负值":
            result["异常"] = True
            result["说明"] = f"排放流量为负值（{_fmt(value)} {FLOW_UNIT}），疑似计量异常，单独标记"
            return result
        if value == 0:
            result["异常"] = True
            result["说明"] = "排放流量为 0，疑似断流或计量异常，单独标记"
            return result
        if upper is not None and value > upper:
            result["异常"] = True
            result["说明"] = (
                f"排放流量 {_fmt(value)} {FLOW_UNIT} 超过厂站配置上限 "
                f"{_fmt(upper)} {FLOW_UNIT}（{value / upper:.2f} 倍），单独标记"
            )
            return result
        if upper is not None:
            result["说明"] = f"排放流量 {_fmt(value)} {FLOW_UNIT}，未超上限 {_fmt(upper)} {FLOW_UNIT}"
        else:
            result["说明"] = f"排放流量 {_fmt(value)} {FLOW_UNIT}，厂站未配置流量上限，仅核查数值有效性"
        return result

    def _build_text(
        self,
        *,
        book: dict[str, Any],
        std_key: str,
        standard: dict[str, Any],
        used_default: bool,
        station: str,
        items: list[dict[str, Any]],
        problems: list[str],
        flow: dict[str, Any],
        conclusion: str,
    ) -> str:
        limit_text = "、".join(
            f"{LIMIT_KEYS[name]}≤{_fmt(standard[LIMIT_KEYS[name]])}{INDICATOR_UNIT}"
            for name in INDICATORS
        )
        scope = f"厂站「{station}」按{standard['标准名称']}（{std_key}）判定"
        if used_default:
            scope += f"；该厂站未配置专用口径，按默认标准 {std_key} 兜底"
        head = f"【判定口径 {book['version']}｜规则指纹 {book['fingerprint']}】{scope}，限值 {limit_text}。"

        lines: list[str] = []
        for item in items:
            name = LIMIT_KEYS[item["指标"]]
            if item["达标"] is None:
                lines.append(f"{name}：{self._problem_phrase(item['问题'])}")
            elif item["达标"]:
                lines.append(
                    f"{name}：实测 {_fmt(item['实测值'])}/{_fmt(item['限值'])} {INDICATOR_UNIT}，达标"
                )
            else:
                lines.append(
                    f"{name}：实测 {_fmt(item['实测值'])} {INDICATOR_UNIT}、限值 "
                    f"{_fmt(item['限值'])} {INDICATOR_UNIT}，超标 {item['倍数']:.2f} 倍，"
                    f"落入{item['倍数区间']}区间"
                )
        body = "【逐项比对】" + "；".join(lines) + "。"

        if problems:
            tail = "【综合结论】指标数据不完整或不合法，按口径不生成达标/超标结论，待补测或更正后重判。"
        elif conclusion == CONCLUSION_FAIL:
            worst = max(
                (item["倍数"] for item in items if item["达标"] is False), default=0
            )
            tail = f"【综合结论】超标，最大超标 {worst:.2f} 倍。"
        else:
            tail = "【综合结论】达标。"

        flow_line = "【流量核查】" + str(flow.get("说明") or "")
        return head + body + tail + flow_line

    # ---- 落记录 / 重判 ------------------------------------------------------

    def apply(
        self,
        entry: dict[str, Any],
        *,
        on_create: bool,
        book: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把当前口径的判定结果写进记录；录入判定只在首次写入，之后永不覆盖。"""
        book = book or self._ensure_ready()
        result = self.evaluate(entry, book)
        snapshot = copy.deepcopy(result["快照"])
        conclusion = result["结论"]
        flow_abnormal = bool(result["流量核查"]["异常"])

        entry["判定结论"] = conclusion
        entry["status"] = conclusion
        entry["出水状态"] = conclusion
        entry["判定规则版本"] = result["规则版本"]
        entry["判定依据"] = result["依据说明"]
        entry["判定快照"] = snapshot
        entry["判定时间"] = result["判定时间"]
        entry["判定问题"] = "；".join(result["问题"])
        over_items = [item for item in result["指标判定"] if item["达标"] is False]
        entry["超标倍数"] = "；".join(
            f"{LIMIT_KEYS[item['指标']]} {item['倍数']:.2f} 倍（{item['倍数区间']}）"
            for item in over_items
        )
        entry["倍数区间"] = "；".join(item["倍数区间"] for item in over_items)
        entry["流量异常"] = flow_abnormal
        entry["流量异常说明"] = result["流量核查"]["说明"]
        entry["abnormal"] = conclusion == CONCLUSION_FAIL or flow_abnormal
        entry["pending"] = (
            conclusion != CONCLUSION_PASS or flow_abnormal
        ) and entry.get("处置状态") != DISPOSAL_FINISHED

        entry.setdefault("处置状态", "未处置")
        if on_create or "录入判定" not in entry:
            # 深拷贝隔离：后续重判只换判定快照，录入时这份不受影响。
            entry["录入判定"] = {
                "结论": conclusion,
                "规则版本": result["规则版本"],
                "规则指纹": result["规则指纹"],
                "依据指纹": snapshot["依据指纹"],
                "依据说明": result["依据说明"],
                "判定时间": result["判定时间"],
                "快照": copy.deepcopy(snapshot),
            }
        entry.setdefault("判定历史", [])
        history_entry = {
            "规则版本": result["规则版本"],
            "规则指纹": result["规则指纹"],
            "依据指纹": snapshot["依据指纹"],
            "结论": conclusion,
            "判定时间": result["判定时间"],
            "依据说明": result["依据说明"],
        }
        last = entry["判定历史"][-1] if entry["判定历史"] else None
        if not last or last["规则版本"] != history_entry["规则版本"] or last["结论"] != conclusion:
            entry["判定历史"].append(history_entry)
        return result

    def rejudge_all(self) -> dict[str, Any]:
        """用当前版本重判全部既有记录，返回各结论计数与结论变化条数。"""
        book = self._ensure_ready()
        counts = {CONCLUSION_PASS: 0, CONCLUSION_FAIL: 0, CONCLUSION_UNKNOWN: 0}
        flow_abnormal = 0
        changed = 0
        total = 0
        for row in store.rows(MODULE):
            total += 1
            before = row.get("判定结论")
            result = self.apply(row, on_create=False, book=book)
            counts[result["结论"]] += 1
            if result["流量核查"]["异常"]:
                flow_abnormal += 1
            if before is not None and before != result["结论"]:
                changed += 1
        return {
            "total": total,
            "changed": changed,
            "flow_abnormal": flow_abnormal,
            **counts,
        }


discharge_rules = DischargeRulebook()
