from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta
from functools import lru_cache

from custom.agents.formal_nine_zone_50_experiment import (
    _outdoor_segments,
    run_formal_nine_zone_50_experiment,
)
from custom.agents.formal_nine_zone_experiment import (
    _weather_adjusted_walk_access_minutes,
)
from custom.transport.network import build_transport_network


@lru_cache(maxsize=1)
def _w0_result():
    return run_formal_nine_zone_50_experiment(weather_scenarios=("W0",))


@lru_cache(maxsize=1)
def _paired_workday_result():
    return run_formal_nine_zone_50_experiment(
        weather_scenarios=("W0", "W2"), day_types=("workday",),
    )


class FormalNineZoneStructuralFixTests(unittest.TestCase):
    def test_w0_only_entry_and_mode_shares(self):
        result = _w0_result()
        self.assertEqual(
            {(row["weather_scenario"], row["day_type"]) for row in result["summary_rows"]},
            {("W0", "workday"), ("W0", "rest_day")},
        )
        for row in result["summary_rows"]:
            self.assertAlmostEqual(
                row["walking_mode_share"] + row["bus_mode_share"]
                + row["metro_mode_share"] + row["ride_hailing_mode_share"],
                1.0,
                places=5,
            )

    def test_metro_outputs_endpoint_access_and_uses_bus_feeder_when_needed(self):
        result = _w0_result()
        feeder_rows = []
        for row in result["mode_choices"]:
            self.assertIsInstance(row["metro_origin_accessible"], bool)
            self.assertIsInstance(row["metro_destination_accessible"], bool)
            if row["final_mode"] == "metro":
                if not row["metro_origin_accessible"]:
                    self.assertEqual(row["origin_feeder_mode"], "bus")
                if not row["metro_destination_accessible"]:
                    self.assertEqual(row["destination_feeder_mode"], "bus")
                if row["bus_metro_transfer_count"]:
                    feeder_rows.append(row)
                    self.assertGreater(row["feeder_bus_time_minutes"], 0)
                    self.assertGreater(row["feeder_bus_fare_yuan"], 0)
                self.assertTrue(row["itinerary_pattern"].startswith("walk-"))
                self.assertTrue(row["itinerary_pattern"].endswith("-walk"))
        self.assertTrue(feeder_rows)

    def test_selected_mode_time_determines_bounded_departure_and_arrival_fields(self):
        result = _w0_result()
        linkage = result["formal_config"]["activity_time_linkage"]
        max_early = float(linkage["maximum_early_departure_min"])
        tolerance = float(linkage["on_time_tolerance_min"])
        inbound = [
            row for row in result["mode_choices"]
            if row["transport_succeeded"] and row["leg_role"] != "return_home"
        ]
        self.assertTrue(inbound)
        for row in inbound:
            self.assertLessEqual(
                (row["planned_activity_start_time"] - row["departure_time"]).total_seconds() / 60.0,
                max_early + 1e-9,
            )
            expected_actual = row["departure_time"] + timedelta(minutes=row["total_travel_time_min"])
            self.assertAlmostEqual(
                (row["actual_arrival_time"] - expected_actual).total_seconds(), 0.0, delta=0.1,
            )
            self.assertTrue(math.isfinite(float(row["arrival_delay_minutes"])))
            self.assertEqual(
                row["on_time_arrival"], row["arrival_delay_minutes"] <= tolerance,
            )
            self.assertEqual(
                row["activity_completed"],
                not row["maximum_commute_time_exceeded"]
                and not row["maximum_lateness_exceeded"],
            )

    def test_transport_arrival_and_activity_completion_are_separate_outputs(self):
        result = _w0_result()
        late = [
            row for row in result["mode_choices"]
            if row["leg_role"] != "return_home" and row["transport_succeeded"]
            and not row["on_time_arrival"]
        ]
        self.assertTrue(late)
        self.assertTrue(any(row["activity_completed"] for row in late))
        self.assertTrue(any(not row["activity_completed"] for row in late))
        self.assertTrue(all(row["transport_succeeded"] for row in late))

    def test_bus_metro_itinerary_components_are_conserved(self):
        result = _w0_result()
        rows = [
            row for row in result["mode_choices"]
            if row["final_mode"] == "metro" and row["bus_metro_transfer_count"] > 0
        ]
        self.assertTrue(rows)
        for row in rows:
            self.assertAlmostEqual(
                row["total_travel_time_min"],
                row["access_time_min"] + row["wait_minutes"]
                + row["in_vehicle_time_min"] + row["transfer_time_min"],
                places=2,
            )
            self.assertLessEqual(row["feeder_bus_fare_yuan"], row["fare_yuan"])
            self.assertTrue(math.isfinite(float(row["metro_main_time_minutes"])))
            self.assertGreaterEqual(row["metro_main_time_minutes"], 0)

    def test_scheduled_bus_vehicle_trips_do_not_follow_passenger_choices(self):
        result = _paired_workday_result()
        values = {row["scheduled_bus_vehicle_trips"] for row in result["summary_rows"]}
        self.assertEqual(len(values), 1)

    def test_metro_platform_wait_is_not_outdoor_but_station_walk_is(self):
        departure = datetime(2026, 7, 7, 8, 0)
        row = {
            "departure_time": departure, "failed_attempt_consumed_minutes": 0.0,
            "transport_succeeded": True, "final_attempt_departure_time": departure,
            "final_mode": "metro", "bus_metro_transfer_count": 0,
            "origin_zone": "Z1", "destination_zone": "Z2",
            "access_time_min": 16.0, "wait_minutes": 5.0,
            "in_vehicle_time_min": 20.0, "transfer_time_min": 0.0,
            "total_travel_time_min": 41.0,
        }
        segments = _outdoor_segments(row, build_transport_network())
        self.assertAlmostEqual(sum(duration for _start, duration in segments), 16.0)

    def test_heavy_rain_slows_direct_walk_access_to_metro(self):
        start = datetime(2026, 7, 7, 8, 0)
        events = [{
            "weather_type": "heavy_rain",
            "start": datetime(2026, 7, 7, 7, 0),
            "end": datetime(2026, 7, 7, 10, 0),
        }]
        self.assertAlmostEqual(
            _weather_adjusted_walk_access_minutes(10.0, start, events), 12.5,
        )
        self.assertAlmostEqual(
            _weather_adjusted_walk_access_minutes(
                10.0, datetime(2026, 7, 7, 13, 0), events,
            ),
            10.0,
        )


if __name__ == "__main__":
    unittest.main()
