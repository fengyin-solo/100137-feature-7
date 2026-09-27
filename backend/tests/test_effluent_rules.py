"""出水监测判定引擎单测：限值比对、倍数区间、缺指标、厂站口径、快照复核、规则版本。"""
from __future__ import annotations

import unittest

from app.services.effluent_rules import (
    RuleBook,
    evaluate,
    parse_number,
    verify_basis,
)


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.book = RuleBook()  # 每个用例独立规则册，避免版本号互相污染

    def test_compliant_record(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "32", "氨氮出水值": "2.1",
             "总磷出水值": "0.32", "排放流量": "620"},
            self.book,
        )
        self.assertEqual(result["conclusion"], "达标")
        self.assertFalse(result["flow_abnormal"])
        self.assertEqual(result["standard"], "一级A")

    def test_boundary_value_equal_to_limit_is_compliant(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "50", "氨氮出水值": "5",
             "总磷出水值": "0.5", "排放流量": "500"},
            self.book,
        )
        self.assertEqual(result["conclusion"], "达标")
        self.assertTrue(all(not it["exceeded"] for it in result["items"]))

    def test_exceeded_gives_ratio_and_bucket(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "130", "氨氮出水值": "12.5",
             "总磷出水值": "0.8", "排放流量": "750"},
            self.book,
        )
        self.assertEqual(result["conclusion"], "超标")
        by_name = {it["indicator"]: it for it in result["items"]}
        self.assertEqual(by_name["COD"]["ratio_bucket"], "2~3倍")
        self.assertAlmostEqual(by_name["总磷"]["ratio"], 1.6, places=2)
        self.assertEqual(by_name["氨氮"]["ratio_bucket"], "2~3倍")
        self.assertIn("最大超标倍数", result["conclusion_text"])

    def test_high_ratio_top_bucket(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "600", "氨氮出水值": "1",
             "总磷出水值": "0.1", "排放流量": "500"},
            self.book,
        )
        cod = next(it for it in result["items"] if it["indicator"] == "COD")
        self.assertEqual(cod["ratio_bucket"], ">10倍")

    def test_missing_indicator_blocks_conclusion(self) -> None:
        result = evaluate(
            {"所属厂站": "南港工业区污水厂", "COD出水值": "80",
             "氨氮出水值": "", "总磷出水值": "2.0", "排放流量": "420"},
            self.book,
        )
        self.assertIsNone(result["conclusion"])
        self.assertEqual(result["status"], "待判定")
        self.assertTrue(any("氨氮" in r and "缺失" in r for r in result["reasons"]))
        self.assertFalse(result["flow_abnormal"])

    def test_bad_format_blocks_conclusion(self) -> None:
        result = evaluate(
            {"所属厂站": "北岛生态站", "COD出水值": "90", "氨氮出水值": "30",
             "总磷出水值": "偏高", "排放流量": "120"},
            self.book,
        )
        self.assertIsNone(result["conclusion"])
        self.assertTrue(any("总磷" in r and "格式" in r for r in result["reasons"]))

    def test_negative_value_invalid(self) -> None:
        value, reason = parse_number("-3")
        self.assertIsNone(value)
        self.assertIn("不能为负", reason)

    def test_station_standards_differ(self) -> None:
        # 同一组数值在一级A超标，在二级却达标
        values = {"COD出水值": "55", "氨氮出水值": "7", "总磷出水值": "0.6", "排放流量": "300"}
        a = evaluate({"所属厂站": "城东净水厂", **values}, self.book)
        b = evaluate({"所属厂站": "南港工业区污水厂", **values}, self.book)
        self.assertEqual(a["conclusion"], "超标")
        self.assertEqual(b["conclusion"], "达标")

    def test_unconfigured_station_no_conclusion(self) -> None:
        result = evaluate(
            {"所属厂站": "西郊临时站", "COD出水值": "40", "氨氮出水值": "3",
             "总磷出水值": "0.4", "排放流量": "100"},
            self.book,
        )
        self.assertIsNone(result["conclusion"])
        self.assertTrue(any("排放标准判定口径" in r for r in result["reasons"]))

    def test_flow_out_of_range_flagged_separately(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "30", "氨氮出水值": "2",
             "总磷出水值": "0.2", "排放流量": "950"},
            self.book,
        )
        self.assertEqual(result["conclusion"], "达标")  # 流量异常不影响达标结论
        self.assertTrue(result["flow_abnormal"])
        self.assertIn("正常区间", result["flow_message"])

    def test_flow_missing_flagged_but_indicator_conclusion_stands(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "30", "氨氮出水值": "2",
             "总磷出水值": "0.2", "排放流量": ""},
            self.book,
        )
        self.assertEqual(result["conclusion"], "达标")
        self.assertTrue(result["flow_abnormal"])
        self.assertIn("未填报", result["flow_message"])

    def test_snapshot_hash_review_passes(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "130", "氨氮出水值": "12.5",
             "总磷出水值": "0.8", "排放流量": "750"},
            self.book,
        )
        report = verify_basis(result)
        self.assertTrue(report["consistent"])
        self.assertEqual(report["rule_version"], 1)

    def test_review_detects_tampered_limit(self) -> None:
        result = evaluate(
            {"所属厂站": "城东净水厂", "COD出水值": "130", "氨氮出水值": "12.5",
             "总磷出水值": "0.8", "排放流量": "750"},
            self.book,
        )
        result["basis"]["limits"]["cod"] = 999
        report = verify_basis(result)
        self.assertFalse(report["consistent"])

    def test_rule_bump_does_not_mutate_old_snapshot(self) -> None:
        values = {"所属厂站": "城东净水厂", "COD出水值": "45", "氨氮出水值": "2",
                  "总磷出水值": "0.2", "排放流量": "500"}
        old = evaluate(values, self.book)
        self.assertEqual(old["rule_version"], 1)
        self.book.update_standard_limits("一级A", {"cod": 40})
        new = evaluate(values, self.book)
        self.assertEqual(new["rule_version"], 2)
        self.assertEqual(old["basis"]["limits"]["cod"], 50)  # 旧快照保持原值
        self.assertEqual(old["conclusion"], "达标")
        self.assertEqual(new["conclusion"], "超标")
        # 旧快照依然可通过一致性复核
        self.assertTrue(verify_basis(old)["consistent"])

    def test_no_bump_when_limits_unchanged(self) -> None:
        self.book.update_standard_limits("一级A", {"cod": 50})
        self.assertEqual(self.book.version, 1)

    def test_station_upsert_bumps_version(self) -> None:
        self.book.upsert_station("西郊临时站", standard="三级", flow_min=10, flow_max=80)
        self.assertEqual(self.book.version, 2)
        result = evaluate(
            {"所属厂站": "西郊临时站", "COD出水值": "40", "氨氮出水值": "3",
             "总磷出水值": "0.4", "排放流量": "50"},
            self.book,
        )
        self.assertEqual(result["conclusion"], "达标")


if __name__ == "__main__":
    unittest.main()
