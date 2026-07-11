"""T3 policy rules for mobility subsidies and elder dispatch eligibility.

This module intentionally stops at policy eligibility. It does not choose a
travel mode, create orders, determine dispatch success, calculate fares, or
update weekly discount-use counts.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Optional


POLICY_SCENARIOS = ("P0", "P1", "P2", "P3", "P4")
DISCOUNT_LEVELS = ("low", "high")
WEEKLY_DISCOUNT_LIMIT = 3

OUTPUT_FIELDS = (
    "policy_scenario",
    "discount_level",
    "discount_amount",
    "coupon_eligible",
    "coupon_seen",
    "coupon_claimed",
    "access_channel",
    "price_discount_eligible",
    "dispatch_priority_eligible",
)


def _read(source: Any, field_name: str) -> Any:
    if isinstance(source, dict):
        if field_name not in source:
            raise ValueError(f"Missing required field: {field_name}")
        return source[field_name]
    if not hasattr(source, field_name):
        raise ValueError(f"Missing required field: {field_name}")
    return getattr(source, field_name)


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be bool")
    return value


def _require_probability(value: Any, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{field_name} must be a finite number in [0, 1]")
    return float(value)


def _require_nonnegative_number(value: Any, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return float(value)


def _require_weekly_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("weekly_discount_use_count must be a non-negative integer")
    return value


def _discount_amount_for_level(
    discount_level: Optional[str],
    discount_amount_low: Any,
    discount_amount_high: Any,
) -> float:
    if discount_level not in DISCOUNT_LEVELS:
        raise ValueError("discount_level must be 'low' or 'high' for P1, P2, and P3")
    if discount_level == "low":
        return _require_nonnegative_number(discount_amount_low, "discount_amount_low")
    return _require_nonnegative_number(discount_amount_high, "discount_amount_high")


def _deterministic_uniform(
    random_seed: Any,
    agent_id: Any,
    leg_id: Any,
    policy_scenario: str,
    stage: str,
) -> float:
    if random_seed is None or isinstance(random_seed, (dict, list, set)):
        raise ValueError("random_seed must be a stable scalar value")
    if agent_id is None:
        raise ValueError("agent_id must not be None")
    if leg_id is None:
        raise ValueError("leg_id must not be None")

    key = "|".join(
        (str(random_seed), str(agent_id), str(leg_id), policy_scenario, stage)
    ).encode("utf-8")
    digest = hashlib.sha256(key).digest()
    integer = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return integer / float(1 << 64)


def _base_output(
    policy_scenario: str,
    discount_level: Optional[str],
    discount_amount: float,
) -> Dict[str, Any]:
    return {
        "policy_scenario": policy_scenario,
        "discount_level": discount_level,
        "discount_amount": discount_amount,
        "coupon_eligible": False,
        "coupon_seen": None,
        "coupon_claimed": None,
        "access_channel": None,
        "price_discount_eligible": False,
        "dispatch_priority_eligible": False,
    }


def evaluate_t3_policy(
    agent: Any,
    leg: Any,
    *,
    policy_scenario: str,
    discount_level: Optional[str] = None,
    discount_amount_low: Any = None,
    discount_amount_high: Any = None,
    weekly_discount_use_count: Any = None,
    random_seed: Any = None,
) -> Optional[Dict[str, Any]]:
    """Evaluate the agreed T3 policy rules for one potential leg.

    Returns ``None`` when ``trip_continues`` is false. Otherwise returns a new
    dictionary containing exactly the fields listed in ``OUTPUT_FIELDS``.
    Inputs are not mutated.
    """
    trip_continues = _require_bool(_read(leg, "trip_continues"), "trip_continues")
    if not trip_continues:
        return None

    if policy_scenario not in POLICY_SCENARIOS:
        raise ValueError(f"Unknown policy_scenario: {policy_scenario}")

    if policy_scenario in ("P0", "P4"):
        if discount_level is not None:
            raise ValueError(f"discount_level must be None for {policy_scenario}")
        output = _base_output(policy_scenario, None, 0.0)
        if policy_scenario == "P4":
            output["dispatch_priority_eligible"] = _require_bool(
                _read(agent, "is_elder"), "is_elder"
            )
        return output

    discount_amount = _discount_amount_for_level(
        discount_level, discount_amount_low, discount_amount_high
    )
    weekly_count = _require_weekly_count(weekly_discount_use_count)
    under_weekly_limit = weekly_count < WEEKLY_DISCOUNT_LIMIT
    output = _base_output(policy_scenario, discount_level, discount_amount)

    if policy_scenario == "P1":
        digital_access = _require_bool(
            _read(agent, "digital_access"), "digital_access"
        )
        if not digital_access:
            return output

        output["coupon_eligible"] = True
        awareness_probability = _require_probability(
            _read(agent, "coupon_awareness_probability"),
            "coupon_awareness_probability",
        )
        claim_probability = _require_probability(
            _read(agent, "coupon_claim_probability"),
            "coupon_claim_probability",
        )
        independent = _require_bool(
            _read(agent, "independent_ride_hailing"),
            "independent_ride_hailing",
        )
        agent_id = _read(agent, "agent_id")
        leg_id = _read(leg, "leg_id")

        seen = _deterministic_uniform(
            random_seed, agent_id, leg_id, policy_scenario, "seen"
        ) < awareness_probability
        output["coupon_seen"] = seen
        if seen:
            claimed = _deterministic_uniform(
                random_seed, agent_id, leg_id, policy_scenario, "claimed"
            ) < claim_probability
            output["coupon_claimed"] = claimed
        else:
            claimed = False

        if independent:
            output["access_channel"] = "online_self"
        output["price_discount_eligible"] = bool(
            seen and claimed and independent and under_weekly_limit
        )
        return output

    if policy_scenario == "P2":
        digital_access = _require_bool(
            _read(agent, "digital_access"), "digital_access"
        )
        output["coupon_eligible"] = digital_access
        if not digital_access:
            return output

        independent = _require_bool(
            _read(agent, "independent_ride_hailing"),
            "independent_ride_hailing",
        )
        if independent:
            output["access_channel"] = "online_self"
        output["price_discount_eligible"] = bool(
            independent and under_weekly_limit
        )
        return output

    output["coupon_eligible"] = True
    output["access_channel"] = "multichannel"
    output["price_discount_eligible"] = under_weekly_limit
    return output

