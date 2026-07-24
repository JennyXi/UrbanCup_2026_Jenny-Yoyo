# Seed 73 E01-E19 consolidated results

This directory consolidates the seed 73 workday mechanism experiment using 2000 Agents, 360 initial ride-hailing vehicles, `represented_trips_per_agent=3.0`, model `qwen3.6-35b-a3b-no-think`, concurrency 8, and progress interval 100.

## Availability

- Complete formal outputs with audit PASS: 18/19 scenarios.
- Incomplete: E11 (W1+C3). It stopped after 100/3839 decisions with 86 API failures and did not produce `summary.json`; it is excluded from formal comparisons.
- No missing result is filled with zero or inferred from another seed.
- E12 and E13 were run after E11 using the unchanged model structure and fixed experiment parameters.

## Key files

- `scenario_summary.csv`: all 19 scenario statuses and core system metrics; E11 is an explicit incomplete row.
- `scenario_detailed_summary.csv`: mode shares, waits, fallback, congestion, exposure, coupons, token use and elapsed time.
- `group_core_summary.csv`: age, digital-access, elder-status and elder access-group outcomes.
- `elder_nondigital_unassisted_summary.csv`: fixed 60+ non-digital, unassisted group.
- `policy_contrasts.csv`: weather and within-weather policy-minus-baseline descriptive contrasts.
- `p4_equity_comparison.csv`: same-seed E19-minus-E03 elder/nonelder allocation comparison with fixed supply.
- `validation_status.csv` and `validation_report.json`: reproducibility and file/invariant checks.

## Interpretation limits

The 2000 Agents are a computational mechanism sample, not a Shanghai population estimate or household survey. Seed 73 is not a real-world forecast. It must be paired with seed 47 to assess directional stability, and any directional disagreement must be reported as random sensitivity. The nine-zone simulated city is parameterized with Shanghai-informed population, function-zone and transport-supply features but is not a Shanghai replica.
