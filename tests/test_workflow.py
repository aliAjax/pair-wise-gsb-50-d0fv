import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed')]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_workflow_and_audit(self):
        record = self.service.create(Actor("creator", "inspector"), "TAX-26001", CREATE_DATA)
        self.assertEqual(record["state"], "opened")
        for action, role, data, expected_state in FLOW:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
            self.assertEqual(record["state"], expected_state)
        # 送达生效前不能结案：先登记直接签收送达，签收日即生效日。
        with self.assertRaises(Exception):
            self.service.act(Actor("closer", "reviewer"), record["id"], record["version"], "close", {'final_decision': '维持处理'})
        record = self.service.get_record(Actor("creator", "inspector"), record["id"])
        book = self.service.register_delivery(
            Actor("clerk", "inspector"),
            record["id"],
            {'method': 'direct', 'voucher_day': date.today().isoformat()},
        )
        self.assertEqual(book["effective_day"], date.today().isoformat())
        self.assertTrue(book["can_close"])
        record = self.service.act(Actor("closer", "reviewer"), record["id"], record["version"], "close", {'final_decision': '维持处理'})
        self.assertEqual(record["state"], "closed")
        timeline = self.service.timeline(Actor("creator", "inspector"), record["id"])
        actions = [event["action"] for event in timeline]
        self.assertIn("delivery_registered", actions)
        self.assertEqual(timeline[-1]["action"], "close")
