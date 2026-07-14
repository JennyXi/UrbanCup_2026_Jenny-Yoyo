"""Run a small reproducible demonstration under W0, W1 and W2."""

from collections import Counter

from custom.agents.simple_mode_choice import SimpleAgent, simulate_trips


def main() -> None:
    agents = [
        SimpleAgent("A1", "18-39", "S2", True, 35),
        SimpleAgent("A2", "40-59", "S2", True, 45),
        SimpleAgent("A3", "60+", "S2", False, 20),
        SimpleAgent("A4", "60+", "S1", True, 25),
    ]
    trips = [
        {"trip_id": "T1", "agent_id": "A1", "origin_zone": "S2", "destination_zone": "S1"},
        {"trip_id": "T2", "agent_id": "A2", "origin_zone": "S2", "destination_zone": "S1"},
        {"trip_id": "T3", "agent_id": "A3", "origin_zone": "S2", "destination_zone": "S2"},
        {"trip_id": "T4", "agent_id": "A4", "origin_zone": "S1", "destination_zone": "S1"},
    ]
    for week in ("W0", "W1", "W2"):
        results = simulate_trips(agents, trips, week)
        counts = Counter(row["chosen_mode"] for row in results)
        print(week, dict(sorted(counts.items())))
        for row in results:
            print(f"  {row['agent_id']} {row['origin_zone']}->{row['destination_zone']}: {row['chosen_mode']}")


if __name__ == "__main__":
    main()
