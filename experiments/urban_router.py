"""Importable environment router for the Urban Cup integration pilot."""

import asyncio
from contextvars import ContextVar

from agentsociety2.env.router_base import RouterBase


class UrbanReadOnlyRouter(RouterBase):
    """Serve profile-contained trip facts without mutating the environment."""

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        # ContextVar is process-local and cannot be pickled by Ray.  The lock
        # is also event-loop-local; both are safe to recreate in the worker.
        state.pop("_trace_ctx", None)
        state.pop("_generate_world_description_lock", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._trace_ctx = ContextVar(
            f"envrouter_trace_ctx_{id(self)}", default=(None, None, None)
        )
        self._generate_world_description_lock = asyncio.Lock()

    async def ask(
        self,
        ctx: dict,
        instruction: str,
        readonly: bool = False,
        template_mode: bool = False,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> tuple[dict, str]:
        return ctx, (
            "All authoritative trip, weather, policy, and mode-option data are "
            "already present in your decision_context profile."
        )
