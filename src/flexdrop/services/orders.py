"""Order intake, flexible-offer quoting (CFI + EIO) and explicit customer consent."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from flexdrop.clock import from_minutes, is_weekend, local_hour, to_minutes
from flexdrop.db.base import utcnow
from flexdrop.db.models import ConsentRow, FlexOfferRow, OrderRow
from flexdrop.domain.models import Category, GeoPoint, Handling, Order
from flexdrop.engines.incentive import CustomerContext, IncentiveOptimizer
from flexdrop.engines.promise import compute_density, standard_promise
from flexdrop.engines.travel import TravelOracle
from flexdrop.errors import BusinessRuleError, ConflictError, NotFoundError
from flexdrop.ml.features import PrepFeatures
from flexdrop.schemas import OfferOut, OrderIn, OrderOut, PreferenceDecision, PreferenceIn, PreferenceOut
from flexdrop.services.catalog import get_store
from flexdrop.services.common import audit, handling_to_json, kill_switch_state, pseudonymize
from flexdrop.services.context import AppContext

log = logging.getLogger(__name__)


class OrderService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.cfg = ctx.engine_config
        self.incentive = IncentiveOptimizer(self.cfg, ctx.models)

    # ------------------------------------------------------------------ create + quote
    def create(self, session: Session, payload: OrderIn, actor: str) -> tuple[OrderOut, list[FlexOfferRow]]:
        if session.get(OrderRow, payload.id) is not None:
            raise ConflictError(f"order '{payload.id}' already exists", {"order_id": payload.id})
        store = get_store(session, payload.store_id)
        placed_dt = payload.placed_at or utcnow()
        placed = to_minutes(placed_dt)
        tz = self.cfg.travel.tz_offset_min

        load = (
            session.scalar(
                select(func.count())
                .select_from(OrderRow)
                .where(OrderRow.store_id == store.id, OrderRow.placed_at >= placed_dt - timedelta(minutes=20))
            )
            or 0
        )
        is_food = store.category == Category.FOOD.value
        mean, p90 = self.ctx.models.prep.predict(
            [
                PrepFeatures(
                    is_food=is_food,
                    store_base_prep=store.base_prep_min,
                    item_count=payload.item_count,
                    hour=local_hour(placed, tz),
                    is_weekend=is_weekend(placed, tz),
                    store_load=int(load),
                )
            ]
        )
        prep, prep90 = float(mean[0]), float(max(p90[0], mean[0]))
        pickup = GeoPoint(store.lat, store.lng)
        drop = GeoPoint(payload.drop.lat, payload.drop.lng)
        oracle = TravelOracle(self.ctx.models.travel, self.cfg.travel, placed)
        trip = oracle.minutes(pickup, drop)
        approach = oracle.minutes_for_km(self.cfg.travel.approach_estimate_km)
        promised = standard_promise(placed, prep, trip, approach, self.cfg)
        h = payload.handling
        handling = Handling(
            temp_classes=frozenset(h.temp_classes),
            volume_l=h.volume_l,
            weight_kg=h.weight_kg,
            leak_risk=h.leak_risk,
            raw_meat=h.raw_meat,
            sealed=h.sealed,
            fragile=h.fragile,
        )
        if store.category == Category.FOOD.value and h.raw_meat and not h.sealed:
            raise BusinessRuleError("unsealed raw meat cannot be accepted for delivery")

        row = OrderRow(
            id=payload.id,
            store_id=store.id,
            customer_ref=pseudonymize(
                payload.customer_id, self.ctx.settings.pseudonymization_secret.get_secret_value()
            ),
            category=store.category,
            pickup_lat=pickup.lat,
            pickup_lng=pickup.lng,
            drop_lat=drop.lat,
            drop_lng=drop.lng,
            placed_at=placed_dt,
            predicted_ready_at=from_minutes(placed + prep),
            ready_p90_at=from_minutes(placed + prep90),
            promised_at=from_minutes(promised),
            item_count=payload.item_count,
            basket_value=payload.basket_value,
            handling=handling_to_json(handling),
            zone=store.zone,
            store_load=int(load),
        )
        session.add(row)
        session.flush()

        offers: list[FlexOfferRow] = []
        reason: str | None = None
        if not payload.request_flex_offers:
            reason = "not_requested"
        elif kill_switch_state(session, self.ctx.settings.kill_switch_default)[0]:
            reason = "flexdrop_disabled"
        else:
            offers = self._quote(session, row, payload, oracle)
            if not offers:
                reason = "no_economically_viable_offer"
        audit(
            session,
            actor,
            "order.create",
            "order",
            row.id,
            {
                "store_id": store.id,
                "predicted_prep_min": round(prep, 3),
                "promised_at": row.promised_at.isoformat(),
                "offers": [{"id": o.id, "minutes": o.extra_minutes, "reward": o.reward} for o in offers],
                "model_versions": self.ctx.models.describe(),
            },
        )
        return self.order_out(row, offers, reason), offers

    def _quote(self, session: Session, row: OrderRow, payload: OrderIn, oracle: TravelOracle) -> list[FlexOfferRow]:
        fp = self.cfg.flex
        window_start = row.placed_at - timedelta(minutes=fp.density_window_min)
        open_rows = session.execute(
            select(OrderRow.pickup_lat, OrderRow.pickup_lng, OrderRow.drop_lat, OrderRow.drop_lng).where(
                OrderRow.status.in_(("PENDING", "PLANNED")),
                OrderRow.zone == row.zone,
                OrderRow.placed_at >= window_start,
                OrderRow.id != row.id,
            )
        ).all()
        density = compute_density(
            GeoPoint(row.pickup_lat, row.pickup_lng),
            GeoPoint(row.drop_lat, row.drop_lng),
            [(GeoPoint(a, b), GeoPoint(c, d)) for a, b, c, d in open_rows],
            fp.density_radius_km,
        )
        order = self.to_domain(row)
        quotes = self.incentive.quote(
            order,
            CustomerContext(payload.customer.past_flex_acceptance_rate, payload.customer.is_student),
            density,
            oracle,
            local_hour(order.placed_at, self.cfg.travel.tz_offset_min),
        )
        expires = max(utcnow(), row.placed_at) + timedelta(minutes=fp.offer_ttl_min)
        rows = []
        for q in quotes:
            offer = FlexOfferRow(
                id=str(uuid.uuid4()),
                order_id=row.id,
                extra_minutes=q.extra_minutes,
                reward=q.reward,
                acceptance_probability=round(q.acceptance_probability, 6),
                coordination_probability=round(q.coordination_probability, 6),
                expected_savings=q.expected_savings,
                expected_net_value=q.expected_net_value,
                flex_eta_at=from_minutes(q.flex_eta_at),
                expires_at=expires,
                model_versions=self.ctx.models.describe(),
            )
            session.add(offer)
            rows.append(offer)
        return rows

    @staticmethod
    def to_domain(row: OrderRow) -> Order:
        from flexdrop.services.common import order_to_domain

        return order_to_domain(row)

    @staticmethod
    def order_out(row: OrderRow, offers: list[FlexOfferRow], reason: str | None = None) -> OrderOut:
        return OrderOut(
            id=row.id,
            store_id=row.store_id,
            category=Category(row.category),
            status=row.status,
            mode=row.mode,
            placed_at=row.placed_at,
            predicted_ready_at=row.predicted_ready_at,
            standard_eta=row.promised_at,
            latest_delivery_at=row.promised_at + timedelta(minutes=row.max_extra_wait),
            max_extra_wait=row.max_extra_wait,
            reward=row.reward,
            reward_status=row.reward_status,
            plan_id=row.plan_id,
            offers=[
                OfferOut(
                    offer_id=o.id,
                    extra_minutes=o.extra_minutes,
                    reward=o.reward,
                    flex_eta=o.flex_eta_at,
                    expires_at=o.expires_at,
                    acceptance_probability=o.acceptance_probability,
                    coordination_probability=o.coordination_probability,
                    expected_net_value=o.expected_net_value,
                )
                for o in sorted(offers, key=lambda o: o.extra_minutes)
            ],
            offers_unavailable_reason=reason,
        )

    def get(self, session: Session, order_id: str) -> OrderOut:
        row = session.get(OrderRow, order_id)
        if row is None:
            raise NotFoundError(f"order '{order_id}' not found")
        offers = list(session.scalars(select(FlexOfferRow).where(FlexOfferRow.order_id == order_id)))
        return self.order_out(row, offers)

    def cancel_pending(self, session: Session, order_id: str, reason: str, actor: str) -> OrderRow:
        """Cancel an order that is not yet planned. Planned orders go through the plan (CRE)."""
        row = session.get(OrderRow, order_id)
        if row is None:
            raise NotFoundError(f"order '{order_id}' not found")
        if row.status != "PENDING":
            raise ConflictError(f"order is {row.status}; only PENDING orders are cancelled here")
        row.status = "CANCELLED"
        row.cancelled_at = utcnow()
        if row.reward_status == "PENDING":
            row.reward_status = "FORFEITED"
        audit(session, actor, "order.cancel", "order", row.id, {"reason": reason, "stage": "PENDING"})
        return row

    # ------------------------------------------------------------------ consent
    def record_preference(self, session: Session, payload: PreferenceIn, actor: str) -> PreferenceOut:
        row = session.get(OrderRow, payload.order_id)
        if row is None:
            raise NotFoundError(f"order '{payload.order_id}' not found")
        if row.status != "PENDING":
            raise ConflictError("delivery preference can only be recorded before the order is planned")
        if session.scalar(select(ConsentRow).where(ConsentRow.order_id == row.id)) is not None:
            raise ConflictError("a delivery preference was already recorded for this order")

        if payload.decision == PreferenceDecision.STANDARD:
            consent = ConsentRow(
                id=str(uuid.uuid4()), order_id=row.id, offer_id=None, accepted=False, source=payload.source
            )
            row.mode, row.max_extra_wait, row.reward, row.reward_status = "STANDARD", 0.0, 0.0, "NONE"
        else:
            if not payload.offer_id:
                raise BusinessRuleError("offer_id is required to accept flexible delivery")
            offer = session.get(FlexOfferRow, payload.offer_id)
            if offer is None or offer.order_id != row.id:
                raise NotFoundError("offer not found for this order")
            if offer.expires_at < utcnow():
                raise ConflictError("offer expired; request a new quote", {"expired_at": offer.expires_at.isoformat()})
            if kill_switch_state(session, self.ctx.settings.kill_switch_default)[0]:
                raise ConflictError("flexible delivery is temporarily unavailable")
            consent = ConsentRow(
                id=str(uuid.uuid4()),
                order_id=row.id,
                offer_id=offer.id,
                accepted=True,
                extra_minutes=offer.extra_minutes,
                reward=offer.reward,
                source=payload.source,
            )
            row.mode = "FLEX"
            row.max_extra_wait = float(offer.extra_minutes)
            row.reward = offer.reward
            row.reward_status = "PENDING"
        session.add(consent)
        audit(
            session,
            actor,
            "consent.recorded",
            "order",
            row.id,
            {
                "decision": payload.decision.value,
                "offer_id": payload.offer_id,
                "approved_extra_minutes": row.max_extra_wait,
                "reward": row.reward,
                "source": payload.source,
            },
        )
        return PreferenceOut(
            order_id=row.id,
            mode=row.mode,
            max_extra_wait=row.max_extra_wait,
            reward=row.reward,
            standard_eta=row.promised_at,
            latest_delivery_at=row.promised_at + timedelta(minutes=row.max_extra_wait),
        )
