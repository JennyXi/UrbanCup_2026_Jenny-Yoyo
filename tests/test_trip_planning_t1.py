import unittest
from collections import Counter, defaultdict
from datetime import datetime

from custom.agents.agent_population import generate_population_agents
from custom.agents.trip_planning import (
    MEDICAL_WEEKLY_COUNT_OPTIONS,
    OUTPUT_FIELDS,
    generate_seven_day_activity_plans,
    generate_seven_day_activity_plans_with_audit,
    generate_weekly_activity_plan,
    generate_weekly_activity_plan_with_audit,
)
from custom.spatial.home_zone_assignment import assign_home_zones
from custom.spatial.zone_configuration import allocate_zone_age_quotas, derive_spatial_configuration, load_zone_configuration

WEEK_START = datetime(2026, 7, 6, 0, 0)


def placed_agent(agent_id, age_group, home_zone="Z1", work_status=None, medical_need_level=None):
    if work_status is None:
        work_status = "retired" if age_group == "60+" else "regular_worker"
    return {
        "agent_id": agent_id,
        "age_group": age_group,
        "home_zone": home_zone,
        "work_status": work_status,
        "medical_need_level": medical_need_level if age_group == "60+" else None,
        "digital_access": True,
        "independent_ride_hailing": True if age_group != "60+" else None,
    }


class BaselineActivityPlanningTests(unittest.TestCase):
    def test_fixed_seed_is_reproducible(self):
        agent = placed_agent(1, "18-39", "Z3")
        self.assertEqual(generate_weekly_activity_plan(agent, WEEK_START, 2026), generate_weekly_activity_plan(agent, WEEK_START, 2026))

    def test_agent_input_order_does_not_change_individual_plans(self):
        agents = [placed_agent(1, "18-39"), placed_agent(2, "40-59"), placed_agent(3, "60+", medical_need_level="standard")]
        first = generate_seven_day_activity_plans(agents, WEEK_START, 17)
        second = generate_seven_day_activity_plans(list(reversed(agents)), WEEK_START, 17)
        def grouped(records):
            result = defaultdict(list)
            for record in records:
                result[record["agent_id"]].append(record)
            return result
        self.assertEqual(grouped(first), grouped(second))

    def test_all_times_are_on_30_minute_grid_and_ordered_without_overlap(self):
        agents = [placed_agent(i, age, medical_need_level="standard" if age == "60+" else None) for i, age in enumerate(("18-39", "40-59", "60+"), 1)]
        activities = generate_seven_day_activity_plans(agents, WEEK_START, 29)
        grouped = defaultdict(list)
        for item in activities:
            self.assertIn(item["planned_start_datetime"].minute, {0, 30})
            self.assertIn(item["planned_end_datetime"].minute, {0, 30})
            grouped[item["agent_id"]].append(item)
        for records in grouped.values():
            self.assertEqual(records, sorted(records, key=lambda item: item["planned_start_datetime"]))
            for previous, current in zip(records, records[1:]):
                self.assertGreaterEqual(current["planned_start_datetime"], previous["planned_end_datetime"])

    def test_sequence_order_is_daily_contiguous_stable_and_time_sorted(self):
        agent = placed_agent(5, "18-39")
        first = generate_weekly_activity_plan(agent, WEEK_START, 29)
        second = generate_weekly_activity_plan(agent, WEEK_START, 29)
        self.assertEqual(first, second)
        grouped = defaultdict(list)
        for item in first:
            grouped[item["planned_start_datetime"].date()].append(item)
        for records in grouped.values():
            self.assertEqual([item["sequence_order"] for item in records], list(range(1, len(records) + 1)))
            self.assertEqual(records, sorted(records, key=lambda item: (item["planned_start_datetime"], item["planned_end_datetime"], item["activity_purpose"])))

    def test_candidate_slot_audit_balances_and_differs_from_empty_days(self):
        agent = placed_agent(7, "18-39")
        result = generate_weekly_activity_plan_with_audit(agent, WEEK_START, 31)
        audit = result["audit"]
        self.assertEqual(audit["modeled_activity_slot_count"] + audit["no_in_scope_slot_count"], audit["total_candidate_slots"])
        self.assertGreaterEqual(audit["fixed_activity_slot_count"], 5)
        self.assertGreater(audit["no_in_scope_slot_count"], audit["empty_agent_day_count"])
        self.assertEqual(audit["slot_breakdown"]["weekday_activity"]["total_candidate_slots"], 15)
        self.assertEqual(audit["slot_breakdown"]["weekend_activity"]["total_candidate_slots"], 8)
        aggregate = generate_seven_day_activity_plans_with_audit([agent, placed_agent(8, "40-59")], WEEK_START, 31)["audit"]
        self.assertEqual(aggregate["modeled_activity_slot_count"] + aggregate["no_in_scope_slot_count"], aggregate["total_candidate_slots"])

    def test_evening_activity_has_no_assumed_home_origin_or_leg(self):
        records = []
        for agent_id in range(1, 100):
            records.extend(generate_weekly_activity_plan(placed_agent(agent_id, "18-39"), WEEK_START, 31))
        evenings = [item for item in records if item["planned_start_datetime"].hour >= 18]
        self.assertTrue(evenings)
        for item in evenings:
            self.assertNotIn("origin_zone", item)
            self.assertNotIn("leg_id", item)
            self.assertIsNone(item["destination_zone"])

    def test_work_status_is_stable_for_the_whole_week(self):
        for status in ("regular_worker", "flexible_non_worker"):
            records = generate_weekly_activity_plan(placed_agent(11, "18-39", work_status=status), WEEK_START, 37)
            self.assertEqual({item["work_status"] for item in records}, {status} if records else set())

    def test_flexible_non_worker_has_no_fixed_work_and_keeps_digital_ability(self):
        agent = placed_agent(12, "18-39", work_status="flexible_non_worker")
        records = generate_weekly_activity_plan(agent, WEEK_START, 41)
        self.assertNotIn("work", {item["activity_purpose"] for item in records})
        self.assertTrue(agent["digital_access"])
        self.assertTrue(agent["independent_ride_hailing"])
        invalid = dict(agent, digital_access=False)
        with self.assertRaisesRegex(ValueError, "digital_access"):
            generate_weekly_activity_plan(invalid, WEEK_START, 41)

    def test_young_and_middle_weekends_can_be_no_in_scope(self):
        for age in ("18-39", "40-59"):
            found_empty_weekend_slot = False
            for agent_id in range(1, 100):
                records = generate_weekly_activity_plan(placed_agent(agent_id, age), WEEK_START, 43)
                weekend_days = {item["day_of_week"] for item in records if item["is_weekend"]}
                if len(weekend_days) < 2:
                    found_empty_weekend_slot = True
                    break
            self.assertTrue(found_empty_weekend_slot, age)

    def test_daily_activity_count_limits_and_weekend_four_activity_tail(self):
        observed_weekend_counts = set()
        for age in ("18-39", "40-59", "60+"):
            for agent_id in range(1, 400):
                records = generate_weekly_activity_plan(
                    placed_agent(agent_id, age, medical_need_level="standard" if age == "60+" else None),
                    WEEK_START,
                    45,
                )
                daily = Counter(item["day_of_week"] for item in records)
                self.assertTrue(all(daily[day] <= 3 for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")))
                self.assertTrue(all(daily[day] <= 4 for day in ("Saturday", "Sunday")))
                observed_weekend_counts.update(daily[day] for day in ("Saturday", "Sunday"))
        self.assertIn(4, observed_weekend_counts)

    def test_all_age_groups_can_emit_reasonable_optional_purposes(self):
        required = {
            "shopping", "social_leisure", "visit", "out_of_home_family_activity",
            "out_of_home_family_care", "medical",
        }
        for age in ("18-39", "40-59", "60+"):
            purposes = set()
            for agent_id in range(1, 500):
                records = generate_weekly_activity_plan(
                    placed_agent(agent_id, age, medical_need_level="standard" if age == "60+" else None),
                    WEEK_START,
                    49,
                )
                purposes.update(item["activity_purpose"] for item in records)
            self.assertTrue(required.issubset(purposes), (age, required - purposes))

    def test_legacy_social_and_leisure_are_never_emitted(self):
        activities = []
        for age in ("18-39", "40-59", "60+"):
            for agent_id in range(1, 200):
                activities.extend(generate_weekly_activity_plan(
                    placed_agent(agent_id, age, medical_need_level="standard" if age == "60+" else None),
                    WEEK_START,
                    51,
                ))
        purposes = {item["activity_purpose"] for item in activities}
        self.assertIn("social_leisure", purposes)
        self.assertTrue({"social", "leisure"}.isdisjoint(purposes))

    def test_standard_elder_has_at_most_two_nonconsecutive_medical_days(self):
        for agent_id in range(1, 100):
            records = generate_weekly_activity_plan(placed_agent(agent_id, "60+", medical_need_level="standard"), WEEK_START, 47)
            medical = [item for item in records if item["activity_purpose"] == "medical"]
            self.assertLessEqual(len(medical), 2)
            weekdays = [item["planned_start_datetime"].weekday() for item in medical]
            self.assertTrue(all(b - a > 1 for a, b in zip(weekdays, weekdays[1:])))

    def test_medical_need_levels_define_distributions_not_fixed_counts(self):
        expected = {"low": {0, 1}, "standard": {0, 1, 2}, "high": {1, 2, 3}}
        self.assertEqual({key: set(value) for key, value in MEDICAL_WEEKLY_COUNT_OPTIONS.items()}, expected)
        observed = {level: set() for level in expected}
        for level in expected:
            for seed in range(10, 30):
                for agent_id in range(1, 25):
                    records = generate_weekly_activity_plan(placed_agent(agent_id, "60+", medical_need_level=level), WEEK_START, seed)
                    observed[level].add(sum(item["activity_purpose"] == "medical" for item in records))
        self.assertEqual(observed, expected)

    def test_elder_part_time_work_schedule_is_stable_and_one_or_two_days(self):
        agent = placed_agent(21, "60+", work_status="part_time_worker", medical_need_level="standard")
        first = generate_weekly_activity_plan(agent, WEEK_START, 53)
        second = generate_weekly_activity_plan(agent, WEEK_START, 53)
        work_days = [item["day_of_week"] for item in first if item["activity_purpose"] == "work"]
        self.assertEqual(first, second)
        self.assertIn(len(work_days), {1, 2})

    def test_only_explicit_out_of_home_family_purposes_are_emitted(self):
        activities = []
        for agent_id in range(1, 150):
            activities.extend(generate_weekly_activity_plan(placed_agent(agent_id, "40-59"), WEEK_START, 59))
        purposes = {item["activity_purpose"] for item in activities}
        self.assertNotIn("family_care", purposes)
        self.assertNotIn("family_activity", purposes)
        self.assertTrue({"out_of_home_family_care", "out_of_home_family_activity"} & purposes)

    def test_weekend_start_times_have_reproducible_individual_variation(self):
        agents = [placed_agent(agent_id, "18-39") for agent_id in range(1, 80)]
        first = generate_seven_day_activity_plans(agents, WEEK_START, 61)
        second = generate_seven_day_activity_plans(agents, WEEK_START, 61)
        first_times = [item["planned_start_datetime"].time() for item in first if item["is_weekend"]]
        self.assertEqual(first, second)
        self.assertGreater(len(set(first_times)), 2)

    def test_real_home_zone_and_activity_only_output(self):
        activities = generate_weekly_activity_plan(placed_agent(1, "18-39", "Z7"), WEEK_START, 67)
        forbidden = {"trip_id", "leg_id", "is_outbound", "origin_zone", "distance", "mode"}
        self.assertTrue(all(tuple(item.keys()) == OUTPUT_FIELDS for item in activities))
        self.assertTrue(all(item["home_zone"] == "Z7" and item["destination_zone"] is None for item in activities))
        self.assertTrue(all(forbidden.isdisjoint(item) for item in activities))

    def test_non_monday_week_start_raises_with_date_and_weekday(self):
        invalid = datetime(2026, 7, 7, 0, 0)
        with self.assertRaisesRegex(ValueError, r"2026-07-07.*Tuesday"):
            generate_weekly_activity_plan(placed_agent(1, "18-39"), invalid, 67)

    def test_medical_remains_mandatory_with_one_percent_cancel_probability(self):
        found = []
        for agent_id in range(1, 50):
            found.extend(item for item in generate_weekly_activity_plan(placed_agent(agent_id, "60+", medical_need_level="standard"), WEEK_START, 71) if item["activity_purpose"] == "medical")
        self.assertTrue(found)
        self.assertTrue(all(item["is_mandatory"] and item["baseline_cancel_probability"] == 0.01 for item in found))

    def test_medical_activities_end_by_twenty_hundred(self):
        for age in ("18-39", "40-59", "60+"):
            for agent_id in range(1, 200):
                records = generate_weekly_activity_plan(
                    placed_agent(agent_id, age, medical_need_level="standard" if age == "60+" else None),
                    WEEK_START,
                    72,
                )
                for item in records:
                    if item["activity_purpose"] == "medical":
                        end = item["planned_end_datetime"].time()
                        self.assertLessEqual(end.hour * 60 + end.minute, 20 * 60)

    def test_removed_local_and_home_family_purposes_never_emit(self):
        removed = {"daily_errand", "grocery", "community", "park", "no_in_scope_trip", "family_care", "family_activity"}
        activities = []
        for age in ("18-39", "40-59", "60+"):
            for agent_id in range(1, 80):
                activities.extend(generate_weekly_activity_plan(placed_agent(agent_id, age, medical_need_level="standard" if age == "60+" else None), WEEK_START, 73))
        self.assertTrue(removed.isdisjoint({item["activity_purpose"] for item in activities}))

    def test_population_home_assignment_and_plans_scale_to_50_100_200(self):
        derived = derive_spatial_configuration(load_zone_configuration())
        matrices = []
        for total_agents in (50, 100, 200):
            quotas = allocate_zone_age_quotas(derived, total_agents=total_agents)
            population = generate_population_agents(total_agents=total_agents, seed=47)
            placed = assign_home_zones(population, quotas["quota_matrix"], seed=47)
            activities = generate_seven_day_activity_plans(placed, WEEK_START, random_seed=47)
            matrices.append(quotas["quota_matrix"])
            self.assertEqual(quotas["total_agents_used"], total_agents)
            self.assertEqual(len(placed), total_agents)
            self.assertTrue(activities)
            status_counts = Counter((agent.age_group, agent.work_status) for agent in population)
            age_counts = Counter(agent.age_group for agent in population)
            self.assertEqual(status_counts[("18-39", "flexible_non_worker")], int(age_counts["18-39"] * 0.10 + 0.5))
            self.assertEqual(status_counts[("40-59", "flexible_non_worker")], int(age_counts["40-59"] * 0.08 + 0.5))
            self.assertEqual(status_counts[("60+", "part_time_worker")], int(age_counts["60+"] * 0.17 + 0.5))
        self.assertEqual(len({repr(matrix) for matrix in matrices}), 3)


if __name__ == "__main__":
    unittest.main()
