T2 - Weather Rules

What this module covers:
- Weather scenario identification, event-window detection, weather-driven trip cancellation sampling, ride-hailing preference shifts, and mode time multipliers.

Main inputs:
- Potential leg dicts with `day`, `departure_time`, `purpose`, and agent `age_group` (or agent_profile supplying `age_group`).
- Scenario configuration in `custom/envs/weather.py` -> `CONFIG`.

Main outputs (only these fields are added to each leg):
- `weather_week`, `weather_type`, `weather_event_active`, `trip_continues`,
  `ride_hailing_preference_shift`, `bus_time_multiplier`, `ride_hailing_time_multiplier`.

Completed:
- `custom/envs/weather.py` implements annotation, deterministic RNG sampling, outbound-return dependency handling, and aggregation utilities.

To be calibrated / not implemented:
- Numeric calibration of cancel-rate bases, purpose modifiers, age modifiers, and time multipliers.
- Mode choice, order creation, dispatch, or congestion dynamics (intentionally out of scope).
