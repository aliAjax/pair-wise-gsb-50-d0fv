import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed'), ('close', 'reviewer', {'final_decision': '维持处理'}, 'closed')]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def _create(self):
        return self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)

    def _run_to_reviewed(self, record):
        for action, role, data, expected_state in FLOW[:3]:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
            self.assertEqual(record["state"], expected_state)
        return record

    def test_complete_workflow_and_audit(self):
        record = self._create()
        self.assertEqual(record["state"], "opened")
        record = self._run_to_reviewed(record)

        # 送达生效前不能结案
        action, role, data, expected_state = FLOW[3]
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", role), record["id"], record["version"], action, data)

        # 登记直接签收送达，凭证日期即生效日
        today = date.today().isoformat()
        delivery = self.service.register_delivery(
            Actor("operator", "inspector"),
            record["id"],
            {"document": "稽查决定书", "method": "direct", "voucher_date": today},
        )
        self.assertEqual(delivery["effective_date"], today)

        record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
        self.assertEqual(record["state"], expected_state)

        timeline = self.service.timeline(Actor("creator", "inspector"), record["id"])
        actions = [event["action"] for event in timeline]
        self.assertEqual(actions, ["created"] + [step[0] for step in FLOW[:3]] + ["delivery_register", "close"])

    def test_delivery_detail_and_correction(self):
        record = self._run_to_reviewed(self._create())
        today = date.today()
        self.service.register_delivery(
            Actor("operator", "inspector"),
            record["id"],
            {"document": "稽查决定书", "method": "direct", "voucher_date": today.isoformat()},
        )
        # 同一文书重复登记被拒绝，须走补正
        with self.assertRaises(Conflict):
            self.service.register_delivery(
                Actor("operator", "inspector"),
                record["id"],
                {"document": "稽查决定书", "method": "mail", "voucher_date": today.isoformat()},
            )
        corrected = self.service.correct_delivery(
            Actor("operator", "reviewer"),
            record["id"],
            {"document": "稽查决定书", "method": "mail", "voucher_date": today.isoformat(), "reason": "签收人无授权，改用邮寄回执"},
        )
        self.assertEqual(corrected["status"], "active")
        self.assertTrue(corrected["supersedes_id"])

        detail = self.service.delivery_detail(Actor("creator", "inspector"), record["id"])
        self.assertEqual(len(detail["current"]), 1)
        self.assertEqual(detail["current"][0]["method"], "mail")
        self.assertEqual(detail["effective_date"], today.isoformat())
        self.assertEqual(detail["appeal_period"]["start"], today.isoformat())
        self.assertEqual(detail["appeal_period"]["deadline_days"], 60)
        self.assertEqual(len(detail["history"]), 2)
        self.assertEqual(len(detail["corrections"]), 1)
        self.assertTrue(detail["todos"])
