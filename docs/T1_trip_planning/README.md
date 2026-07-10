T1 - Trip Planning

What this module covers:
- Agent population and trip-plan related utilities used for generating trip legs and age-stratified profiles.

Main inputs:
- Agent profiles with `age_group` fields; trip purpose and planned departure times.

Main outputs:
- Generated agent profiles (`AgentProfile`) and leg dictionaries with basic fields.

Completed:
- `custom/agents/agent_population.py` provides `generate_population_agents()` and `summarize_population()`.

To be calibrated / not implemented:
- Detailed per-person trip scheduling, modal choice, and calibration parameters.
