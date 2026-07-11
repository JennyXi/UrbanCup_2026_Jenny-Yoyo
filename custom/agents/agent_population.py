from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple


FLEXIBLE_NON_WORKER_SHARES = {"18-39": 0.10, "40-59": 0.08}
PART_TIME_WORKER_SHARE = 0.17
MEDICAL_NEED_LEVEL_SHARES = {"low": 0.35, "standard": 0.55, "high": 0.10}


@dataclass
class AgentProfile:
    agent_id: int
    age_group: str
    age_range: Tuple[int, int]
    is_elder: bool
    digital_access: bool
    family_assistance: Optional[bool]
    segment: str
    coupon_awareness_probability: Optional[float] = None
    coupon_claim_probability: Optional[float] = None
    independent_ride_hailing: Optional[bool] = None
    home_zone: Optional[str] = None
    work_status: Optional[str] = None
    medical_need_level: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def validate_agent_profile(agent: AgentProfile) -> None:
    """Validate optional coupon-access attributes without requiring calibration."""
    for field_name in (
        "coupon_awareness_probability",
        "coupon_claim_probability",
    ):
        value = getattr(agent, field_name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError(f"{field_name} must be None or a number in [0, 1]")

    if (
        agent.independent_ride_hailing is not None
        and not isinstance(agent.independent_ride_hailing, bool)
    ):
        raise ValueError("independent_ride_hailing must be None or bool")

    if not agent.is_elder and agent.family_assistance is not None:
        raise ValueError("family_assistance must be None for non-elder agents")

    allowed_work_statuses = {
        "18-39": {"regular_worker", "flexible_non_worker"},
        "40-59": {"regular_worker", "flexible_non_worker"},
        "60+": {"retired", "part_time_worker"},
    }
    if agent.work_status is not None and agent.work_status not in allowed_work_statuses[agent.age_group]:
        raise ValueError(f"Invalid work_status for {agent.age_group}: {agent.work_status}")
    if agent.is_elder:
        if agent.medical_need_level is not None and agent.medical_need_level not in {"low", "standard", "high"}:
            raise ValueError("medical_need_level must be low, standard, or high for elder agents")
    elif agent.medical_need_level is not None:
        raise ValueError("medical_need_level must be None for non-elder agents")


def _stable_profile_rng(seed: Optional[int], agent_id: int) -> random.Random:
    key = f"{seed!r}|{agent_id}|T1-agent-profile".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    return random.Random(value)


def _split_counts(total: int, ratios: List[float]) -> List[int]:
    counts = [int(total * r) for r in ratios]
    remainder = total - sum(counts)
    fractional_order = sorted(
        range(len(ratios)),
        key=lambda idx: (-(total * ratios[idx] - counts[idx]), idx),
    )
    for idx in fractional_order[:remainder]:
        counts[idx] += 1
    return counts


def generate_population_agents(
    total_agents: int,
    seed: Optional[int] = None,
    elder_digital_access_rate: float = 0.70,
    elder_assistance_rate: float = 0.68,
) -> List[AgentProfile]:
    """
    生成仿真所需的成年代理人群。

    设计原则：
    - 仅包含独立决策主体：18-39、40-59、60+。
    - 60+ 老年人进一步区分数字接入与否、家庭协助与否。

    :param total_agents: 代理总数。
    :param seed: 随机种子，用于可重复生成。
    :param elder_digital_access_rate: 老年人中数字接入比例。
    :param elder_assistance_rate: 老年人中有家庭协助比例。
    """
    if seed is not None:
        random.seed(seed)

    age_groups = ["18-39", "40-59", "60+"]
    ratios = [0.40, 0.33, 0.27]
    counts = _split_counts(total_agents, ratios)

    agents: List[AgentProfile] = []
    agent_id = 1

    for age_group, count in zip(age_groups, counts):
        group_agent_ids = list(range(agent_id, agent_id + count))
        status_rate = PART_TIME_WORKER_SHARE if age_group == "60+" else FLEXIBLE_NON_WORKER_SHARES[age_group]
        minority_status_count = int(count * status_rate + 0.5)
        ranked_for_status = sorted(
            group_agent_ids,
            key=lambda current_id: _stable_profile_rng(seed, current_id).random(),
        )
        minority_status_ids = set(ranked_for_status[:minority_status_count])
        for _ in range(count):
            is_elder = age_group == "60+"
            profile_rng = _stable_profile_rng(seed, agent_id)
            if is_elder:
                digital_access = random.random() < elder_digital_access_rate
                family_assistance = random.random() < elder_assistance_rate
                work_status = "part_time_worker" if agent_id in minority_status_ids else "retired"
                medical_need_level = profile_rng.choices(
                    tuple(MEDICAL_NEED_LEVEL_SHARES),
                    weights=tuple(MEDICAL_NEED_LEVEL_SHARES.values()),
                    k=1,
                )[0]
                independent_ride_hailing = None
            else:
                digital_access = True
                family_assistance = None
                work_status = "flexible_non_worker" if agent_id in minority_status_ids else "regular_worker"
                medical_need_level = None
                independent_ride_hailing = True

            age_range = (18, 39) if age_group == "18-39" else (40, 59) if age_group == "40-59" else (60, 99)
            segment = age_group

            agents.append(
                AgentProfile(
                    agent_id=agent_id,
                    age_group=age_group,
                    age_range=age_range,
                    is_elder=is_elder,
                    digital_access=digital_access,
                    family_assistance=family_assistance,
                    segment=segment,
                    independent_ride_hailing=independent_ride_hailing,
                    work_status=work_status,
                    medical_need_level=medical_need_level,
                )
            )
            agent_id += 1

    return agents


def summarize_population(agents: List[AgentProfile]) -> dict:
    summary = {
        "total_agents": len(agents),
        "age_group_counts": {},
        "elderly_digital_access": {"digital": 0, "non_digital": 0},
        "elderly_assistance": {"assisted": 0, "unassisted": 0},
        "coupon_attributes": {
            "awareness_configured": 0,
            "claim_configured": 0,
            "independent_ride_hailing_configured": 0,
        },
    }

    for agent in agents:
        validate_agent_profile(agent)
        summary["age_group_counts"][agent.age_group] = summary["age_group_counts"].get(agent.age_group, 0) + 1
        if agent.coupon_awareness_probability is not None:
            summary["coupon_attributes"]["awareness_configured"] += 1
        if agent.coupon_claim_probability is not None:
            summary["coupon_attributes"]["claim_configured"] += 1
        if agent.independent_ride_hailing is not None:
            summary["coupon_attributes"]["independent_ride_hailing_configured"] += 1
        if agent.is_elder:
            if agent.digital_access:
                summary["elderly_digital_access"]["digital"] += 1
            else:
                summary["elderly_digital_access"]["non_digital"] += 1
            if agent.family_assistance:
                summary["elderly_assistance"]["assisted"] += 1
            else:
                summary["elderly_assistance"]["unassisted"] += 1

    return summary
