"""Run the 1000-Agent real-API interdependent city-mobility experiment.

This is the production-scale successor to ``run_city_mobility_200_api.py``.
It deliberately keeps the 200-Agent W2 A0 behavioral definition, while adding
strict no-fallback API semantics, attempt-level auditing, rate limiting, and
durable per-decision checkpoints.  A failed API decision never becomes a local
mode choice: the run stops and can be resumed from the last committed success.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import fields
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable, Mapping, Sequence

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.agent_population import AgentProfile  # noqa: E402
from custom.agents.formal_nine_zone_experiment import (  # noqa: E402
    ENABLED_MODES,
    _activity_results,
    _events_for,
    _scenario_summary,
    _simulate_final_choices,
    build_formal_nine_zone_inputs,
)
from custom.agents.interdependent_decision_system import (  # noqa: E402
    SharedTrafficStateRegistry,
    _bin_start,
    _decision_order_key,
    _prepare_legs,
    _scheduled_bus_base_flow,
    load_interdependent_decision_config,
    validate_interdependent_decision_config,
)
from custom.agents.public_goods_coupon import (  # noqa: E402
    allocate_public_goods_coupons,
)
from custom.transport.network import build_transport_network  # noqa: E402
from scripts.run_city_mobility_200_api import (  # noqa: E402
    _build_formal_config,
    _evaluate,
    _extract_json_object,
    _prompt_payload,
    _round_mapping,
    _serial,
    _write_csv,
)


DEFAULT_CONFIG = ROOT / "config" / "city_mobility_1000_api.json"
DEFAULT_COUPLING = ROOT / "config" / "interdependent_agent_decisions.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "city_mobility_1000_api_w2_seed47"
BASELINE_OUTPUT = ROOT / "outputs" / "city_mobility_200_api_w2_seed47"
AGE_GROUPS = ("18-39", "40-59", "60+")
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".log", ".txt", ".yaml", ".yml"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 6) if denominator else None


def _safe_mean(values: Iterable[float]) -> float | None:
    rows = list(values)
    return round(mean(rows), 6) if rows else None


class JsonlStore:
    """Append-only JSONL store with immediate flush for crash recovery."""

    def __init__(self, path: Path, *, key_field: str | None = None) -> None:
        self.path = path
        self.key_field = key_field
        self.lock = asyncio.Lock()
        self.records: list[dict[str, Any]] = []
        self.by_key: dict[str, dict[str, Any]] = {}
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"invalid checkpoint JSONL at {path}:{line_number}"
                        ) from exc
                    self.records.append(row)
                    if key_field is not None:
                        self.by_key[str(row[key_field])] = row

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.by_key.get(str(key))
        return None if row is None else dict(row)

    async def append(self, row: Mapping[str, Any]) -> None:
        materialized = dict(row)
        encoded = json.dumps(
            materialized, ensure_ascii=False, separators=(",", ":"), default=_json_default
        )
        async with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.records.append(materialized)
            if self.key_field is not None:
                self.by_key[str(materialized[self.key_field])] = materialized


class GlobalRateLimiter:
    def __init__(self, requests_per_minute: float) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.interval = 60.0 / float(requests_per_minute)
        self.next_allowed = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_allowed - now)
            if delay:
                await asyncio.sleep(delay)
            self.next_allowed = max(now, self.next_allowed) + self.interval


class APIResponseError(RuntimeError):
    pass


class AuditedAPIClient:
    """Minimal OpenAI-compatible client that never persists credentials."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        concurrency: int,
        requests_per_minute: float,
        max_attempts: int,
        timeout_seconds: float,
        attempt_journal: JsonlStore,
        session_id: str,
    ) -> None:
        if not api_key.strip():
            raise ValueError("AGENTSOCIETY_LLM_API_KEY is empty")
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.endpoint = self.api_base + "/chat/completions"
        self.model = model
        self.max_attempts = int(max_attempts)
        self.semaphore = asyncio.Semaphore(int(concurrency))
        self.limiter = GlobalRateLimiter(requests_per_minute)
        self.attempt_journal = attempt_journal
        self.session_id = session_id
        timeout = httpx.Timeout(
            timeout_seconds, connect=min(30.0, timeout_seconds), pool=timeout_seconds
        )
        limits = httpx.Limits(
            max_connections=max(2, int(concurrency) + 2),
            max_keepalive_connections=max(2, int(concurrency)),
        )
        self.http = httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False)

    async def close(self) -> None:
        await self.http.aclose()

    async def complete_json(
        self,
        *,
        scope: str,
        decision_id: str,
        agent_id: int,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        validator: Callable[[Mapping[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        request_payload = {
            "model": self.model,
            "messages": [dict(row) for row in messages],
            "temperature": 0.2,
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        request_chars = sum(len(str(row.get("content", ""))) for row in messages)
        attempt_rows: list[dict[str, Any]] = []
        last_error = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            await self.limiter.wait()
            started_at = _utc_now()
            started = time.perf_counter()
            status: int | None = None
            input_tokens = 0
            output_tokens = 0
            response_hash = ""
            validated: dict[str, Any] | None = None
            error_type = ""
            retryable = True
            try:
                # A stable key across retries lets compatible providers deduplicate
                # an accepted request whose response was lost in transit.
                idempotency_source = f"urban-cup-1000|{scope}|{decision_id}"
                idempotency_key = hashlib.sha256(
                    idempotency_source.encode("utf-8")
                ).hexdigest()
                async with self.semaphore:
                    response = await self.http.post(
                        self.endpoint,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "User-Agent": "UrbanCup-1000-Agent-Audit/1.0",
                            "Idempotency-Key": idempotency_key,
                        },
                        json=request_payload,
                    )
                status = response.status_code
                if status >= 400:
                    error_type = f"HTTP_{status}"
                    retryable = status in {408, 409, 425, 429} or status >= 500
                    raise APIResponseError(error_type)
                payload = response.json()
                usage = payload.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens") or 0)
                output_tokens = int(usage.get("completion_tokens") or 0)
                content = str(payload["choices"][0]["message"].get("content") or "")
                response_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                validated = validator(_extract_json_object(content))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                error_type = type(exc).__name__
                last_error = error_type
            except (APIResponseError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                if not error_type:
                    error_type = type(exc).__name__
                last_error = error_type

            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            success = validated is not None
            attempt_row = {
                "attempt_id": uuid.uuid4().hex,
                "session_id": self.session_id,
                "scope": scope,
                "decision_id": decision_id,
                "agent_id": agent_id,
                "attempt_number": attempt,
                "started_at_utc": started_at,
                "elapsed_ms": elapsed_ms,
                "success": success,
                "http_status": status,
                "error_type": "" if success else error_type,
                "retryable": False if success else retryable,
                "model": self.model,
                "api_base": self.api_base,
                "request_characters": request_chars,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "response_sha256": response_hash,
                "credential_persisted": False,
            }
            await self.attempt_journal.append(attempt_row)
            attempt_rows.append(attempt_row)
            if success:
                return {
                    **validated,
                    "api_succeeded": True,
                    "attempt_count": attempt,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "elapsed_ms": round(sum(row["elapsed_ms"] for row in attempt_rows), 3),
                    "response_sha256": response_hash,
                    "attempt_ids": [row["attempt_id"] for row in attempt_rows],
                }
            if not retryable or attempt >= self.max_attempts:
                break
            await asyncio.sleep(min(30.0, 1.5 * (2 ** (attempt - 1))))
        raise RuntimeError(
            f"API decision {scope}/{decision_id} failed after "
            f"{len(attempt_rows)} attempt(s): {last_error}"
        )


def _travel_validator(available_modes: set[str]) -> Callable[[Mapping[str, Any] | None], dict[str, Any]]:
    def validate(parsed: Mapping[str, Any] | None) -> dict[str, Any]:
        if parsed is None:
            raise ValueError("missing JSON object")
        mode = str(parsed.get("mode", "")).strip().lower()
        if mode not in available_modes:
            raise ValueError("invalid or unavailable mode")
        reason = str(parsed.get("reason", "")).strip()[:500]
        if not reason:
            raise ValueError("missing reason")
        return {"mode": mode, "reason": reason}

    return validate


def _coupon_validator(endowment: int) -> Callable[[Mapping[str, Any] | None], dict[str, Any]]:
    def validate(parsed: Mapping[str, Any] | None) -> dict[str, Any]:
        if parsed is None:
            raise ValueError("missing JSON object")
        value = parsed.get("contribution_tokens")
        if isinstance(value, bool):
            raise ValueError("boolean contribution is invalid")
        contribution = int(value)
        if contribution != float(value) or not 0 <= contribution <= endowment:
            raise ValueError("contribution is outside the endowment")
        reason = str(parsed.get("reason", "")).strip()[:500]
        if not reason:
            raise ValueError("missing reason")
        return {"contribution_tokens": contribution, "reason": reason}

    return validate


def _profile_from_agent(agent: Mapping[str, Any]) -> AgentProfile:
    names = {field.name for field in fields(AgentProfile)}
    return AgentProfile(**{key: agent.get(key) for key in names})


def _coupon_messages(
    profile: AgentProfile,
    *,
    participant_count: int,
    game: Mapping[str, Any],
) -> list[dict[str, str]]:
    prompt = {
        "task": "public_goods_coupon_round_one_contribution",
        "resident": {
            "agent_id": profile.agent_id,
            "age_group": profile.age_group,
            "digital_access": profile.digital_access,
            "family_assistance": profile.family_assistance,
            "medical_need_level": profile.medical_need_level,
        },
        "game": {
            "participants": participant_count,
            "rounds": int(game["num_rounds"]),
            "initial_endowment_tokens": int(game["initial_endowment"]),
            "public_pool_multiplier": float(game["public_pool_multiplier"]),
            "base_cooperation_propensity": float(
                game["base_cooperation_by_age"][profile.age_group]
            ),
            "later_rounds_observe_prior_group_signal": True,
            "physical_coupon_pool_is_not_created_by_multiplier": True,
        },
        "response_schema": {
            "contribution_tokens": (
                f"integer from 0 to {int(game['initial_endowment'])}"
            ),
            "reason": "one short sentence",
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是公共品博弈中的城市居民Agent。只决定第一轮投入；后续轮次由共享信号联动。"
                "投入影响合作得分和优惠券优先级，但公共品倍率不能创造实体优惠券。"
                "只返回符合response_schema的JSON，不要Markdown。"
            ),
        },
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]


async def _run_coupon_agent(
    *,
    profiles: Sequence[AgentProfile],
    config: Mapping[str, Any],
    seed: int,
    day_type: str,
    client: AuditedAPIClient | None,
    checkpoint: JsonlStore,
    dry_run: bool,
    max_coupon_decisions: int | None,
    progress_every: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    baseline = allocate_public_goods_coupons(
        profiles, day_type, seed=seed, config=config
    )
    baseline_by_id = {int(row["agent_id"]): row for row in baseline}
    participants = [
        profile
        for profile in profiles
        if baseline_by_id[profile.agent_id]["coupon_participated"]
    ]
    selected = (
        participants[: int(max_coupon_decisions)]
        if max_coupon_decisions is not None
        else participants
    )
    game = config["coupon_experiment"]["public_goods_game"]
    endowment = int(game["initial_endowment"])
    completed = 0
    progress_lock = asyncio.Lock()

    async def decide(profile: AgentProfile) -> dict[str, Any]:
        nonlocal completed
        decision_id = f"coupon-round1:{profile.agent_id}"
        cached = checkpoint.get(decision_id)
        if cached is not None:
            result = cached
            source = "checkpoint"
        elif dry_run:
            contribution = int(
                round(endowment * float(game["base_cooperation_by_age"][profile.age_group]))
            )
            result = {
                "decision_id": decision_id,
                "agent_id": profile.agent_id,
                "contribution_tokens": contribution,
                "reason": "dry-run local baseline; not an API result",
                "api_succeeded": False,
                "attempt_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "elapsed_ms": 0.0,
                "response_sha256": "",
                "attempt_ids": [],
                "committed_at_utc": _utc_now(),
            }
            source = "dry_run"
        else:
            if client is None:
                raise RuntimeError("API client is required")
            api_result = await client.complete_json(
                scope="coupon",
                decision_id=decision_id,
                agent_id=profile.agent_id,
                messages=_coupon_messages(
                    profile, participant_count=len(participants), game=game
                ),
                max_tokens=120,
                validator=_coupon_validator(endowment),
            )
            result = {
                "decision_id": decision_id,
                "agent_id": profile.agent_id,
                **api_result,
                "committed_at_utc": _utc_now(),
            }
            await checkpoint.append(result)
            source = "live_api"
        row = {
            "decision_id": decision_id,
            "agent_id": profile.agent_id,
            "age_group": profile.age_group,
            "digital_access": bool(profile.digital_access),
            "family_assistance": profile.family_assistance,
            "medical_need_level": profile.medical_need_level,
            "participant_count": len(participants),
            "initial_endowment": endowment,
            "contribution_tokens": int(result["contribution_tokens"]),
            "cooperation_fraction": round(
                int(result["contribution_tokens"]) / endowment, 6
            ),
            "llm_reason": result["reason"],
            "api_decision_succeeded": bool(result["api_succeeded"]),
            "api_response_source": source,
            "api_attempt_count": int(result["attempt_count"]),
            "api_input_tokens": int(result["input_tokens"]),
            "api_output_tokens": int(result["output_tokens"]),
            "api_elapsed_ms": float(result["elapsed_ms"]),
            "response_sha256": result["response_sha256"],
        }
        async with progress_lock:
            completed += 1
            if completed % max(1, progress_every) == 0 or completed == len(selected):
                print(
                    json.dumps(
                        {"coupon_progress": f"{completed}/{len(selected)}"},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        return row

    decisions = await asyncio.gather(*(decide(profile) for profile in selected))
    decisions.sort(key=lambda row: int(row["agent_id"]))
    decision_by_id = {int(row["agent_id"]): row for row in decisions}
    overrides = {
        agent_id: float(row["cooperation_fraction"])
        for agent_id, row in decision_by_id.items()
    }
    allocations = allocate_public_goods_coupons(
        profiles,
        day_type,
        seed=seed,
        config=config,
        cooperation_overrides=overrides,
    )
    for row in allocations:
        decision = decision_by_id.get(int(row["agent_id"]))
        row.update(
            {
                "pg_api_round_one_called": decision is not None and not dry_run,
                "pg_api_round_one_succeeded": bool(
                    decision and decision["api_decision_succeeded"]
                ),
                "pg_api_contribution_tokens": (
                    decision["contribution_tokens"] if decision else None
                ),
                "pg_api_response_source": (
                    decision["api_response_source"] if decision else "not_selected_test_only"
                ),
            }
        )
    summary = {
        "controller": "PublicGoodsCouponAPIAgent",
        "official_parent_agent_class": game["official_parent_agent_class"],
        "adapter_agent_class": game["adapter_agent_class"],
        "allocation_logic": "version-controlled CouponPublicGoodsAgent-compatible allocator",
        "agents": len(profiles),
        "participants": len(participants),
        "api_contribution_decisions": len(decisions) if not dry_run else 0,
        "api_decision_failures": sum(
            not bool(row["api_decision_succeeded"]) for row in decisions
        ) if not dry_run else 0,
        "configured_coupon_pool": int(
            config["coupon_experiment"]["daily_total_coupon_pool"]
        ),
        "awarded": sum(bool(row["coupon_awarded"]) for row in allocations),
        "coupons_created_by_multiplier": sum(
            int(row["pg_coupons_created_by_multiplier"]) for row in allocations
        ),
        "linked_decisions": sum(bool(row["pg_linked_decision"]) for row in allocations),
        "partial_test": max_coupon_decisions is not None,
    }
    return allocations, decisions, summary


def _summarize_age_results(
    *,
    agents: Mapping[int, Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    mode_choices: Sequence[Mapping[str, Any]],
    activity_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for age_group in AGE_GROUPS:
        agent_ids = {
            agent_id
            for agent_id, agent in agents.items()
            if agent["age_group"] == age_group
        }
        age_decisions = [row for row in decisions if int(row["agent_id"]) in agent_ids]
        choices = [row for row in mode_choices if int(row["agent_id"]) in agent_ids]
        activities = [
            row for row in activity_results if int(row["agent_id"]) in agent_ids
        ]
        successful = [row for row in choices if row["transport_succeeded"]]
        necessary = [row for row in activities if row["is_mandatory"]]
        mode_counts = Counter(row["final_mode"] for row in successful)
        output[age_group] = {
            "agents": len(agent_ids),
            "travel_decisions": len(age_decisions),
            "agents_with_travel": len({int(row["agent_id"]) for row in age_decisions}),
            "final_successful_mode_counts": {
                mode: mode_counts[mode] for mode in ENABLED_MODES
            },
            "transport_success_rate": _rate(
                sum(bool(row["transport_succeeded"]) for row in choices), len(choices)
            ),
            "mean_total_travel_time": _safe_mean(
                float(row["total_travel_time_min"]) for row in successful
            ),
            "mean_access_time": _safe_mean(
                float(row.get("access_time_min") or 0.0) for row in successful
            ),
            "mean_transfer_time": _safe_mean(
                float(row.get("transfer_time_min") or 0.0) for row in successful
            ),
            "necessary_activity_completion_rate": _rate(
                sum(bool(row["completed"]) for row in necessary), len(necessary)
            ),
            "activity_completion_rate": _rate(
                sum(bool(row["completed"]) for row in activities), len(activities)
            ),
        }
    return output


def _elder_digital_results(
    *,
    agents: Mapping[int, Mapping[str, Any]],
    mode_choices: Sequence[Mapping[str, Any]],
    activity_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    elder_agents = {
        agent_id: row for agent_id, row in agents.items() if row["age_group"] == "60+"
    }
    segments = {
        "digital_self": {
            agent_id for agent_id, row in elder_agents.items() if row["digital_access"]
        },
        "family_proxy": {
            agent_id
            for agent_id, row in elder_agents.items()
            if not row["digital_access"] and row.get("family_assistance")
        },
        "nondigital_unassisted": {
            agent_id
            for agent_id, row in elder_agents.items()
            if not row["digital_access"] and not row.get("family_assistance")
        },
    }
    output: dict[str, Any] = {}
    for segment, agent_ids in segments.items():
        choices = [row for row in mode_choices if int(row["agent_id"]) in agent_ids]
        activities = [
            row for row in activity_results if int(row["agent_id"]) in agent_ids
        ]
        necessary = [row for row in activities if row["is_mandatory"]]
        output[segment] = {
            "agents": len(agent_ids),
            "travel_legs": len(choices),
            "ride_hailing_legs": sum(
                row["primary_mode"] == "ride_hailing" for row in choices
            ),
            "ride_hailing_share": _rate(
                sum(row["primary_mode"] == "ride_hailing" for row in choices),
                len(choices),
            ),
            "transport_success_rate": _rate(
                sum(bool(row["transport_succeeded"]) for row in choices), len(choices)
            ),
            "necessary_activity_completion_rate": _rate(
                sum(bool(row["completed"]) for row in necessary), len(necessary)
            ),
            "mean_total_travel_time": _safe_mean(
                float(row["total_travel_time_min"])
                for row in choices
                if row["transport_succeeded"]
            ),
        }
    return output


async def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    session_id = uuid.uuid4().hex[:16]
    output = args.output_dir.resolve()
    if output == BASELINE_OUTPUT.resolve():
        raise ValueError("1000-Agent output must not overwrite the 200-Agent baseline")
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output / "checkpoint"
    attempt_journal = JsonlStore(checkpoint_dir / "api_attempts.jsonl")
    coupon_checkpoint = JsonlStore(
        checkpoint_dir / "coupon_successes.jsonl", key_field="decision_id"
    )
    travel_checkpoint = JsonlStore(
        checkpoint_dir / "travel_successes.jsonl", key_field="decision_id"
    )

    config = json.loads(args.experiment_config.read_text(encoding="utf-8-sig"))
    if int(config["total_agents"]) != 1000:
        raise ValueError("production config must define exactly 1000 Agents")
    if config["comparability"]["age_weather_exposure_multiplier_loaded"]:
        raise ValueError("primary run must preserve the 200-Agent A0 exposure definition")

    experiment, formal = _build_formal_config(args.experiment_config, {}, 1.0)
    coupling = load_interdependent_decision_config(args.coupling_config)
    coupling = json.loads(json.dumps(coupling))
    coupling["shared_traffic_state"]["represented_trips_per_agent"] = float(
        args.represented_trips_per_agent
    )
    validate_interdependent_decision_config(coupling)
    seed = int(args.seed)
    weather_scenario = str(args.weather_scenario)
    day_type = str(args.day_type)
    inputs = build_formal_nine_zone_inputs(config=formal, seed=seed)
    agents = {int(row["agent_id"]): row for row in inputs["agents"]}
    profiles = [_profile_from_agent(agents[agent_id]) for agent_id in sorted(agents)]
    if len(agents) != 1000:
        raise AssertionError(f"expected 1000 Agents, got {len(agents)}")

    api_key = os.getenv("AGENTSOCIETY_LLM_API_KEY", "")
    api_base = (
        os.getenv("AGENTSOCIETY_LLM_API_BASE")
        or os.getenv("AGENTSOCIETY_LLM_BASE_URL")
        or "https://api.deepseek.com"
    )
    model = os.getenv("AGENTSOCIETY_LLM_MODEL") or "deepseek-v4-flash"
    client: AuditedAPIClient | None = None
    if not args.dry_run:
        if not api_key:
            raise RuntimeError(
                "AGENTSOCIETY_LLM_API_KEY is not available; set it in the terminal environment"
            )
        client = AuditedAPIClient(
            api_key=api_key,
            api_base=api_base,
            model=model,
            concurrency=args.concurrency,
            requests_per_minute=args.requests_per_minute,
            max_attempts=args.max_attempts,
            timeout_seconds=args.timeout_seconds,
            attempt_journal=attempt_journal,
            session_id=session_id,
        )

    manifest = {
        "experiment_id": experiment["experiment_id"],
        "started_at_utc": _utc_now(),
        "session_id": session_id,
        "seed": seed,
        "weather_scenario": weather_scenario,
        "day_type": day_type,
        "agents": len(agents),
        "model": "dry-run" if args.dry_run else model,
        "api_base": "" if args.dry_run else api_base,
        "api_key_present": bool(api_key),
        "api_key_persisted": False,
        "concurrency": int(args.concurrency),
        "requests_per_minute": float(args.requests_per_minute),
        "max_attempts": int(args.max_attempts),
        "checkpoint_resume_enabled": True,
        "comparability": config["comparability"],
    }
    _write_json(output / "run_manifest.json", manifest)

    try:
        coupon_allocations, coupon_decisions, coupon_summary = await _run_coupon_agent(
            profiles=profiles,
            config=config,
            seed=seed,
            day_type=day_type,
            client=client,
            checkpoint=coupon_checkpoint,
            dry_run=args.dry_run,
            max_coupon_decisions=args.max_coupon_decisions,
            progress_every=args.progress_every,
        )
        coupon_by_id = {int(row["agent_id"]): row for row in coupon_allocations}
        formal["_coupon_allocations"] = coupon_by_id
        formal["_coupon_discount_multiplier"] = float(args.discount_multiplier)

        selected_date = date.fromisoformat(formal["selected_days"][day_type])
        activities = [
            row
            for row in inputs["activities"]
            if row["planned_start_datetime"].date() == selected_date
        ]
        legs = [
            row
            for row in inputs["legs"]
            if row["departure_time"].date() == selected_date
        ]
        network = build_transport_network()
        events = _events_for(formal, weather_scenario, day_type)
        planned_legs = _prepare_legs(
            agents, activities, legs, network, events, formal, seed
        )
        ordered_legs = sorted(
            planned_legs, key=lambda row: _decision_order_key(seed, row)
        )
        if args.max_decisions is not None:
            ordered_legs = ordered_legs[: int(args.max_decisions)]

        precision = int(coupling["choice_model"]["probability_precision"])
        tolerance = float(coupling["audit"]["probability_change_tolerance"])
        coupon_bound_agents: set[int] = set()
        decisions: list[dict[str, Any]] = []
        influence_edges: list[dict[str, Any]] = []
        simulation_choices: list[dict[str, Any]] = []
        bin_minutes = int(coupling["shared_traffic_state"]["time_bin_minutes"])
        sequence_by_leg_id = {
            str(leg["leg_id"]): sequence
            for sequence, leg in enumerate(ordered_legs, start=1)
        }
        first_leg_id_by_agent: dict[int, str] = {}
        legs_by_bin: dict[datetime, list[Mapping[str, Any]]] = {}
        for leg in ordered_legs:
            agent_id = int(leg["agent_id"])
            first_leg_id_by_agent.setdefault(agent_id, str(leg["leg_id"]))
            legs_by_bin.setdefault(
                _bin_start(leg["departure_time"], bin_minutes), []
            ).append(leg)
        registries = {
            bin_start: SharedTrafficStateRegistry(coupling["shared_traffic_state"])
            for bin_start in legs_by_bin
        }
        progress_lock = asyncio.Lock()
        completed_count = 0
        completed_ride_count = 0
        completed_affected_count = 0

        async def process_time_bin(
            bin_start: datetime,
            bin_legs: Sequence[Mapping[str, Any]],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
            nonlocal completed_count, completed_ride_count, completed_affected_count
            registry = registries[bin_start]
            local_decisions: list[dict[str, Any]] = []
            local_edges: list[dict[str, Any]] = []
            local_simulation: list[dict[str, Any]] = []
            for leg in sorted(
                bin_legs, key=lambda row: sequence_by_leg_id[str(row["leg_id"])]
            ):
                sequence = sequence_by_leg_id[str(leg["leg_id"])]
                agent_id = int(leg["agent_id"])
                agent = agents[agent_id]
                allocation = coupon_by_id.get(agent_id, {})
                coupon_available = bool(
                    allocation.get("coupon_awarded")
                    and str(leg["leg_id"]) == first_leg_id_by_agent[agent_id]
                )
                coupon_proxy_access = bool(
                    coupon_available
                    and allocation.get("coupon_access_channel")
                    in {"community_phone", "family_proxy"}
                )
                base_flow = _scheduled_bus_base_flow(
                    leg["departure_time"], network, formal, coupling
                )
                before = registry.snapshot(leg["departure_time"], base_flow)
                coupled = _evaluate(
                    leg=leg,
                    agent=agent,
                    flow=float(before["total_flow_pcu_per_hour"]),
                    network=network,
                    events=events,
                    formal=formal,
                    coupling=coupling,
                    seed=seed,
                    coupon_available=coupon_available,
                    coupon_proxy_access=coupon_proxy_access,
                )
                uncoupled = (
                    _evaluate(
                        leg=leg,
                        agent=agent,
                        flow=base_flow,
                        network=network,
                        events=events,
                        formal=formal,
                        coupling=coupling,
                        seed=seed,
                        coupon_available=coupon_available,
                        coupon_proxy_access=coupon_proxy_access,
                    )
                    if before["sources"]
                    else coupled
                )
                probabilities = coupled["probabilities"]
                deltas = {
                    mode: float(probabilities.get(mode, 0.0))
                    - float(uncoupled["probabilities"].get(mode, 0.0))
                    for mode in ENABLED_MODES
                }
                max_delta = max((abs(value) for value in deltas.values()), default=0.0)
                affected = bool(before["sources"] and max_delta > tolerance)
                decision_id = f"travel:{leg['leg_id']}"
                cached = travel_checkpoint.get(decision_id)
                if cached is not None:
                    api_result = cached
                    response_source = "checkpoint"
                elif args.dry_run:
                    chosen_mode = max(probabilities, key=probabilities.get)
                    api_result = {
                        "mode": chosen_mode,
                        "reason": "dry-run local maximum; not an API result",
                        "api_succeeded": False,
                        "attempt_count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "elapsed_ms": 0.0,
                        "response_sha256": "",
                        "attempt_ids": [],
                    }
                    response_source = "dry_run"
                else:
                    if client is None:
                        raise RuntimeError("API client is required")
                    prompt = _prompt_payload(
                        agent=agent,
                        leg=leg,
                        evaluation=coupled,
                        weather_scenario=weather_scenario,
                        road_state=before,
                        coupon_available=coupon_available,
                    )
                    api_result = await client.complete_json(
                        scope="travel",
                        decision_id=decision_id,
                        agent_id=agent_id,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "你是城市居民出行决策Agent。根据个人属性、天气、优惠券、实时道路状态"
                                    "和候选方式作出一次选择。只能选择提供的方式，只返回符合"
                                    "response_schema的JSON，不要Markdown。"
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(prompt, ensure_ascii=False),
                            },
                        ],
                        max_tokens=120,
                        validator=_travel_validator(set(probabilities)),
                    )
                    api_result = {
                        "decision_id": decision_id,
                        "agent_id": agent_id,
                        **api_result,
                        "committed_at_utc": _utc_now(),
                    }
                    await travel_checkpoint.append(api_result)
                    response_source = "live_api"
                chosen_mode = str(api_result["mode"])
                chosen_option = next(
                    row
                    for row in coupled["scored_options"]
                    if row["mode"] == chosen_mode
                )
                coupon_bound = bool(
                    chosen_mode == "ride_hailing"
                    and chosen_option.get("coupon_applied_to_choice")
                )
                if coupon_bound:
                    coupon_bound_agents.add(agent_id)
                event = registry.publish_choice(
                    agent_id=agent_id,
                    leg_id=str(leg["leg_id"]),
                    mode=chosen_mode,
                    departure_time=leg["departure_time"],
                    decision_sequence=sequence,
                    base_flow=base_flow,
                )
                after = registry.snapshot(leg["departure_time"], base_flow)
                source_leg_ids = [str(row["leg_id"]) for row in before["sources"]]
                local_decisions.append(
                    {
                        "decision_sequence": sequence,
                        "decision_id": decision_id,
                        "agent_id": agent_id,
                        "age_group": agent["age_group"],
                        "digital_access": bool(agent["digital_access"]),
                        "family_assistance": agent.get("family_assistance"),
                        "leg_id": str(leg["leg_id"]),
                        "purpose": leg.get("purpose"),
                        "origin_zone": leg["origin_zone"],
                        "destination_zone": leg["destination_zone"],
                        "departure_time": leg["departure_time"],
                        "weather_scenario": weather_scenario,
                        "shared_state_key": before["state_key"],
                        "state_version_before": before["state_version"],
                        "state_version_after": after["state_version"],
                        "base_road_flow_pcu_per_hour": round(base_flow, 6),
                        "endogenous_flow_before": round(
                            float(before["endogenous_flow_pcu_per_hour"]), 6
                        ),
                        "endogenous_flow_after": round(
                            float(after["endogenous_flow_pcu_per_hour"]), 6
                        ),
                        "prior_influencer_count": len(before["sources"]),
                        "prior_influencer_leg_ids": source_leg_ids,
                        "mode_probabilities_without_prior_agents": _round_mapping(
                            uncoupled["probabilities"], precision
                        ),
                        "mode_probabilities_with_prior_agents": _round_mapping(
                            probabilities, precision
                        ),
                        "probability_delta_from_prior_agents": _round_mapping(
                            deltas, precision
                        ),
                        "maximum_absolute_probability_delta": round(
                            max_delta, precision
                        ),
                        "affected_by_prior_agents": affected,
                        "coupon_awarded": bool(allocation.get("coupon_awarded")),
                        "coupon_available_at_choice": coupon_available,
                        "coupon_binding_rule": "first_trip_decision_only",
                        "coupon_bound_to_ride_hailing": coupon_bound,
                        "chosen_mode": chosen_mode,
                        "chosen_probability": round(
                            float(probabilities.get(chosen_mode, 0.0)), precision
                        ),
                        "llm_reason": api_result["reason"],
                        "api_decision_succeeded": bool(api_result["api_succeeded"]),
                        "api_call_attempted": not args.dry_run,
                        "api_response_source": response_source,
                        "api_attempt_count": int(api_result["attempt_count"]),
                        "api_input_tokens": int(api_result["input_tokens"]),
                        "api_output_tokens": int(api_result["output_tokens"]),
                        "api_elapsed_ms": float(api_result["elapsed_ms"]),
                        "response_sha256": api_result["response_sha256"],
                        "published_traffic_event": event is not None,
                    }
                )
                local_simulation.append(
                    {
                        "_decision_sequence": sequence,
                        "leg": leg,
                        "chosen_mode": chosen_mode,
                        "chosen_option": chosen_option,
                        "options": coupled["options"],
                        "scored_options": coupled["scored_options"],
                        "coupon_bound_to_primary": coupon_bound,
                    }
                )
                if affected and coupling["audit"].get("record_influence_edges", True):
                    rounded_delta = _round_mapping(deltas, precision)
                    for source in before["sources"]:
                        local_edges.append(
                            {
                                "source_decision_sequence": source[
                                    "decision_sequence"
                                ],
                                "source_agent_id": source["agent_id"],
                                "source_leg_id": source["leg_id"],
                                "target_decision_sequence": sequence,
                                "target_agent_id": agent_id,
                                "target_leg_id": str(leg["leg_id"]),
                                "shared_state_key": before["state_key"],
                                "mechanism": (
                                    "ride_hailing_choice_to_shared_road_flow_to_mode_probability"
                                ),
                                "source_flow_contribution_pcu_per_hour": source[
                                    "flow_contribution_pcu_per_hour"
                                ],
                                "target_probability_delta": rounded_delta,
                            }
                        )
                async with progress_lock:
                    completed_count += 1
                    completed_ride_count += chosen_mode == "ride_hailing"
                    completed_affected_count += affected
                    if (
                        completed_count % int(args.progress_every) == 0
                        or completed_count == len(ordered_legs)
                    ):
                        print(
                            json.dumps(
                                {
                                    "travel_progress": (
                                        f"{completed_count}/{len(ordered_legs)}"
                                    ),
                                    "ride_hailing": completed_ride_count,
                                    "affected": completed_affected_count,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
            return local_decisions, local_edges, local_simulation

        bin_results = await asyncio.gather(
            *(
                process_time_bin(bin_start, bin_legs)
                for bin_start, bin_legs in sorted(legs_by_bin.items())
            )
        )
        for local_decisions, local_edges, local_simulation in bin_results:
            decisions.extend(local_decisions)
            influence_edges.extend(local_edges)
            simulation_choices.extend(local_simulation)
        decisions.sort(key=lambda row: int(row["decision_sequence"]))
        influence_edges.sort(
            key=lambda row: (
                int(row["target_decision_sequence"]),
                int(row["source_decision_sequence"]),
            )
        )
        simulation_choices.sort(key=lambda row: int(row["_decision_sequence"]))

        traffic_events = sorted(
            [event for registry in registries.values() for event in registry.events],
            key=lambda row: int(row["decision_sequence"]),
        )
        for event_sequence, event in enumerate(traffic_events, start=1):
            event["event_sequence"] = event_sequence
            event["global_version_after"] = event_sequence
        traffic_state_rows = sorted(
            [row for registry in registries.values() for row in registry.state_rows()],
            key=lambda row: row["time_bin_start"],
        )
        unique_bins: dict[datetime, float] = {}
        for bin_start, bin_legs in legs_by_bin.items():
            leg = bin_legs[0]
            base_flow = _scheduled_bus_base_flow(
                leg["departure_time"], network, formal, coupling
            )
            unique_bins[bin_start] = float(
                registries[bin_start].snapshot(
                    leg["departure_time"], base_flow
                )["total_flow_pcu_per_hour"]
            )
        mode_choices, dispatch, vehicle_states = _simulate_final_choices(
            simulation_choices,
            agents,
            network,
            events,
            formal,
            unique_bins,
            day_type,
            seed,
        )
        for rows in (mode_choices, dispatch, vehicle_states):
            for row in rows:
                row.update(
                    {
                        "weather_scenario": weather_scenario,
                        "policy": "C4_public_goods",
                        "experiment_condition": (
                            "API_interdependent_city_mobility_1000"
                        ),
                    }
                )
        for row in mode_choices:
            row["day_type"] = day_type
        for row in dispatch:
            row["day_type"] = day_type
        activity_results = _activity_results(
            activities, mode_choices, formal, weather_scenario, day_type
        )
        formal_summary = _scenario_summary(
            mode_choices,
            activities,
            dispatch,
            vehicle_states,
            network,
            formal,
            weather_scenario,
            day_type,
            unique_bins,
            seed,
        )

        api_attempts = list(attempt_journal.records)
        travel_api_successes = sum(
            bool(row["api_decision_succeeded"]) for row in decisions
        )
        chosen_counts = Counter(row["chosen_mode"] for row in decisions)
        final_counts = Counter(
            row["final_mode"] for row in mode_choices if row["transport_succeeded"]
        )
        redeemed = sum(bool(row.get("coupon_redeemed")) for row in mode_choices)
        prior_candidates = sum(int(row["prior_influencer_count"]) > 0 for row in decisions)
        affected_count = sum(bool(row["affected_by_prior_agents"]) for row in decisions)
        age_results = _summarize_age_results(
            agents=agents,
            decisions=decisions,
            mode_choices=mode_choices,
            activity_results=activity_results,
        )
        elder_digital = _elder_digital_results(
            agents=agents,
            mode_choices=mode_choices,
            activity_results=activity_results,
        )
        partial = args.max_decisions is not None or args.max_coupon_decisions is not None
        summary = {
            "status": "DRY_RUN" if args.dry_run else "PARTIAL_TEST" if partial else "PASS",
            "experiment": "1000-Agent real-API interdependent urban mobility",
            "experiment_version": "city_mobility_1000_api_w2_seed47_v1",
            "seed": seed,
            "weather_scenario": weather_scenario,
            "day_type": day_type,
            "agents": len(agents),
            "age_group_counts": dict(
                sorted(Counter(row["age_group"] for row in agents.values()).items())
            ),
            "agents_with_travel": len(
                {int(row["agent_id"]) for row in ordered_legs}
            ),
            "agents_without_workday_travel": len(agents)
            - len({int(row["agent_id"]) for row in ordered_legs}),
            "travel_decisions": len(decisions),
            "api_travel_decisions": len(decisions) if not args.dry_run else 0,
            "api_successful_travel_decisions": travel_api_successes if not args.dry_run else 0,
            "api_travel_coverage_rate": (
                _rate(travel_api_successes, len(decisions)) if not args.dry_run else 0.0
            ),
            "api_decision_failures": (
                len(decisions) - travel_api_successes if not args.dry_run else 0
            ),
            "model": "dry-run" if args.dry_run else model,
            "api_base": "" if args.dry_run else api_base,
            "api_key_present": bool(api_key),
            "api_key_persisted": False,
            "api_attempts_total": len(api_attempts),
            "api_attempt_failures": sum(not bool(row["success"]) for row in api_attempts),
            "usage": {
                "successful_calls": sum(bool(row["success"]) for row in api_attempts),
                "input_tokens": sum(int(row["input_tokens"]) for row in api_attempts),
                "output_tokens": sum(int(row["output_tokens"]) for row in api_attempts),
                "total_tokens": sum(int(row["total_tokens"]) for row in api_attempts),
                "elapsed_api_ms": round(
                    sum(float(row["elapsed_ms"]) for row in api_attempts), 3
                ),
            },
            "checkpoint": {
                "travel_successes": len(travel_checkpoint.by_key),
                "coupon_successes": len(coupon_checkpoint.by_key),
                "resume_enabled": True,
                "responses_reused_from_checkpoint": sum(
                    row["api_response_source"] == "checkpoint" for row in decisions
                )
                + sum(
                    row["api_response_source"] == "checkpoint"
                    for row in coupon_decisions
                ),
            },
            "chosen_mode_counts": {
                mode: chosen_counts[mode] for mode in ENABLED_MODES
            },
            "final_successful_mode_counts": {
                mode: final_counts[mode] for mode in ENABLED_MODES
            },
            "ride_hailing_traffic_events": len(traffic_events),
            "decisions_with_prior_influencers": prior_candidates,
            "affected_decisions": affected_count,
            "linkage_coverage_rate": _rate(affected_count, len(decisions)),
            "conditional_linkage_rate": _rate(affected_count, prior_candidates),
            "maximum_absolute_probability_change": max(
                (
                    float(row["maximum_absolute_probability_delta"])
                    for row in decisions
                ),
                default=0.0,
            ),
            "influence_edges": len(influence_edges),
            "represented_trips_per_agent": float(
                coupling["shared_traffic_state"]["represented_trips_per_agent"]
            ),
            "aggregate_represented_trips": len(agents)
            * float(coupling["shared_traffic_state"]["represented_trips_per_agent"]),
            "coupon_agent": coupon_summary,
            "coupon_funnel": {
                "reached": sum(bool(row["coupon_reached"]) for row in coupon_allocations),
                "participated": sum(
                    bool(row["coupon_participated"]) for row in coupon_allocations
                ),
                "awarded": sum(
                    bool(row["coupon_awarded"]) for row in coupon_allocations
                ),
                "available_at_first_travel_choice": sum(
                    bool(row["coupon_available_at_choice"]) for row in decisions
                ),
                "bound_to_ride_hailing": len(coupon_bound_agents),
                "redeemed": redeemed,
            },
            "coupon_binding_rule": (
                "awarded coupon is offered on the first trip decision only and "
                "affects only ride-hailing fare/choice; redemption requires successful dispatch"
            ),
            "ride_hailing_requests": len(dispatch),
            "successful_ride_hailing_requests": sum(
                bool(row["succeeded"]) for row in dispatch
            ),
            "failed_ride_hailing_requests": sum(
                not bool(row["succeeded"]) for row in dispatch
            ),
            "transport_success_rate": formal_summary["transport_success_rate"],
            "activity_completion_rate": formal_summary["activity_completion_rate"],
            "necessary_activity_completion_rate": formal_summary[
                "necessary_activity_completion_rate"
            ],
            "mean_total_travel_time": formal_summary["mean_total_travel_time"],
            "initial_ride_hailing_vehicles": formal_summary[
                "initial_ride_hailing_vehicles"
            ],
            "age_results": age_results,
            "elder_digital_gap_results": elder_digital,
            "age_parameter_version": {
                "version": "A0_strict_200_api_baseline",
                "w2_age_weather_exposure_multiplier_loaded": False,
                "age_mode_constant": formal["mode_choice"]["age_mode_constant"],
                "value_of_time_yuan_per_hour": formal["mode_choice"][
                    "value_of_time_yuan_per_hour"
                ],
                "ride_hailing_access_rule": (
                    "digital_access OR family_assistance OR coupon community/family proxy"
                ),
                "elder_inconvenience_note": (
                    "60+ retains walk=-1.5 age constant, lower value of time, "
                    "and network access/transfer time; no later A1 exposure multiplier is added"
                ),
            },
            "comparability": config["comparability"],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "completed_at_utc": _utc_now(),
        }

        agent_rows = [dict(agents[agent_id]) for agent_id in sorted(agents)]
        _write_csv(output / "agents.csv", agent_rows)
        _write_csv(output / "coupon_api_decisions.csv", coupon_decisions)
        _write_csv(output / "coupon_allocations.csv", coupon_allocations)
        _write_csv(output / "decision_audit.csv", decisions)
        _write_csv(output / "api_call_audit.csv", api_attempts)
        _write_csv(output / "influence_edges.csv", influence_edges)
        _write_csv(output / "traffic_state_events.csv", traffic_events)
        _write_csv(output / "traffic_state_final.csv", traffic_state_rows)
        _write_csv(output / "mode_choices.csv", mode_choices)
        _write_csv(output / "ride_hailing_dispatch.csv", dispatch)
        _write_csv(output / "vehicle_end_states.csv", vehicle_states)
        _write_csv(output / "activity_results.csv", activity_results)
        _write_json(output / "summary.json", summary)
        return summary
    except Exception as exc:
        incomplete = {
            **manifest,
            "status": "INCOMPLETE_API_FAILURE",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:500],
            "api_attempts_recorded": len(attempt_journal.records),
            "coupon_successes_checkpointed": len(coupon_checkpoint.by_key),
            "travel_successes_checkpointed": len(travel_checkpoint.by_key),
            "resume_command_note": "rerun the same command; committed successes are reused",
            "failed_at_utc": _utc_now(),
        }
        _write_json(output / "incomplete_summary.json", incomplete)
        raise
    finally:
        if client is not None:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--coupling-config", type=Path, default=DEFAULT_COUPLING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--weather-scenario", choices=("W0", "W1", "W2"), default="W2")
    parser.add_argument("--day-type", choices=("workday", "rest_day"), default="workday")
    parser.add_argument("--discount-multiplier", type=float, default=0.8)
    parser.add_argument("--represented-trips-per-agent", type=float, default=6.0)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--requests-per-minute", type=float, default=120.0)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--max-decisions", type=int, default=None)
    parser.add_argument("--max-coupon-decisions", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for name in (
        "concurrency",
        "requests_per_minute",
        "max_attempts",
        "timeout_seconds",
        "progress_every",
        "represented_trips_per_agent",
    ):
        if float(getattr(args, name)) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if args.max_decisions is not None and args.max_decisions <= 0:
        raise ValueError("--max-decisions must be positive")
    if args.max_coupon_decisions is not None and args.max_coupon_decisions <= 0:
        raise ValueError("--max-coupon-decisions must be positive")
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
