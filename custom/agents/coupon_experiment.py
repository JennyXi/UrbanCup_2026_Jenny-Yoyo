"""Finite daily coupon allocation for the coupon competition experiment."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Iterable, Mapping

from custom.agents.agent_population import AgentProfile
from custom.agents.public_goods_coupon import (
    PUBLIC_GOODS_POLICY,
    allocate_public_goods_coupons,
    validate_public_goods_coupon_config,
)


COUPON_POLICIES = (
    "C0_no_coupon",
    "C1_public_limited",
    "C2_elder_limited",
    "C3_mixed",
)
COUPON_POLICIES_WITH_PUBLIC_GOODS = (
    *COUPON_POLICIES,
    PUBLIC_GOODS_POLICY,
)

PUBLIC_GOODS_AUDIT_DEFAULTS = {
    "pg_official_parent_agent_class": "",
    "pg_adapter_agent_class": "",
    "pg_num_rounds": 0,
    "pg_initial_endowment": 0,
    "pg_public_pool_multiplier": 0.0,
    "pg_round_contributions": "",
    "pg_final_contribution": 0,
    "pg_total_contribution": 0,
    "pg_cumulative_payoff": 0.0,
    "pg_need_score": 0.0,
    "pg_cooperation_score": 0.0,
    "pg_priority_score": 0.0,
    "pg_peer_feedback_source_count": 0,
    "pg_peer_signal_round_2": None,
    "pg_peer_signal_round_3": None,
    "pg_linked_decision": False,
    "pg_physical_coupon_pool": 0,
    "pg_coupons_created_by_multiplier": 0,
    "pg_allocation_reason": "not_applicable",
}


def _coupon_uniform(seed: int, agent_id: int, day_type: str, stage: str) -> float:
    """Common random number: deliberately independent of policy and weather."""
    payload = f"{seed}|{agent_id}|{day_type}|coupon|{stage}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value / 2**64


def _access_channel(profile: AgentProfile, *, community_covered: bool = False) -> str:
    if profile.digital_access:
        return "digital_self"
    if profile.family_assistance:
        return "family_proxy"
    if community_covered:
        return "community_phone"
    return "unreachable"


def validate_coupon_config(config: Mapping[str, Any]) -> None:
    coupon = config["coupon_experiment"]
    policies = tuple(coupon["policies"])
    if policies not in {COUPON_POLICIES, COUPON_POLICIES_WITH_PUBLIC_GOODS}:
        raise ValueError("unexpected coupon policy order")
    multiplier = float(coupon["discount_multiplier"])
    if not 0.0 < multiplier < 1.0:
        raise ValueError("coupon discount multiplier must be in (0, 1)")
    pool = coupon["daily_total_coupon_pool"]
    if not isinstance(pool, int) or isinstance(pool, bool) or pool <= 0:
        raise ValueError("daily coupon pool must be a positive integer")
    for value in coupon["public_participation_probability"].values():
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("coupon participation probabilities must be in [0, 1]")
    for key in ("mixed_public_pool_share", "community_phone_coverage_rate"):
        value = float(coupon[key])
        if not 0.0 <= value < 1.0:
            raise ValueError(f"{key} must be in [0, 1)")
    if int(coupon["maximum_coupons_per_agent_day"]) != 1:
        raise ValueError("coupon experiment permits exactly one coupon per agent-day")
    if int(coupon["maximum_redemptions_per_agent_day"]) != 1:
        raise ValueError("coupon experiment permits exactly one redemption per agent-day")
    if float(coupon["main_experiment_ride_hailing_noncapacity_success_probability"]) != 1.0:
        raise ValueError("main coupon experiment must disable ride-hailing non-capacity failures")
    if PUBLIC_GOODS_POLICY in policies:
        validate_public_goods_coupon_config(config)


def allocate_daily_coupons(
    profiles: Iterable[AgentProfile], policy: str, day_type: str, *, seed: int,
    config: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    """Allocate a finite pool once per representative day, before weather runs."""
    validate_coupon_config(config)
    if policy not in COUPON_POLICIES_WITH_PUBLIC_GOODS:
        raise ValueError(f"unknown coupon policy: {policy}")
    if policy == PUBLIC_GOODS_POLICY:
        return allocate_public_goods_coupons(
            profiles, day_type, seed=seed, config=config,
        )
    coupon = config["coupon_experiment"]
    profiles = sorted(profiles, key=lambda row: row.agent_id)
    total_pool = int(coupon["daily_total_coupon_pool"])
    public_pool = 0
    elder_pool = 0
    if policy == "C1_public_limited":
        public_pool = total_pool
    elif policy == "C2_elder_limited":
        elder_pool = total_pool
    elif policy == "C3_mixed":
        public_pool = int(math.floor(total_pool * float(coupon["mixed_public_pool_share"])))
        elder_pool = total_pool - public_pool

    rows: Dict[int, Dict[str, Any]] = {}
    for profile in profiles:
        elder_public_reachable = bool(profile.digital_access or profile.family_assistance)
        public_reached = not profile.is_elder or elder_public_reachable
        public_probability = float(coupon["public_participation_probability"][profile.age_group])
        public_draw = _coupon_uniform(seed, profile.agent_id, day_type, "public-participation")
        public_participated = public_reached and public_draw < public_probability
        community_draw = _coupon_uniform(seed, profile.agent_id, day_type, "community-phone-coverage")
        community_covered = bool(
            profile.is_elder and not profile.digital_access and not profile.family_assistance
            and community_draw < float(coupon["community_phone_coverage_rate"])
        )
        reserve_reached = bool(
            profile.is_elder
            and (profile.digital_access or profile.family_assistance or (policy == "C3_mixed" and community_covered))
        )
        rows[profile.agent_id] = {
            "agent_id": profile.agent_id, "day_type": day_type,
            "age_group": profile.age_group,
            "digital_access": bool(profile.digital_access),
            "family_assistance": bool(profile.family_assistance),
            "nondigital_unassisted": bool(
                profile.is_elder and not profile.digital_access and not profile.family_assistance
            ),
            "coupon_policy": policy,
            "coupon_eligible": False, "coupon_reached": False,
            "coupon_participated": False, "coupon_awarded": False,
            "public_coupon_participated": False,
            "elder_reserve_reached": reserve_reached,
            "coupon_pool_type": "", "coupon_allocation_rank": None,
            "coupon_access_channel": "none",
            "public_participation_probability": public_probability,
            "public_participation_draw": public_draw,
            "public_dispatch_rank": _coupon_uniform(seed, profile.agent_id, day_type, "public-allocation-rank"),
            "elder_reserve_rank": _coupon_uniform(seed, profile.agent_id, day_type, "elder-reserve-rank"),
            "community_phone_covered": community_covered,
            "community_phone_coverage_draw": community_draw,
            **PUBLIC_GOODS_AUDIT_DEFAULTS,
        }

    if policy in {"C1_public_limited", "C3_mixed"}:
        candidates = [
            profile for profile in profiles
            if rows[profile.agent_id]["coupon_participated"] is False
            and (not profile.is_elder or profile.digital_access or profile.family_assistance)
            and rows[profile.agent_id]["public_participation_draw"]
            < rows[profile.agent_id]["public_participation_probability"]
        ]
        candidates.sort(key=lambda profile: (
            rows[profile.agent_id]["public_dispatch_rank"], profile.agent_id,
        ))
        for rank, profile in enumerate(candidates, start=1):
            row = rows[profile.agent_id]
            row.update({
                "coupon_eligible": True, "coupon_reached": True,
                "coupon_participated": True,
                "public_coupon_participated": True,
                "coupon_access_channel": _access_channel(profile),
            })
            if rank <= public_pool:
                row.update({
                    "coupon_awarded": True, "coupon_pool_type": "public",
                    "coupon_allocation_rank": rank,
                })
        # Reached non-participants must remain visible in the outreach denominator.
        for profile in profiles:
            if not profile.is_elder or profile.digital_access or profile.family_assistance:
                row = rows[profile.agent_id]
                row["coupon_eligible"] = True
                row["coupon_reached"] = True
                row["coupon_access_channel"] = _access_channel(profile)

    if policy in {"C2_elder_limited", "C3_mixed"}:
        candidates = [
            profile for profile in profiles
            if profile.is_elder and not rows[profile.agent_id]["coupon_awarded"]
            and (
                profile.digital_access or profile.family_assistance
                or (policy == "C3_mixed" and rows[profile.agent_id]["community_phone_covered"])
            )
        ]
        candidates.sort(key=lambda profile: (
            rows[profile.agent_id]["elder_reserve_rank"], profile.agent_id,
        ))
        for rank, profile in enumerate(candidates, start=1):
            row = rows[profile.agent_id]
            row.update({
                "coupon_eligible": True, "coupon_reached": True,
                "elder_reserve_reached": True,
                "coupon_access_channel": _access_channel(
                    profile, community_covered=bool(row["community_phone_covered"]),
                ),
            })
            if rank <= elder_pool:
                row.update({
                    "coupon_awarded": True, "coupon_pool_type": "elder_reserved",
                    "coupon_allocation_rank": rank,
                })

    return [rows[profile.agent_id] for profile in profiles]


def allocation_map(rows: Iterable[Mapping[str, Any]]) -> Dict[tuple[int, str], Dict[str, Any]]:
    return {
        (int(row["agent_id"]), str(row["day_type"])): dict(row)
        for row in rows
    }


def community_assisted_booking(allocation: Mapping[str, Any] | None) -> bool:
    """Whether a C3/C4 award includes one community proxy booking."""
    return bool(
        allocation
        and allocation.get("coupon_policy") in {"C3_mixed", PUBLIC_GOODS_POLICY}
        and allocation.get("coupon_awarded")
        and allocation.get("coupon_pool_type") in {"elder_reserved", "public_goods"}
        and allocation.get("coupon_access_channel") == "community_phone"
        and allocation.get("nondigital_unassisted")
    )
