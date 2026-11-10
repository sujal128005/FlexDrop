"""Discrete-event simulation of a dispatch policy on a scenario.

Events (processed in time order; ties broken by priority then insertion order):

=============  ========================================================================
ARRIVE         partner reaches the next stop of its route (pickup waits for true ready)
PREP_DELAY     store signals that an order will be ready later than predicted -> CRE
CANCEL         customer cancels (accepted only before pickup) -> CRE
DROPOUT        partner becomes unavailable -> CRE releases unpicked orders
ORDER          a new order is placed: predictions, promise, flexible offer, customer choice
EPOCH          decision epoch: DCO optimises pending orders / idle partners / insertions
=============  ========================================================================

The decision engines only observe *predictions*; the simulator advances with *ground
truth* (true preparation times, noisy travel times, customer utilities).
"""

from __future__ import annotations

import heapq
import logging
import math
import random
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from flexdrop.clock import is_weekend, local_hour
from flexdrop.domain.models import (
    ActiveRoute,
    Category,
    ColumnKind,
    CostBreakdown,
    DeliveryMode,
    GeoPoint,
    Order,
    OrderStatus,
    Partner,
    PartnerStatus,
    PlannedRoute,
    RoutePlan,
    StopType,
)
from flexdrop.engines.cost import CostModel
from flexdrop.engines.incentive import CustomerContext, FlexOffer, IncentiveOptimizer
from flexdrop.engines.optimizer import DynamicCoordinationOptimizer
from flexdrop.engines.promise import compute_density, standard_promise
from flexdrop.engines.reoptimizer import ReoptAction, ReoptimizationEngine
from flexdrop.engines.travel import TravelOracle
from flexdrop.geo import interpolate, road_km
from flexdrop.ml.features import CoordinationFeatures, PrepFeatures
from flexdrop.ml.registry import ModelBundle
from flexdrop.simulation.policies import PolicySpec
from flexdrop.simulation.scenario import Customer, OrderRequest, Scenario

log = logging.getLogger(__name__)

ARRIVE, PREP_DELAY, CANCEL, DROPOUT, ORDER, EPOCH = range(6)
EVENT_PRIORITY = {ARRIVE: 0, PREP_DELAY: 1, CANCEL: 1, DROPOUT: 1, ORDER: 2, EPOCH: 3}
DRAIN_LIMIT_MIN = 180.0


@dataclass
class SimOrder:
    req: OrderRequest
    order: Order
    customer: Customer
    true_ready_at: float
    offers: list[FlexOffer] = field(default_factory=list)
    accepted: FlexOffer | None = None
    route_id: str | None = None
    picked_at: float | None = None
    delivered_at: float | None = None
    cancelled_at: float | None = None
    store_wait: float = 0.0
    route_size: int = 0
    at_risk: bool = False
    released_count: int = 0
    coord_features: CoordinationFeatures | None = None


@dataclass
class SimPartner:
    partner: Partner
    route_id: str | None = None
    pending_offline: bool = False
    offline_at: float | None = None
    engaged_min: float = 0.0
    earnings: float = 0.0
    routes: int = 0
    # idle partners drift back to the nearest demand hub (unpaid, not part of route cost)
    reposition: tuple[GeoPoint, float, GeoPoint, float] | None = None


@dataclass
class SimRoute:
    id: str
    partner_id: str
    plan: RoutePlan
    kind: ColumnKind
    remaining: list[tuple[str, StopType]]
    order_ids: set[str]
    start_at: float
    version: int = 0
    started: bool = False
    changes: int = 0
    leg: tuple[GeoPoint, float, GeoPoint, float] | None = None
    distance_km: float = 0.0
    completed: bool = False
    delivered: int = 0
    cost: CostBreakdown | None = None
    end_at: float | None = None


@dataclass
class SimulationResult:
    metrics: dict[str, Any]
    orders: list[dict[str, Any]]
    epochs: list[dict[str, Any]]
    coordination_samples: list[dict[str, Any]]


class Simulator:
    def __init__(self, scenario: Scenario, policy: PolicySpec, models: ModelBundle, seed: int = 0):
        self.scenario = scenario
        self.policy = policy
        self.cfg = policy.engine
        self.models = models
        self.seed = seed
        self.truth = scenario.spec.truth
        self.cost = CostModel(self.cfg.cost)
        self.optimizer = DynamicCoordinationOptimizer(self.cfg, models.travel, self.cost)
        self.reopt = ReoptimizationEngine(self.cfg, models.travel, self.cost)
        self.incentive = IncentiveOptimizer(self.cfg, models, self.cost)
        self.rng = np.random.default_rng(seed + 1000)
        self.tz = self.cfg.travel.tz_offset_min
        self.stores = scenario.store_by_id
        self.hubs = self._demand_hubs()

        self.queue: list[tuple[float, int, int, int, Any]] = []
        self._seq = 0
        self.now = scenario.start_at
        self.orders: dict[str, SimOrder] = {}
        self.partners: dict[str, SimPartner] = {}
        self.routes: dict[str, SimRoute] = {}
        self.epochs: list[dict[str, Any]] = []
        self.counters: dict[str, int] = {
            "reopt_events": 0,
            "reopt_resequenced": 0,
            "reopt_released_orders": 0,
            "reopt_at_risk": 0,
            "late_cancellations_rejected": 0,
            "insertions": 0,
            "fallback_routes": 0,
            "partner_dropouts": 0,
        }

    def _demand_hubs(self, n_food: int = 6) -> list[GeoPoint]:
        """Busiest restaurants plus all dark stores: where idle partners wait for work."""
        counts: dict[str, int] = {}
        for req in self.scenario.orders:
            counts[req.store_id] = counts.get(req.store_id, 0) + 1
        food = sorted(
            (s for s in self.scenario.stores if s.category == Category.FOOD),
            key=lambda s: (-counts.get(s.id, 0), s.id),
        )[:n_food]
        grocery = [s for s in self.scenario.stores if s.category == Category.GROCERY]
        return [s.location for s in (*food, *grocery)] or [self.scenario.spec.center]

    def _update_idle_positions(self, now: float) -> None:
        for sp in self.partners.values():
            if sp.partner.status != PartnerStatus.IDLE or sp.reposition is None:
                continue
            a, t0, b, t1 = sp.reposition
            if now >= t1:
                sp.partner.location = b
                sp.reposition = None
            elif now > t0:
                sp.partner.location = interpolate(a, b, (now - t0) / max(1e-9, t1 - t0))

    # ------------------------------------------------------------------ event queue
    def _push(self, t: float, kind: int, payload: Any) -> None:
        self._seq += 1
        heapq.heappush(self.queue, (t, EVENT_PRIORITY[kind], self._seq, kind, payload))

    def run(self) -> SimulationResult:
        sc = self.scenario
        for ps in sc.partners:
            self.partners[ps.id] = SimPartner(Partner(ps.id, ps.location, sc.start_at, ps.vehicle, zone=sc.spec.name))
            if ps.dropout_at is not None:
                self._push(ps.dropout_at, DROPOUT, ps.id)
        for req in sc.orders:
            self._push(req.placed_at, ORDER, req)
        self._push(sc.start_at + self.cfg.optimizer.epoch_min, EPOCH, None)

        handlers: dict[int, Callable[[Any], None]] = {
            ARRIVE: self._on_arrive,
            PREP_DELAY: self._on_prep_delay,
            CANCEL: self._on_cancel,
            DROPOUT: self._on_dropout,
            ORDER: self._on_order,
            EPOCH: self._on_epoch,
        }
        while self.queue:
            t, _, _, kind, payload = heapq.heappop(self.queue)
            self.now = t
            handlers[kind](payload)
        return self._result()

    # ------------------------------------------------------------------ ground truth
    def _true_travel(self, a: GeoPoint, b: GeoPoint, at: float) -> float:
        km = road_km(a, b, self.cfg.travel.road_factor)
        mean = self.truth.mean_travel_minutes(km, local_hour(at, self.tz))
        return mean * float(self.rng.lognormal(0.0, self.truth.travel_noise_sigma)) if mean > 0 else 0.0

    def _customer_choice(self, order_id: str, customer: Customer, offers: list[FlexOffer]) -> FlexOffer | None:
        """Multinomial-logit choice between standard delivery (u=0) and the offered tiers."""
        if not offers:
            return None
        utilities = [
            self.truth.utility(
                o.reward, o.extra_minutes, customer.urgency, customer.price_sensitivity, customer.is_student
            )
            for o in offers
        ]
        weights = [1.0] + [math.exp(min(50.0, u)) for u in utilities]
        rnd = random.Random(zlib.crc32(f"{self.seed}:{order_id}".encode()))  # noqa: S311 - simulation only
        x = rnd.random() * sum(weights)
        acc = weights[0]
        if x < acc:
            return None
        for offer, w in zip(offers, weights[1:], strict=True):
            acc += w
            if x < acc:
                return offer
        return offers[-1]

    # ------------------------------------------------------------------ handlers
    def _on_order(self, req: OrderRequest) -> None:
        store = self.stores[req.store_id]
        cust = self.scenario.customers[req.customer_id]
        hour = local_hour(req.placed_at, self.tz)
        mean, p90 = self.models.prep.predict(
            [
                PrepFeatures(
                    is_food=req.category == Category.FOOD,
                    store_base_prep=store.base_prep_min,
                    item_count=req.item_count,
                    hour=hour,
                    is_weekend=is_weekend(req.placed_at, self.tz),
                    store_load=req.store_load,
                )
            ]
        )
        pred, pred90 = float(mean[0]), float(p90[0])
        oracle = TravelOracle(self.models.travel, self.cfg.travel, req.placed_at)
        trip = oracle.minutes(store.location, cust.drop)
        approach = oracle.minutes_for_km(self.cfg.travel.approach_estimate_km)
        order = Order(
            id=req.id,
            store_id=store.id,
            category=req.category,
            pickup=store.location,
            drop=cust.drop,
            placed_at=req.placed_at,
            predicted_ready_at=req.placed_at + pred,
            ready_p90_at=req.placed_at + max(pred90, pred),
            promised_at=standard_promise(req.placed_at, pred, trip, approach, self.cfg),
            handling=req.handling,
            item_count=req.item_count,
            basket_value=req.basket_value,
            zone=self.scenario.spec.name,
        )
        so = SimOrder(req=req, order=order, customer=cust, true_ready_at=req.placed_at + req.true_prep_min)
        self.orders[req.id] = so

        open_orders = [
            (s.order.pickup, s.order.drop)
            for s in self.orders.values()
            if s.order.status in (OrderStatus.PENDING, OrderStatus.PLANNED)
            and s.order.id != order.id
            and s.order.placed_at >= req.placed_at - self.cfg.flex.density_window_min
        ]
        density = compute_density(order.pickup, order.drop, open_orders, self.cfg.flex.density_radius_km)
        if self.policy.offers_enabled:
            so.offers = self.incentive.quote(
                order, CustomerContext(cust.past_acceptance_rate, cust.is_student), density, oracle, hour
            )
            choice = self._customer_choice(order.id, cust, so.offers)
            if choice is not None:
                so.accepted = choice
                order.mode = DeliveryMode.FLEX
                order.max_extra_wait = float(choice.extra_minutes)
                order.reward = choice.reward
        so.coord_features = CoordinationFeatures(
            extra_min=order.max_extra_wait,
            n_near_pickup=density.n_near_pickup,
            n_near_drop=density.n_near_drop,
            n_near_both=density.n_near_both,
            hour=hour,
            is_food=order.category == Category.FOOD,
        )

        if so.true_ready_at > order.predicted_ready_at + self.truth.delay_signal_threshold_min:
            signal_at = max(req.placed_at + 1.0, order.predicted_ready_at - 3.0)
            self._push(signal_at, PREP_DELAY, (order.id, so.true_ready_at))
        if req.cancel_after_min is not None:
            self._push(req.placed_at + req.cancel_after_min, CANCEL, order.id)

    def _on_epoch(self, _: Any) -> None:
        now = self.now
        self._update_idle_positions(now)
        pending = [s.order for s in self.orders.values() if s.order.status == OrderStatus.PENDING]
        partners = [sp.partner for sp in self.partners.values() if sp.partner.status != PartnerStatus.OFFLINE]
        active: list[ActiveRoute] = []
        route_orders: dict[str, Order] = {}
        if self.cfg.optimizer.insertion_enabled:
            for r in self.routes.values():
                if r.completed or r.started or r.leg is None:
                    continue
                live = [oid for oid in r.order_ids if self.orders[oid].order.status == OrderStatus.PLANNED]
                if set(live) != set(r.plan.order_ids):
                    continue
                active.append(ActiveRoute(r.id, r.plan, self._position(r, now), now, r.changes, r.started))
                for oid in live:
                    route_orders[oid] = self.orders[oid].order

        if pending:
            decision = self.optimizer.optimize(now, pending, partners, active, route_orders)
            for planned in decision.routes:
                if planned.kind == ColumnKind.INSERT:
                    self._apply_insertion(planned, now)
                else:
                    self._start_route(planned, now)
            st = decision.stats
            self.epochs.append(
                {
                    "t": round(now - self.scenario.start_at, 3),
                    "pending": st.n_orders,
                    "idle_partners": st.n_partners,
                    "groups": st.n_groups,
                    "columns": st.n_columns,
                    "routes": len(decision.routes),
                    "held": len(decision.held_order_ids),
                    "deferred": len(decision.deferred_order_ids),
                    "unassigned_due": len(decision.unassigned_due_order_ids),
                    "status": st.status,
                    "latency_ms": round(st.latency_ms, 3),
                }
            )

        unfinished = any(
            s.order.status not in (OrderStatus.DELIVERED, OrderStatus.CANCELLED) for s in self.orders.values()
        )
        nxt = now + self.cfg.optimizer.epoch_min
        if nxt <= self.scenario.end_at or (unfinished and nxt <= self.scenario.end_at + DRAIN_LIMIT_MIN):
            self._push(nxt, EPOCH, None)

    def _on_arrive(self, payload: tuple[str, int]) -> None:
        rid, version = payload
        route = self.routes[rid]
        if route.completed or version != route.version or not route.remaining or route.leg is None:
            return
        oid, stop_type = route.remaining.pop(0)
        leg_from, _, leg_to, _ = route.leg
        route.distance_km += road_km(leg_from, leg_to, self.cfg.travel.road_factor)
        route.started = True
        so = self.orders[oid]
        sp = self.partners[route.partner_id]
        sp.partner.location = leg_to
        tp = self.cfg.travel
        if stop_type == StopType.PICKUP:
            if so.order.status == OrderStatus.CANCELLED:
                departure = self.now
            else:
                wait = max(0.0, so.true_ready_at - self.now)
                so.store_wait = wait
                so.picked_at = self.now + wait
                so.order.status = OrderStatus.PICKED_UP
                departure = self.now + wait + tp.pickup_service_min
        else:
            so.delivered_at = self.now
            so.order.status = OrderStatus.DELIVERED
            route.delivered += 1
            departure = self.now + tp.drop_service_min
        if route.remaining:
            self._schedule_leg(route, leg_to, departure)
        else:
            self._finish_route(route, departure)

    def _on_prep_delay(self, payload: tuple[str, float]) -> None:
        oid, new_ready = payload
        so = self.orders[oid]
        o = so.order
        o.predicted_ready_at = max(o.predicted_ready_at, new_ready)
        o.ready_p90_at = max(o.ready_p90_at, new_ready + 2.0)
        if o.status == OrderStatus.PLANNED and so.route_id is not None:
            self._replan(self.routes[so.route_id], "prep_delay")

    def _on_cancel(self, oid: str) -> None:
        so = self.orders[oid]
        o = so.order
        if o.status == OrderStatus.PENDING:
            o.status = OrderStatus.CANCELLED
            so.cancelled_at = self.now
        elif o.status == OrderStatus.PLANNED and so.route_id is not None:
            route = self.routes[so.route_id]
            o.status = OrderStatus.CANCELLED
            so.cancelled_at = self.now
            route.order_ids.discard(oid)
            anchor = route.remaining[:1]
            rest = [s for s in route.remaining[1:] if s[0] != oid]
            route.remaining = anchor + rest
            if rest:
                self._replan(route, "cancellation")
        elif o.status in (OrderStatus.PICKED_UP, OrderStatus.DELIVERED):
            self.counters["late_cancellations_rejected"] += 1

    def _on_dropout(self, pid: str) -> None:
        sp = self.partners[pid]
        self.counters["partner_dropouts"] += 1
        if sp.partner.status == PartnerStatus.IDLE:
            sp.partner.status = PartnerStatus.OFFLINE
            sp.offline_at = self.now
            return
        route = self.routes.get(sp.route_id or "")
        if route is None:
            return
        if route.started:
            sp.pending_offline = True
            return
        live = [self.orders[o].order for o in route.order_ids if self.orders[o].order.status == OrderStatus.PLANNED]
        result = self.reopt.partner_unavailable(live, frozenset())
        self.counters["reopt_events"] += 1
        self._release(route, result.released_order_ids)
        if route.leg is not None and self.now > route.leg[1]:
            frac = (self.now - route.leg[1]) / max(1e-9, route.leg[3] - route.leg[1])
            route.distance_km += road_km(route.leg[0], route.leg[2], self.cfg.travel.road_factor) * min(1.0, frac)
        route.remaining = []
        route.version += 1
        sp.pending_offline = True
        self._finish_route(route, self.now)

    # ------------------------------------------------------------------ route lifecycle
    def _start_route(self, planned: PlannedRoute, now: float) -> None:
        plan = planned.route
        sp = self.partners[plan.partner_id]
        rid = f"R{len(self.routes) + 1:05d}"
        route = SimRoute(
            id=rid,
            partner_id=sp.partner.id,
            plan=plan,
            kind=planned.kind,
            remaining=[(s.order_id, s.type) for s in plan.stops],
            order_ids=set(plan.order_ids),
            start_at=max(now, sp.partner.available_at),
        )
        self.routes[rid] = route
        if planned.kind == ColumnKind.FALLBACK:
            self.counters["fallback_routes"] += 1
        sp.partner.status = PartnerStatus.ASSIGNED
        sp.route_id = rid
        sp.routes += 1
        sp.reposition = None
        for oid in plan.order_ids:
            self.orders[oid].order.status = OrderStatus.PLANNED
            self.orders[oid].route_id = rid
        self._schedule_leg(route, sp.partner.location, route.start_at)

    def _apply_insertion(self, planned: PlannedRoute, now: float) -> None:
        route = self.routes[planned.replaces_route_id or ""]
        if route.started or route.completed or route.leg is None:
            return
        position = self._position(route, now)
        if now > route.leg[1]:
            route.distance_km += road_km(route.leg[0], position, self.cfg.travel.road_factor)
        route.plan = planned.route
        route.remaining = [(s.order_id, s.type) for s in planned.route.stops]
        route.order_ids.update(planned.new_order_ids)
        route.changes += 1
        route.version += 1
        self.counters["insertions"] += 1
        for oid in planned.new_order_ids:
            self.orders[oid].order.status = OrderStatus.PLANNED
            self.orders[oid].route_id = route.id
        self._schedule_leg(route, position, now)

    def _schedule_leg(self, route: SimRoute, origin: GeoPoint, depart_at: float) -> None:
        oid, stop_type = route.remaining[0]
        o = self.orders[oid].order
        target = o.pickup if stop_type == StopType.PICKUP else o.drop
        arrive = depart_at + self._true_travel(origin, target, depart_at)
        route.leg = (origin, depart_at, target, arrive)
        self._push(arrive, ARRIVE, (route.id, route.version))

    def _finish_route(self, route: SimRoute, end_at: float) -> None:
        route.completed = True
        route.end_at = end_at
        engaged = max(0.0, end_at - route.start_at)
        route.cost = self.cost.route_cost(route.distance_km, engaged, max(1, route.delivered))
        for oid in route.order_ids:
            so = self.orders[oid]
            if so.order.status == OrderStatus.DELIVERED:
                so.route_size = route.delivered
        sp = self.partners[route.partner_id]
        sp.engaged_min += engaged
        sp.earnings += route.cost.partner_pay
        sp.route_id = None
        sp.partner.available_at = end_at
        if sp.pending_offline:
            sp.partner.status = PartnerStatus.OFFLINE
            sp.offline_at = end_at
        else:
            sp.partner.status = PartnerStatus.IDLE
            here = sp.partner.location
            hub = min(self.hubs, key=lambda h: road_km(here, h, 1.0))
            km = road_km(here, hub, self.cfg.travel.road_factor)
            minutes = self.truth.mean_travel_minutes(km, local_hour(end_at, self.tz))
            sp.reposition = (here, end_at, hub, end_at + minutes) if minutes > 0 else None

    def _position(self, route: SimRoute, t: float) -> GeoPoint:
        if route.leg is None:
            return self.partners[route.partner_id].partner.location
        a, t0, b, t1 = route.leg
        if t <= t0:
            return a
        return interpolate(a, b, (t - t0) / max(1e-9, t1 - t0))

    def _release(self, route: SimRoute, order_ids: list[str]) -> None:
        for oid in order_ids:
            so = self.orders[oid]
            so.order.status = OrderStatus.PENDING
            so.route_id = None
            so.released_count += 1
            route.order_ids.discard(oid)
            route.remaining = [s for s in route.remaining if s[0] != oid]
        self.counters["reopt_released_orders"] += len(order_ids)

    def _replan(self, route: SimRoute, reason: str) -> None:
        """CRE: re-plan the stops after the in-flight one."""
        if route.completed or not route.remaining or route.leg is None:
            return
        tp = self.cfg.travel
        anchor_oid, anchor_type = route.remaining[0]
        anchor_order = self.orders[anchor_oid].order
        anchor_loc, arrive = route.leg[2], route.leg[3]
        if anchor_type == StopType.PICKUP:
            if anchor_order.status == OrderStatus.CANCELLED:
                anchor_dep = arrive
            else:
                anchor_dep = max(arrive, anchor_order.predicted_ready_at) + tp.pickup_service_min
        else:
            anchor_dep = arrive + tp.drop_service_min
        live = [
            oid
            for oid in sorted(route.order_ids)
            if self.orders[oid].order.status in (OrderStatus.PLANNED, OrderStatus.PICKED_UP)
        ]
        picked = {oid for oid in live if self.orders[oid].order.status == OrderStatus.PICKED_UP}
        if anchor_type == StopType.PICKUP and anchor_order.status != OrderStatus.CANCELLED:
            picked.add(anchor_oid)
        remaining_orders = [
            self.orders[oid].order for oid in live if not (anchor_type == StopType.DROP and oid == anchor_oid)
        ]
        rest = route.remaining[1:]
        result = self.reopt.replan(
            self.now,
            route.partner_id,
            anchor_loc,
            anchor_dep,
            remaining_orders,
            frozenset(picked),
            current_sequence=rest,
            reason=reason,
        )
        self.counters["reopt_events"] += 1
        if result.action == ReoptAction.COMPLETED:
            return
        self._release(route, result.released_order_ids)
        if result.route is not None:
            route.remaining = route.remaining[:1] + [(s.order_id, s.type) for s in result.route.stops]
            route.plan = result.route
            if result.action != ReoptAction.UNCHANGED:
                self.counters["reopt_resequenced"] += 1
        for oid in result.at_risk_order_ids:
            self.orders[oid].at_risk = True
            self.counters["reopt_at_risk"] += 1

    # ------------------------------------------------------------------ results
    def _result(self) -> SimulationResult:
        from flexdrop.simulation.metrics import compute_metrics

        metrics = compute_metrics(self)
        orders = []
        samples = []
        for so in self.orders.values():
            o = so.order
            rec = {
                "order_id": o.id,
                "category": o.category.value,
                "placed_at": round(o.placed_at - self.scenario.start_at, 3),
                "mode": o.mode.value,
                "max_extra_wait": o.max_extra_wait,
                "reward": o.reward,
                "offers_shown": len(so.offers),
                "status": o.status.value,
                "promised_min": round(o.promised_at - o.placed_at, 3),
                "delivered_min": round(so.delivered_at - o.placed_at, 3) if so.delivered_at else None,
                "on_time": (so.delivered_at <= o.latest_delivery_at + 1e-6) if so.delivered_at else None,
                "route_size": so.route_size,
                "store_wait": round(so.store_wait, 3),
                "released_count": so.released_count,
                "at_risk": so.at_risk,
            }
            orders.append(rec)
            if so.coord_features is not None and o.status == OrderStatus.DELIVERED:
                f = so.coord_features
                samples.append(
                    {
                        "extra_min": f.extra_min,
                        "n_near_pickup": f.n_near_pickup,
                        "n_near_drop": f.n_near_drop,
                        "n_near_both": f.n_near_both,
                        "hour": round(f.hour, 4),
                        "is_food": int(f.is_food),
                        "coordinated": int(so.route_size >= 2),
                    }
                )
        return SimulationResult(metrics=metrics, orders=orders, epochs=self.epochs, coordination_samples=samples)
