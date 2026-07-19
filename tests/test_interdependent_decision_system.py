from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom.agents.formal_nine_zone_experiment import load_formal_nine_zone_config
from custom.agents.interdependent_decision_system import (
    SharedTrafficStateRegistry,
    load_interdependent_decision_config,
    run_interdependent_decision_experiment,
    softmax_choice_probabilities,
)


def test_softmax_probabilities_are_normalized_and_utility_monotone():
    probabilities = softmax_choice_probabilities([
        {"mode": "bus", "systematic_utility": -2.0},
        {"mode": "metro", "systematic_utility": -1.0},
        {"mode": "ride_hailing", "systematic_utility": 0.5},
    ])
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities["ride_hailing"] > probabilities["metro"] > probabilities["bus"]


def test_a_ride_choice_is_visible_to_b_in_the_same_state_but_not_the_next_bin():
    config = load_interdependent_decision_config()["shared_traffic_state"]
    registry = SharedTrafficStateRegistry(config)
    departure = datetime(2026, 7, 7, 8, 5)
    base_flow = 100.0
    before = registry.snapshot(departure, base_flow)
    event = registry.publish_choice(
        agent_id="A", leg_id="A-leg", mode="ride_hailing",
        departure_time=departure, decision_sequence=1, base_flow=base_flow,
    )
    seen_by_b = registry.snapshot(departure + timedelta(minutes=10), base_flow)
    next_bin = registry.snapshot(departure + timedelta(minutes=30), base_flow)

    assert event is not None
    assert seen_by_b["state_key"] == before["state_key"]
    assert seen_by_b["total_flow_pcu_per_hour"] > before["total_flow_pcu_per_hour"]
    assert seen_by_b["sources"][0]["agent_id"] == "A"
    assert next_bin["endogenous_flow_pcu_per_hour"] == 0.0


def test_formal_agents_produce_auditable_probability_influence_edges():
    formal = load_formal_nine_zone_config()
    formal["total_agents"] = 20
    result = run_interdependent_decision_experiment(formal_config=formal, seed=47)
    summary = result["summary"]

    assert summary["traffic_event_count"] == summary["ride_hailing_choice_count"]
    assert summary["affected_decision_count"] > 0
    assert summary["maximum_absolute_probability_change"] > 0
    affected = [row for row in result["decisions"] if row["affected_by_prior_agents"]]
    assert all(row["prior_influencer_count"] > 0 for row in affected)
    assert all(
        any(abs(value) > 0 for value in row["probability_delta_from_prior_agents"].values())
        for row in affected
    )
    assert result["influence_edges"]
    assert all(
        edge["source_decision_sequence"] < edge["target_decision_sequence"]
        for edge in result["influence_edges"]
    )
    assert all(
        edge["mechanism"]
        == "ride_hailing_choice_to_shared_road_flow_to_mode_probability"
        for edge in result["influence_edges"]
    )
