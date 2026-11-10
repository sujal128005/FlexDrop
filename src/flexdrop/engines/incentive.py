"""Customer Flexibility Intelligence (CFI) + Economic Incentive Optimizer (EIO).

At checkout we quote, for each extra-wait option ``m`` (e.g. 5/10/15 minutes), the reward
``r`` that maximises the expected net value of showing the offer::

    S_bar       = max(0, (C_solo - C_marginal) / 2)      # savings attributable per order
    q(m)        = P(order gets coordinated | m, local density)   (coordination model)
    p(m, r)     = P(customer accepts | m, r, features)           (acceptance model)
    E(m, r)     = p(m, r) * (q(m) * S_bar - r)

    r*(m) = argmax_r E(m, r)
      s.t.  r_min <= r <= r_max
            r <= share_max * q(m) * S_bar                 # never give away the savings
            q(m) * S_bar - r >= margin_min                # offer must remain viable
            promised + m <= ready + age_max - margin_q    # food-quality guard

The reward is paid whenever the order is delivered inside its approved window, even if no
coordination happened - that risk is priced in through ``q(m)``.
The ML prediction never replaces consent: offers are only *quotes*; a customer's explicit
acceptance is recorded separately before any delivery window changes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from flexdrop.config import EngineConfig
from flexdrop.domain.models import Category, Order
from flexdrop.engines.cost import CostModel
from flexdrop.engines.travel import TravelOracle
from flexdrop.ml.features import AcceptanceFeatures, CoordinationFeatures
from flexdrop.ml.registry import ModelBundle


@dataclass(frozen=True, slots=True)
class CustomerContext:
    past_acceptance_rate: float = 0.3
    is_student: bool = True


@dataclass(frozen=True, slots=True)
class DensityContext:
    n_near_pickup: int = 0
    n_near_drop: int = 0
    n_near_both: int = 0


@dataclass(frozen=True, slots=True)
class FlexOffer:
    extra_minutes: int
    reward: float
    acceptance_probability: float
    coordination_probability: float
    expected_savings: float  # q * S_bar
    expected_net_value: float  # p * (q * S_bar - r)
    flex_eta_at: float  # promised + m


class IncentiveOptimizer:
    def __init__(self, cfg: EngineConfig, models: ModelBundle, cost: CostModel | None = None):
        self.cfg = cfg
        self.models = models
        self.cost = cost or CostModel(cfg.cost)

    def attributable_savings(self, order: Order, travel: TravelOracle) -> float:
        tp = self.cfg.travel
        trip = travel.minutes(order.pickup, order.drop)
        approach = travel.minutes_for_km(tp.approach_estimate_km)
        solo = self.cost.solo_baseline(order.pickup, order.drop, trip, approach, tp)
        marginal = self.cost.marginal_order_cost(self.cfg.flex.expected_detour_km, self.cfg.flex.expected_extra_minutes)
        return max(0.0, (solo - marginal) / 2.0)

    def allowed_wait_options(self, order: Order, travel: TravelOracle) -> list[int]:
        """Wait options that keep the food-quality limit with a safety margin."""
        fp = self.cfg.flex
        age_max = order.planning_age_limit(self.cfg.quality)
        # the whole approved window must be usable without breaking the food-age limit
        limit = order.predicted_ready_at + age_max - fp.quality_margin_min
        return [m for m in fp.wait_options_min if order.promised_at + m <= limit]

    def quote(
        self,
        order: Order,
        customer: CustomerContext,
        density: DensityContext,
        travel: TravelOracle,
        hour: float,
    ) -> list[FlexOffer]:
        fp = self.cfg.flex
        options = self.allowed_wait_options(order, travel)
        if not options:
            return []
        s_bar = self.attributable_savings(order, travel)
        if s_bar <= 0:
            return []
        is_food = order.category == Category.FOOD
        q = self.models.coordination.predict_proba(
            [
                CoordinationFeatures(
                    extra_min=float(m),
                    n_near_pickup=density.n_near_pickup,
                    n_near_drop=density.n_near_drop,
                    n_near_both=density.n_near_both,
                    hour=hour,
                    is_food=is_food,
                )
                for m in options
            ]
        )
        rewards = np.arange(fp.reward_min, fp.reward_max + 1e-9, fp.reward_step, dtype=float)
        rows = [
            AcceptanceFeatures(
                reward=float(r),
                extra_min=float(m),
                past_rate=customer.past_acceptance_rate,
                is_student=customer.is_student,
                basket_value=order.basket_value,
                hour=hour,
            )
            for m in options
            for r in rewards
        ]
        p_all = self.models.acceptance.predict_proba(rows).reshape(len(options), len(rewards))

        offers: list[FlexOffer] = []
        for k, m in enumerate(options):
            expected_savings = float(q[k]) * s_bar
            cap = fp.max_reward_share * expected_savings
            valid = (rewards <= cap + 1e-9) & (expected_savings - rewards >= fp.min_expected_margin - 1e-9)
            if not valid.any():
                continue
            value = np.where(valid, p_all[k] * (expected_savings - rewards), -np.inf)
            j = int(np.argmax(value))
            if value[j] <= 0:
                continue
            offers.append(
                FlexOffer(
                    extra_minutes=int(m),
                    reward=float(rewards[j]),
                    acceptance_probability=float(p_all[k, j]),
                    coordination_probability=float(q[k]),
                    expected_savings=round(expected_savings, 4),
                    expected_net_value=round(float(value[j]), 4),
                    flex_eta_at=order.promised_at + m,
                )
            )
        return offers
