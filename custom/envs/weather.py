"""T2: weather-driven *additional* activity disruption before mode choice.

T2 decides whether a planned activity survives weather exposure and emits a
ride-hailing preference signal.  It does not choose a mode, alter transport
supply, create ride-hailing demand, or calculate road congestion.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


WEEK_LABELS = ("W0", "W1", "W2")
SCENARIO_LEVELS = ("low", "base", "high")
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "weather_activity_disruption.json"


def _load_parameters(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    metadata = config.get("metadata", {})
    expected = {
        "source_type": "model_assumption",
        "calibration_status": "sensitivity_analysis",
        "not_database_estimate": True,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("T2 parameters must be labelled as MVP model assumptions")
    if tuple(config.get("scenario_levels", {}).keys()) != SCENARIO_LEVELS:
        raise ValueError("T2 must define low, base, and high scenario levels")
    return config


PARAMETERS = _load_parameters()


def _parse_time_hhmm(value: str) -> datetime.time:
    try:
        hour, minute = value.split(":")
        return datetime.time(int(hour), int(minute))
    except Exception as exc:
        raise ValueError(f"Invalid time format '{value}', expected HH:MM") from exc


def _clock(value: Any) -> datetime.time:
    if isinstance(value, datetime.datetime):
        return value.time()
    if isinstance(value, datetime.time):
        return value
    return _parse_time_hhmm(str(value)[-5:])


@dataclass(frozen=True)
class WeatherEvent:
    day: str
    start_time: str
    end_time: str

    def overlaps(self, day: str, departure_time: Any, arrival_time: Any = None) -> bool:
        if day != self.day:
            return False
        departure = _clock(departure_time)
        arrival = _clock(arrival_time) if arrival_time is not None else departure
        start = _parse_time_hhmm(self.start_time)
        end = _parse_time_hhmm(self.end_time)
        if not start < end:
            raise ValueError(f"Weather event start must precede end: {self}")
        if arrival_time is None or arrival == departure:
            return start <= departure < end
        return departure < end and arrival > start


@dataclass
class WeatherConfig:
    current_week: str = "W0"
    scenario_level: str = "base"
    random_seed: int = 0
    w1_windows: List[WeatherEvent] = field(default_factory=lambda: [
        WeatherEvent(day, "11:00", "18:00")
        for day in ("Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
    ])
    w2_windows: List[WeatherEvent] = field(default_factory=list)

    def validate(self) -> None:
        if self.current_week not in WEEK_LABELS:
            raise ValueError(f"Unknown current_week: {self.current_week}")
        if self.scenario_level not in SCENARIO_LEVELS:
            raise ValueError(f"Unknown scenario_level: {self.scenario_level}")
        if self.current_week == "W2" and len(self.w2_windows) != 3:
            raise ValueError("W2 requires exactly three explicit weather windows")
        for event in self.w1_windows + self.w2_windows:
            if not _parse_time_hhmm(event.start_time) < _parse_time_hhmm(event.end_time):
                raise ValueError(f"Weather event start must precede end: {event}")


CONFIG = WeatherConfig()


def set_week(week_label: str) -> None:
    if week_label not in WEEK_LABELS:
        raise ValueError(f"Invalid week label: {week_label}")
    CONFIG.current_week = week_label


def set_scenario_level(level: str) -> None:
    if level not in SCENARIO_LEVELS:
        raise ValueError(f"Invalid scenario level: {level}")
    CONFIG.scenario_level = level


def init_rng(seed: Optional[int] = None) -> None:
    """Set the stable sampling seed; no mutable global RNG is used."""
    if seed is not None:
        CONFIG.random_seed = int(seed)


def set_w2_windows(events: Sequence[Tuple[str, str, str]]) -> None:
    if len(events) != 3:
        raise ValueError("W2 requires exactly three explicit weather windows")
    CONFIG.w2_windows = [WeatherEvent(day, start, end) for day, start, end in events]
    CONFIG.validate()


def map_activity_to_weather_purpose(activity_purpose: str) -> str:
    aliases = PARAMETERS["activity_purpose_aliases"]
    purpose = aliases.get(activity_purpose, activity_purpose)
    supported = {
        "medical", "work", "family_care", "family_activity", "visit",
        "shopping", "social_leisure",
    }
    if purpose not in supported:
        raise ValueError(f"Unsupported activity purpose for T2: {activity_purpose}")
    return purpose


def _weather_type() -> str:
    return {"W0": "normal", "W1": "extreme_heat", "W2": "heavy_rain"}[CONFIG.current_week]


def _active_windows() -> Sequence[WeatherEvent]:
    if CONFIG.current_week == "W1":
        return CONFIG.w1_windows
    if CONFIG.current_week == "W2":
        CONFIG.validate()
        return CONFIG.w2_windows
    return ()


def outbound_weather_exposure(day: str, planned_outbound_departure: Any, planned_activity_arrival: Any) -> bool:
    """Cancellation exposure is based only on the planned outbound interval."""
    return any(
        event.overlaps(day, planned_outbound_departure, planned_activity_arrival)
        for event in _active_windows()
    )


def _scenario_parameters(level: Optional[str] = None) -> Mapping[str, Any]:
    selected = level or CONFIG.scenario_level
    if selected not in SCENARIO_LEVELS:
        raise ValueError(f"Invalid scenario level: {selected}")
    return PARAMETERS["scenario_levels"][selected]


def _purpose_modifier(purpose: str, level: Optional[str] = None) -> float:
    purpose = map_activity_to_weather_purpose(purpose)
    if purpose in {"medical", "work"}:
        return float(_scenario_parameters(level)["purpose_multiplier"][purpose])
    return float(PARAMETERS["fixed_purpose_multiplier"][purpose])


def _age_modifier(age_group: str, level: Optional[str] = None) -> float:
    if age_group == "60+":
        return float(_scenario_parameters(level)["age_multiplier_60_plus"])
    try:
        return float(PARAMETERS["fixed_age_multiplier"][age_group])
    except KeyError as exc:
        raise ValueError(f"Unsupported age group for T2: {age_group}") from exc


def _required_profile_value(profile: Any, name: str) -> str:
    if isinstance(profile, Mapping):
        if name not in profile:
            raise ValueError(f"agent_profile missing required T2 field: {name}")
        value = profile[name]
    else:
        if not hasattr(profile, name):
            raise ValueError(f"agent_profile missing required T2 field: {name}")
        value = getattr(profile, name)
    if value is None:
        raise ValueError(f"agent_profile field may not be null: {name}")
    return str(value)


def _validate_agent_profile(profile: Any, scenario_level: Optional[str] = None) -> Tuple[str, str, str]:
    age_group = _required_profile_value(profile, "age_group")
    mobility = _required_profile_value(profile, "mobility_constraint")
    flexibility = _required_profile_value(profile, "schedule_flexibility")
    _age_modifier(age_group, scenario_level)
    if mobility not in PARAMETERS["mobility_constraint_multiplier"]:
        raise ValueError(f"Unsupported mobility_constraint for T2: {mobility}")
    if flexibility not in PARAMETERS["schedule_flexibility_multiplier"]:
        raise ValueError(f"Unsupported schedule_flexibility for T2: {flexibility}")
    return age_group, mobility, flexibility


def compute_weather_cancel_probability(
    weather_type: str,
    purpose: str,
    age_group: str,
    mobility_constraint: str = "none",
    schedule_flexibility: str = "medium",
    scenario_level: Optional[str] = None,
) -> float:
    if weather_type == "normal":
        return 0.0
    level = scenario_level or CONFIG.scenario_level
    scenario = _scenario_parameters(level)
    try:
        base = float(scenario["weather_cancel_rate_base"][weather_type])
        mobility = float(PARAMETERS["mobility_constraint_multiplier"][mobility_constraint])
        flexibility = float(PARAMETERS["schedule_flexibility_multiplier"][schedule_flexibility])
    except KeyError as exc:
        raise ValueError(f"Unsupported T2 parameter category: {exc.args[0]}") from exc
    probability = base * _purpose_modifier(purpose, level) * _age_modifier(age_group, level) * mobility * flexibility
    return min(1.0, max(0.0, probability))


# Backward-compatible internal name used by earlier callers/tests.
def _compute_p_weather_cancel(weather_type: str, purpose: str, age_group: str) -> float:
    return compute_weather_cancel_probability(weather_type, purpose, age_group)


def combine_baseline_and_weather_cancel_probability(baseline: float, weather: float) -> float:
    if not 0 <= baseline <= 1 or not 0 <= weather <= 1:
        raise ValueError("Cancellation probabilities must be within [0, 1]")
    return 1.0 - (1.0 - baseline) * (1.0 - weather)


def _stable_uniform(agent_id: Any, activity_id: Any, weather_scenario: str, seed: int) -> float:
    """One common draw per agent/activity/weather week, reused across levels."""
    payload = "|".join(map(str, (agent_id, activity_id, weather_scenario, seed))).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return integer / float(2**64)


def _preference_signal(weather_type: str, active: bool, level: Optional[str] = None) -> Tuple[float, float]:
    if not active or weather_type == "normal":
        return 1.0, 0.0
    odds = float(_scenario_parameters(level)["ride_hailing_odds_multiplier"][weather_type])
    return odds, math.log(odds)


def evaluate_planned_activity(
    activity: Mapping[str, Any],
    agent_profile: Any,
    *,
    scenario_level: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate a planned activity before leg retention and mode choice.

    Required activity fields are ``agent_id``, ``activity_id``, ``day_of_week``,
    ``activity_purpose``, ``planned_outbound_departure`` and
    ``planned_activity_arrival``.  A caller may populate the final two fields
    from a provisional travel-time feasibility pass; T2 never chooses a mode.
    """
    required = (
        "agent_id", "activity_id", "day_of_week", "activity_purpose",
        "planned_outbound_departure", "planned_activity_arrival",
    )
    missing = [name for name in required if name not in activity]
    if missing:
        raise ValueError(f"Activity missing required T2 fields: {missing}")
    result = copy.deepcopy(dict(activity))
    purpose = map_activity_to_weather_purpose(str(activity["activity_purpose"]))
    weather_type = _weather_type()
    exposed = outbound_weather_exposure(
        str(activity["day_of_week"]), activity["planned_outbound_departure"],
        activity["planned_activity_arrival"],
    )
    age_group, mobility, flexibility = _validate_agent_profile(agent_profile, scenario_level)
    probability = compute_weather_cancel_probability(
        weather_type, purpose, age_group, mobility, flexibility, scenario_level,
    ) if exposed else 0.0
    draw = _stable_uniform(
        activity["agent_id"], activity["activity_id"], CONFIG.current_week,
        CONFIG.random_seed if seed is None else int(seed),
    )
    cancelled = exposed and draw < probability
    odds, utility = _preference_signal(weather_type, exposed and not cancelled, scenario_level)
    result.update({
        "weather_week": CONFIG.current_week,
        "weather_type": weather_type,
        "outbound_weather_exposed": exposed,
        "p_weather_cancel": probability,
        "weather_random_draw": draw,
        "weather_cancelled": cancelled,
        "activity_executes": not cancelled,
        "outbound_leg_executes": not cancelled,
        "return_leg_executes": not cancelled,
        "unmet_mandatory_trip": cancelled and purpose in {"work", "medical"},
        "ride_hailing_odds_multiplier": odds,
        "ride_hailing_utility_shift": utility,
        "mode_choice_applied": False,
    })
    return result


def apply_weather_disruption_before_mode_choice(
    activities: Sequence[Mapping[str, Any]],
    agent_profiles: Mapping[Any, Any],
    *,
    scenario_level: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return auditable decisions and only the activities eligible for leg/mode generation."""
    decisions: List[Dict[str, Any]] = []
    retained: List[Dict[str, Any]] = []
    for activity in activities:
        agent_id = activity.get("agent_id")
        if agent_id not in agent_profiles:
            raise ValueError(f"Missing agent profile for {agent_id}")
        decision = evaluate_planned_activity(
            activity, agent_profiles[agent_id], scenario_level=scenario_level, seed=seed,
        )
        decisions.append(decision)
        if decision["activity_executes"]:
            retained.append(decision)
    return {"activity_decisions": decisions, "retained_activities": retained}


def annotate_leg_with_weather(leg: Dict[str, Any]) -> Dict[str, Any]:
    """Annotate a leg for supply hand-off; this function never cancels an activity."""
    if "day" not in leg or "departure_time" not in leg:
        raise ValueError("Leg requires day and departure_time")
    active = any(
        event.overlaps(leg["day"], leg["departure_time"], leg.get("arrival_time"))
        for event in _active_windows()
    )
    weather_type = _weather_type()
    odds, utility = _preference_signal(weather_type, active)
    leg.update({
        "weather_week": CONFIG.current_week,
        "weather_type": weather_type,
        "weather_event_active": active,
        "ride_hailing_odds_multiplier": odds,
        "ride_hailing_utility_shift": utility,
        "mode_choice_applied": False,
    })
    return leg


def sample_weather_cancel_for_leg(leg: Dict[str, Any], agent_profile: Any) -> bool:
    """Compatibility adapter: sample an explicitly identified outbound leg once."""
    if leg.get("leg_role", "outbound") not in {"outbound", "between_activities"}:
        raise ValueError("T2 cancellation may only be evaluated on a planned outbound leg")
    activity = {
        "agent_id": leg.get("agent_id"),
        "activity_id": leg.get("activity_id"),
        "day_of_week": leg.get("day"),
        "activity_purpose": leg.get("purpose"),
        "planned_outbound_departure": leg.get("departure_time"),
        "planned_activity_arrival": leg.get("arrival_time", leg.get("departure_time")),
    }
    if activity["agent_id"] is None or activity["activity_id"] is None:
        raise ValueError("Stable T2 sampling requires agent_id and activity_id")
    decision = evaluate_planned_activity(activity, agent_profile)
    leg.update({key: value for key, value in decision.items() if key not in activity})
    leg["trip_continues"] = decision["activity_executes"]
    return bool(leg["trip_continues"])


def process_outbound_return(
    outbound: Dict[str, Any],
    ret: Dict[str, Any],
    agent_profile: Any,
    outbound_trip_completed: bool = False,
) -> Tuple[bool, Optional[bool]]:
    """Cancel from outbound exposure only; return weather cannot undo a completed activity."""
    outbound["leg_role"] = outbound.get("leg_role", "outbound")
    continues = sample_weather_cancel_for_leg(outbound, agent_profile)
    if not continues:
        ret.update({"trip_continues": False, "leg_executes": False, "invalidated_by_outbound": True})
        return False, False
    if not outbound_trip_completed:
        return True, None
    annotate_leg_with_weather(ret)
    ret.update({"trip_continues": True, "leg_executes": True, "invalidated_by_outbound": False})
    return True, True


def validate_parameter_ordering() -> None:
    purposes = ("medical", "work", "shopping", "social_leisure")
    for level in SCENARIO_LEVELS:
        values = [_purpose_modifier(purpose, level) for purpose in purposes]
        if not values[0] < values[1] < values[2] < values[3]:
            raise ValueError(f"Invalid purpose ordering in {level}")
        heat = _scenario_parameters(level)["weather_cancel_rate_base"]["extreme_heat"]
        rain = _scenario_parameters(level)["weather_cancel_rate_base"]["heavy_rain"]
        if not heat < rain:
            raise ValueError(f"Heavy rain must exceed heat in {level}")


validate_parameter_ordering()
