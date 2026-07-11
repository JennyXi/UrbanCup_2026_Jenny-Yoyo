"""Deterministic assignment of existing Agents to exact T4 home-zone quotas.

The module only adds ``home_zone``. It does not generate agents, trip plans,
destinations, OD pairs, distances, modes, weather responses, subsidies,
prices, dispatch outcomes, or congestion.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

from custom.spatial.zone_configuration import AGE_GROUPS


VALID_ZONE_IDS = tuple(f"Z{index}" for index in range(1, 10))


def _read(agent: Any, field_name: str) -> Any:
    if isinstance(agent, dict):
        if field_name not in agent:
            raise ValueError(f"Agent missing required field: {field_name}")
        return agent[field_name]
    if not hasattr(agent, field_name):
        raise ValueError(f"Agent missing required field: {field_name}")
    return getattr(agent, field_name)


def _existing_home_zone(agent: Any) -> Any:
    if isinstance(agent, dict):
        return agent.get("home_zone")
    return getattr(agent, "home_zone", None)


def _set_home_zone(agent: Any, zone_id: str) -> None:
    if isinstance(agent, dict):
        agent["home_zone"] = zone_id
    else:
        setattr(agent, "home_zone", zone_id)


def _stable_identity(agent_id: Any) -> str:
    return f"{type(agent_id).__name__}:{agent_id!r}"


def _assignment_key(seed: Any, age_group: str, agent_id: Any) -> tuple:
    identity = _stable_identity(agent_id)
    payload = f"{seed!r}|{age_group}|{identity}|home-zone".encode("utf-8")
    return hashlib.sha256(payload).digest(), identity


def _validate_seed(seed: Any) -> None:
    if seed is None or isinstance(seed, (dict, list, set)):
        raise ValueError("seed must be a stable scalar value")


def _validate_quotas(zone_age_quotas: Mapping[str, Mapping[str, int]]) -> Dict[str, Dict[str, int]]:
    if not isinstance(zone_age_quotas, Mapping):
        raise ValueError("zone_age_quotas must be a mapping")
    if set(zone_age_quotas) != set(VALID_ZONE_IDS):
        raise ValueError(f"zone_age_quotas must contain exactly {VALID_ZONE_IDS}")

    normalized = {}
    for zone_id in VALID_ZONE_IDS:
        row = zone_age_quotas[zone_id]
        if not isinstance(row, Mapping) or set(row) != set(AGE_GROUPS):
            raise ValueError(f"Quota row for {zone_id} must contain exactly {AGE_GROUPS}")
        normalized[zone_id] = {}
        for age_group in AGE_GROUPS:
            value = row[age_group]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"Quota for {zone_id} x {age_group} must be a non-negative integer"
                )
            normalized[zone_id][age_group] = value
    return normalized


def assign_home_zones(
    agents: Iterable[Any],
    zone_age_quotas: Mapping[str, Mapping[str, int]],
    seed: Any,
) -> List[Any]:
    """Return deep-copied Agents with exact, deterministic ``home_zone`` values.

    Assignment is performed by age group against the supplied T4 quota matrix.
    Within each age group, Agents are ordered using a stable hash of ``seed``
    and ``agent_id``. Consequently input ordering cannot alter the individual
    ``agent_id -> home_zone`` mapping.
    """
    _validate_seed(seed)
    quotas = _validate_quotas(zone_age_quotas)
    agent_list = list(agents)

    agent_ids = []
    agents_by_age = defaultdict(list)
    for agent in agent_list:
        agent_id = _read(agent, "agent_id")
        age_group = _read(agent, "age_group")
        if agent_id is None:
            raise ValueError("agent_id must not be None")
        if age_group not in AGE_GROUPS:
            raise ValueError(f"Unsupported age_group: {age_group}")
        if _existing_home_zone(agent) is not None:
            raise ValueError(f"Agent {agent_id} already has home_zone")
        agent_ids.append(agent_id)
        agents_by_age[age_group].append(agent)

    identities = [_stable_identity(agent_id) for agent_id in agent_ids]
    if len(identities) != len(set(identities)):
        raise ValueError("agent_id values must be unique")

    quota_age_totals = {
        age_group: sum(quotas[zone_id][age_group] for zone_id in VALID_ZONE_IDS)
        for age_group in AGE_GROUPS
    }
    actual_age_totals = Counter(_read(agent, "age_group") for agent in agent_list)
    if len(agent_list) != sum(quota_age_totals.values()):
        raise ValueError(
            f"Agent total {len(agent_list)} does not match quota total "
            f"{sum(quota_age_totals.values())}"
        )
    for age_group in AGE_GROUPS:
        if actual_age_totals[age_group] != quota_age_totals[age_group]:
            raise ValueError(
                f"Agent count for {age_group} ({actual_age_totals[age_group]}) "
                f"does not match quota total ({quota_age_totals[age_group]})"
            )

    assignment_by_identity = {}
    for age_group in AGE_GROUPS:
        ordered = sorted(
            agents_by_age[age_group],
            key=lambda agent: _assignment_key(seed, age_group, _read(agent, "agent_id")),
        )
        position = 0
        for zone_id in VALID_ZONE_IDS:
            count = quotas[zone_id][age_group]
            for agent in ordered[position : position + count]:
                assignment_by_identity[_stable_identity(_read(agent, "agent_id"))] = zone_id
            position += count
        if position != len(ordered):
            raise AssertionError(f"Assignment did not consume all {age_group} Agents")

    assigned = []
    for agent in agent_list:
        copied = deepcopy(agent)
        identity = _stable_identity(_read(agent, "agent_id"))
        _set_home_zone(copied, assignment_by_identity[identity])
        assigned.append(copied)
    return assigned

