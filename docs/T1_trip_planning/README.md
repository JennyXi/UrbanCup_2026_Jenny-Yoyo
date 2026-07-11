T1 - Population and Seven-Day Trip Planning

What this module covers:
- Age-stratified agent population generation.
- Deterministic Monday-Sunday potential trip-plan generation.
- Distinct weekday/weekend activity patterns for ages 18-39, 40-59, and 60+.

Main inputs:
- Agent profiles with stable `agent_id` and `age_group` fields.
- A Monday 00:00 simulation-week start and a fixed random seed.

Main outputs:
- Generated agent profiles (`AgentProfile`).
- Potential outbound/return leg dictionaries with stable `trip_id` and `leg_id`,
  purpose, zones, planned datetimes, mandatory status, and baseline cancellation
  probability.

Time-field semantics:
- On the outbound leg, `planned_departure_datetime` is the planned departure
  time from the origin.
- On the return leg, `planned_departure_datetime` is the actual planned return
  departure time. It never reuses the outbound departure time.
- `planned_return_datetime` is an activity-level field shared by both legs and
  means the planned time at which the return leg departs. Therefore:
  `return.planned_departure_datetime == outbound.planned_return_datetime`.
- A later weather layer must evaluate each leg using that leg's own
  `planned_departure_datetime`.

Reproducibility semantics:
- Each Agent receives a separate deterministic random source derived from the
  fixed `random_seed` and stable `agent_id`.
- Reordering the input Agent list may reorder the combined output list, but it
  cannot change any individual Agent's trips, IDs, purposes, or times.

Completed:
- `custom/agents/agent_population.py` provides `generate_population_agents()` and `summarize_population()`.
- `custom/agents/trip_planning.py` provides `generate_weekly_trip_plan()`,
  `generate_seven_day_trip_plans()`, and `validate_trip_plan()`.
- Each activity produces exactly one outbound and one return leg.
- Plans are reproducible for a fixed seed and reject overlapping trips.

To be calibrated / not implemented:
- Baseline cancellation probabilities and activity-pattern weights require calibration.
- Weather cancellation, subsidies, mode choice, pricing, order creation, dispatch,
  waiting time, and congestion are intentionally not implemented in T1.
