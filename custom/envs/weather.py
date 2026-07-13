"""
T2 第一部分：天气场景识别与事件窗口判断（模块接口）

职责（仅此部分）:
- 识别当前实验周（W0/W1/W2）
- 读取 leg 的计划出发日期/时间，并判断是否落入天气事件窗口
- 设置 leg 的天气标签字段（weather_week, weather_type, weather_event_active）
- 暴露天气参数配置接口（占位、校验）

严格约束：本模块不执行取消抽样、不处理去返程依赖、不执行最终方式选择或任何派单/订单逻辑。
所有参数为占位（placeholder/to_be_calibrated）并集中在 `WeatherConfig` 中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import datetime


WEEK_LABELS = ("W0", "W1", "W2")

# Activity purposes are collapsed only for weather-cancellation sensitivity.
# The baseline activity records retain their detailed purpose labels.
WEATHER_PURPOSE_BY_ACTIVITY = {
    "work": "work",
    "medical": "medical",
    "shopping": "daily",
    "social_leisure": "daily",
    "visit": "daily",
    "out_of_home_family_care": "daily",
    "out_of_home_family_activity": "daily",
}


def map_activity_to_weather_purpose(activity_purpose: str) -> str:
    """Map a modeled activity type to work/medical/daily weather behavior."""
    try:
        return WEATHER_PURPOSE_BY_ACTIVITY[activity_purpose]
    except KeyError as exc:
        raise ValueError(f"Unsupported activity purpose for weather: {activity_purpose}") from exc


def _parse_time_hhmm(t: str) -> datetime.time:
    try:
        hh, mm = t.split(":")
        return datetime.time(int(hh), int(mm))
    except Exception as e:
        raise ValueError(f"Invalid time format '{t}', expected HH:MM") from e


@dataclass
class WeatherEvent:
    day: str  # e.g., 'Tuesday'
    start_time: str  # 'HH:MM'
    end_time: str  # 'HH:MM'

    def contains(self, day: str, departure_time: str) -> bool:
        """Check if given day and departure_time fall into this event window.

        Uses left-closed, right-open interval: [start_time, end_time)
        """
        if day != self.day:
            return False
        t = _parse_time_hhmm(departure_time)
        s = _parse_time_hhmm(self.start_time)
        e = _parse_time_hhmm(self.end_time)
        return (t >= s) and (t < e)

    def overlaps(self, day: str, departure_time: Any, arrival_time: Any = None) -> bool:
        """Return whether the actual leg interval intersects this event window."""
        if day != self.day:
            return False
        def clock(value: Any) -> datetime.time:
            if isinstance(value, datetime.datetime):
                return value.time()
            if isinstance(value, datetime.time):
                return value
            return _parse_time_hhmm(str(value)[-5:])
        departure = clock(departure_time)
        arrival = clock(arrival_time) if arrival_time is not None else departure
        start = _parse_time_hhmm(self.start_time)
        end = _parse_time_hhmm(self.end_time)
        if arrival_time is None or arrival == departure:
            return start <= departure < end
        return departure < end and arrival > start


@dataclass
class WeatherConfig:
    # week label: 'W0' | 'W1' | 'W2'
    current_week: str = "W0"

    # parameter placeholders (to be calibrated)
    weather_cancel_rate_base_extreme_heat: Any = field(default="to_be_calibrated")
    weather_cancel_rate_base_heavy_rain: Any = field(default="to_be_calibrated")

    cancel_rate_modifier_medical: Any = field(default="to_be_calibrated")
    cancel_rate_modifier_work: Any = field(default="to_be_calibrated")
    cancel_rate_modifier_daily: Any = field(default="to_be_calibrated")

    age_sensitivity_modifier_18_39: Any = field(default="to_be_calibrated")
    age_sensitivity_modifier_40_59: Any = field(default="to_be_calibrated")
    age_sensitivity_modifier_60_plus: Any = field(default="to_be_calibrated")

    ride_hailing_preference_shift_extreme_heat: Any = field(default="to_be_calibrated")
    ride_hailing_preference_shift_heavy_rain: Any = field(default="to_be_calibrated")

    bus_time_multiplier_heavy_rain: Any = field(default="to_be_calibrated")
    ride_hailing_time_multiplier_heavy_rain: Any = field(default="to_be_calibrated")

    # W1 windows are fixed per spec
    w1_windows: List[WeatherEvent] = field(default_factory=lambda: [
        WeatherEvent(day="Tuesday", start_time="11:00", end_time="18:00"),
        WeatherEvent(day="Wednesday", start_time="11:00", end_time="18:00"),
        WeatherEvent(day="Thursday", start_time="11:00", end_time="18:00"),
        WeatherEvent(day="Friday", start_time="11:00", end_time="18:00"),
        WeatherEvent(day="Saturday", start_time="11:00", end_time="18:00"),
    ])

    # W2 windows must be provided via configuration (three explicit events)
    w2_windows: List[WeatherEvent] = field(default_factory=list)

    random_seed: Optional[int] = None

    def validate(self) -> None:
        # validate week label
        if self.current_week not in WEEK_LABELS:
            raise ValueError(f"Unknown current_week: {self.current_week}")

        # validate W2 windows: must be 3 events and each have valid times
        for ev in self.w2_windows:
            s = _parse_time_hhmm(ev.start_time)
            e = _parse_time_hhmm(ev.end_time)
            if not (s < e):
                raise ValueError(f"W2 event start_time must be < end_time: {ev}")

        # constraint placeholders direction checks if numeric provided
        try:
            a = self.weather_cancel_rate_base_heavy_rain
            b = self.weather_cancel_rate_base_extreme_heat
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if not (a > b):
                    raise ValueError("Constraint violated: heavy_rain cancel base must be > extreme_heat")
        except Exception:
            # placeholders may not be numeric yet
            pass


# module-level config instance
CONFIG = WeatherConfig()


def set_week(week_label: str) -> None:
    if week_label not in WEEK_LABELS:
        raise ValueError(f"Invalid week label: {week_label}")
    CONFIG.current_week = week_label


def set_w2_windows(events: List[Tuple[str, str, str]]) -> None:
    """Set W2 windows from a list of tuples: (day, start_time, end_time).

    Example: [("Tuesday", "07:00", "10:00"), ...]
    """
    CONFIG.w2_windows = [WeatherEvent(day=d, start_time=s, end_time=e) for d, s, e in events]


def annotate_leg_with_weather(leg: Dict[str, Any]) -> Dict[str, Any]:
    """Annotate a leg dict with weather fields per T2 first-part rules.

    Expects leg to contain at least: 'day', 'departure_time'.
    Adds/overwrites: 'weather_week', 'weather_type', 'weather_event_active'.

    Does NOT perform cancellation sampling or set trip_continues.
    """
    # basic validation
    if 'day' not in leg:
        raise ValueError('Leg missing required field: day')
    if 'departure_time' not in leg:
        raise ValueError('Leg missing required field: departure_time')
    day = leg['day']
    dep = leg['departure_time']
    arrival = leg.get('arrival_time')

    # set week and type
    leg['weather_week'] = CONFIG.current_week
    if CONFIG.current_week == 'W0':
        leg['weather_type'] = 'normal'
        leg['weather_event_active'] = False
        # set neutral shifts/multipliers
        leg['ride_hailing_preference_shift'] = 0
        leg['bus_time_multiplier'] = 1.0
        leg['ride_hailing_time_multiplier'] = 1.0
        return leg

    if CONFIG.current_week == 'W1':
        leg['weather_type'] = 'extreme_heat'
        # check W1 windows
        active = any(ev.overlaps(day, dep, arrival) for ev in CONFIG.w1_windows)
        leg['weather_event_active'] = bool(active)
        # shifts: only active in event window
        if leg['weather_event_active']:
            leg['ride_hailing_preference_shift'] = CONFIG.ride_hailing_preference_shift_extreme_heat
        else:
            leg['ride_hailing_preference_shift'] = 0
        # time multipliers unchanged in W1
        leg['bus_time_multiplier'] = 1.0
        leg['ride_hailing_time_multiplier'] = 1.0
        return leg

    if CONFIG.current_week == 'W2':
        leg['weather_type'] = 'heavy_rain'
        # check configured W2 windows
        if not CONFIG.w2_windows:
            raise ValueError('W2 windows not configured (use set_w2_windows)')
        active = any(ev.overlaps(day, dep, arrival) for ev in CONFIG.w2_windows)
        leg['weather_event_active'] = bool(active)
        if leg['weather_event_active']:
            leg['ride_hailing_preference_shift'] = CONFIG.ride_hailing_preference_shift_heavy_rain
            leg['bus_time_multiplier'] = CONFIG.bus_time_multiplier_heavy_rain
            leg['ride_hailing_time_multiplier'] = CONFIG.ride_hailing_time_multiplier_heavy_rain
        else:
            leg['ride_hailing_preference_shift'] = 0
            leg['bus_time_multiplier'] = 1.0
            leg['ride_hailing_time_multiplier'] = 1.0
        return leg

    return leg


def get_config_placeholder_summary() -> Dict[str, Any]:
    """Return a summary of current config parameter placeholders for auditing."""
    return {
        'current_week': CONFIG.current_week,
        'w2_windows_count': len(CONFIG.w2_windows),
        'placeholders': {
            'weather_cancel_rate_base_extreme_heat': CONFIG.weather_cancel_rate_base_extreme_heat,
            'weather_cancel_rate_base_heavy_rain': CONFIG.weather_cancel_rate_base_heavy_rain,
            'cancel_rate_modifier_medical': CONFIG.cancel_rate_modifier_medical,
            'cancel_rate_modifier_work': CONFIG.cancel_rate_modifier_work,
            'cancel_rate_modifier_daily': CONFIG.cancel_rate_modifier_daily,
            'age_sensitivity_modifier_18_39': CONFIG.age_sensitivity_modifier_18_39,
            'age_sensitivity_modifier_40_59': CONFIG.age_sensitivity_modifier_40_59,
            'age_sensitivity_modifier_60_plus': CONFIG.age_sensitivity_modifier_60_plus,
            'ride_hailing_preference_shift_extreme_heat': CONFIG.ride_hailing_preference_shift_extreme_heat,
            'ride_hailing_preference_shift_heavy_rain': CONFIG.ride_hailing_preference_shift_heavy_rain,
            'bus_time_multiplier_heavy_rain': CONFIG.bus_time_multiplier_heavy_rain,
            'ride_hailing_time_multiplier_heavy_rain': CONFIG.ride_hailing_time_multiplier_heavy_rain,
        }
    }


def assert_no_internal_markers(leg: Dict[str, Any]) -> None:
    """Assert that leg dict does not contain internal markers (for final export validation)."""
    for mk in ('_weather_sampled', 'invalidated_by_outbound', 'awaits_outbound_completion'):
        if mk in leg:
            raise AssertionError(f"Internal marker present in leg: {mk}")


# --- T2 第二部分: 取消抽样、trip_continues 与 去返程依赖 ---

import random

# unified RNG for all sampling in this module
_RNG: random.Random = random.Random()

# internal per-leg runtime state (not written to leg). Keying strategy:
# - If leg contains stable 'leg_id', use that (recommended).
# - Otherwise use id(leg) as a best-effort runtime key. Caller should avoid
#   serializing/deserializing legs between annotate/sample calls when no leg_id.
_LEG_STATE: Dict[int, Dict[str, Any]] = {}


def _leg_key(leg: Dict[str, Any]) -> int:
    if isinstance(leg, dict) and 'leg_id' in leg:
        return hash(('leg', leg['leg_id']))
    return id(leg)


def _get_state(leg: Dict[str, Any], create: bool = True) -> Dict[str, Any]:
    k = _leg_key(leg)
    if k not in _LEG_STATE:
        if not create:
            return {}
        _LEG_STATE[k] = {}
    return _LEG_STATE[k]


def _set_state_field(leg: Dict[str, Any], name: str, value: Any) -> None:
    s = _get_state(leg, create=True)
    s[name] = value


def _get_state_field(leg: Dict[str, Any], name: str, default: Any = None) -> Any:
    s = _get_state(leg, create=False)
    return s.get(name, default)


def _clear_state(leg: Dict[str, Any]) -> None:
    k = _leg_key(leg)
    if k in _LEG_STATE:
        del _LEG_STATE[k]


def _ensure_no_internal_markers_in_leg(leg: Dict[str, Any]) -> None:
    # defensive: remove any internal markers if present in leg dict
    for mk in ('_weather_sampled', 'invalidated_by_outbound', 'awaits_outbound_completion'):
        if mk in leg:
            del leg[mk]


def init_rng(seed: Optional[int] = None) -> None:
    """Initialize the module-level RNG. Call once at simulation start for determinism."""
    if seed is not None:
        CONFIG.random_seed = seed
        _RNG.seed(seed)


def get_rng() -> random.Random:
    return _RNG


def _get_base_rate(weather_type: str) -> Any:
    if weather_type == "extreme_heat":
        return CONFIG.weather_cancel_rate_base_extreme_heat
    if weather_type == "heavy_rain":
        return CONFIG.weather_cancel_rate_base_heavy_rain
    raise ValueError(f"Unsupported weather_type for base rate: {weather_type}")


def _get_purpose_modifier(purpose: str) -> Any:
    purpose = purpose.lower()
    if purpose == "medical":
        return CONFIG.cancel_rate_modifier_medical
    if purpose == "work":
        return CONFIG.cancel_rate_modifier_work
    if purpose == "daily":
        return CONFIG.cancel_rate_modifier_daily
    raise ValueError(f"Unsupported purpose: {purpose}")


def _get_age_modifier(age_group: str) -> Any:
    if age_group == "18-39":
        return CONFIG.age_sensitivity_modifier_18_39
    if age_group == "40-59":
        return CONFIG.age_sensitivity_modifier_40_59
    if age_group == "60+":
        return CONFIG.age_sensitivity_modifier_60_plus
    # if unknown, treat as neutral placeholder
    return 1.0


def validate_purpose_ordering() -> None:
    """Ensure numeric purpose modifiers follow: medical < work < daily when numeric."""
    vals = [CONFIG.cancel_rate_modifier_medical, CONFIG.cancel_rate_modifier_work, CONFIG.cancel_rate_modifier_daily]
    if all(isinstance(v, (int, float)) for v in vals):
        if not (vals[0] < vals[1] < vals[2]):
            raise ValueError("Constraint violated: cancel_rate_modifier_medical < cancel_rate_modifier_work < cancel_rate_modifier_daily")


def _compute_p_weather_cancel(weather_type: str, purpose: str, age_group: str) -> Optional[float]:
    """Compute p_weather_cancel according to spec. Returns None if any parameter is non-numeric (placeholders)."""
    base = _get_base_rate(weather_type)
    purpose_mod = _get_purpose_modifier(purpose)
    age_mod = _get_age_modifier(age_group)

    if not all(isinstance(x, (int, float)) for x in (base, purpose_mod, age_mod)):
        # Do not silently degrade to 0. Require numeric parameters for sampling.
        return None

    p = base * purpose_mod * age_mod
    # clamp to [0,1]
    if p < 0:
        p = 0.0
    if p > 1:
        p = 1.0
    return float(p)


def validate_heavy_vs_heat_stronger() -> None:
    """Validate that for every age_group×purpose, heavy_rain cancel prob > extreme_heat cancel prob when numeric."""
    age_groups = ["18-39", "40-59", "60+"]
    purposes = ["work", "medical", "daily"]
    for ag in age_groups:
        for pu in purposes:
            p_heavy = _compute_p_weather_cancel("heavy_rain", pu, ag)
            p_heat = _compute_p_weather_cancel("extreme_heat", pu, ag)
            if p_heavy is None or p_heat is None:
                # placeholders present; cannot validate numerically
                continue
            if not (p_heavy > p_heat):
                raise ValueError(f"Constraint violated for {ag}×{pu}: p_heavy_rain ({p_heavy}) must be > p_extreme_heat ({p_heat})")


def sample_weather_cancel_for_leg(leg: Dict[str, Any], agent_profile: Any) -> bool:
    """Perform one-time weather cancel sampling for a leg.

    Modifies leg in-place: sets 'trip_continues' (bool) and internal marker '_weather_sampled'.

    Returns trip_continues.

    Expects leg to contain 'weather_event_active' (bool), 'weather_type' ('extreme_heat'|'heavy_rain'|'normal'),
    'purpose' ('work'|'medical'|'daily'), and agent_profile to expose 'age_group'.
    """
    # Precondition checks
    if _get_state_field(leg, '_weather_sampled', False):
        # already sampled; do not re-sample
        return bool(leg.get('trip_continues', True))

    if not leg.get('weather_event_active', False):
        leg['trip_continues'] = True
        _set_state_field(leg, '_weather_sampled', True)
        # ensure output fields exist
        leg.setdefault('ride_hailing_preference_shift', 0)
        leg.setdefault('bus_time_multiplier', 1.0)
        leg.setdefault('ride_hailing_time_multiplier', 1.0)
        return True

    weather_type = leg.get('weather_type')
    if weather_type not in ("extreme_heat", "heavy_rain"):
        raise ValueError(f"Unsupported or missing weather_type for sampling: {weather_type}")

    purpose = leg.get('purpose')
    if purpose is None:
        raise ValueError('Leg missing purpose for weather cancel sampling')

    # get agent age_group
    age_group = getattr(agent_profile, 'age_group', None) if not isinstance(agent_profile, dict) else agent_profile.get('age_group')
    if age_group is None:
        raise ValueError('agent_profile missing age_group')

    # validate ordering when numeric
    validate_purpose_ordering()
    validate_heavy_vs_heat_stronger()

    p = _compute_p_weather_cancel(weather_type, purpose, age_group)
    if p is None:
        raise ValueError("Cannot compute weather cancel probability: one or more parameters are non-numeric. Please set numeric values in CONFIG before sampling.")

    # sample once using module RNG
    u = _RNG.random()
    cont = (u >= p)
    leg['trip_continues'] = bool(cont)
    # mark sampled to avoid duplicate sampling
    _set_state_field(leg, '_weather_sampled', True)
    # ensure ride_hailing_preference_shift and multipliers exist (safety)
    leg.setdefault('ride_hailing_preference_shift', 0)
    leg.setdefault('bus_time_multiplier', 1.0)
    leg.setdefault('ride_hailing_time_multiplier', 1.0)
    return leg['trip_continues']


def process_outbound_return(outbound: Dict[str, Any], ret: Dict[str, Any], agent_profile: Any, outbound_trip_completed: bool = False) -> Tuple[bool, Optional[bool]]:
    """Process outbound and return legs per spec.

    - Samples outbound always (if in event window).
    - If outbound.trip_continues == False: invalidate return (do not check return weather).
    - If outbound.trip_continues == True and outbound_trip_completed == True: process return sampling.
    - If outbound.trip_continues == True and outbound_trip_completed == False: defer return sampling and mark it awaits completion.

    Returns tuple (outbound_continues, return_continues_or_None).
    """
    # If caller provided a return leg and outbound is not yet completed, the return must NOT be pre-annotated.
    # Enforce calling order to avoid annotate-then-rollback patterns.
    if not outbound_trip_completed:
        # check if ret already contains any weather output fields
        for fld in ('weather_week','weather_type','weather_event_active','ride_hailing_preference_shift','bus_time_multiplier','ride_hailing_time_multiplier','trip_continues'):
            if fld in ret:
                raise ValueError('Return leg must not be annotated before outbound completion; call process_outbound_return after outbound completion or omit pre-annotation of return')

    # Process outbound first (annotate+sample). Caller must not pre-annotate return when outbound not completed.
    ob_cont = sample_weather_cancel_for_leg(outbound, agent_profile)
    if not ob_cont:
        # invalidate return per rules: do NOT annotate or modify return's weather fields.
        # store invalidation in internal state instead of writing to leg
        _set_state_field(ret, 'invalidated_by_outbound', True)
        _set_state_field(ret, '_weather_sampled', False)
        # ensure final exported leg does not carry internal markers
        _ensure_no_internal_markers_in_leg(ret)
        return False, False

    # outbound continues (not cancelled by weather)
    if not outbound_trip_completed:
        # cannot activate return yet; leave return untouched and mark in internal state
        _set_state_field(ret, 'awaits_outbound_completion', True)
        return True, None

    # outbound completed -> activate and process return
    _set_state_field(ret, 'awaits_outbound_completion', False)
    # annotate and sample return now (caller should not have annotated earlier when outbound not completed)
    annotate_leg_with_weather(ret)
    r_cont = sample_weather_cancel_for_leg(ret, agent_profile)
    return True, r_cont


def compute_trip_continue_rates(legs: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, int]]:
    """Compute trip_continue_rate per (weather_type, age_group, purpose).

    Returns mapping: (weather_type, age_group, purpose) -> {'continued': int, 'eligible': int}

    Denominator rules:
    - Include legs with weather_event_active == True
    - Exclude legs marked 'invalidated_by_outbound' or with 'awaits_outbound_completion' True
    """
    stats: Dict[Tuple[str, str, str], Dict[str, int]] = {}
    for leg in legs:
        if not leg.get('weather_event_active', False):
            continue
        if leg.get('invalidated_by_outbound', False):
            continue
        if leg.get('awaits_outbound_completion', False):
            continue

        wt = leg.get('weather_type')
        ag = leg.get('age_group') or leg.get('agent_age_group')
        pu = leg.get('purpose')
        if not (wt and ag and pu):
            continue
        key = (wt, ag, pu)
        if key not in stats:
            stats[key] = {'continued': 0, 'eligible': 0}
        stats[key]['eligible'] += 1
        if leg.get('trip_continues', False):
            stats[key]['continued'] += 1

    return stats

