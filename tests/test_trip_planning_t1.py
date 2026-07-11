import unittest
from collections import Counter, defaultdict
from datetime import datetime, time

from custom.agents.trip_planning import (
    OUTPUT_FIELDS,
    generate_seven_day_trip_plans,
    generate_weekly_trip_plan,
)


WEEK_START = datetime(2026, 7, 6, 0, 0)


def agent(agent_id, age_group):
    return {"agent_id": agent_id, "age_group": age_group}


def outbound_legs(legs):
    return [leg for leg in legs if leg["is_outbound"]]


class SevenDayTripPlanningTests(unittest.TestCase):
    def test_same_seed_is_reproducible(self):
        agents = [agent(1, "18-39"), agent(2, "40-59"), agent(3, "60+")]
        first = generate_seven_day_trip_plans(agents, WEEK_START, 2026)
        second = generate_seven_day_trip_plans(agents, WEEK_START, 2026)
        self.assertEqual(first, second)

    def test_agent_input_order_does_not_change_individual_plans(self):
        forward_agents = [agent(1, "18-39"), agent(2, "40-59"), agent(3, "60+")]
        reordered_agents = [agent(3, "60+"), agent(1, "18-39"), agent(2, "40-59")]
        forward = generate_seven_day_trip_plans(forward_agents, WEEK_START, 2026)
        reordered = generate_seven_day_trip_plans(reordered_agents, WEEK_START, 2026)

        def grouped(legs):
            result = defaultdict(list)
            for leg in legs:
                result[leg["agent_id"]].append(leg)
            return result

        self.assertEqual(grouped(forward), grouped(reordered))

    def test_age_groups_have_different_patterns(self):
        plans = {
            age: outbound_legs(generate_weekly_trip_plan(agent(index, age), WEEK_START, 17))
            for index, age in enumerate(("18-39", "40-59", "60+"), start=1)
        }
        purposes = {age: Counter(leg["trip_purpose"] for leg in legs) for age, legs in plans.items()}
        self.assertNotEqual(purposes["18-39"], purposes["40-59"])
        self.assertNotEqual(purposes["40-59"], purposes["60+"])
        young_hours = {leg["planned_departure_datetime"].hour for leg in plans["18-39"]}
        elder_hours = {leg["planned_departure_datetime"].hour for leg in plans["60+"]}
        self.assertNotEqual(young_hours, elder_hours)

    def test_weekday_and_weekend_patterns_are_different(self):
        weekend_expected = {
            "18-39": {"shopping", "leisure", "social", "visit"},
            "40-59": {"family_activity", "shopping", "visit"},
            "60+": {"visit", "park", "family_activity"},
        }
        for index, age in enumerate(("18-39", "40-59", "60+"), start=1):
            with self.subTest(age_group=age):
                legs = outbound_legs(generate_weekly_trip_plan(agent(index, age), WEEK_START, 19))
                weekday = Counter(leg["trip_purpose"] for leg in legs if not leg["is_weekend"])
                weekend = Counter(leg["trip_purpose"] for leg in legs if leg["is_weekend"])
                self.assertNotEqual(weekday, weekend)
                self.assertTrue(set(weekend).issubset(weekend_expected[age]))

    def test_every_trip_has_outbound_and_return(self):
        legs = generate_seven_day_trip_plans(
            [agent(1, "18-39"), agent(2, "40-59"), agent(3, "60+")],
            WEEK_START,
            21,
        )
        by_trip = defaultdict(list)
        for leg in legs:
            by_trip[leg["trip_id"]].append(leg)
        for trip_id, pair in by_trip.items():
            with self.subTest(trip_id=trip_id):
                self.assertEqual(len(pair), 2)
                self.assertEqual({leg["is_outbound"] for leg in pair}, {True, False})
                self.assertEqual(len({leg["leg_id"] for leg in pair}), 2)

    def test_return_is_later_than_outbound(self):
        legs = generate_weekly_trip_plan(agent(1, "18-39"), WEEK_START, 23)
        by_trip = defaultdict(list)
        for leg in legs:
            by_trip[leg["trip_id"]].append(leg)
        for pair in by_trip.values():
            outbound = next(leg for leg in pair if leg["is_outbound"])
            returning = next(leg for leg in pair if not leg["is_outbound"])
            self.assertGreater(
                returning["planned_departure_datetime"],
                outbound["planned_departure_datetime"],
            )

    def test_return_leg_departure_matches_planned_return_time(self):
        legs = generate_weekly_trip_plan(agent(1, "18-39"), WEEK_START, 23)
        by_trip = defaultdict(list)
        for leg in legs:
            by_trip[leg["trip_id"]].append(leg)

        for trip_id, pair in by_trip.items():
            with self.subTest(trip_id=trip_id):
                outbound = next(leg for leg in pair if leg["is_outbound"])
                returning = next(leg for leg in pair if not leg["is_outbound"])
                self.assertEqual(
                    returning["planned_departure_datetime"],
                    outbound["planned_return_datetime"],
                )
                self.assertGreater(
                    returning["planned_departure_datetime"],
                    outbound["planned_departure_datetime"],
                )

    def test_agent_trips_do_not_overlap(self):
        for index, age in enumerate(("18-39", "40-59", "60+"), start=1):
            legs = outbound_legs(generate_weekly_trip_plan(agent(index, age), WEEK_START, 29))
            intervals = sorted(
                (leg["planned_departure_datetime"], leg["planned_return_datetime"])
                for leg in legs
            )
            for previous, current in zip(intervals, intervals[1:]):
                self.assertGreaterEqual(current[0], previous[1])

    def test_leg_ids_are_unique_and_stable(self):
        agents = [agent(1, "18-39"), agent(2, "40-59"), agent(3, "60+")]
        first = generate_seven_day_trip_plans(agents, WEEK_START, 31)
        second = generate_seven_day_trip_plans(agents, WEEK_START, 31)
        first_ids = [leg["leg_id"] for leg in first]
        second_ids = [leg["leg_id"] for leg in second]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(first_ids), len(set(first_ids)))

    def test_elder_medical_trips_are_mandatory(self):
        found_medical = False
        for agent_id in range(1, 30):
            legs = generate_weekly_trip_plan(agent(agent_id, "60+"), WEEK_START, 37)
            medical = [leg for leg in legs if leg["trip_purpose"] == "medical"]
            if medical:
                found_medical = True
                self.assertTrue(all(leg["is_mandatory"] for leg in medical))
        self.assertTrue(found_medical)

    def test_young_weekday_work_is_dominant(self):
        legs = []
        for agent_id in range(1, 21):
            legs.extend(generate_weekly_trip_plan(agent(agent_id, "18-39"), WEEK_START, 41))
        weekday = [leg for leg in outbound_legs(legs) if not leg["is_weekend"]]
        core = sum(leg["trip_purpose"] == "work" for leg in weekday)
        self.assertGreater(core / len(weekday), 0.65)

    def test_young_plans_do_not_include_study(self):
        legs = []
        for agent_id in range(1, 21):
            legs.extend(generate_weekly_trip_plan(agent(agent_id, "18-39"), WEEK_START, 41))
        self.assertNotIn("study", {leg["trip_purpose"] for leg in legs})

    def test_elder_weekday_departures_are_daytime_dominant(self):
        legs = []
        for agent_id in range(1, 21):
            legs.extend(generate_weekly_trip_plan(agent(agent_id, "60+"), WEEK_START, 43))
        weekday = [leg for leg in outbound_legs(legs) if not leg["is_weekend"]]
        daytime = sum(
            time(9, 0) <= leg["planned_departure_datetime"].time() <= time(16, 0)
            for leg in weekday
        )
        self.assertGreater(daytime / len(weekday), 0.90)

    def test_output_contains_exactly_minimum_fields(self):
        legs = generate_weekly_trip_plan(agent(1, "18-39"), WEEK_START, 47)
        self.assertTrue(legs)
        self.assertTrue(all(tuple(leg.keys()) == OUTPUT_FIELDS for leg in legs))


if __name__ == "__main__":
    unittest.main()
