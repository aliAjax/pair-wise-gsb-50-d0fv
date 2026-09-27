import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import build_service
from src.delivery import DeliveryForms, DeliveryRules, NOTICE_DAYS
from src.domain import Actor, Conflict, PermissionDenied, ValidationError


CREATE_DATA = {'taxpayer': 'Star Ltd', 'tax_period': '2025-Q4', 'declared_tax': 500000.0, 'assessed_tax': 760000.0, 'penalty_rate': 0.2, 'evidence_count': 4, 'days_late': 90, 'appeal_deadline_day': 60}
FLOW = [('investigate', 'inspector', {'plan': '核对账簿'}, 'investigating'), ('propose', 'inspector', {'proposal': '补税并处罚'}, 'proposed'), ('review', 'reviewer', {'outcome': 'accepted', 'review_note': '证据充分'}, 'reviewed')]


def today():
    return date.today().isoformat()


class DeliveryRuleTest(unittest.TestCase):
    def setUp(self):
        self.rules = DeliveryRules()
        self.forms = DeliveryForms()

    def test_direct_and_mail_effective_on_voucher_day(self):
        direct = self.forms.validate_registration({'method': 'direct', 'voucher_day': '2026-03-02'}, False)
        self.assertEqual(self.rules.effective_day(direct), '2026-03-02')
        mail = self.forms.validate_registration({'method': 'mail', 'voucher_day': '2026-03-05'}, False)
        self.assertEqual(self.rules.effective_day(mail), '2026-03-05')
        self.assertEqual(self.rules.appeal_deadline('2026-03-02', 60), '2026-05-01')

    def test_notice_requires_unreachable_reason(self):
        with self.assertRaises(ValidationError):
            self.forms.validate_registration({'method': 'notice', 'notice_day': '2026-03-01', 'reason': '走流程'}, False)
        entry = self.forms.validate_registration(
            {'method': 'notice', 'notice_day': '2026-03-01', 'reason': '纳税人下落不明无法直接送达'}, False
        )
        # 公告满三十日次日生效：2026-03-31生效。
        self.assertEqual(self.rules.effective_day(entry, today='2026-03-31'), '2026-03-31')
        # 期内尚未生效。
        self.assertEqual(self.rules.effective_day(entry, today='2026-03-30'), '')

    def test_close_blocked_until_effective(self):
        entry = self.forms.validate_registration(
            {'method': 'notice', 'notice_day': today(), 'reason': '无法直接送达，已留置无果'}, False
        )
        blockers = self.rules.close_blockers([entry])
        self.assertTrue(blockers)
        later = (date.today() + timedelta(days=NOTICE_DAYS)).isoformat()
        self.assertEqual(self.rules.close_blockers([entry], today=later), [])
        self.assertTrue(self.rules.close_blockers([]))

    def test_appeal_window(self):
        self.rules.appeal_check('2026-03-31', '2026-04-01', 60)
        with self.assertRaises(ValidationError):
            self.rules.appeal_check('2026-03-31', '2026-03-30', 60)
        with self.assertRaises(ValidationError):
            self.rules.appeal_check('2026-03-31', '2026-06-01', 60)
        with self.assertRaises(ValidationError):
            self.rules.appeal_check('', '2026-04-01', 60)


class DeliveryBookServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))
        self.record = self.service.create(Actor("creator", "inspector"), "TAX-27001", CREATE_DATA)
        for action, role, data, _state in FLOW:
            self.record = self.service.act(Actor("operator", role), self.record["id"], self.record["version"], action, data)

    def tearDown(self):
        self.temp.cleanup()

    def _register(self, payload, actor=None):
        return self.service.register_delivery(actor or Actor("clerk", "inspector"), self.record["id"], payload)

    def test_register_and_book_view(self):
        book = self._register({'method': 'direct', 'voucher_day': today()})
        self.assertEqual(book['active']['method'], 'direct')
        self.assertEqual(book['effective_day'], today())
        self.assertTrue(book['can_close'])
        self.assertEqual(book['corrections'], [])
        stored = self.service.delivery_book(Actor("viewer", "reviewer"), self.record["id"])
        self.assertEqual(len(stored['history']), 1)

    def test_correction_keeps_original_single_active(self):
        first = self._register({'method': 'direct', 'voucher_day': today()})
        first_id = first['active']['id']
        with self.assertRaises(ValidationError):
            # 已有有效送达，未给补正原因不能重复登记。
            self._register({'method': 'mail', 'voucher_day': today()})
        book = self._register({
            'method': 'mail', 'voucher_day': today(),
            'corrects_id': first_id, 'correction_reason': '签收人无授权，改以邮寄回执为准',
        })
        self.assertEqual(book['active']['method'], 'mail')
        self.assertEqual(book['active']['corrects_id'], first_id)
        self.assertEqual(len(book['history']), 2)
        statuses = {entry['id']: entry['status'] for entry in book['history']}
        self.assertEqual(statuses[first_id], 'superseded')
        self.assertEqual(len(book['corrections']), 1)
        self.assertEqual(book['corrections'][0]['correction_reason'], '签收人无授权，改以邮寄回执为准')

    def test_correction_must_point_to_active(self):
        first = self._register({'method': 'direct', 'voucher_day': today()})
        with self.assertRaises(Conflict):
            self._register({'method': 'mail', 'voucher_day': today(), 'corrects_id': 9999, 'correction_reason': 'x'})
        self._register({'method': 'mail', 'voucher_day': today(), 'corrects_id': first['active']['id'], 'correction_reason': '更换凭证'})
        # 原记录已被取代，不能再次指向它补正。
        with self.assertRaises(Conflict):
            self._register({'method': 'direct', 'voucher_day': today(), 'corrects_id': first['active']['id'], 'correction_reason': '再次补正'})

    def test_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            self._register({'method': 'direct', 'voucher_day': today()}, Actor("rep", "taxpayer_rep"))

    def test_close_and_appeal_gating(self):
        # 未送达不能结案。
        with self.assertRaises(ValidationError):
            self.service.act(Actor("closer", "reviewer"), self.record["id"], self.record["version"], "close", {'final_decision': '维持'})
        # 公告期未满不能结案、不能复议。
        pending = self._register({'method': 'notice', 'notice_day': today(), 'reason': '无法直接送达，原址查无此人'})
        with self.assertRaises(ValidationError):
            self.service.act(Actor("closer", "reviewer"), self.record["id"], self.record["version"], "close", {'final_decision': '维持'})
        with self.assertRaises(ValidationError):
            self.service.act(Actor("rep", "taxpayer_rep"), self.record["id"], self.record["version"], "appeal",
                            {'appeal_day': today(), 'appeal_reason': '有异议'})
        # 补正为三十天前发布的公告（保留原记录），公告满三十日次日生效后可以结案。
        published = (date.today() - timedelta(days=NOTICE_DAYS)).isoformat()
        effective = (date.today()).isoformat()
        book = self._register({
            'method': 'notice', 'notice_day': published, 'reason': '无法直接送达，公告日期更正',
            'corrects_id': pending['active']['id'], 'correction_reason': '公告发布日期登记有误',
        })
        self.assertEqual(book['effective_day'], effective)
        record = self.service.get_record(Actor("viewer", "reviewer"), self.record["id"])
        closed = self.service.act(Actor("closer", "reviewer"), record["id"], record["version"], "close", {'final_decision': '维持'})
        self.assertEqual(closed["payload"]["delivery_effective_day"], effective)
