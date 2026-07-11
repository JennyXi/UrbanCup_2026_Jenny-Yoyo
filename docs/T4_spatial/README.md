# T4 - Nine-Zone Synthetic Spatial Configuration

## Scope

This module derives and audits a Shanghai-referenced synthetic nine-zone city.
It does not reproduce Shanghai administrative geography and does not generate
agents, home zones, destinations, OD pairs, trips, travel modes, prices,
dispatch outcomes, weather effects, or congestion.

## Configuration boundary

All model assumptions are stored in
`config/shanghai_synthetic_city.json`, including:

- concentric-ring radii;
- `modeled_coverage_share`;
- within-ring representative-area allocations;
- centroid radius and angular position;
- relative residential density;
- base zone age composition;
- citywide age target and tolerance.

`modeled_coverage_share` is the share of theoretical ring land represented by
the nine-zone model. It is neither a real built-up-area share nor an empirical
quantity derived from concentric-circle theory.

## Representative area and distance nodes

Theoretical and represented ring areas are:

```text
theoretical_ring_area = pi * (outer_radius^2 - inner_radius^2)
represented_ring_area = theoretical_ring_area * modeled_coverage_share
synthetic_area = represented_ring_area * within_ring_area_share
```

`synthetic_area` is representative functional-zone area used for population
capacity and a baseline intrazonal-distance estimate. A zone centroid is the
distance-computation node. Together they do not define a strict circular
administrative boundary, and equivalent-radius circles need not fit entirely
inside their theoretical rings.

Centroids are always derived at full floating-point precision from configured
radius and angle:

```text
x = r * cos(radians(theta))
y = r * sin(radians(theta))
```

Displayed rounded coordinates are never reused for computation.

Interzonal Euclidean distance uses centroids. Synthetic area is not a commuting
distance. A later OD layer may combine centroid distance with other explicit
rules, but this module does not produce commuting trips.

## Population capacity and dynamic age calibration

```text
residential_population_capacity = synthetic_area * residential_density_factor
population_weight = capacity / sum(capacity)
```

The density factor is dimensionless relative residential carrying capacity,
not people per square kilometre and not building, employment, or road density.

The configuration stores `base_age_composition` and `citywide_age_target`.
Calibrated zone shares are never hard-coded. They are recomputed from the
current population weights using one explicit uniform correction per age
group. An infeasible correction outside `[0, 1]` raises `ValueError`; the
module never clips values or silently changes calibration methods.

## Integer quota interface

```python
allocate_zone_age_quotas(derived_config, total_agents=None) -> dict
```

An explicit total overrides the configuration value. If neither is available,
the function raises. Audit output records the total and whether it came from
the configuration or the explicit argument.

The quota matrix uses deterministic two-dimensional controlled rounding. Zone
row totals, citywide age column totals, non-negativity, integer type, and the
grand total are all enforced exactly. Small populations are allowed to contain
zero cells; full three-age mixing is an audit result for the current 1000-agent
configuration, not a universal constraint.

## Automatic audit

`build_spatial_audit()` reports:

- equivalent radius and baseline mean intrazonal distance;
- the full zone-to-zone Euclidean distance matrix;
- minimum non-zero interzonal distance;
- area and population-weight orderings by zone and ring;
- full-precision citywide age consistency;
- dynamically regenerated zone-by-age integer quotas;
- the maximum/minimum zone-area ratio.

The current area ratio of about 4.96 is a soft model-complexity diagnostic used
to detect order-of-magnitude imbalance. It is not a validity threshold or an
urban-theory constant.

The expected descending ring-area order is:

```text
middle > peripheral > suburban > inner > core
```

The expected descending ring population-weight order is:

```text
middle > inner > suburban > peripheral > core
```

## Public functions

```text
load_zone_configuration
validate_zone_configuration
derive_spatial_configuration
calibrate_age_composition
allocate_zone_age_quotas
build_spatial_audit
```

These functions operate on configuration and aggregate quota data only. They
do not assign any individual Agent to a zone.

