import unittest
from pathlib import Path

from custom.agents.formal_nine_zone_experiment import _coupon_fare
from scripts.run_formal_nine_zone_50_coupon_pricing_experiment import (
    DEFAULT_CONFIG, _distance_band, _load, _request_segment_rows,
    _threshold_binding_passed,
)
from custom.agents.formal_nine_zone_experiment import (
    _age_transfer_burden_minutes,
    _conditional_fare_sensitivity_multiplier,
    _medical_need_exposure_weight,
)
from scripts.run_formal_nine_zone_50_coupon_experiment import _outcomes


class FormalNineZoneCouponPricingTests(unittest.TestCase):
    def test_percentage_flat_and_threshold_prices(self):
        percentage = _coupon_fare(40.0, {
            "_coupon_pricing": {
                "scheme_id": "K80", "pricing_type": "percentage",
                "discount_multiplier": 0.8,
            }
        }, True)
        flat = _coupon_fare(14.0, {
            "_coupon_pricing": {
                "scheme_id": "K5", "pricing_type": "flat_amount",
                "discount_amount_yuan": 5.0,
            }
        }, True)
        threshold = _coupon_fare(30.0, {
            "_coupon_pricing": {
                "scheme_id": "K30_5", "pricing_type": "threshold_flat",
                "minimum_original_fare_yuan": 30.0,
                "discount_amount_yuan": 5.0,
            }
        }, True)
        self.assertAlmostEqual(percentage["fare"], 32.0)
        self.assertAlmostEqual(flat["fare"], 9.0)
        self.assertAlmostEqual(threshold["fare"], 25.0)
        self.assertTrue(percentage["coupon_applied_to_choice"])
        self.assertTrue(flat["coupon_applied_to_choice"])
        self.assertTrue(threshold["coupon_applied_to_choice"])

    def test_below_threshold_does_not_bind_or_discount(self):
        result = _coupon_fare(29.99, {
            "_coupon_pricing": {
                "scheme_id": "K30_5", "pricing_type": "threshold_flat",
                "minimum_original_fare_yuan": 30.0,
                "discount_amount_yuan": 5.0,
            }
        }, True)
        self.assertEqual(result["fare"], 29.99)
        self.assertFalse(result["coupon_applied_to_choice"])
        self.assertFalse(result["coupon_fare_eligible"])
        self.assertEqual(result["coupon_subsidy_yuan"], 0.0)
        self.assertEqual(
            result["coupon_ineligibility_reason"],
            "minimum_original_fare_not_met",
        )

    def test_flat_coupon_never_produces_negative_fare(self):
        result = _coupon_fare(2.0, {
            "_coupon_pricing": {
                "scheme_id": "K5", "pricing_type": "flat_amount",
                "discount_amount_yuan": 5.0,
            }
        }, True)
        self.assertEqual(result["fare"], 0.0)
        self.assertEqual(result["coupon_subsidy_yuan"], 2.0)

    def test_percentage_coupon_respects_absolute_discount_cap(self):
        long_trip = _coupon_fare(50.0, {
            "_coupon_pricing": {
                "scheme_id": "B80_cap5", "pricing_type": "percentage",
                "discount_multiplier": 0.8,
                "maximum_discount_amount_yuan": 5.0,
            }
        }, True)
        short_trip = _coupon_fare(20.0, {
            "_coupon_pricing": {
                "scheme_id": "B80_cap5", "pricing_type": "percentage",
                "discount_multiplier": 0.8,
                "maximum_discount_amount_yuan": 5.0,
            }
        }, True)
        self.assertEqual(long_trip["fare"], 45.0)
        self.assertEqual(long_trip["coupon_subsidy_yuan"], 5.0)
        self.assertEqual(short_trip["fare"], 16.0)
        self.assertEqual(short_trip["coupon_subsidy_yuan"], 4.0)

    def test_unavailable_coupon_does_not_change_fare(self):
        result = _coupon_fare(40.0, {
            "_coupon_pricing": {
                "scheme_id": "K70", "pricing_type": "percentage",
                "discount_multiplier": 0.7,
            }
        }, False)
        self.assertEqual(result["fare"], 40.0)
        self.assertFalse(result["coupon_applied_to_choice"])

    def test_shared_k80_definition_and_three_seed_audit(self):
        config = _load(DEFAULT_CONFIG)
        self.assertEqual(config["seed_count"], 3)
        self.assertEqual(config["weather_scenarios"], ["W0", "W1", "W2"])
        self.assertEqual(config["allocation_policy"], "C3_mixed")
        self.assertEqual(
            config["experiment_sets"]["intensity"]["scenarios"]["K80_eight_tenths"],
            config["experiment_sets"]["format"]["scenarios"]["K80_eight_tenths"],
        )
        budget = config["experiment_sets"]["equal_budget"]
        self.assertEqual(budget["maximum_daily_subsidy_budget_yuan"], 50.0)
        self.assertEqual(budget["maximum_discount_amount_yuan_per_coupon"], 5.0)
        self.assertEqual(config["coupon_experiment"]["daily_total_coupon_pool"], 10)
        high_value = config["experiment_sets"]["high_value_format"]["scenarios"]
        self.assertEqual(high_value["H10_flat_ten"]["discount_amount_yuan"], 10.0)
        self.assertEqual(
            high_value["H50_10_threshold"]["minimum_original_fare_yuan"],
            50.0,
        )

    def test_200_agent_intensity_bridge_preserves_main_scale(self):
        path = Path(DEFAULT_CONFIG).with_name(
            "formal_nine_zone_200_coupon_sensitivity.json"
        )
        config = _load(path)
        self.assertEqual(config["scale_definition"]["agents"], 200)
        self.assertEqual(sum(config["initial_vehicles"].values()), 36)
        self.assertEqual(config["coupon_experiment"]["daily_total_coupon_pool"], 40)
        self.assertEqual(config["allocation_policy"], "C3_mixed")
        self.assertEqual(
            config["experiment_sets"]["intensity"]["scenarios"]
            ["K80_eight_tenths"]["discount_multiplier"],
            0.8,
        )
        high_value = config["experiment_sets"]["high_value_format"]["scenarios"]
        self.assertEqual(high_value["H10_flat_ten"]["discount_amount_yuan"], 10.0)
        self.assertEqual(
            high_value["H50_10_threshold"]["minimum_original_fare_yuan"],
            50.0,
        )

    def test_200_agent_elder_ride_preference_screen_changes_one_constant(self):
        path = Path(DEFAULT_CONFIG).with_name(
            "formal_nine_zone_200_elder_ride_preference_sensitivity.json"
        )
        config = _load(path)
        self.assertEqual(config["preference_scenarios"], {
            "A0_current_minus_0_5": -0.5,
            "A1_minus_0_3": -0.3,
            "A2_minus_0_1": -0.1,
            "A3_plus_0_1": 0.1,
            "A4_plus_0_3": 0.3,
        })
        self.assertEqual(sum(config["initial_vehicles"].values()), 36)
        self.assertEqual(config["seed_count"], 3)

        confirmation = _load(path.with_name(
            "formal_nine_zone_200_elder_ride_preference_confirmation.json"
        ))
        self.assertEqual(list(confirmation["preference_scenarios"].values()), [
            -0.1, 0.1, 0.3,
        ])
        self.assertEqual(confirmation["seed_count"], 10)
        self.assertEqual(confirmation["initial_vehicles"], config["initial_vehicles"])

        vulnerability = _load(path.with_name(
            "formal_nine_zone_200_elder_weather_vulnerability_sensitivity.json"
        ))
        self.assertEqual(
            vulnerability["sensitivity_parameter"],
            "elder_weather_exposure_choice_weight",
        )
        self.assertEqual(vulnerability["fixed_elder_ride_hailing_mode_constant"], 0.1)
        self.assertEqual(list(vulnerability["preference_scenarios"].values()), [
            1.3, 1.6, 2.0,
        ])

        medical = _load(path.with_name(
            "formal_nine_zone_200_elder_medical_need_exposure_sensitivity.json"
        ))
        self.assertEqual(
            medical["behavior_scenarios"]["M1_proposed"],
            {"low": 1.0, "standard": 1.2, "high": 1.5},
        )
        medical_confirmation = _load(path.with_name(
            "formal_nine_zone_200_elder_medical_need_exposure_confirmation.json"
        ))
        self.assertEqual(medical_confirmation["seed_count"], 10)
        self.assertEqual(
            list(medical_confirmation["behavior_scenarios"]),
            ["M0_no_medical_differentiation", "M1_proposed"],
        )
        combined = _load(path.with_name(
            "formal_nine_zone_200_elder_combined_behavior_smoke.json"
        ))
        self.assertEqual(combined["sensitivity_parameter"], "combined_elder_behavior")
        self.assertEqual(
            combined["behavior_scenarios"]["J1_candidate_plus_0_3"]
            ["ride_hailing_mode_constant"],
            0.3,
        )
        diagnostic = _load(path.with_name(
            "formal_nine_zone_200_elder_necessary_mode_diagnostic.json"
        ))
        self.assertTrue(diagnostic["write_option_audit"])
        self.assertEqual(len(diagnostic["behavior_scenarios"]), 1)

    def test_medical_need_weight_only_applies_to_elder_choice_exposure(self):
        exposure = {
            "medical_need_level_weight": {
                "low": 1.0, "standard": 1.2, "high": 1.5,
            }
        }
        self.assertEqual(_medical_need_exposure_weight(
            {"age_group": "60+", "medical_need_level": "high"}, exposure,
        ), 1.5)
        self.assertEqual(_medical_need_exposure_weight(
            {"age_group": "18-39", "medical_need_level": None}, exposure,
        ), 1.0)

    def test_conditional_elder_fare_sensitivity_has_strict_scope(self):
        choice = {"conditional_fare_sensitivity": {
            "enabled": True, "necessary_purposes": ["work", "medical"],
            "weather_types": ["extreme_heat", "heavy_rain"],
            "elder_exposed_necessary_multiplier": 0.8,
        }}
        elder = {"age_group": "60+"}
        self.assertEqual(_conditional_fare_sensitivity_multiplier(
            elder, {"purpose": "medical"}, "heavy_rain", choice,
        ), 0.8)
        self.assertEqual(_conditional_fare_sensitivity_multiplier(
            elder, {"purpose": "shopping"}, "heavy_rain", choice,
        ), 1.0)
        self.assertEqual(_conditional_fare_sensitivity_multiplier(
            {"age_group": "40-59"}, {"purpose": "medical"}, "heavy_rain", choice,
        ), 1.0)

    def test_age_transfer_burden_only_changes_perceived_transfer_time(self):
        choice = {
            "age_transfer_burden": {
                "enabled": True,
                "minutes_per_transfer_by_age": {
                    "18-39": 0.0, "40-59": 0.0, "60+": 3.0,
                },
            }
        }
        two_transfers = {"bus_metro_transfer_count": 2, "final_total_time_min": 50.0}
        direct_metro = {"bus_metro_transfer_count": 0, "final_total_time_min": 50.0}
        self.assertEqual(
            _age_transfer_burden_minutes({"age_group": "60+"}, two_transfers, choice),
            6.0,
        )
        self.assertEqual(
            _age_transfer_burden_minutes({"age_group": "40-59"}, two_transfers, choice),
            0.0,
        )
        self.assertEqual(
            _age_transfer_burden_minutes({"age_group": "60+"}, direct_metro, choice),
            0.0,
        )
        self.assertEqual(two_transfers["final_total_time_min"], 50.0)

    def test_ineligible_request_is_not_reported_as_no_request(self):
        allocation = {
            "agent_id": 1, "coupon_awarded": True,
        }
        choice = {
            "agent_id": 1, "weather_scenario": "W2", "coupon_bound": False,
            "primary_mode": "ride_hailing", "departure_time": "08:00",
            "leg_id": "L1",
            "coupon_ineligibility_reason": "minimum_original_fare_not_met",
        }
        outcome = _outcomes([allocation], [choice], "K30_5", "W2", 47)[0]
        self.assertEqual(outcome["coupon_status"], "unused_ineligible_ride_request")
        self.assertEqual(outcome["ineligible_ride_request_count"], 1)
        self.assertFalse(outcome["coupon_redeemed"])

    def test_distance_band_boundaries_are_explicit(self):
        config = _load(DEFAULT_CONFIG)
        self.assertEqual(_distance_band(5.0, config), "short_le_5km")
        self.assertEqual(_distance_band(5.001, config), "medium_5_10km")
        self.assertEqual(_distance_band(10.0, config), "medium_5_10km")
        self.assertEqual(_distance_band(10.001, config), "long_gt_10km")

    def test_threshold_audit_uses_primary_ride_fare_after_fallback(self):
        choice = {
            "coupon_bound": True,
            "primary_fare_before_coupon_yuan": 33.12,
            "fare_before_coupon_yuan": 2.0,
        }
        self.assertTrue(_threshold_binding_passed([choice], 30.0))

    def test_request_segments_use_original_fare_and_absolute_benefit(self):
        config = _load(DEFAULT_CONFIG)
        dispatch = [{
            "seed": 47, "policy": "K80_eight_tenths",
            "weather_scenario": "W0", "request_network_distance_km": 12.0,
            "origin_zone": "Z2", "destination_zone": "Z1",
            "purpose": "work",
            "succeeded": True, "coupon_bound": True,
            "coupon_induced_request": True, "fare_before_coupon_yuan": 38.3,
            "fare_after_coupon_yuan": 30.64, "coupon_subsidy_yuan": 7.66,
            "pickup_wait_min": 6.0,
        }]
        rows = _request_segment_rows(dispatch, config, "intensity")
        row = next(
            item for item in rows
            if item["seed"] == 47
            and item["coupon_scenario"] == "K80_eight_tenths"
            and item["weather_scenario"] == "W0"
            and item["segment_dimension"] == "distance_band"
            and item["segment"] == "long_gt_10km"
        )
        self.assertEqual(row["ride_hailing_requests"], 1)
        self.assertEqual(row["mean_original_fare_yuan"], 38.3)
        self.assertEqual(row["mean_offered_discount_yuan_per_bound_request"], 7.66)
        self.assertEqual(row["total_realized_subsidy_yuan"], 7.66)
        self.assertEqual(row["coupon_induced_requests"], 1)
        work_row = next(
            item for item in rows
            if item["seed"] == 47
            and item["coupon_scenario"] == "K80_eight_tenths"
            and item["weather_scenario"] == "W0"
            and item["segment_dimension"] == "work_distance_band"
            and item["segment"] == "long_gt_10km"
        )
        self.assertEqual(work_row["ride_hailing_requests"], 1)


if __name__ == "__main__":
    unittest.main()
