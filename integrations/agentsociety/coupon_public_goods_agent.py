"""Official AgentSociety PublicGoodsAgent adapter for finite coupon allocation."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from agentsociety2.contrib.agent import PublicGoodsAgent
from agentsociety2.custom.agents import register_agent

from custom.agents.agent_population import AgentProfile
from custom.agents.public_goods_coupon import (
    OFFICIAL_PUBLIC_GOODS_AGENT,
    allocate_public_goods_coupons,
)


class CouponPublicGoodsAgent(PublicGoodsAgent):
    """Official PublicGoodsAgent adapted to distribute a finite coupon pool."""

    official_parent_agent_class = OFFICIAL_PUBLIC_GOODS_AGENT

    @classmethod
    def init_description(cls) -> str:
        return (
            super().init_description()
            + "\n\nUrban Cup coupon role: run linked virtual contribution rounds, "
            "then allocate an unchanged physical coupon pool using configured "
            "accessibility-need and cooperation weights. The public-goods "
            "multiplier never creates coupons."
        )

    @classmethod
    def allocate_coupon_pool(
        cls,
        profiles: Iterable[AgentProfile],
        day_type: str,
        *,
        seed: int,
        config: Mapping[str, Any],
        cooperation_overrides: Mapping[int, float] | None = None,
    ) -> list[dict[str, Any]]:
        return allocate_public_goods_coupons(
            profiles,
            day_type,
            seed=seed,
            config=config,
            cooperation_overrides=cooperation_overrides,
        )


def register_coupon_public_goods_agent() -> type[CouponPublicGoodsAgent]:
    """Register the adapter explicitly; importing this module has no side effect."""

    register_agent(CouponPublicGoodsAgent.__name__, CouponPublicGoodsAgent)
    return CouponPublicGoodsAgent
