import unittest
from datetime import date, timedelta

from src.delivery import (
    ANNOUNCEMENT_FULL_DAYS,
    METHOD_ANNOUNCEMENT,
    METHOD_DIRECT,
    METHOD_MAIL,
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    ensure_action_allowed,
    validate_delivery,
)
from src.domain import Conflict, ValidationError


class DeliveryRulesTest(unittest.TestCase):
    def test_direct_and_mail_need_voucher_date(self):
        for method in (METHOD_DIRECT, METHOD_MAIL):
            with self.assertRaises(ValidationError):
                validate_delivery({"document": "决定书", "method": method}, date.today())

    def test_direct_effective_on_voucher_date(self):
        voucher = date(2026, 9, 1)
        for method in (METHOD_DIRECT, METHOD_MAIL):
            entry = validate_delivery(
                {"document": "决定书", "method": method, "voucher_date": voucher.isoformat()},
                date(2026, 9, 10),
            )
            self.assertEqual(entry["voucher_date"], "2026-09-01")
            self.assertEqual(entry["effective_date"], "2026-09-01")

    def test_announcement_needs_unavailable_reason(self):
        with self.assertRaises(ValidationError):
            validate_delivery(
                {"document": "决定书", "method": METHOD_ANNOUNCEMENT, "announcement_date": "2026-09-01"},
                date.today(),
            )

    def test_announcement_effective_next_day_after_thirty_days(self):
        entry = validate_delivery(
            {
                "document": "决定书",
                "method": METHOD_ANNOUNCEMENT,
                "announcement_date": "2026-09-01",
                "unavailable_reason": "下落不明，直接送达和邮寄均无法送达",
            },
            date(2026, 9, 1),
        )
        expected = (date(2026, 9, 1) + timedelta(days=ANNOUNCEMENT_FULL_DAYS + 1)).isoformat()
        self.assertEqual(entry["voucher_date"], "2026-09-01")
        self.assertEqual(entry["effective_date"], expected)
        self.assertEqual(expected, "2026-10-02")

    def test_voucher_date_cannot_be_future(self):
        with self.assertRaises(ValidationError):
            validate_delivery(
                {"document": "决定书", "method": METHOD_DIRECT, "voucher_date": "2099-01-01"},
                date(2026, 9, 1),
            )

    def _row(self, effective, status=STATUS_ACTIVE):
        return {"status": status, "effective_date": effective.isoformat(), "document": "决定书", "method": METHOD_DIRECT}

    def test_close_and_appeal_blocked_before_effective(self):
        future = date.today() + timedelta(days=5)
        for action in ("close", "appeal"):
            with self.assertRaises(Conflict):
                ensure_action_allowed(action, [self._row(future)], date.today())
            with self.assertRaises(Conflict):
                ensure_action_allowed(action, [], date.today())

        superseded = self._row(date.today() - timedelta(days=1), STATUS_SUPERSEDED)
        with self.assertRaises(Conflict):
            ensure_action_allowed("close", [superseded], date.today())

    def test_close_and_appeal_allowed_once_effective(self):
        past = date.today() - timedelta(days=1)
        ensure_action_allowed("close", [self._row(past)], date.today())
        ensure_action_allowed("appeal", [self._row(past)], date.today())
        ensure_action_allowed("investigate", [], date.today())


if __name__ == "__main__":
    unittest.main()
