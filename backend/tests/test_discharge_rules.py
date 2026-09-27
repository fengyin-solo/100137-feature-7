"""出水限值判定的服务层回归。

不依赖 fastapi，直接用标准库即可运行：
    cd backend && python3 -m tests.test_discharge_rules
"""
from __future__ import annotations

from app.services.discharge_rules import discharge_rules
from app.services.effluent import EffluentService
from app.store import store


def _reset() -> None:
    """把单例规则集恢复成全新状态，并清掉出水表里运行期产生的测试记录。"""
    discharge_rules._versions = []
    discharge_rules._current = None
    for row in list(store.rows("effluent")):
        if int(row["id"]) > 7:  # 7 条为 seed.py 里的示例记录
            store.rows("effluent").remove(row)
    discharge_rules.bootstrap()


def test_bootstrap_seed_judged() -> None:
    _reset()
    rows = store.rows("effluent")
    assert len(rows) == 7
    assert rows[0]["判定结论"] == "达标"
    assert rows[1]["判定结论"] == "超标"
    assert "1～2倍" in rows[1]["超标倍数"]
    assert rows[3]["判定结论"] == "无法判定"
    assert "氨氮出水值缺失" in rows[3]["判定问题"]
    assert rows[3]["流量异常"] is True  # 流量为 0
    assert rows[4]["判定结论"] == "无法判定"
    assert "格式" in rows[4]["判定问题"]
    # 多指标超标：倍数区间逐项给出
    assert "10倍以上" in rows[6]["超标倍数"]
    assert rows[6]["流量异常"] is True  # 超厂站流量上限


def test_missing_and_bad_format_block_conclusion() -> None:
    _reset()
    svc = EffluentService()
    entry, missing = svc.create_entry({
        "记录编号": "EFFL-T1", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 08:00",
        "COD出水值": "120", "氨氮出水值": "", "总磷出水值": "0.4", "排放流量": "-50",
    })
    assert not missing
    assert entry["判定结论"] == "无法判定"
    assert "氨氮出水值缺失" in entry["判定问题"]
    assert entry["流量异常"] is True and "负值" in entry["流量异常说明"]

    entry2, _ = svc.create_entry({
        "记录编号": "EFFL-T2", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 09:00",
        "COD出水值": "四十", "氨氮出水值": "4", "总磷出水值": "0.4", "排放流量": "80000",
    })
    assert entry2["判定结论"] == "无法判定"
    assert "COD出水值格式" in entry2["判定问题"]


def test_disposal_actions_keep_judgment() -> None:
    _reset()
    svc = EffluentService()
    entry, _ = svc.create_entry({
        "记录编号": "EFFL-T3", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 09:00",
        "COD出水值": "40", "氨氮出水值": "4", "总磷出水值": "0.4", "排放流量": "80000",
    })
    assert entry["判定结论"] == "达标" and entry["pending"] is False
    updated, _ = svc.run_action(entry["id"], "预警通知")
    assert updated["处置状态"] == "预警通知" and updated["判定结论"] == "达标"
    updated, _ = svc.run_action(entry["id"], "恢复排放")
    assert updated["处置状态"] == "已恢复" and updated["pending"] is False
    assert svc.run_action(entry["id"], "删除")[0] is None


def test_publish_rejudges_and_freezes_entry_basis() -> None:
    _reset()
    svc = EffluentService()
    entry, _ = svc.create_entry({
        "记录编号": "EFFL-T4", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 09:00",
        "COD出水值": "40", "氨氮出水值": "4", "总磷出水值": "0.4", "排放流量": "80000",
    })
    frozen = entry["录入判定"]
    payload = discharge_rules.current()
    payload["standards"]["一级A"]["氨氮"] = 3.0
    book, summary = discharge_rules.publish(payload)
    assert book["version"] == "v2"
    assert summary["total"] >= 8
    assert entry["判定结论"] == "超标"
    assert "1～2倍" in entry["超标倍数"]
    # 录入时那份依据原封不动
    assert entry["录入判定"] == frozen
    assert frozen["规则版本"] == "v1" and frozen["结论"] == "达标"
    assert frozen["快照"]["限值"]["氨氮"] == 5.0
    # 当前判定已是新版
    assert entry["判定规则版本"] == "v2"
    assert entry["判定快照"]["限值"]["氨氮"] == 3.0
    assert [h["结论"] for h in entry["判定历史"]] == ["达标", "超标"]
    # 同版本重判幂等，不重复追加历史
    before = len(entry["判定历史"])
    svc.rejudge_entry(entry["id"])
    assert len(entry["判定历史"]) == before


def test_station_standard_and_fallback() -> None:
    _reset()
    rules = discharge_rules
    # 高新区执行准IV类：COD 33 对一级B 达标，对准IV类超标
    gaoxin = store.rows("effluent")[5]
    assert gaoxin["判定快照"]["排放标准"] == "准IV类"
    assert gaoxin["判定结论"] == "超标"
    # 未配置厂站走默认标准（一级A）
    result = rules.evaluate({"所属厂站": "未知厂", "COD出水值": "55", "氨氮出水值": "2",
                             "总磷出水值": "0.4", "排放流量": "100"})
    assert result["结论"] == "超标"
    assert result["快照"]["走默认标准"] is True


def test_flow_check_is_independent() -> None:
    _reset()
    rules = discharge_rules
    # 流量缺失：水质照常判定，流量只说明不标记异常
    r = rules.evaluate({"所属厂站": "城东净水厂", "COD出水值": "30", "氨氮出水值": "2",
                        "总磷出水值": "0.3", "排放流量": ""})
    assert r["结论"] == "达标"
    assert r["流量核查"]["异常"] is False and "缺失" in r["流量核查"]["说明"]
    # 流量超限：水质达标也单独标记
    r2 = rules.evaluate({"所属厂站": "城东净水厂", "COD出水值": "30", "氨氮出水值": "2",
                         "总磷出水值": "0.3", "排放流量": "150000"})
    assert r2["结论"] == "达标" and r2["流量核查"]["异常"] is True


def test_publish_validation() -> None:
    _reset()
    bad_payloads = [
        {},
        {"standards": {"一级A": {"COD": 0, "氨氮": 5, "总磷": 0.5}}, "default_standard": "一级A"},
        {"standards": {"一级A": {"COD": 50, "氨氮": "x", "总磷": 0.5}}, "default_standard": "一级A"},
        {"standards": {"一级A": {"COD": 50, "氨氮": 5, "总磷": 0.5}}, "default_standard": "二级"},
        {"standards": {"一级A": {"COD": 50, "氨氮": 5, "总磷": 0.5}}, "default_standard": "一级A",
         "stations": {"怪厂": {"标准": "二级", "排放流量上限": 1}}},
    ]
    for payload in bad_payloads:
        try:
            discharge_rules.publish(payload)
        except ValueError:
            continue
        raise AssertionError(f"非法口径未被拒绝：{payload}")
    # 拒绝非法口径时不应产生新版本
    assert [v["version"] for v in discharge_rules.versions()] == ["v1"]


def test_required_fields_still_enforced() -> None:
    _reset()
    svc = EffluentService()
    entry, missing = svc.create_entry({"记录编号": "", "所属厂站": "城东净水厂"})
    assert entry is None and "记录编号" in missing


def main() -> None:
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(tests)} 个用例全部通过")


if __name__ == "__main__":
    main()
