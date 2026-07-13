T2 - Weather Rules

What this module covers:
- Weather scenario identification, event-window detection, weather-driven trip cancellation sampling, and ride-hailing preference shifts.

Main inputs:
- Potential leg dicts with `day`, `departure_time`, `purpose`, and agent `age_group` (or agent_profile supplying `age_group`).
- Scenario configuration in `custom/envs/weather.py` -> `CONFIG`.

Main outputs (only these fields are added to each leg):
- `weather_week`, `weather_type`, `weather_event_active`, `trip_continues`,
  `ride_hailing_preference_shift`.

Completed:
- `custom/envs/weather.py` implements annotation, deterministic RNG sampling, outbound-return dependency handling, and aggregation utilities.

To be calibrated / not implemented:
- Numeric calibration of cancel-rate bases, purpose modifiers, and age modifiers.
- Mode choice, order creation, dispatch, or congestion dynamics (intentionally out of scope).

Transport-supply ownership:
- T2 supplies scenario identity and event windows only.
- Weather speed and road-capacity multipliers are defined once in
  `config/weather_transport_supply.json` and applied by
  `custom/transport/weather_supply.py`.
- T2 does not emit transport time/speed multipliers, preventing weather effects
  from being applied twice.
