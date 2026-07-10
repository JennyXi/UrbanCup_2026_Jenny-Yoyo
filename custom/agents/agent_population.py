from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple


@dataclass
class AgentProfile:
    agent_id: int
    age_group: str
    age_range: Tuple[int, int]
    is_elder: bool
    digital_access: bool
    family_assistance: Optional[bool]
    segment: str

    def to_dict(self) -> dict:
        return asdict(self)


def _split_counts(total: int, ratios: List[float]) -> List[int]:
    counts = [int(total * r) for r in ratios]
    remainder = total - sum(counts)
    for idx in range(remainder):
        counts[idx % len(counts)] += 1
    return counts


def generate_population_agents(
    total_agents: int = 1000,
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
        for _ in range(count):
            is_elder = age_group == "60+"
            if is_elder:
                digital_access = random.random() < elder_digital_access_rate
                family_assistance = random.random() < elder_assistance_rate
            else:
                digital_access = True
                family_assistance = None

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
    }

    for agent in agents:
        summary["age_group_counts"][agent.age_group] = summary["age_group_counts"].get(agent.age_group, 0) + 1
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
