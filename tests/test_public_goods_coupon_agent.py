from __future__ import annotations

import copy
import json
from pathlib import Path

from custom.agents.agent_population import AgentProfile, generate_population_agents
from custom.agents.coupon_experiment import (
    COUPON_POLICIES_WITH_PUBLIC_GOODS,
    allocate_daily_coupons,
    community_assisted_booking,
)
from custom.agents.public_goods_coupon import (
    COUPON_PUBLIC_GOODS_ADAPTER,
    OFFICIAL_PUBLIC_GOODS_AGENT,
    PUBLIC_GOODS_POLICY,
    allocate_public_goods_coupons,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "formal_nine_zone_50_coupon_experiment.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))


def _young_profile(agent_id: int) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        age_group="18-39",
        age_range=(18, 39),
        is_elder=False,
        digital_access=True,
        family_assistance=None,
        segment="18-39",
        smartphone_access=True,
        work_status="regular_worker",
    )


def test_c4_names_the_official_agentsociety_parent_and_adapter():
    config = _config()
    game = config["coupon_experiment"]["public_goods_game"]
    assert COUPON_POLICIES_WITH_PUBLIC_GOODS[-1] == PUBLIC_GOODS_POLICY
    assert game["official_parent_agent_class"] == OFFICIAL_PUBLIC_GOODS_AGENT
    assert game["adapter_agent_class"] == COUPON_PUBLIC_GOODS_ADAPTER


def test_public_goods_allocation_is_reproducible_and_coupon_conserving():
    config = _config()
    profiles = generate_population_agents(50, seed=47)
    first = allocate_daily_coupons(
        profiles, PUBLIC_GOODS_POLICY, "workday", seed=47, config=config
    )
    second = allocate_daily_coupons(
        profiles, PUBLIC_GOODS_POLICY, "workday", seed=47, config=config
    )
    assert first == second
    participant_count = sum(row["coupon_participated"] for row in first)
    assert sum(row["coupon_awarded"] for row in first) == min(
        config["coupon_experiment"]["daily_total_coupon_pool"], participant_count
    )
    assert all(row["pg_coupons_created_by_multiplier"] == 0 for row in first)
    assert all(
        row["pg_physical_coupon_pool"]
        == config["coupon_experiment"]["daily_total_coupon_pool"]
        for row in first
    )


def test_c4_reuses_c1_participation_draws_for_paired_comparison():
    config = _config()
    profiles = generate_population_agents(50, seed=47)
    c1 = allocate_daily_coupons(
        profiles, "C1_public_limited", "workday", seed=47, config=config
    )
    c4 = allocate_daily_coupons(
        profiles, PUBLIC_GOODS_POLICY, "workday", seed=47, config=config
    )
    c4_by_id = {row["agent_id"]: row for row in c4}
    assert all(
        row["public_participation_draw"]
        == c4_by_id[row["agent_id"]]["public_participation_draw"]
        for row in c1
    )


def test_one_agents_contribution_changes_other_agents_later_decisions():
    config = _config()
    config["coupon_experiment"]["public_participation_probability"] = {
        key: 1.0
        for key in config["coupon_experiment"]["public_participation_probability"]
    }
    profiles = [_young_profile(agent_id) for agent_id in range(1, 4)]
    low_a = allocate_public_goods_coupons(
        profiles,
        "workday",
        seed=47,
        config=config,
        cooperation_overrides={1: 0.0},
    )
    high_a = allocate_public_goods_coupons(
        profiles,
        "workday",
        seed=47,
        config=config,
        cooperation_overrides={1: 1.0},
    )
    low_b = next(row for row in low_a if row["agent_id"] == 2)
    high_b = next(row for row in high_a if row["agent_id"] == 2)
    assert low_b["pg_round_contributions"].split("|")[0] == high_b[
        "pg_round_contributions"
    ].split("|")[0]
    assert low_b["pg_peer_signal_round_2"] != high_b["pg_peer_signal_round_2"]
    assert low_b["pg_round_contributions"] != high_b["pg_round_contributions"]
    assert low_b["pg_linked_decision"] is True


def test_public_goods_priority_combines_need_and_cooperation():
    config = copy.deepcopy(_config())
    config["coupon_experiment"]["daily_total_coupon_pool"] = 1
    config["coupon_experiment"]["public_participation_probability"] = {
        key: 1.0
        for key in config["coupon_experiment"]["public_participation_probability"]
    }
    young = _young_profile(1)
    elder = AgentProfile(
        agent_id=2,
        age_group="60+",
        age_range=(60, 99),
        is_elder=True,
        digital_access=True,
        family_assistance=False,
        segment="60+",
        smartphone_access=True,
        work_status="retired",
        medical_need_level="high",
    )
    rows = allocate_public_goods_coupons(
        [young, elder],
        "workday",
        seed=47,
        config=config,
        cooperation_overrides={1: 1.0, 2: 0.0},
    )
    awarded = next(row for row in rows if row["coupon_awarded"])
    assert awarded["agent_id"] == elder.agent_id
    assert awarded["pg_need_score"] == 0.75
    assert awarded["pg_allocation_reason"] == "need_and_cooperation_priority"


def test_c4_community_phone_award_enables_only_one_coupon_proxy_channel():
    allocation = {
        "coupon_policy": PUBLIC_GOODS_POLICY,
        "coupon_awarded": True,
        "coupon_pool_type": "public_goods",
        "coupon_access_channel": "community_phone",
        "nondigital_unassisted": True,
    }
    assert community_assisted_booking(allocation)
    assert not community_assisted_booking({**allocation, "coupon_awarded": False})
