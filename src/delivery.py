"""送达台账：资料校验、生效判定与详情汇总。

资料（登记内容的校验与归一化）与判定（生效日、能否结案/复议、待办）
集中在本模块维护，不依赖保存与接口层。
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .domain import Conflict, ValidationError, optional_text, text


METHOD_DIRECT = "direct"                # 直接签收
METHOD_MAIL = "mail"                    # 邮寄回执
METHOD_ANNOUNCEMENT = "announcement"    # 公告送达
METHODS = (METHOD_DIRECT, METHOD_MAIL, METHOD_ANNOUNCEMENT)
METHOD_LABELS = {
    METHOD_DIRECT: "直接签收",
    METHOD_MAIL: "邮寄回执",
    METHOD_ANNOUNCEMENT: "公告送达",
}

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"

# 公告之日起满三十日，次日生效
ANNOUNCEMENT_FULL_DAYS = 30

DECISION_GATED_ACTIONS = ("appeal", "close")


def parse_date(value: Any, key: str) -> date:
    if not isinstance(value, str):
        raise ValidationError("%s必须是YYYY-MM-DD格式的日期" % key)
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError("%s必须是YYYY-MM-DD格式的日期" % key) from exc


def effective_date(method: str, base_date: date) -> date:
    """按送达方式计算生效日。

    直接签收/邮寄回执以凭证日期（签收日、回执日）为生效日；
    公告自公告之日起满三十日的次日生效。
    """
    if method == METHOD_ANNOUNCEMENT:
        return base_date + timedelta(days=ANNOUNCEMENT_FULL_DAYS + 1)
    return base_date


def validate_delivery(payload: Dict[str, Any], today: date) -> Dict[str, Any]:
    """校验一条送达登记资料并归一化，凭证日期/公告日期不得晚于登记日。"""
    p = payload or {}
    document = text(p, "document")
    method = text(p, "method")
    if method not in METHODS:
        raise ValidationError("method只能是%s" % "/".join(METHODS))
    note = optional_text(p, "note")

    if method == METHOD_ANNOUNCEMENT:
        # 公告只能在无法直接送达时使用，必须登记原因
        unavailable_reason = text(p, "unavailable_reason")
        base_date = parse_date(p.get("announcement_date"), "announcement_date")
    else:
        # 直接签收、邮寄回执必须有凭证日期
        unavailable_reason = optional_text(p, "unavailable_reason")
        base_date = parse_date(p.get("voucher_date"), "voucher_date")

    if base_date > today:
        raise ValidationError("凭证日期不能晚于登记日期")

    return {
        "document": document,
        "method": method,
        "voucher_date": base_date.isoformat(),
        "effective_date": effective_date(method, base_date).isoformat(),
        "unavailable_reason": unavailable_reason,
        "note": note,
    }


def is_effective(row: Dict[str, Any], today: date) -> bool:
    return row["status"] == STATUS_ACTIVE and date.fromisoformat(row["effective_date"]) <= today


def ensure_action_allowed(action: str, deliveries: List[Dict[str, Any]], today: date) -> None:
    """结案/复议前的送达生效判定。"""
    if action not in DECISION_GATED_ACTIONS:
        return
    if any(is_effective(row, today) for row in deliveries):
        return
    if action == "close":
        raise Conflict("送达生效前不能结案")
    raise Conflict("送达尚未生效，复议期限还未起算")


def _build_todos(state: str, current: List[Dict[str, Any]], today: date, deadline_days: int) -> List[str]:
    if state == "closed":
        return []
    todos: List[str] = []
    if not current:
        todos.append("尚未登记有效送达，送达生效前不能结案")
    for item in current:
        document = item["document"]
        label = METHOD_LABELS[item["method"]]
        eff = date.fromisoformat(item["effective_date"])
        if eff <= today:
            end = (eff + timedelta(days=deadline_days)).isoformat()
            todos.append(
                "《%s》已于%s经%s送达生效，复议期限自生效日起%s日（至%s）"
                % (document, item["effective_date"], label, deadline_days, end)
            )
        elif item["method"] == METHOD_ANNOUNCEMENT:
            todos.append(
                "《%s》采取公告送达，公告满三十日次日即%s生效，生效前不能结案" % (document, item["effective_date"])
            )
        else:
            todos.append("《%s》%s尚未生效，预计%s生效" % (document, label, item["effective_date"]))
    if state == "reviewed" and any(date.fromisoformat(i["effective_date"]) <= today for i in current):
        todos.append("送达已生效，可在复议期限内受理复议或办理结案")
    return todos


def summarize(
    record: Dict[str, Any],
    deliveries: List[Dict[str, Any]],
    deadline_days: int,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """组装送达台账详情：当前送达、生效日、待办、历次补正与完整历史。"""
    today = today or date.today()
    state = record["state"]

    current: List[Dict[str, Any]] = []
    for row in deliveries:
        if row["status"] != STATUS_ACTIVE:
            continue
        item = dict(row)
        eff = date.fromisoformat(row["effective_date"])
        item["method_label"] = METHOD_LABELS[row["method"]]
        item["effective"] = eff <= today
        item["appeal_deadline"] = (eff + timedelta(days=deadline_days)).isoformat()
        current.append(item)
    current.sort(key=lambda i: i["effective_date"])

    effective_date = current[0]["effective_date"] if current else None
    appeal_period = None
    for item in current:
        if item["effective"]:
            start = date.fromisoformat(item["effective_date"])
            appeal_period = {
                "start": item["effective_date"],
                "deadline_days": deadline_days,
                "end": (start + timedelta(days=deadline_days)).isoformat(),
            }
            break

    corrections = [dict(row) for row in deliveries if row.get("correction_reason")]
    return {
        "record_id": record["id"],
        "state": state,
        "current": current,
        "effective_date": effective_date,
        "appeal_period": appeal_period,
        "todos": _build_todos(state, current, today, deadline_days),
        "corrections": corrections,
        "history": [dict(row) for row in deliveries],
    }
