"""业务用例编排、权限检查与审计。"""
from typing import Any, Dict, List, Optional

from .audit import AuditRecorder
from .delivery import DEFAULT_DOCUMENT, DeliveryForms, DeliveryRules
from .domain import Actor, PermissionDenied, text
from .repository import Repository
from .rules import DomainRules


class Service:
    def __init__(self, repository: Repository, rules: DomainRules, audit: AuditRecorder = None, delivery_forms: DeliveryForms = None, delivery_rules: DeliveryRules = None) -> None:
        self.repository = repository
        self.rules = rules
        self.audit = audit or AuditRecorder(repository)
        self.delivery_forms = delivery_forms or DeliveryForms()
        self.delivery_rules = delivery_rules or DeliveryRules()

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
        record = self.repository.get(record_id)
        deadline_days = int(record["payload"].get("appeal_deadline_day", 60))
        view = self._delivery_payload(self.repository.list_deliveries(record_id), deadline_days)
        record["delivery"] = {
            "active": view["active"],
            "effective_day": view["effective_day"],
            "appeal_deadline": view["appeal_deadline"],
            "todos": view["todos"],
            "can_close": view["can_close"],
            "correction_count": len(view["corrections"]),
        }
        return record

    def act(self, actor: Actor, record_id: int, expected_version: int, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        action = text({"action": action}, "action")
        if not self.rules.role_can_action(actor.role, action):
            raise PermissionDenied("角色无权执行该操作")
        record = self.repository.get(record_id)
        self.rules.require_transition(record, action)
        delivery_view = None
        if action in {"appeal", "close"}:
            entries = self.repository.list_deliveries(record_id)
            deadline_days = int(record["payload"].get("appeal_deadline_day", 60))
            delivery_view = self.delivery_rules.build_view(entries, deadline_days=deadline_days)
        new_state, new_payload, summary = self.rules.apply_action(record, action, data or {}, delivery_view)
        return self.repository.mutate(
            record_id=record_id,
            expected_version=int(expected_version),
            state=new_state,
            payload=new_payload,
            actor_id=actor.user_id,
            action=action,
            details={"summary": summary, "input": data or {}, "from": record["state"], "to": new_state},
        )

    def register_delivery(self, actor: Actor, record_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """登记送达或补正送达；同一文书始终只保留一条有效送达。"""
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        if not self.rules.role_can_action(actor.role, "deliver"):
            raise PermissionDenied("角色无权登记送达")
        record = self.repository.get(record_id)
        document = (data or {}).get("document")
        document = document.strip() if isinstance(document, str) and document.strip() else DEFAULT_DOCUMENT
        existing = self.repository.list_deliveries(record_id)
        has_active = any(e["status"] == "active" and e["document"] == document for e in existing)
        entry = self.delivery_forms.validate_registration(data, has_active)
        deadline_days = int(record["payload"].get("appeal_deadline_day", 60))
        entries = self.repository.register_delivery(
            record_id=record_id,
            entry=entry,
            actor_id=actor.user_id,
            audit_details={"document": document, "method": entry["method"], "correction": entry["corrects_id"] is not None},
        )
        return self._delivery_payload(entries, deadline_days)

    def delivery_book(self, actor: Actor, record_id: int) -> Dict[str, Any]:
        """送达台账详情：当前送达、生效日、待办和历次补正。"""
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        record = self.repository.get(record_id)
        deadline_days = int(record["payload"].get("appeal_deadline_day", 60))
        entries = self.repository.list_deliveries(record_id)
        payload = self._delivery_payload(entries, deadline_days)
        payload["record_id"] = record_id
        return payload

    def _delivery_payload(self, entries: List[Dict[str, Any]], deadline_days: int) -> Dict[str, Any]:
        return self.delivery_rules.build_view(entries, deadline_days=deadline_days)

    def timeline(self, actor: Actor, record_id: int) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.audit.timeline(record_id)

    def stats(self, actor: Actor) -> Dict[str, int]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.repository.stats()
