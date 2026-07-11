import unittest

from custom.policies.t3_policy import OUTPUT_FIELDS, evaluate_t3_policy


def make_agent(**overrides):
    agent = {
        "agent_id": 1,
        "is_elder": False,
        "digital_access": True,
        "independent_ride_hailing": True,
        "coupon_awareness_probability": 1.0,
        "coupon_claim_probability": 1.0,
    }
    agent.update(overrides)
    return agent


def make_leg(**overrides):
    leg = {"leg_id": "leg-1", "trip_continues": True}
    leg.update(overrides)
    return leg


def evaluate(policy, *, agent=None, leg=None, level="low", count=0, **kwargs):
    if agent is None:
        agent = make_agent()
    if leg is None:
        leg = make_leg()
    if policy in ("P0", "P4"):
        level = None
    return evaluate_t3_policy(
        agent,
        leg,
        policy_scenario=policy,
        discount_level=level,
        discount_amount_low=10,
        discount_amount_high=20,
        weekly_discount_use_count=count,
        random_seed=2026,
        **kwargs,
    )


class T3PolicyTests(unittest.TestCase):
    def test_cancelled_leg_is_skipped(self):
        result = evaluate_t3_policy(
            {},
            {"trip_continues": False},
            policy_scenario="invalid",
        )
        self.assertIsNone(result)

    def test_p0_has_no_discount_or_priority(self):
        result = evaluate("P0", agent={})
        self.assertEqual(result["discount_level"], None)
        self.assertEqual(result["discount_amount"], 0)
        self.assertFalse(result["coupon_eligible"])
        self.assertIsNone(result["coupon_seen"])
        self.assertIsNone(result["coupon_claimed"])
        self.assertIsNone(result["access_channel"])
        self.assertFalse(result["price_discount_eligible"])
        self.assertFalse(result["dispatch_priority_eligible"])

    def test_p1_non_digital_state_semantics(self):
        result = evaluate("P1", agent=make_agent(digital_access=False))
        self.assertFalse(result["coupon_eligible"])
        self.assertIsNone(result["coupon_seen"])
        self.assertIsNone(result["coupon_claimed"])
        self.assertFalse(result["price_discount_eligible"])

    def test_p1_claim_requires_seen(self):
        result = evaluate(
            "P1",
            agent=make_agent(
                coupon_awareness_probability=0.0,
                coupon_claim_probability=1.0,
            ),
        )
        self.assertFalse(result["coupon_seen"])
        self.assertIsNone(result["coupon_claimed"])

    def test_p1_low_high_share_seen_and_claimed(self):
        low = evaluate("P1", level="low")
        high = evaluate("P1", level="high")
        self.assertEqual(low["coupon_seen"], high["coupon_seen"])
        self.assertEqual(low["coupon_claimed"], high["coupon_claimed"])
        self.assertNotEqual(low["discount_amount"], high["discount_amount"])

    def test_p1_repeated_call_is_deterministic(self):
        first = evaluate("P1")
        second = evaluate("P1")
        self.assertEqual(first, second)

    def test_p2_does_not_use_seen_or_claimed(self):
        result = evaluate(
            "P2",
            agent=make_agent(
                coupon_awareness_probability=None,
                coupon_claim_probability="placeholder",
            ),
        )
        self.assertIsNone(result["coupon_seen"])
        self.assertIsNone(result["coupon_claimed"])
        self.assertTrue(result["price_discount_eligible"])

    def test_p2_requires_independent_ride_hailing_for_discount(self):
        result = evaluate(
            "P2", agent=make_agent(independent_ride_hailing=False)
        )
        self.assertTrue(result["coupon_eligible"])
        self.assertIsNone(result["access_channel"])
        self.assertFalse(result["price_discount_eligible"])

    def test_p3_covers_every_agent_without_digital_access(self):
        result = evaluate("P3", agent={})
        self.assertTrue(result["coupon_eligible"])
        self.assertEqual(result["access_channel"], "multichannel")
        self.assertTrue(result["price_discount_eligible"])

    def test_p4_priority_is_only_for_elder_agents(self):
        elder = evaluate("P4", agent={"is_elder": True})
        non_elder = evaluate("P4", agent={"is_elder": False})
        self.assertTrue(elder["dispatch_priority_eligible"])
        self.assertFalse(non_elder["dispatch_priority_eligible"])

    def test_p4_has_no_discount_and_does_not_change_channel(self):
        result = evaluate("P4", agent={"is_elder": True})
        self.assertEqual(result["discount_amount"], 0)
        self.assertFalse(result["coupon_eligible"])
        self.assertIsNone(result["access_channel"])
        self.assertFalse(result["price_discount_eligible"])

    def test_weekly_limit_blocks_discount_for_p1_p2_p3(self):
        for policy in ("P1", "P2", "P3"):
            with self.subTest(policy=policy):
                result = evaluate(policy, count=3)
                self.assertFalse(result["price_discount_eligible"])

    def test_p1_keeps_seen_and_claimed_at_weekly_limit(self):
        result = evaluate("P1", count=3)
        self.assertTrue(result["coupon_seen"])
        self.assertTrue(result["coupon_claimed"])
        self.assertFalse(result["price_discount_eligible"])

    def test_p1_p2_p3_read_low_and_high_amounts(self):
        for policy in ("P1", "P2", "P3"):
            with self.subTest(policy=policy):
                self.assertEqual(evaluate(policy, level="low")["discount_amount"], 10)
                self.assertEqual(evaluate(policy, level="high")["discount_amount"], 20)

    def test_p0_p4_do_not_read_discount_configuration(self):
        for policy in ("P0", "P4"):
            agent = {} if policy == "P0" else {"is_elder": True}
            result = evaluate_t3_policy(
                agent,
                make_leg(),
                policy_scenario=policy,
                discount_level=None,
                discount_amount_low="placeholder",
                discount_amount_high=None,
                weekly_discount_use_count=None,
                random_seed=None,
            )
            self.assertEqual(result["discount_amount"], 0)

    def test_invalid_required_parameters_raise(self):
        cases = [
            {"discount_level": "medium"},
            {"discount_amount_low": -1},
            {"weekly_discount_use_count": None},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                arguments = {
                    "policy_scenario": "P3",
                    "discount_level": "low",
                    "discount_amount_low": 10,
                    "discount_amount_high": 20,
                    "weekly_discount_use_count": 0,
                    "random_seed": 2026,
                }
                arguments.update(overrides)
                with self.assertRaises(ValueError):
                    evaluate_t3_policy({}, make_leg(), **arguments)

        with self.assertRaises(ValueError):
            evaluate(
                "P1",
                agent=make_agent(coupon_awareness_probability="placeholder"),
            )

    def test_output_contains_exactly_allowed_fields(self):
        for policy in ("P0", "P1", "P2", "P3", "P4"):
            with self.subTest(policy=policy):
                agent = make_agent(is_elder=True)
                result = evaluate(policy, agent=agent)
                self.assertEqual(tuple(result.keys()), OUTPUT_FIELDS)


if __name__ == "__main__":
    unittest.main()
