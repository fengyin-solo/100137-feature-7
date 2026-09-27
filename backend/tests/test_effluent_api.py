"""出水监测接口集成测试：登记、缺指标、重判、规则版本与复核链路。"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient


class EffluentApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # store 在导入时即装载示例数据，整个类共享一份进程内状态
        from app.main import app

        cls.client = TestClient(app)

    def test_seed_records_have_judgment(self) -> None:
        resp = self.client.get("/api/effluent", params={"size": 200})
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertGreaterEqual(len(items), 8)
        for item in items:
            self.assertIn("判定", item)
            self.assertIn("basis_hash", item["判定"])

    def test_create_compliant(self) -> None:
        resp = self.client.post("/api/effluent", json={"values": {
            "记录编号": "EFFL-T1", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 08:00",
            "COD出水值": "30", "氨氮出水值": "2", "总磷出水值": "0.3", "排放流量": "600",
        }})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"], body["message"])
        self.assertIn("达标", body["message"])
        self.assertEqual(body["entry"]["判定"]["conclusion"], "达标")

    def test_create_exceeded_shows_multiple(self) -> None:
        resp = self.client.post("/api/effluent", json={"values": {
            "记录编号": "EFFL-T2", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 09:00",
            "COD出水值": "130", "氨氮出水值": "12", "总磷出水值": "0.8", "排放流量": "600",
        }})
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["entry"]["判定"]["conclusion"], "超标")
        self.assertIn("倍", body["message"])

    def test_create_missing_indicator_keeps_record_no_conclusion(self) -> None:
        resp = self.client.post("/api/effluent", json={"values": {
            "记录编号": "EFFL-T3", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 10:00",
            "COD出水值": "30", "氨氮出水值": "", "总磷出水值": "0.3", "排放流量": "600",
        }})
        body = resp.json()
        self.assertTrue(body["ok"])  # 记录保留
        self.assertIsNone(body["entry"]["判定"]["conclusion"])
        self.assertIn("氨氮", body["message"])
        self.assertIn("缺失", body["message"])

    def test_create_missing_required_field_rejected(self) -> None:
        resp = self.client.post("/api/effluent", json={"values": {
            "记录编号": "", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 11:00",
        }})
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("记录编号", body["message"])

    def test_flow_abnormal_filter(self) -> None:
        resp = self.client.get("/api/effluent", params={"flow_abnormal": "true", "size": 200})
        items = resp.json()["items"]
        self.assertTrue(items)
        self.assertTrue(all(item["判定"]["flow_abnormal"] for item in items))

    def test_review_consistent(self) -> None:
        resp = self.client.get("/api/effluent/1/review")
        self.assertEqual(resp.status_code, 200)
        report = resp.json()["review"]
        self.assertTrue(report["consistent"], report["reason"])

    def test_update_then_judgment_refreshes(self) -> None:
        create = self.client.post("/api/effluent", json={"values": {
            "记录编号": "EFFL-T4", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 12:00",
            "COD出水值": "30", "氨氮出水值": "2", "总磷出水值": "0.3", "排放流量": "600",
        }})
        entry_id = create.json()["entry"]["id"]
        updated = self.client.put(f"/api/effluent/{entry_id}", json={"values": {"COD出水值": "90"}})
        self.assertEqual(updated.json()["entry"]["判定"]["conclusion"], "超标")

    def test_rule_change_then_rejudge_all(self) -> None:
        # 找一条当前按一级A达标且 COD 接近限值的记录；没有就造一条
        create = self.client.post("/api/effluent", json={"values": {
            "记录编号": "EFFL-T5", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 13:00",
            "COD出水值": "45", "氨氮出水值": "2", "总磷出水值": "0.3", "排放流量": "600",
        }})
        entry = create.json()["entry"]
        entry_id = entry["id"]
        self.assertEqual(entry["判定"]["conclusion"], "达标")

        changed = self.client.put("/api/effluent/rules/standards/一级A",
                                  json={"values": {"cod": 40}})
        self.assertTrue(changed.json()["ok"])

        # 规则改完，既有记录结论不变（快照保留）
        before = self.client.get(f"/api/effluent/{entry_id}").json()
        self.assertEqual(before["判定"]["conclusion"], "达标")

        report = self.client.post("/api/effluent/rejudge-all").json()
        self.assertTrue(report["ok"])
        self.assertIn("重新判定", report["message"])
        self.assertGreaterEqual(report["entry"]["to_exceeded"], 1)

        after = self.client.get(f"/api/effluent/{entry_id}").json()
        self.assertEqual(after["判定"]["conclusion"], "超标")
        self.assertEqual(after["判定"]["rule_version"], 2)

        # 重判后复核仍应一致（依据与新录入快照一致）
        review = self.client.get(f"/api/effluent/{entry_id}/review").json()
        self.assertTrue(review["review"]["consistent"])

    def test_actions_change_only_disposal_state(self) -> None:
        create = self.client.post("/api/effluent", json={"values": {
            "记录编号": "EFFL-T6", "所属厂站": "城东净水厂", "监测时间": "2026-09-27 14:00",
            "COD出水值": "130", "氨氮出水值": "12", "总磷出水值": "0.8", "排放流量": "600",
        }})
        entry_id = create.json()["entry"]["id"]
        act = self.client.post(f"/api/effluent/{entry_id}/actions", json={"values": {"action": "关阀截流"}})
        body = act.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["entry"]["处置状态"], "已关阀")
        self.assertEqual(body["entry"]["判定"]["conclusion"], "超标")

    def test_rules_endpoint(self) -> None:
        resp = self.client.get("/api/effluent/rules")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("一级A", body["standards"])
        self.assertIn("城东净水厂", body["stations"])
        self.assertGreaterEqual(body["version"], 1)

    def test_export_includes_judgment_basis(self) -> None:
        resp = self.client.get("/api/effluent/export")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertTrue(items)
        self.assertTrue(all("判定" in item and "basis" in item["判定"] for item in items))


if __name__ == "__main__":
    unittest.main()
