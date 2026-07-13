import unittest
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta

from custom.agents.agent_population import generate_population_agents
from custom.agents.leg_generation import HOME_ARRIVAL_DEADLINES, build_time_feasible_legs, sample_intrazonal_distance
from custom.agents.trip_planning import NON_WORK_DURATION_OPTIONS, generate_seven_day_activity_plans
from custom.spatial.destination_assignment import assign_destination_zones, effective_choice_distance, load_destination_configuration
from custom.spatial.home_zone_assignment import assign_home_zones
from custom.spatial.zone_configuration import allocate_zone_age_quotas, derive_spatial_configuration, load_zone_configuration
from custom.transport.network import build_transport_network, calculate_leg_mode_option


class LegGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spatial = derive_spatial_configuration(load_zone_configuration())
        cls.spatial_by_id = {zone["zone_id"]: zone for zone in cls.spatial["zones"]}
        population = generate_population_agents(50, seed=47)
        quotas = allocate_zone_age_quotas(cls.spatial, 50)["quota_matrix"]
        cls.agents = assign_home_zones(population, quotas, seed=47)
        baseline = generate_seven_day_activity_plans(cls.agents, datetime(2026, 7, 6), 47)
        assigned = assign_destination_zones(cls.agents, baseline, cls.spatial, load_destination_configuration(), 47)
        result = build_time_feasible_legs(cls.agents, assigned, cls.spatial_by_id)
        cls.activities = result["activities"]
        cls.legs = result["legs"]
        cls.transport_network = build_transport_network()

    def test_activity_identity_fields_match_agent_row_by_row(self):
        agents = {agent.agent_id: agent for agent in self.agents}
        for activity in self.activities:
            agent = agents[activity["agent_id"]]
            for field in ("home_zone", "age_group", "work_status", "medical_need_level"):
                self.assertEqual(activity[field], getattr(agent, field))

    def test_leg_time_identity_and_daily_continuity(self):
        grouped = defaultdict(list)
        for leg in self.legs:
            self.assertNotIn("effective_distance_km", leg)
            self.assertIn("euclidean_distance_km", leg)
            self.assertIn("road_network_distance_km", leg)
            self.assertEqual(
                leg["departure_time"] + timedelta(minutes=leg["travel_time_minutes"]),
                leg["arrival_time"],
            )
            grouped[(leg["agent_id"], leg["day"])].append(leg)
        for rows in grouped.values():
            rows.sort(key=lambda row: row["leg_sequence"])
            for previous, current in zip(rows, rows[1:]):
                self.assertEqual(previous["destination_zone"], current["origin_zone"])

    def test_long_cross_zone_trip_can_reach_ninety_minutes(self):
        travel_times = [
            leg["travel_time_minutes"]
            for leg in self.legs
            if leg["origin_zone"] != leg["destination_zone"]
        ]
        self.assertIn(90, travel_times)
        self.assertTrue(all(10 <= minutes <= 90 and minutes % 5 == 0 for minutes in travel_times))

    def test_leg_and_mode_option_share_the_same_road_path_distance(self):
        for leg in self.legs:
            option = calculate_leg_mode_option(self.transport_network, leg, "ride_hailing", seed=47)
            self.assertEqual(option["road_network_distance_km"], leg["road_network_distance_km"])

    def test_intrazonal_distance_is_seeded_by_leg_and_purpose(self):
        def samples(zone, purpose, count=1200):
            return [
                sample_intrazonal_distance(
                    zone, purpose, self.spatial_by_id, seed=47, agent_id="sample-agent",
                    origin_location_key="sample-agent:home",
                    destination_location_key=f"sample-agent:{purpose}:{index}",
                )
                for index in range(count)
            ]

        shopping = samples("Z3", "shopping")
        social = samples("Z3", "social_leisure")
        medical = samples("Z3", "medical")
        family = samples("Z3", "visit")
        work = samples("Z3", "work")
        self.assertLess(sum(shopping) / len(shopping), sum(social) / len(social))
        self.assertLess(sum(social) / len(social), sum(medical) / len(medical))
        self.assertLess(sum(social) / len(social), sum(work) / len(work))
        self.assertGreater(max(family) - min(family), max(shopping) - min(shopping))
        self.assertTrue(all(0.5 <= value <= 20.0 for values in (shopping, social, medical, family, work) for value in values))
        self.assertEqual(shopping, samples("Z3", "shopping"))
        self.assertGreater(len({round(value, 3) for value in shopping}), 100)

    def test_large_zone_intrazonal_sample_mean_exceeds_small_zone(self):
        def average(zone):
            values = [
                sample_intrazonal_distance(
                    zone, "social_leisure", self.spatial_by_id, seed=47, agent_id="scale-test",
                    origin_location_key="scale-test:home",
                    destination_location_key=f"scale-test:activity:{index}",
                )
                for index in range(2000)
            ]
            return sum(values) / len(values)
        self.assertGreater(average("Z9"), average("Z1") * 1.2)

    def test_same_zone_legs_do_not_all_share_the_zone_mean(self):
        same_zone = [leg["road_network_distance_km"] for leg in self.legs if leg["origin_zone"] == leg["destination_zone"]]
        self.assertGreater(len(set(same_zone)), 10)

    def test_same_activity_location_has_identical_outbound_and_return_road_distance(self):
        activity = deepcopy(next(item for item in self.activities if item["activity_purpose"] == "work"))
        agent = next(agent for agent in self.agents if agent.agent_id == activity["agent_id"])
        activity["destination_zone"] = agent.home_zone
        result = build_time_feasible_legs([agent], [activity], self.spatial_by_id, seed=47)
        self.assertEqual(len(result["legs"]), 2)
        outbound, inbound = result["legs"]
        self.assertEqual(outbound["euclidean_distance_km"], 0.0)
        self.assertEqual(inbound["euclidean_distance_km"], 0.0)
        self.assertEqual(outbound["road_network_distance_km"], inbound["road_network_distance_km"])

    def test_activity_intervals_leave_required_travel_gap(self):
        activities = {item["activity_id"]: item for item in self.activities}
        grouped = defaultdict(list)
        for leg in self.legs:
            grouped[(leg["agent_id"], leg["day"])].append(leg)
        for rows in grouped.values():
            rows.sort(key=lambda row: row["leg_sequence"])
            for previous_leg, next_leg in zip(rows, rows[1:]):
                if next_leg["leg_role"] == "return_home":
                    continue
                previous_activity = activities[previous_leg["activity_id"]]
                self.assertGreaterEqual(next_leg["departure_time"], previous_activity["planned_end_datetime"])

    def test_work_schedule_is_fixed_per_agent_and_in_bounds(self):
        grouped = defaultdict(set)
        for activity in self.activities:
            if activity["activity_purpose"] == "work":
                start = activity["planned_start_datetime"].time()
                end = activity["planned_end_datetime"].time()
                self.assertGreaterEqual(start.hour * 60 + start.minute, 8 * 60)
                self.assertLessEqual(start.hour * 60 + start.minute, 10 * 60 + 30)
                self.assertGreaterEqual(end.hour * 60 + end.minute, 17 * 60)
                self.assertLessEqual(end.hour * 60 + end.minute, 21 * 60 + 30)
                grouped[activity["agent_id"]].add((start, end, activity["destination_zone"]))
        self.assertTrue(grouped)
        self.assertTrue(all(len(values) == 1 for values in grouped.values()))

    def test_non_work_duration_contract_and_family_care_optional(self):
        allowed = {purpose: {minutes for minutes, _ in options} for purpose, options in NON_WORK_DURATION_OPTIONS.items()}
        for activity in self.activities:
            if activity["activity_purpose"] != "work":
                duration = int((activity["planned_end_datetime"] - activity["planned_start_datetime"]).total_seconds() / 60)
                self.assertIn(duration, allowed[activity["activity_purpose"]])
                self.assertGreaterEqual(duration, 30)
            if activity["activity_purpose"] == "out_of_home_family_care":
                self.assertFalse(activity["is_mandatory"])

    def test_shopping_respects_mall_opening_hours(self):
        shopping = [item for item in self.activities if item["activity_purpose"] == "shopping"]
        self.assertTrue(shopping)
        for activity in shopping:
            start = activity["planned_start_datetime"].time()
            end = activity["planned_end_datetime"].time()
            self.assertGreaterEqual(start.hour * 60 + start.minute, 10 * 60)
            self.assertLessEqual(end.hour * 60 + end.minute, 22 * 60)

    def test_age_specific_home_arrival_deadlines(self):
        age_by_agent = {agent.agent_id: agent.age_group for agent in self.agents}
        for leg in self.legs:
            if leg["leg_role"] != "return_home":
                continue
            age_group = age_by_agent[leg["agent_id"]]
            deadline = HOME_ARRIVAL_DEADLINES[age_group]
            arrival = leg["arrival_time"]
            if age_group == "18-39":
                self.assertLessEqual(arrival.date(), datetime.fromisoformat(leg["date"]).date() + timedelta(days=1))
                self.assertFalse(arrival.date() > datetime.fromisoformat(leg["date"]).date() and arrival.time() > deadline)
            else:
                self.assertLessEqual(arrival.time(), deadline)

    def test_optional_activity_with_next_day_start_is_cancelled_not_wrapped(self):
        agent = next(agent for agent in self.agents if agent.age_group == "18-39")
        activity = deepcopy(next(item for item in self.activities if item["agent_id"] == agent.agent_id))
        day = activity["planned_start_datetime"].date()
        activity.update({
            "activity_id": "bad-midnight-wrap",
            "activity_purpose": "social_leisure",
            "is_mandatory": False,
            "destination_zone": agent.home_zone,
            "planned_start_datetime": datetime.combine(day + timedelta(days=1), datetime.min.time()),
            "planned_end_datetime": datetime.combine(day, datetime.min.time().replace(hour=23, minute=30)),
        })
        result = build_time_feasible_legs([agent], [activity], self.spatial_by_id)
        self.assertEqual(result, {"activities": [], "legs": []})

    def test_optional_activity_over_eight_hours_is_retained_when_feasible(self):
        agent = next(agent for agent in self.agents if agent.age_group == "18-39")
        activity = deepcopy(next(item for item in self.activities if item["agent_id"] == agent.agent_id))
        day = activity["planned_start_datetime"].date()
        activity.update({
            "activity_id": "overlong-optional",
            "activity_purpose": "social_leisure",
            "is_mandatory": False,
            "destination_zone": agent.home_zone,
            "planned_start_datetime": datetime.combine(day, datetime.min.time().replace(hour=9)),
            "planned_end_datetime": datetime.combine(day, datetime.min.time().replace(hour=22)),
        })
        retained = build_time_feasible_legs([agent], [activity], self.spatial_by_id)["activities"]
        self.assertEqual(len(retained), 1)
        duration = retained[0]["planned_end_datetime"] - retained[0]["planned_start_datetime"]
        self.assertEqual(duration, timedelta(hours=13))

    def test_final_optional_activity_is_moved_or_shortened_before_elder_return_deadline(self):
        agent = next(agent for agent in self.agents if agent.age_group == "60+")
        activity = deepcopy(next(item for item in self.activities if item["agent_id"] == agent.agent_id))
        day = activity["planned_start_datetime"].date()
        farthest = max(
            self.spatial_by_id,
            key=lambda zone: effective_choice_distance(agent.home_zone, zone, self.spatial_by_id),
        )
        activity.update({
            "activity_id": "elder-late-optional",
            "activity_purpose": "social_leisure",
            "is_mandatory": False,
            "destination_zone": farthest,
            "planned_start_datetime": datetime.combine(day, datetime.min.time().replace(hour=18)),
            "planned_end_datetime": datetime.combine(day, datetime.min.time().replace(hour=19, minute=30)),
        })
        result = build_time_feasible_legs([agent], [activity], self.spatial_by_id)
        self.assertEqual(len(result["activities"]), 1)
        return_leg = next(leg for leg in result["legs"] if leg["leg_role"] == "return_home")
        self.assertLessEqual(return_leg["arrival_time"], datetime.combine(day, HOME_ARRIVAL_DEADLINES["60+"]))


if __name__ == "__main__":
    unittest.main()
