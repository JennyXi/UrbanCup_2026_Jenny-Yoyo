"""Public-goods-game mechanism for allocating a finite daily coupon pool.

The game uses virtual contribution tokens to expose cooperation and peer
feedback.  Tokens and their multiplied public return are accounting signals;
they never create additional coupons.  Coupon awards remain bounded by the
configured physical daily pool.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Iterable, Mapping

from custom.agents.agent_population import AgentProfile


PUBLIC_GOODS_POLICY = "C4_public_goods"
OFFICIAL_PUBLIC_GOODS_AGENT = "agentsociety2.contrib.agent.PublicGoodsAgent"
COUPON_PUBLIC_GOODS_ADAPTER = (
    "integrations.agentsociety.coupon_public_goods_agent.CouponPublicGoodsAgent"
)


def _stable_uniform(seed: int, agent_id: int, day_type: str, stage: str) -> float:
    payload = f"{seed}|{agent_id}|{day_type}|public-goods-coupon|{stage}".encode(
        "utf-8"
    )
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value / 2**64


def _paired_coupon_uniform(
    seed: int, agent_id: int, day_type: str, stage: str
) -> float:
    """Reuse the C1-C3 draw key for paired-policy comparability."""

    payload = f"{seed}|{agent_id}|{day_type}|coupon|{stage}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value / 2**64


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, value))


def _access_channel(
    profile: AgentProfile, *, community_covered: bool = False
) -> str:
    if profile.digital_access:
        return "digital_self"
    if profile.family_assistance:
        return "family_proxy"
    if community_covered:
        return "community_phone"
    return "unreachable"


def validate_public_goods_coupon_config(config: Mapping[str, Any]) -> None:
    coupon = config["coupon_experiment"]
    game = coupon.get("public_goods_game")
    if not isinstance(game, Mapping):
        raise ValueError("coupon_experiment.public_goods_game is required")
    if game.get("official_parent_agent_class") != OFFICIAL_PUBLIC_GOODS_AGENT:
        raise ValueError("public-goods allocation must name the official PublicGoodsAgent")
    if game.get("adapter_agent_class") != COUPON_PUBLIC_GOODS_ADAPTER:
        raise ValueError("unexpected coupon PublicGoodsAgent adapter class")

    for key in ("num_rounds", "initial_endowment"):
        value = game.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"public_goods_game.{key} must be a positive integer")
    if int(game["num_rounds"]) < 2:
        raise ValueError("public goods coupon game needs at least two linked rounds")
    if float(game.get("public_pool_multiplier", 0.0)) <= 1.0:
        raise ValueError("public_pool_multiplier must exceed one")

    for key in (
        "peer_reciprocity_weight",
        "initial_cooperation_noise",
        "need_priority_weight",
        "cooperation_priority_weight",
    ):
        value = float(game.get(key, -1.0))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"public_goods_game.{key} must be in [0, 1]")
    if not math.isclose(
        float(game["need_priority_weight"])
        + float(game["cooperation_priority_weight"]),
        1.0,
        abs_tol=1e-12,
    ):
        raise ValueError("public-goods allocation priority weights must sum to one")

    age_groups = {"18-39", "40-59", "60+"}
    for key in ("base_cooperation_by_age", "age_need_score"):
        values = game.get(key)
        if not isinstance(values, Mapping) or set(values) != age_groups:
            raise ValueError(f"public_goods_game.{key} must cover all age groups")
        if any(not 0.0 <= float(value) <= 1.0 for value in values.values()):
            raise ValueError(f"public_goods_game.{key} values must be in [0, 1]")

    medical = game.get("medical_need_score")
    if not isinstance(medical, Mapping) or set(medical) != {
        "none",
        "low",
        "standard",
        "high",
    }:
        raise ValueError("medical_need_score must cover none/low/standard/high")
    if any(not 0.0 <= float(value) <= 1.0 for value in medical.values()):
        raise ValueError("medical_need_score values must be in [0, 1]")
    if not 0.0 <= float(game.get("nondigital_unassisted_need_score", -1.0)) <= 1.0:
        raise ValueError("nondigital_unassisted_need_score must be in [0, 1]")


def _need_score(profile: AgentProfile, game: Mapping[str, Any]) -> float:
    medical_level = profile.medical_need_level or "none"
    components = (
        float(game["age_need_score"][profile.age_group]),
        float(game["medical_need_score"][medical_level]),
        float(game["nondigital_unassisted_need_score"])
        if profile.is_elder
        and not profile.digital_access
        and not profile.family_assistance
        else 0.0,
    )
    # Use the strongest modeled accessibility need rather than double-counting
    # correlated age, medical-need and digital-exclusion attributes.
    return max(components)


def allocate_public_goods_coupons(
    profiles: Iterable[AgentProfile],
    day_type: str,
    *,
    seed: int,
    config: Mapping[str, Any],
    cooperation_overrides: Mapping[int, float] | None = None,
) -> list[Dict[str, Any]]:
    """Run linked contribution rounds and allocate the unchanged coupon pool.

    ``cooperation_overrides`` is an auditable experiment hook.  It changes only
    the selected resident's round-one cooperation propensity; subsequent
    contributions by every participant still respond to the shared prior-round
    signal, making cross-Agent influence directly testable.
    """

    validate_public_goods_coupon_config(config)
    coupon = config["coupon_experiment"]
    game = coupon["public_goods_game"]
    profiles = sorted(profiles, key=lambda row: row.agent_id)
    total_coupon_pool = int(coupon["daily_total_coupon_pool"])
    endowment = int(game["initial_endowment"])
    rounds = int(game["num_rounds"])
    multiplier = float(game["public_pool_multiplier"])
    reciprocity = float(game["peer_reciprocity_weight"])
    initial_noise = float(game["initial_cooperation_noise"])

    state: Dict[int, Dict[str, Any]] = {}
    participants: list[AgentProfile] = []
    for profile in profiles:
        nondigital_unassisted = bool(
            profile.is_elder
            and not profile.digital_access
            and not profile.family_assistance
        )
        community_draw = _paired_coupon_uniform(
            seed, profile.agent_id, day_type, "community-phone-coverage"
        )
        community_covered = bool(
            nondigital_unassisted
            and community_draw < float(coupon["community_phone_coverage_rate"])
        )
        reached = bool(
            not profile.is_elder
            or profile.digital_access
            or profile.family_assistance
            or community_covered
        )
        participation_probability = float(
            coupon["public_participation_probability"][profile.age_group]
        )
        participation_draw = _paired_coupon_uniform(
            seed, profile.agent_id, day_type, "public-participation"
        )
        participated = bool(
            reached and participation_draw < participation_probability
        )
        base = float(game["base_cooperation_by_age"][profile.age_group])
        jitter = (
            2.0
            * _stable_uniform(seed, profile.agent_id, day_type, "cooperation-noise")
            - 1.0
        ) * initial_noise
        individual_propensity = _clamp_probability(base + jitter)
        if cooperation_overrides and profile.agent_id in cooperation_overrides:
            override = cooperation_overrides[profile.agent_id]
            if isinstance(override, bool) or not 0.0 <= float(override) <= 1.0:
                raise ValueError("cooperation overrides must be numbers in [0, 1]")
            individual_propensity = float(override)
        state[profile.agent_id] = {
            "profile": profile,
            "nondigital_unassisted": nondigital_unassisted,
            "community_phone_covered": community_covered,
            "community_phone_coverage_draw": community_draw,
            "reached": reached,
            "participation_probability": participation_probability,
            "participation_draw": participation_draw,
            "participated": participated,
            "individual_propensity": individual_propensity,
            "contributions": [],
            "payoffs": [],
            "peer_signals": [],
        }
        if participated:
            participants.append(profile)

    participant_count = len(participants)
    prior_mean_fraction = 0.0
    prior_public_return_fraction = 0.0
    for round_index in range(rounds):
        for profile in participants:
            row = state[profile.agent_id]
            if round_index == 0:
                contribution_fraction = row["individual_propensity"]
                peer_signal = None
            else:
                # Every participant observes the prior aggregate.  Therefore a
                # contribution by A changes this signal before B's next choice.
                peer_signal = 0.5 * (
                    prior_mean_fraction + prior_public_return_fraction
                )
                contribution_fraction = (
                    (1.0 - reciprocity) * row["individual_propensity"]
                    + reciprocity * peer_signal
                )
            contribution = int(round(endowment * _clamp_probability(contribution_fraction)))
            row["contributions"].append(contribution)
            row["peer_signals"].append(peer_signal)

        round_total = sum(
            state[profile.agent_id]["contributions"][-1]
            for profile in participants
        )
        shared_return = (
            round_total * multiplier / participant_count if participant_count else 0.0
        )
        for profile in participants:
            row = state[profile.agent_id]
            row["payoffs"].append(
                endowment - row["contributions"][-1] + shared_return
            )
        prior_mean_fraction = (
            round_total / (participant_count * endowment)
            if participant_count
            else 0.0
        )
        prior_public_return_fraction = _clamp_probability(shared_return / endowment)

    ranked: list[tuple[float, float, int]] = []
    for profile in participants:
        row = state[profile.agent_id]
        cooperation_score = sum(row["contributions"]) / (rounds * endowment)
        need_score = _need_score(profile, game)
        priority_score = (
            float(game["need_priority_weight"]) * need_score
            + float(game["cooperation_priority_weight"]) * cooperation_score
        )
        tiebreak = _paired_coupon_uniform(
            seed, profile.agent_id, day_type, "public-allocation-rank"
        )
        row.update(
            cooperation_score=cooperation_score,
            need_score=need_score,
            priority_score=priority_score,
            tiebreak=tiebreak,
        )
        ranked.append((-priority_score, tiebreak, profile.agent_id))
    ranked.sort()
    allocation_rank = {
        agent_id: rank for rank, (_, _, agent_id) in enumerate(ranked, start=1)
    }
    award_count = min(total_coupon_pool, participant_count)

    output: list[Dict[str, Any]] = []
    for profile in profiles:
        row = state[profile.agent_id]
        rank = allocation_rank.get(profile.agent_id)
        awarded = bool(rank is not None and rank <= award_count)
        contributions = row["contributions"]
        peer_signals = row["peer_signals"]
        output.append(
            {
                "agent_id": profile.agent_id,
                "day_type": day_type,
                "age_group": profile.age_group,
                "digital_access": bool(profile.digital_access),
                "family_assistance": bool(profile.family_assistance),
                "nondigital_unassisted": row["nondigital_unassisted"],
                "coupon_policy": PUBLIC_GOODS_POLICY,
                "coupon_eligible": row["reached"],
                "coupon_reached": row["reached"],
                "coupon_participated": row["participated"],
                "coupon_awarded": awarded,
                "public_coupon_participated": row["participated"],
                "elder_reserve_reached": False,
                "coupon_pool_type": "public_goods" if awarded else "",
                "coupon_allocation_rank": rank if awarded else None,
                "coupon_access_channel": _access_channel(
                    profile,
                    community_covered=bool(row["community_phone_covered"]),
                )
                if row["reached"]
                else "none",
                "public_participation_probability": row[
                    "participation_probability"
                ],
                "public_participation_draw": row["participation_draw"],
                "public_dispatch_rank": row.get("tiebreak"),
                "elder_reserve_rank": None,
                "community_phone_covered": row["community_phone_covered"],
                "community_phone_coverage_draw": row[
                    "community_phone_coverage_draw"
                ],
                "pg_official_parent_agent_class": OFFICIAL_PUBLIC_GOODS_AGENT,
                "pg_adapter_agent_class": COUPON_PUBLIC_GOODS_ADAPTER,
                "pg_num_rounds": rounds,
                "pg_initial_endowment": endowment,
                "pg_public_pool_multiplier": multiplier,
                "pg_round_contributions": "|".join(map(str, contributions)),
                "pg_final_contribution": contributions[-1] if contributions else 0,
                "pg_total_contribution": sum(contributions),
                "pg_cumulative_payoff": round(sum(row["payoffs"]), 6),
                "pg_need_score": round(float(row.get("need_score", 0.0)), 6),
                "pg_cooperation_score": round(
                    float(row.get("cooperation_score", 0.0)), 6
                ),
                "pg_priority_score": round(
                    float(row.get("priority_score", 0.0)), 6
                ),
                "pg_peer_feedback_source_count": (
                    participant_count - 1 if row["participated"] else 0
                ),
                "pg_peer_signal_round_2": round(float(peer_signals[1]), 6)
                if len(peer_signals) > 1 and peer_signals[1] is not None
                else None,
                "pg_peer_signal_round_3": round(float(peer_signals[2]), 6)
                if len(peer_signals) > 2 and peer_signals[2] is not None
                else None,
                "pg_linked_decision": bool(
                    row["participated"] and participant_count > 1 and rounds > 1
                ),
                "pg_physical_coupon_pool": total_coupon_pool,
                "pg_coupons_created_by_multiplier": 0,
                "pg_allocation_reason": (
                    "need_and_cooperation_priority"
                    if awarded
                    else "below_priority_cutoff"
                    if row["participated"]
                    else "did_not_participate"
                    if row["reached"]
                    else "not_reached"
                ),
            }
        )
    return output
