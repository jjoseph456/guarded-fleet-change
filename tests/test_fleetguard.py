import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fleetguard.approval import ApprovalError, approve, verify_approval
from fleetguard.cli import main
from fleetguard.models import load_change, load_fleet, load_plan, plan_digest, write_json
from fleetguard.planner import build_plan
from fleetguard.policy import evaluate
from fleetguard.runner import WaveGateError, record_completion, wave_command

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 6, 14, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 6, 13, tzinfo=timezone.utc)


def rules(plan):
    return sorted({v.rule for v in evaluate(plan)})


def rehash(plan):
    plan["plan_sha256"] = plan_digest(plan)
    return plan


class Fixture(unittest.TestCase):
    def setUp(self):
        self.change = load_change(ROOT / "changes/directory-migration.yml")
        self.fleet = load_fleet(ROOT / "inventory/synthetic/fleet.json")
        self.plan = build_plan(self.change, self.fleet, requested_by="alex", start_at=START)


class PlannerTests(Fixture):
    def test_generated_plan_passes_policy(self):
        self.assertEqual([], rules(self.plan))

    def test_unready_hosts_are_excluded_with_reasons(self):
        excluded = {item["host"]: item["reasons"][0].split(":")[0] for item in self.plan["excluded"]}
        self.assertEqual(
            {
                "app-03.site-a.example.test": "STALE_BACKUP",
                "app-04.site-b.example.test": "NO_MONITORING",
                "bastion-01.site-b.example.test": "UNSUPPORTED_OS",
            },
            excluded,
        )

    def test_first_wave_is_single_staging_canary(self):
        first = self.plan["waves"][0]
        self.assertEqual(("canary", "staging", 1), (first["kind"], first["environment"], len(first["hosts"])))

    def test_staging_finishes_before_production(self):
        envs = [w["environment"] for w in self.plan["waves"]]
        self.assertEqual(envs, sorted(envs, key=lambda e: e == "production"))

    def test_ha_pairs_never_share_a_wave(self):
        for wave in self.plan["waves"]:
            pairs = [self.plan["host_facts"][h]["ha_pair"] for h in wave["hosts"]]
            pairs = [p for p in pairs if p]
            self.assertEqual(len(pairs), len(set(pairs)))

    def test_every_eligible_host_is_planned_once(self):
        planned = [h for w in self.plan["waves"] for h in w["hosts"]]
        self.assertEqual(len(planned), len(set(planned)))
        self.assertEqual(len(self.fleet) - len(self.plan["excluded"]), len(planned))


class PolicyTests(Fixture):
    def edited(self, mutate):
        plan = copy.deepcopy(self.plan)
        mutate(plan)
        return rehash(plan)

    def test_oversized_wave_is_denied(self):
        def grow(plan):
            plan["waves"][-2]["hosts"] += plan["waves"][-1]["hosts"]
            plan["waves"][-2]["hosts"] += plan["waves"][-3]["hosts"][:1]
            plan["max_wave_size"] = 2
        self.assertIn("WAVE_TOO_LARGE", rules(self.edited(grow)))

    def test_pair_in_one_wave_is_denied(self):
        def merge(plan):
            prod = [w for w in plan["waves"] if w["environment"] == "production"]
            pair_hosts = [h for w in prod for h in w["hosts"] if h.startswith("db-") and "site-b" in h]
            for w in prod:
                w["hosts"] = [h for h in w["hosts"] if h not in pair_hosts]
            prod[0]["hosts"] += pair_hosts
        self.assertIn("HA_PAIR_SAME_WAVE", rules(self.edited(merge)))

    def test_blocked_host_added_by_hand_is_denied(self):
        def inject(plan):
            plan["waves"][-1]["hosts"].append("app-03.site-a.example.test")
        self.assertIn("BLOCKED_HOST_IN_PLAN", rules(self.edited(inject)))

    def test_missing_canary_is_denied(self):
        self.assertIn("CANARY_REQUIRED", rules(self.edited(lambda p: p["waves"].pop(0))))

    def test_staging_after_production_is_denied(self):
        def reorder(plan):
            plan["waves"].append(plan["waves"].pop(1))
        self.assertIn("PRODUCTION_BEFORE_STAGING", rules(self.edited(reorder)))

    def test_start_inside_freeze_is_denied(self):
        plan = build_plan(
            self.change, self.fleet, requested_by="alex",
            start_at=datetime(2026, 12, 24, tzinfo=timezone.utc),
        )
        self.assertEqual(["FREEZE_WINDOW"], rules(plan))

    def test_hand_edited_plan_fails_digest_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            plan = copy.deepcopy(self.plan)
            plan["waves"][0]["hosts"].append("db-01.site-a.example.test")
            write_json(path, plan)
            with self.assertRaisesRegex(ValueError, "modified"):
                load_plan(path)


class ApprovalAndRunTests(Fixture):
    def test_requester_cannot_self_approve(self):
        with self.assertRaisesRegex(ApprovalError, "cannot approve"):
            approve(self.plan, approver="alex", now=NOW)

    def test_policy_violations_block_approval(self):
        plan = rehash({**copy.deepcopy(self.plan), "start_at": "2026-12-24T00:00:00Z"})
        with self.assertRaisesRegex(ApprovalError, "policy violation"):
            approve(plan, approver="sam", now=NOW)

    def test_approval_is_bound_to_plan_digest(self):
        record = approve(self.plan, approver="sam", now=NOW)
        changed = rehash({**copy.deepcopy(self.plan), "pause_minutes_between_waves": 0})
        with self.assertRaisesRegex(ApprovalError, "stale"):
            verify_approval(changed, record)

    def test_dry_run_needs_no_approval_and_uses_check_mode(self):
        cmd = wave_command(self.plan, 1, inventory="hosts.ini", apply=False, approval=None, state=None)
        self.assertEqual(["--check", "--diff"], cmd[-2:])
        self.assertIn("app-01.site-c.example.test", cmd)

    def test_apply_requires_approval(self):
        with self.assertRaisesRegex(ApprovalError, "requires an approval"):
            wave_command(self.plan, 1, inventory="hosts.ini", apply=True, approval=None, state=None)

    def test_apply_enforces_wave_order(self):
        record = approve(self.plan, approver="sam", now=NOW)
        with self.assertRaisesRegex(WaveGateError, "in order"):
            wave_command(self.plan, 2, inventory="hosts.ini", apply=True, approval=record, state=None)
        state = record_completion(None, self.plan, 1)
        cmd = wave_command(self.plan, 2, inventory="hosts.ini", apply=True, approval=record, state=state)
        self.assertNotIn("--check", cmd)

    def test_state_from_another_plan_is_ignored(self):
        record = approve(self.plan, approver="sam", now=NOW)
        foreign = {"plan_sha256": "0" * 64, "completed_waves": [1]}
        with self.assertRaisesRegex(WaveGateError, "in order"):
            wave_command(self.plan, 2, inventory="hosts.ini", apply=True, approval=record, state=foreign)


class CliTests(unittest.TestCase):
    def test_end_to_end_plan_check_approve_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            approval = Path(tmp) / "approval.json"
            self.assertEqual(0, main([
                "plan", str(ROOT / "changes/directory-migration.yml"),
                "--fleet", str(ROOT / "inventory/synthetic/fleet.json"),
                "--requested-by", "alex", "--start-at", "2026-10-06T14:00:00Z", "-o", str(plan),
            ]))
            self.assertEqual(0, main(["check", str(plan)]))
            self.assertEqual(1, main(["approve", str(plan), "--approver", "alex", "-o", str(approval)]))
            self.assertEqual(0, main(["approve", str(plan), "--approver", "sam", "-o", str(approval)]))
            self.assertEqual(0, main([
                "run", str(plan), "--wave", "1", "--apply", "--print-only",
                "--approval", str(approval), "--state", str(Path(tmp) / "state.json"),
            ]))
            self.assertEqual(json.loads(approval.read_text())["approver"], "sam")


if __name__ == "__main__":
    unittest.main()
