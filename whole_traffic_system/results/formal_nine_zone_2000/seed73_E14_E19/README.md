# Seed 73 E14-E19 platform results

All six scenarios use commit `66fc34790ef0589ae0e05039dbc674a4daccddb8`, 2000 Agents, workday, seed 73, 360 conserved ride-hailing vehicles, `represented_trips_per_agent=3.0`, model `qwen3.6-35b-a3b-no-think`, concurrency 8, and progress interval 100.

| Scenario | Condition | Status | Decisions | Ride requests | Success | Fail | Necessary completion | Mean travel min | API fallback |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| E14 | W1+D1 | PASS | 3839 | 518 | 518 | 0 | 0.998624 | 58.571 | 0 |
| E15 | W2+D1 | PASS_WITH_API_FALLBACKS | 3687 | 998 | 988 | 10 | 0.992435 | 59.780 | 1 |
| E16 | W0+D3 | PASS | 3914 | 404 | 404 | 0 | 0.998624 | 59.206 | 0 |
| E17 | W1+D3 | PASS | 3839 | 518 | 518 | 0 | 0.998624 | 58.471 | 0 |
| E18 | W2+D3 | PASS | 3687 | 996 | 982 | 14 | 0.992435 | 59.814 | 0 |
| E19 | W2+P4 | PASS_WITH_API_FALLBACKS | 3687 | 1001 | 985 | 16 | 0.993122 | 59.926 | 1 |

Total API usage: 11,164,735 input tokens + 883,445 output tokens = 12,048,180 tokens. Sum of per-scenario elapsed time: 8610.248 seconds.

Checks: every scenario has 2000 Agents, 360 unique final vehicle rows, the configured initial zone distribution, and 3.0 represented trips per Agent. D1 reaches 405/540 elder digital access (75%); D3 reaches 540/540 (100%); nonelder profile changes are zero. E15 and E19 each contain one API fallback and remain within the predeclared acceptance limit of five.

Interpretation limits: these are mechanism experiments, not a Shanghai population estimate or a real-world forecast. A single seed cannot establish external validity. Seed 47 and seed 73 should be paired by scenario, and directional disagreement must be reported as random sensitivity.

Local seed pairing is currently complete only for E14: the checked-in seed47 checkpoint contains 14 scenarios and does not include E15-E19. `seed47_seed73_pairing_status.csv` records this explicitly instead of silently treating missing seed47 outputs as zero.

P4 limitation: E14-E19 does not contain the seed73 W2+D0+P0 counterfactual. The P4 group table therefore reports who received rides, waits, failures and fallbacks under P4, but cannot causally label differences as elder benefit or nonelder crowd-out. That claim requires pairing E19 with E03 from the same seed and identical API/model setup.

Digital-access activity coverage: age-group and 60+ access-group activity metrics are complete. All-age `digital_access` activity rows are complete for elders via the intervention roster, but under-60 no-travel Agents lack a saved digital profile; their activity metrics are therefore marked as travel-profile-known only. Mode, wait, fallback and exposure metrics remain complete for all realized travel legs.

Files:

- `scenario_summary.csv`: compact required-run checks.
- `scenario_detailed_summary.csv`: mode shares, waits, fallback, congestion, exposure, tokens and timing.
- `group_summary.csv`: age, digital-access and elder access-group outcomes.
- `baseline_elder_focus_summary.csv`: fixed pre-policy 60+ non-digital, unassisted group outcomes.
- `policy_weather_contrasts.csv`: within-seed weather and D3-minus-D1 descriptive contrasts.
- `p4_dispatch_equity.csv`: P4 allocation composition with an explicit non-causal flag.
- `seed47_seed73_pairing_status.csv`: local availability and paired deltas where seed47 exists.
