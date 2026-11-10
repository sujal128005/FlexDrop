"""Optimisation runs (DCO), plan persistence/versioning and plan events (CRE)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from flexdrop.clock import from_minutes, to_minutes
from flexdrop.db.base import utcnow
from flexdrop.db.models import OptimizationRunRow, OrderRow, PartnerRow, PlanEventRow, PlanRow, PlanStopRow
from flexdrop.domain.models import (
    ActiveRoute,
    ColumnKind,
    DispatchDecision,
    GeoPoint,
    Order,
    PlannedRoute,
    RoutePlan,
    StopType,
)
from flexdrop.engines.flexibility import FlexibilityViolation, validate_allocation
from flexdrop.engines.optimizer import DynamicCoordinationOptimizer
from flexdrop.engines.reoptimizer import ReoptAction, ReoptimizationEngine, ReoptimizationResult
from flexdrop.errors import BusinessRuleError, ConflictError, NotFoundError
from flexdrop.schemas import OptimizeIn, OptimizeOut, PlanEventIn, PlanEventOut, PlanEventType, PlanOut
from flexdrop.services.common import (
    audit,
    cost_breakdown_json,
    kill_switch_state,
    order_to_domain,
    partner_to_domain,
    plan_row_to_out,
    plan_row_to_route,
    route_to_out,
)
from flexdrop.services.context import AppContext

log = logging.getLogger(__name__)

ACTIVE_PLAN_STATUSES = ("PROPOSED", "ACCEPTED", "IN_PROGRESS")
TERMINAL_PLAN_STATUSES = ("COMPLETED", "REJECTED", "CANCELLED")


class DispatchService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.cfg = ctx.engine_config
        self.optimizer = DynamicCoordinationOptimizer(self.cfg, ctx.models.travel)
        self.reopt = ReoptimizationEngine(self.cfg, ctx.models.travel)

    # ================================================================== optimisation
    def optimize(self, payload: OptimizeIn, actor: str) -> OptimizeOut:
        as_of = payload.as_of or utcnow()
        now = to_minutes(as_of)
        run_id = str(uuid.uuid4())
        with self.ctx.optimize_lock, self.ctx.db.session() as s:
            if kill_switch_state(s, self.ctx.settings.kill_switch_default)[0]:
                self._record_run(s, run_id, as_of, payload, "FALLBACK_KILL_SWITCH", "DISABLED", None, 0.0)
                return self._fallback(run_id, as_of, payload, "FALLBACK_KILL_SWITCH", "DISABLED")

            order_q = select(OrderRow).where(OrderRow.status == "PENDING")
            partner_q = select(PartnerRow).where(PartnerRow.status != "OFFLINE")
            if payload.zone:
                order_q = order_q.where(OrderRow.zone == payload.zone)
                partner_q = partner_q.where(PartnerRow.zone == payload.zone)
            order_rows = list(s.scalars(order_q))
            partner_rows = list(s.scalars(partner_q))
            orders = [order_to_domain(r) for r in order_rows]
            partners = [partner_to_domain(r) for r in partner_rows]
            partner_by_id = {p.id: p for p in partners}

            active: list[ActiveRoute] = []
            route_orders: dict[str, Order] = {}
            if self.cfg.optimizer.insertion_enabled:
                plan_q = select(PlanRow).where(PlanRow.status.in_(("PROPOSED", "ACCEPTED")), PlanRow.started.is_(False))
                for plan in s.scalars(plan_q):
                    p = partner_by_id.get(plan.partner_id)
                    if p is None:
                        continue
                    rows = [r for oid in plan.order_ids if (r := s.get(OrderRow, oid)) is not None]
                    if len(rows) != len(plan.order_ids) or any(r.status != "PLANNED" for r in rows):
                        continue
                    for r in rows:
                        route_orders[r.id] = order_to_domain(r)
                    active.append(
                        ActiveRoute(plan.id, plan_row_to_route(plan), p.location, now, plan.changes, plan.started)
                    )

            try:
                decision = self.optimizer.optimize(now, orders, partners, active, route_orders)
                self._validate(decision, orders, route_orders)
            except Exception as exc:  # automatic fallback to standard dispatch, never fail open
                log.exception("optimisation failed; falling back to standard dispatch")
                self._record_run(s, run_id, as_of, payload, "FALLBACK_ERROR", "ERROR", None, 0.0, error=repr(exc))
                return self._fallback(run_id, as_of, payload, "FALLBACK_ERROR", "ERROR")

            st = decision.stats
            self._record_run(s, run_id, as_of, payload, "FLEXDROP", st.status, decision, st.latency_ms)
            s.flush()  # plans reference the run row
            plans_out: list[PlanOut] = []
            if payload.dry_run:
                for pr in decision.routes:
                    plans_out.append(route_to_out(pr.route, pr.kind.value, pr.baseline_cost, None, 0, "DRY_RUN"))
            else:
                order_by_id = {r.id: r for r in order_rows}
                try:
                    for pr in decision.routes:
                        plans_out.append(self._persist_plan(s, pr, order_by_id, run_id, actor))
                    s.flush()
                except StaleDataError as exc:
                    raise ConflictError("orders or partners changed during optimisation; retry") from exc
            return OptimizeOut(
                run_id=run_id,
                mode="FLEXDROP",
                status=st.status,
                as_of=as_of,
                dry_run=payload.dry_run,
                latency_ms=round(st.latency_ms, 3),
                plans=plans_out,
                held_order_ids=decision.held_order_ids,
                deferred_order_ids=decision.deferred_order_ids,
                unassigned_due_order_ids=decision.unassigned_due_order_ids,
            )

    @staticmethod
    def _validate(decision: DispatchDecision, orders: list[Order], route_orders: dict[str, Order]) -> None:
        """Defence in depth: never emit a plan that breaks an approved window or budget."""
        pool = [*orders, *route_orders.values()]
        for pr in decision.routes:
            if pr.kind == ColumnKind.FALLBACK:
                continue
            validate_allocation(pr.route, pool)
            if pr.route.is_late:
                raise FlexibilityViolation("strict plan with lateness")

    def _record_run(
        self,
        s: Session,
        run_id: str,
        as_of: datetime,
        payload: OptimizeIn,
        mode: str,
        status: str,
        decision: DispatchDecision | None,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        st = decision.stats if decision else None
        s.add(
            OptimizationRunRow(
                id=run_id,
                as_of=as_of,
                mode=mode,
                status=status,
                solver=self.cfg.optimizer.solver,
                zone=payload.zone,
                dry_run=payload.dry_run,
                latency_ms=latency_ms,
                n_orders=st.n_orders if st else 0,
                n_partners=st.n_partners if st else 0,
                n_groups=st.n_groups if st else 0,
                n_columns=st.n_columns if st else 0,
                n_plans=len(decision.routes) if decision else 0,
                objective=st.objective if st else 0.0,
                error=error,
            )
        )

    @staticmethod
    def _fallback(run_id: str, as_of: datetime, payload: OptimizeIn, mode: str, status: str) -> OptimizeOut:
        return OptimizeOut(
            run_id=run_id,
            mode=mode,
            status=status,
            as_of=as_of,
            dry_run=payload.dry_run,
            latency_ms=0.0,
            plans=[],
            held_order_ids=[],
            deferred_order_ids=[],
            unassigned_due_order_ids=[],
        )

    def _persist_plan(
        self, s: Session, pr: PlannedRoute, order_by_id: dict[str, OrderRow], run_id: str, actor: str
    ) -> PlanOut:
        route: RoutePlan = pr.route
        if pr.kind == ColumnKind.INSERT:
            plan = s.get(PlanRow, pr.replaces_route_id)
            if plan is None or plan.status not in ("PROPOSED", "ACCEPTED") or plan.started:
                raise ConflictError("route changed before insertion could be applied")
            plan.version += 1
            plan.changes += 1
        else:
            partner = s.get(PartnerRow, route.partner_id)
            if partner is None or partner.status != "IDLE":
                raise ConflictError(f"partner '{route.partner_id}' is no longer idle")
            partner.status = "ASSIGNED"
            plan = PlanRow(id=str(uuid.uuid4()), version=1, status="PROPOSED", kind=pr.kind.value, changes=0)
            plan.partner_id = route.partner_id
            plan.optimization_run_id = run_id
            s.add(plan)
        self._write_route(s, plan, route, pr.baseline_cost)
        s.flush()  # the plan row must exist before orders reference it (no ORM relationship orders->plans)
        for oid in pr.new_order_ids:
            o = order_by_id.get(oid) or s.get(OrderRow, oid)
            if o is None or o.status != "PENDING":
                raise ConflictError(f"order '{oid}' is no longer pending")
            o.status = "PLANNED"
            o.plan_id = plan.id
        audit(
            s,
            actor,
            "plan.insert" if pr.kind == ColumnKind.INSERT else "plan.create",
            "plan",
            plan.id,
            {
                "version": plan.version,
                "kind": pr.kind.value,
                "partner_id": route.partner_id,
                "order_ids": list(route.order_ids),
                "flex_allocation": {k: round(v, 3) for k, v in route.flex_used.items()},
                "expected_savings": round(pr.expected_savings, 3),
                "objective_value": round(pr.value, 3),
                "risk_violations": route.risk_violations,
            },
        )
        return plan_row_to_out(plan)

    @staticmethod
    def _write_route(s: Session, plan: PlanRow, route: RoutePlan, baseline: float) -> None:
        plan.order_ids = list(route.order_ids)
        plan.cost_total = route.cost.total
        plan.partner_pay = route.cost.partner_pay
        plan.cost_breakdown = cost_breakdown_json(route)
        plan.distance_km = route.distance_km
        plan.start_at = from_minutes(route.start_at)
        plan.end_at = from_minutes(route.end_at)
        plan.baseline_cost = baseline
        plan.expected_savings = baseline - route.cost.total
        plan.objective = route.objective
        plan.risk_violations = route.risk_violations
        plan.flex_allocation = {k: round(v, 4) for k, v in route.flex_used.items()}
        plan.lateness = {k: round(v, 4) for k, v in route.lateness.items()}
        for seq, stop in enumerate(route.stops):
            plan.stops.append(
                PlanStopRow(
                    version=plan.version,
                    seq=seq,
                    order_id=stop.order_id,
                    stop_type=stop.type.value,
                    lat=stop.location.lat,
                    lng=stop.location.lng,
                    eta=from_minutes(stop.arrival),
                    etd=from_minutes(stop.departure),
                    wait_min=stop.wait,
                )
            )

    # ================================================================== plans
    def get_plan(self, s: Session, plan_id: str) -> PlanOut:
        plan = s.get(PlanRow, plan_id)
        if plan is None:
            raise NotFoundError(f"plan '{plan_id}' not found")
        return plan_row_to_out(plan)

    def handle_event(self, plan_id: str, event: PlanEventIn, actor: str) -> PlanEventOut:
        at = event.at or utcnow()
        now = to_minutes(at)
        with self.ctx.db.session() as s:
            plan = s.get(PlanRow, plan_id)
            if plan is None:
                raise NotFoundError(f"plan '{plan_id}' not found")
            if plan.status in TERMINAL_PLAN_STATUSES:
                raise ConflictError(f"plan is {plan.status}; no further events accepted")
            partner = s.get(PartnerRow, plan.partner_id)
            if partner is None:  # pragma: no cover - FK guarantees existence
                raise NotFoundError("partner of plan not found")
            if event.partner_location is not None:
                partner.lat, partner.lng = event.partner_location.lat, event.partner_location.lng

            result: ReoptimizationResult | None = None
            action = event.type.value
            orders = {oid: s.get(OrderRow, oid) for oid in plan.order_ids}
            order = self._event_order(event, orders)

            if event.type == PlanEventType.DISPATCH_ACCEPTED:
                self._require(plan.status == "PROPOSED", "only PROPOSED plans can be accepted")
                plan.status = "ACCEPTED"
            elif event.type == PlanEventType.DISPATCH_REJECTED:
                self._require(not plan.started, "a started plan cannot be rejected")
                released = [oid for oid, o in orders.items() if o is not None and o.status == "PLANNED"]
                self._release(orders, released)
                plan.status = "REJECTED"
                partner.status = "IDLE"
                result = ReoptimizationResult(ReoptAction.RELEASED_ALL, None, released_order_ids=released)
            elif event.type == PlanEventType.PICKED_UP:
                order = self._need(order)
                self._require(order.status == "PLANNED", f"order is {order.status}, expected PLANNED")
                order.status = "PICKED_UP"
                order.picked_up_at = at
                plan.started = True
                plan.status = "IN_PROGRESS"
            elif event.type == PlanEventType.DELIVERED:
                order = self._need(order)
                self._require(order.status == "PICKED_UP", f"order is {order.status}, expected PICKED_UP")
                order.status = "DELIVERED"
                order.delivered_at = at
                if order.mode == "FLEX":
                    order.reward_status = "CREDITED"
                partner.lat, partner.lng = order.drop_lat, order.drop_lng
                if all(o is None or o.status in ("DELIVERED", "CANCELLED") for o in orders.values()):
                    plan.status = "COMPLETED"
                    partner.status = "IDLE"
                    partner.available_at = at
                    action = "COMPLETED"
            elif event.type == PlanEventType.ORDER_CANCELLED:
                order = self._need(order)
                self._require(order.status == "PLANNED", "orders can only be cancelled before pickup")
                order.status = "CANCELLED"
                order.cancelled_at = at
                if order.reward_status == "PENDING":
                    order.reward_status = "FORFEITED"
                result = self._replan(s, plan, partner, orders, now, "cancellation")
            elif event.type == PlanEventType.PREP_DELAY:
                order = self._need(order)
                if event.new_ready_at is None:
                    raise BusinessRuleError("new_ready_at is required for PREP_DELAY")
                self._require(order.status == "PLANNED", "delay only applies before pickup")
                if event.new_ready_at > order.predicted_ready_at:
                    order.predicted_ready_at = event.new_ready_at
                    order.ready_p90_at = max(order.ready_p90_at, event.new_ready_at)
                result = self._replan(s, plan, partner, orders, now, "prep_delay")
            elif event.type == PlanEventType.PARTNER_UNAVAILABLE:
                remaining = [order_to_domain(o) for o in orders.values() if o and o.status in ("PLANNED", "PICKED_UP")]
                picked = frozenset(o.id for o in orders.values() if o and o.status == "PICKED_UP")
                result = self.reopt.partner_unavailable(remaining, picked)
                self._release(orders, result.released_order_ids)
                partner.status = "OFFLINE"
                plan.status = "CANCELLED" if not picked else "IN_PROGRESS"
            if result is not None:
                action = result.action.value if event.type != PlanEventType.DISPATCH_REJECTED else "REJECTED"

            s.add(
                PlanEventRow(
                    plan_id=plan.id,
                    event_type=event.type.value,
                    order_id=event.order_id,
                    payload=event.model_dump(mode="json"),
                    result_action=action,
                    plan_version=plan.version,
                )
            )
            audit(
                s,
                actor,
                f"plan.event.{event.type.value.lower()}",
                "plan",
                plan.id,
                {
                    "order_id": event.order_id,
                    "action": action,
                    "version": plan.version,
                    "released": result.released_order_ids if result else [],
                    "at_risk": result.at_risk_order_ids if result else [],
                },
            )
            s.flush()
            return PlanEventOut(
                plan=plan_row_to_out(plan),
                action=action,
                released_order_ids=result.released_order_ids if result else [],
                at_risk_order_ids=result.at_risk_order_ids if result else [],
            )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise ConflictError(message)

    @staticmethod
    def _need(order: OrderRow | None) -> OrderRow:
        if order is None:  # guarded by _event_order; explicit check survives `python -O`
            raise BusinessRuleError("order_id is required for this event")
        return order

    @staticmethod
    def _event_order(event: PlanEventIn, orders: dict[str, OrderRow | None]) -> OrderRow | None:
        needs_order = event.type in (
            PlanEventType.PICKED_UP,
            PlanEventType.DELIVERED,
            PlanEventType.ORDER_CANCELLED,
            PlanEventType.PREP_DELAY,
        )
        if not needs_order:
            return None
        if not event.order_id:
            raise BusinessRuleError(f"order_id is required for {event.type.value}")
        order = orders.get(event.order_id)
        if order is None:
            raise NotFoundError(f"order '{event.order_id}' is not part of this plan")
        return order

    @staticmethod
    def _release(orders: dict[str, OrderRow | None], order_ids: list[str]) -> None:
        for oid in order_ids:
            o = orders.get(oid)
            if o is not None and o.status == "PLANNED":
                o.status = "PENDING"
                o.plan_id = None

    def _replan(
        self,
        s: Session,
        plan: PlanRow,
        partner: PartnerRow,
        orders: dict[str, OrderRow | None],
        now: float,
        reason: str,
    ) -> ReoptimizationResult:
        live = [o for o in orders.values() if o is not None and o.status in ("PLANNED", "PICKED_UP")]
        if not live:
            plan.status = "CANCELLED" if not plan.started else "COMPLETED"
            partner.status = "IDLE"
            partner.available_at = from_minutes(now)
            return ReoptimizationResult(ReoptAction.COMPLETED, None, reason=reason)
        picked = frozenset(o.id for o in live if o.status == "PICKED_UP")
        live_ids = {o.id for o in live}
        current = [
            (st.order_id, StopType(st.stop_type))
            for st in sorted((x for x in plan.stops if x.version == plan.version), key=lambda x: x.seq)
            if st.order_id in live_ids and not (st.stop_type == "PICKUP" and st.order_id in picked)
        ]
        result = self.reopt.replan(
            now,
            plan.partner_id,
            GeoPoint(partner.lat, partner.lng),
            max(now, to_minutes(plan.start_at), to_minutes(partner.available_at)) if not plan.started else now,
            [order_to_domain(o) for o in live],
            picked,
            current_sequence=current,
            reason=reason,
        )
        self._release(orders, result.released_order_ids)
        if result.route is not None:
            done = [oid for oid in plan.order_ids if (o := orders.get(oid)) is not None and o.status == "DELIVERED"]
            plan.version += 1
            plan.changes += 1
            self._write_route(s, plan, result.route, plan.baseline_cost)
            plan.order_ids = [*done, *result.route.order_ids]
        elif result.action == ReoptAction.RELEASED_ALL:
            plan.status = "CANCELLED"
            partner.status = "IDLE"
            partner.available_at = from_minutes(now)
        return result
