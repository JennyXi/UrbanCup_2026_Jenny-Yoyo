# T3 - Subsidy Policy Rules

## Scope

T3 is the pure policy-rule layer for mobility subsidies and elder dispatch
eligibility. It evaluates one potential trip leg after T2. A leg enters T3
only when `trip_continues` is `True`; otherwise the module returns `None` and
does not grant a discount or dispatch priority.

Weather, policy, and discount level remain separate experiment dimensions:

- `weather_scenario`: `W0`, `W1`, `W2` (managed outside T3)
- `policy_scenario`: `P0`, `P1`, `P2`, `P3`, `P4`
- `discount_level`: `low`, `high`, or `None`

T3 does not create compound policy names such as `P1_low` or `W1_P1_high`.

## Policy rules

| Policy | Coverage and access | Price discount | Dispatch priority |
| --- | --- | --- | --- |
| P0 | No coupon or policy-provided channel | No | No |
| P1 | Digital-access agents; coupon must be seen and claimed; self-service online ride-hailing only | Eligible when all P1 conditions hold and weekly use count is below 3 | No |
| P2 | Digital-access agents; automatic credit; self-service online ride-hailing only | Eligible when independent ride-hailing is possible and weekly use count is below 3 | No |
| P3 | All agents; ideal inclusive upper-bound scenario with self-service online, family-assisted, phone, and community channels | Eligible while weekly use count is below 3 | No |
| P4 | No coupon and no new access channel | No | Boolean elder eligibility only |

P3 represents an ideal upper bound after major digital and channel barriers
are removed. It does not assert that phone and community channels are already
universally available in reality.

P4 is strictly "no discount + elder dispatch protection." It does not change
price, add ride-hailing access, guarantee dispatch success, add driver
capacity, or shorten waiting time.

## Public interface

```python
evaluate_t3_policy(
    agent,
    leg,
    *,
    policy_scenario,
    discount_level=None,
    discount_amount_low=None,
    discount_amount_high=None,
    weekly_discount_use_count=None,
    random_seed=None,
) -> Optional[dict]
```

Agent fields are read only when required by the active policy:

- `agent_id`
- `is_elder`
- `digital_access`
- `independent_ride_hailing`
- `coupon_awareness_probability`
- `coupon_claim_probability`

Leg fields:

- `leg_id`
- `trip_continues`

For an active leg, the returned dictionary contains exactly:

- `policy_scenario`
- `discount_level`
- `discount_amount`
- `coupon_eligible`
- `coupon_seen`
- `coupon_claimed`
- `access_channel`
- `price_discount_eligible`
- `dispatch_priority_eligible`

## Deterministic P1 sampling

P1 coupon awareness and claiming use stable SHA-256-based sampling derived
from:

```text
random_seed + agent_id + leg_id + policy_scenario
```

The key excludes both `weather_scenario` and `discount_level`. Consequently,
P1 low/high experiments share the same `coupon_seen` and `coupon_claimed`
results, repeated calls are consistent, and results do not depend on an
in-memory cache.

P1 state semantics:

- `None`: the stage was not entered or does not apply
- `False`: the stage was entered and failed
- `True`: the stage was entered and succeeded

## Discount amount and weekly limit

P1, P2, and P3 share the configured fixed amounts for `low` and `high`. T3
outputs the selected amount but does not calculate the final fare.

T3 reads `weekly_discount_use_count` and blocks price eligibility when the
count is 3 or greater. It does not update the count. A later execution layer
must increment it only after ride-hailing is selected, dispatch succeeds, and
the discount is actually applied.

## Validation

Required probabilities must be finite numeric values in `[0, 1]`. Required
discount amounts must be finite non-negative numbers. The weekly use count
must be a non-negative integer. Missing values, string placeholders, invalid
types, and out-of-range values raise `ValueError` when the active policy needs
them. P0 and P4 do not read discount configuration or the weekly use count.

## Explicitly out of scope

T3 does not implement:

- final mode choice
- ride-hailing order creation
- dispatch success or failure
- fare calculation or `discount_applied`
- coupon redemption probability
- weekly discount-use count updates
- waiting time, driver capacity, or congestion

## Tests

Run the T3 unit tests from the repository root:

```text
python -B -X utf8 -m unittest tests.test_policy_t3 -v
```

