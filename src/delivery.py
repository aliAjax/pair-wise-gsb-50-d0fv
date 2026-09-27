"""稽查决定送达台账：登记资料与送达判定分开维护。

- DeliveryForms 负责登记/补正资料的字段级校验（"资料"）。
- DeliveryRules 负责生效日、复议期限、结案卡口与待办（"判定"）。

送达方式：
- direct 直接签收：以签收凭证日期生效。
- mail 邮寄送达：以回执凭证日期生效。
- notice 公告送达：仅在无法直接送达时使用，公告满三十日的次日生效。
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .domain import Conflict, ValidationError, iso_date, optional_text, text


METHODS = ("direct", "mail", "notice")
METHOD_LABELS = {"direct": "直接签收", "mail": "邮寄送达", "notice": "公告送达"}

ACTIVE_STATUS = "active"
SUPERSEDED_STATUS = "superseded"
NOTICE_DAYS = 30

DEFAULT_DOCUMENT = "稽查决定书"


def today_iso() -> str:
    return date.today().isoformat()


def _parse(day: str) -> date:
    return date.fromisoformat(day)


class DeliveryForms:
    """送达登记资料校验。"""

    def validate_registration(self, data: Dict[str, Any], has_active: bool) -> Dict[str, Any]:
        data = data or {}
        document = optional_text(data, "document", DEFAULT_DOCUMENT) or DEFAULT_DOCUMENT
        method = text(data, "method")
        if method not in METHODS:
            raise ValidationError("method只能是direct/mail/notice")

        if method == "notice":
            reason = text(data, "reason")
            if "无法直接送达" not in reason:
                raise ValidationError("公告送达只能在无法直接送达时使用，须说明无法直接送达原因")
            notice_day = iso_date(data, "notice_day")
            if notice_day > today_iso():
                raise ValidationError("公告发布日期不能晚于今天")
            voucher_day = ""
            voucher_label = ""
        else:
            reason = optional_text(data, "reason")
            notice_day = ""
            voucher_label = "签收日期" if method == "direct" else "回执日期"
            voucher_day = iso_date(data, "voucher_day")
            if voucher_day > today_iso():
                raise ValidationError("%s不能晚于今天" % voucher_label)

        # 同一文书只留一条有效送达：已有有效送达时，本次只能作为补正登记，且必须填写补正原因。
        corrects_id = data.get("corrects_id")
        if corrects_id is not None:
            if isinstance(corrects_id, bool) or not isinstance(corrects_id, int):
                raise ValidationError("corrects_id必须是整数")
            if corrects_id <= 0:
                raise ValidationError("corrects_id无效")
        correction_reason = optional_text(data, "correction_reason")
        if has_active:
            if not correction_reason:
                raise ValidationError("该文书已有有效送达，补正送达必须填写补正原因")
            if corrects_id is None:
                raise ValidationError("补正送达必须指明被补正的送达记录")
        else:
            if correction_reason and corrects_id is None:
                raise ValidationError("补正原因只能配合被补正记录一起提交")

        return {
            "document": document,
            "method": method,
            "voucher_day": voucher_day,
            "voucher_label": voucher_label,
            "notice_day": notice_day,
            "reason": reason,
            "corrects_id": corrects_id,
            "correction_reason": correction_reason,
        }


class DeliveryRules:
    """送达生效、复议期限与流程卡口判定。"""

    def effective_day(self, entry: Dict[str, Any], today: Optional[str] = None) -> str:
        """计算送达生效日；公告未满三十日时返回空串（尚未生效）。"""
        if entry["method"] == "notice":
            return self._notice_effective_day(entry["notice_day"], today=today)
        return entry["voucher_day"]

    def _notice_effective_day(self, notice_day: str, today: Optional[str] = None) -> str:
        # 公告满三十日，次日生效：公告日 + 30天为生效日。
        effective = _parse(notice_day) + timedelta(days=NOTICE_DAYS)
        current = _parse(today) if today else _parse(today_iso())
        if current < effective:
            return ""
        return effective.isoformat()

    def appeal_deadline(self, effective_day: str, deadline_days: int = 60) -> str:
        """复议期限从送达生效日起算，期限为60日。"""
        if not effective_day:
            return ""
        return (_parse(effective_day) + timedelta(days=int(deadline_days))).isoformat()

    def appeal_check(self, effective_day: str, appeal_day: str, deadline_days: int = 60) -> None:
        """复议申请必须在送达生效之后、复议期限届满之前。"""
        if not effective_day:
            raise ValidationError("送达尚未生效，不能申请复议")
        appeal = _parse(appeal_day)
        if appeal < _parse(effective_day):
            raise ValidationError("复议申请不能早于送达生效日")
        if appeal > _parse(self.appeal_deadline(effective_day, deadline_days)):
            raise ValidationError("复议申请超过期限")

    def close_blockers(self, entries: List[Dict[str, Any]], today: Optional[str] = None) -> List[str]:
        """送达生效前不能结案：返回阻断结案的原因列表，空列表表示可以结案。"""
        active = self.active_entry(entries)
        if active is None:
            return ["稽查决定尚未登记送达，送达生效前不能结案"]
        effective = self.effective_day(active, today=today)
        if not effective:
            label = METHOD_LABELS.get(active["method"], active["method"])
            return ["%s尚未生效，送达生效前不能结案" % label]
        return []

    def active_entry(self, entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for entry in entries:
            if entry.get("status", ACTIVE_STATUS) == ACTIVE_STATUS:
                return entry
        return None

    def build_view(self, entries: List[Dict[str, Any]], today: Optional[str] = None, deadline_days: int = 60) -> Dict[str, Any]:
        """组装台账详情：当前送达、生效日、待办和历次补正。"""
        active = self.active_entry(entries)
        effective = ""
        deadline = ""
        todos: List[str] = []
        if active is not None:
            effective = self.effective_day(active, today=today)
            if effective:
                deadline = self.appeal_deadline(effective, deadline_days)
            method_label = METHOD_LABELS.get(active["method"], active["method"])
            if active["method"] == "notice" and not effective:
                finish_day = (_parse(active["notice_day"]) + timedelta(days=NOTICE_DAYS)).isoformat()
                todos.append("公告送达等待期满：公告满三十日次日（%s）生效" % finish_day)
            else:
                todos.append("送达已于%s生效，复议期限自生效日起算至%s" % (effective, deadline))
        else:
            todos.append("尚未登记有效送达：按直接签收、邮寄回执或公告登记送达")
        blockers = self.close_blockers(entries, today=today)
        todos.extend(blockers)
        corrections = [
            {
                "id": entry["id"],
                "method": entry["method"],
                "method_label": METHOD_LABELS.get(entry["method"], entry["method"]),
                "voucher_day": entry["voucher_day"],
                "notice_day": entry["notice_day"],
                "effective_day": self.effective_day(entry, today=today) if entry["method"] == "notice" else entry["voucher_day"],
                "reason": entry["reason"],
                "corrects_id": entry["corrects_id"],
                "correction_reason": entry["correction_reason"],
                "created_by": entry["created_by"],
                "created_at": entry["created_at"],
            }
            for entry in entries
            if entry["corrects_id"] is not None
        ]
        return {
            "active": active,
            "effective_day": effective,
            "appeal_deadline": deadline,
            "todos": todos,
            "corrections": corrections,
            "can_close": not blockers,
            "history": entries,
        }
