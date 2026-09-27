"""业务用例编排、权限检查与审计。"""
from datetime import date
from typing import Any, Dict, List, Optional

from . import delivery as delivery_domain
from .audit import AuditRecorder
from .domain import Actor, PermissionDenied, text
from .repository import Repository
from .rules import DomainRules


DELIVERY_ROLES = {"inspector", "reviewer"}
DELIVERY_ACTIONS = {"delivery_register", "delivery_correct"}


class Service:
    def __init__(self, repository: Repository, rules: DomainRules, audit: AuditRecorder = None, appeal_deadline_days: int = 60) -> None:
        self.repository = repository
        self.rules = rules
        self.audit = audit or AuditRecorder(repository)
        self.appeal_deadline_days = int(appeal_deadline_days)

    @staticmethod
    def _actor(actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        return actor

    def _ensure_known_role(self, actor: Actor) -> None:
        if not self.rules.known_role(actor.role):
            raise PermissionDenied("角色无权访问该服务")

    def create(self, actor: Actor, reference: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        if not self.rules.role_can_create(actor.role):
            raise PermissionDenied("角色无权创建记录")
        reference = text({"reference": reference}, "reference")
        prepared = self.rules.prepare_create(payload or {})
        self.rules.check_create_conflicts(prepared, self.repository.list_records(limit=500))
        return self.repository.create(reference, self.rules.INITIAL_STATE, prepared, actor.user_id)

    def list_records(self, actor: Actor, state: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.list_records(state=state, limit=limit)

    def get_record(self, actor: Actor, record_id: int) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.get(record_id)

    def act(self, actor: Actor, record_id: int, expected_version: int, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        action = text({"action": action}, "action")
        if not self.rules.role_can_action(actor.role, action):
            raise PermissionDenied("角色无权执行该操作")
        record = self.repository.get(record_id)
        self.rules.require_transition(record, action)
        # 送达生效前不能结案；复议期限从送达生效日起算，未生效不受理
        delivery_domain.ensure_action_allowed(action, self.repository.list_deliveries(record_id), date.today())
        new_state, new_payload, summary = self.rules.apply_action(record, action, data or {})
        return self.repository.mutate(
            record_id=record_id,
            expected_version=int(expected_version),
            state=new_state,
            payload=new_payload,
            actor_id=actor.user_id,
            action=action,
            details={"summary": summary, "input": data or {}, "from": record["state"], "to": new_state},
        )

    def timeline(self, actor: Actor, record_id: int) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.audit.timeline(record_id)

    def _ensure_delivery_role(self, actor: Actor) -> None:
        if actor.role != "admin" and actor.role not in DELIVERY_ROLES:
            raise PermissionDenied("角色无权登记送达")

    def register_delivery(self, actor: Actor, record_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        self._ensure_delivery_role(actor)
        record = self.repository.get(record_id)
        if record["state"] == "closed":
            raise PermissionDenied("案件已结案，不能再登记送达")
        entry = delivery_domain.validate_delivery(data or {}, date.today())
        saved = self.repository.save_delivery(
            record_id, entry, actor.user_id, "delivery_register",
            {"document": entry["document"], "method": entry["method"], "effective_date": entry["effective_date"]},
        )
        return saved

    def correct_delivery(self, actor: Actor, record_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        self._ensure_delivery_role(actor)
        record = self.repository.get(record_id)
        if record["state"] == "closed":
            raise PermissionDenied("案件已结案，不能再补正送达")
        reason = text(data or {}, "reason")
        entry = delivery_domain.validate_delivery(data or {}, date.today())
        saved = self.repository.save_delivery(
            record_id, entry, actor.user_id, "delivery_correct",
            {"document": entry["document"], "method": entry["method"], "effective_date": entry["effective_date"], "reason": reason},
            correction_reason=reason,
        )
        return saved

    def delivery_detail(self, actor: Actor, record_id: int, today: date = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        record = self.repository.get(record_id)
        deliveries = self.repository.list_deliveries(record_id)
        return delivery_domain.summarize(record, deliveries, self.appeal_deadline_days, today or date.today())

    def stats(self, actor: Actor) -> Dict[str, int]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.stats()
