"""陶片双时间轴核对的规则测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from excavation_evidence_workbench import process

ROOT = Path(__file__).resolve().parent.parent


def _facts(data):
    return {fact["fact_id"]: fact for fact in process("ceramic-reconciliation", data)}


def _registration(event_id, date, received, sherd_id, number, context, bag, rim=False):
    return {
        "id": event_id, "date": date, "received_date": received,
        "sherd_id": sherd_id, "initial_number": number,
        "context": context, "bag_id": bag, "rim": rim,
    }


def _renumber(event_id, date, received, sherd_id, new_number):
    return {"id": event_id, "date": date, "received_date": received,
            "sherd_id": sherd_id, "new_number": new_number}


def _move(event_id, date, received, sherd_id, bag):
    return {"id": event_id, "date": date, "received_date": received,
            "sherd_id": sherd_id, "bag_id": bag}


def _group(event_id, date, received, members):
    return {"id": event_id, "date": date, "received_date": received,
            "member_numbers": members}


def _withdrawal(event_id, date, received, group_id):
    return {"id": event_id, "date": date, "received_date": received, "group_id": group_id}


def _review(event_id, date, received, group_id, number, action="hold", **extra):
    return {"id": event_id, "date": date, "received_date": received,
            "group_id": group_id, "sherd_number": number, "action": action, **extra}


class CeramicReconciliationTests(unittest.TestCase):
    def test_example_matches_expected_fixture(self):
        source = json.loads((ROOT / "examples" / "ceramic-reconciliation.json").read_text(encoding="utf-8"))
        expected = json.loads(
            (ROOT / "tests" / "expected" / "ceramic-reconciliation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(process("ceramic-reconciliation", source), expected)

    def test_renumber_keeps_one_physical_identity_and_bag_move_keeps_context(self):
        data = {
            "registrations": [_registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1", rim=True)],
            "renumbers": [_renumber("n1", "2026-04-01", "2026-04-01", "S1", "A2")],
            "bag_moves": [_move("m1", "2026-04-02", "2026-04-02", "S1", "B9")],
            "queries": [{"id": "q", "mode": "latest", "date": "2026-05-01"}],
        }
        facts = _facts(data)
        context = facts["q:context:C7"]
        self.assertEqual(context["sherd_count"], 1)  # 改号不产生第二片
        self.assertEqual(context["rim_sherd_ids"], ["S1"])
        self.assertEqual(context["current_numbers"], {"S1": "A2"})
        self.assertEqual(context["bag_index"], {"B9": ["S1"]})  # 移袋改位置
        self.assertEqual(context["context"], "C7")  # 出土单位不变

    def test_original_view_excludes_records_received_after_issue_date(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1"),
                _registration("r2", "2026-03-02", "2026-05-20", "S2", "A2", "C7", "B1"),
            ],
            "queries": [
                {"id": "original", "mode": "original", "date": "2026-06-01", "received_date": "2026-05-01"},
                {"id": "latest", "mode": "latest", "date": "2026-06-01"},
            ],
        }
        facts = _facts(data)
        self.assertEqual(facts["original:context:C7"]["sherd_count"], 1)
        self.assertEqual(facts["latest:context:C7"]["sherd_count"], 2)
        self.assertNotIn("r2", facts["original:ledger"]["record_ids"])
        self.assertIn("r2", facts["latest:ledger"]["record_ids"])

    def test_late_correction_with_early_valid_date_is_replayed_in_latest_view(self):
        # 更正 6 月才收到，但生效日回填到 3 月：原认定不可见，最新认定必须回放。
        data = {
            "registrations": [_registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1", rim=True)],
            "renumbers": [_renumber("late", "2026-03-10", "2026-06-01", "S1", "A1x")],
            "queries": [
                {"id": "original", "mode": "original", "date": "2026-05-31", "received_date": "2026-05-31"},
                {"id": "latest", "mode": "latest", "date": "2026-06-30"},
            ],
        }
        facts = _facts(data)
        self.assertEqual(facts["original:context:C7"]["current_numbers"], {"S1": "A1"})
        self.assertEqual(facts["latest:context:C7"]["current_numbers"], {"S1": "A1x"})

    def test_group_confirmation_resolves_alias_to_physical_sherd_without_double_count(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1", rim=True),
                _registration("r2", "2026-03-01", "2026-03-01", "S2", "A2", "C7", "B1"),
            ],
            "renumbers": [_renumber("n1", "2026-04-01", "2026-04-01", "S1", "A1b")],
            "group_confirmations": [_group("G1", "2026-04-10", "2026-04-10", ["A1b", "A2"])],
            "queries": [{"id": "q", "mode": "latest", "date": "2026-05-01"}],
        }
        facts = _facts(data)
        self.assertEqual(facts["q:group:G1"]["member_sherd_ids"], ["S1", "S2"])
        self.assertEqual(facts["q:context:C7"]["rim_count"], 1)
        self.assertEqual(facts["q:context:C7"]["pending_sherd_ids"], [])

    def test_conflict_hold_preserves_other_memberships_and_marks_pending_review(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1"),
                _registration("r2", "2026-03-01", "2026-03-01", "S2", "A2", "C7", "B1"),
                _registration("r3", "2026-03-01", "2026-03-01", "S3", "A3", "C7", "B1"),
            ],
            "group_confirmations": [_group("G1", "2026-04-10", "2026-04-10", ["A1", "A2", "A3"])],
            "reviews": [_review("rv", "2026-05-01", "2026-05-01", "G1", "A2")],
            "queries": [{"id": "q", "mode": "latest", "date": "2026-06-01"}],
        }
        facts = _facts(data)
        group = facts["q:group:G1"]
        self.assertEqual(group["status"], "confirmed")  # 组仍然有效
        self.assertEqual(group["member_sherd_ids"], ["S1", "S2", "S3"])
        self.assertEqual(group["held_sherd_ids"], ["S2"])
        self.assertEqual(group["effective_member_count"], 2)  # 其他片归属维持
        context = facts["q:context:C7"]
        self.assertEqual(context["in_review_sherd_ids"], ["S2"])
        self.assertEqual(context["pending_sherd_ids"], [])  # 冲突片仍在组内、只是留待复核
        evidence = context["group_evidence"][0]
        self.assertEqual(evidence["held_sherd_ids"], ["S2"])
        self.assertEqual(evidence["effective_member_count"], 2)

    def test_release_review_clears_hold(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1"),
                _registration("r2", "2026-03-01", "2026-03-01", "S2", "A2", "C7", "B1"),
            ],
            "group_confirmations": [_group("G1", "2026-04-10", "2026-04-10", ["A1", "A2"])],
            "reviews": [
                _review("rv", "2026-05-01", "2026-05-01", "G1", "A2"),
                _review("rv-ok", "2026-05-10", "2026-05-10", "G1", "A2",
                        action="release", review_id="rv"),
            ],
            "queries": [{"id": "q", "mode": "latest", "date": "2026-06-01"}],
        }
        facts = _facts(data)
        self.assertEqual(facts["q:group:G1"]["held_sherd_ids"], [])
        self.assertEqual(facts["q:group:G1"]["effective_member_count"], 2)
        self.assertEqual(facts["q:context:C7"]["in_review_sherd_ids"], [])

    def test_cross_context_group_counts_once_but_each_context_keeps_evidence(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1", rim=True),
                _registration("r2", "2026-03-01", "2026-03-01", "S2", "A2", "C8", "B2"),
            ],
            "group_confirmations": [_group("G1", "2026-04-10", "2026-04-10", ["A1", "A2"])],
            "queries": [{"id": "q", "mode": "latest", "date": "2026-05-01"}],
        }
        facts = _facts(data)
        self.assertEqual(facts["q:batch"]["confirmed_group_count"], 1)  # 整批只计一组
        self.assertEqual(facts["q:batch"]["cross_context_group_ids"], ["G1"])
        c7 = facts["q:context:C7"]
        c8 = facts["q:context:C8"]
        self.assertEqual(c7["confirmed_group_count"], 1)  # 本单位仍查得实物证据
        self.assertEqual(c8["confirmed_group_count"], 1)
        self.assertTrue(c7["group_evidence"][0]["cross_context"])
        self.assertEqual(c7["group_evidence"][0]["member_sherd_ids"], ["S1"])
        self.assertEqual(c8["group_evidence"][0]["member_sherd_ids"], ["S2"])

    def test_withdrawal_returns_members_to_pending_and_is_replayable(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1"),
                _registration("r2", "2026-03-01", "2026-03-01", "S2", "A2", "C7", "B1"),
            ],
            "group_confirmations": [_group("G1", "2026-04-10", "2026-04-10", ["A1", "A2"])],
            "group_withdrawals": [_withdrawal("w1", "2026-05-02", "2026-05-02", "G1")],
            "queries": [
                {"id": "before", "mode": "original", "date": "2026-05-01", "received_date": "2026-05-01"},
                {"id": "after", "mode": "latest", "date": "2026-06-01"},
            ],
        }
        facts = _facts(data)
        self.assertEqual(facts["before:group:G1"]["status"], "confirmed")
        self.assertEqual(facts["after:group:G1"]["status"], "withdrawn")
        self.assertEqual(facts["after:group:G1"]["withdrawal_id"], "w1")
        self.assertEqual(facts["after:batch"]["confirmed_group_count"], 0)
        self.assertEqual(facts["after:context:C7"]["pending_sherd_ids"], ["S1", "S2"])

    def test_diff_traces_back_to_causal_records(self):
        data = {
            "registrations": [
                _registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1"),
                _registration("r2", "2026-05-20", "2026-06-02", "S2", "A2", "C7", "B1"),
            ],
            "renumbers": [_renumber("n1", "2026-03-15", "2026-06-01", "S1", "A1b")],
            "queries": [
                {"id": "original", "mode": "original", "date": "2026-05-31", "received_date": "2026-05-31"},
                {"id": "latest", "mode": "latest", "date": "2026-06-30"},
            ],
            "diff_pairs": [{"id": "d", "base_query_id": "original", "target_query_id": "latest"}],
        }
        facts = _facts(data)
        batch_diff = facts["d:diff:batch"]
        self.assertIn("r2", batch_diff["causal_record_ids"])
        self.assertEqual(batch_diff["before"]["sherd_count"], 1)
        self.assertEqual(batch_diff["after"]["sherd_count"], 2)
        context_diff = facts["d:diff:context:C7"]
        self.assertIn("n1", context_diff["causal_record_ids"])
        self.assertIn("r2", context_diff["causal_record_ids"])
        self.assertIn("sherd_count", context_diff["changed_fields"])
        self.assertIn("current_numbers", context_diff["changed_fields"])

    def test_validation_errors(self):
        base = {
            "registrations": [_registration("r1", "2026-03-01", "2026-03-01", "S1", "A1", "C7", "B1")],
            "queries": [{"id": "q", "mode": "latest", "date": "2026-05-01"}],
        }
        with self.assertRaises(ValueError):
            process("ceramic-reconciliation", {})  # 缺少查询
        bad_mode = json.loads(json.dumps(base))
        bad_mode["queries"][0]["mode"] = "mystery"
        with self.assertRaises(ValueError):
            process("ceramic-reconciliation", bad_mode)
        bad_date = json.loads(json.dumps(base))
        bad_date["registrations"][0]["date"] = "2026/03/01"
        with self.assertRaises(ValueError):
            process("ceramic-reconciliation", bad_date)
        dup_id = json.loads(json.dumps(base))
        dup_id["renumbers"] = [
            _renumber("r1", "2026-04-01", "2026-04-01", "S1", "A2"),
        ]
        with self.assertRaises(ValueError):
            process("ceramic-reconciliation", dup_id)
        unknown_ref = json.loads(json.dumps(base))
        unknown_ref["renumbers"] = [_renumber("n9", "2026-04-01", "2026-04-01", "S99", "A2")]
        with self.assertRaises(ValueError):
            process("ceramic-reconciliation", unknown_ref)
        two_groups = json.loads(json.dumps(base))
        two_groups["registrations"].append(
            _registration("r2", "2026-03-02", "2026-03-02", "S2", "A2", "C7", "B1")
        )
        two_groups["group_confirmations"] = [
            _group("G1", "2026-04-01", "2026-04-01", ["A1", "A2"]),
            _group("G2", "2026-04-02", "2026-04-02", ["A2"]),
        ]
        with self.assertRaises(ValueError):
            process("ceramic-reconciliation", two_groups)

    def test_cli_runs_and_sqlite_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "facts.sqlite"
            source = ROOT / "examples" / "ceramic-reconciliation.json"
            cmd = [sys.executable, "-m", "excavation_evidence_workbench",
                   "ceramic-reconciliation", str(source), "--sqlite", str(dest)]
            first = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            second = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            self.assertEqual(result["pipeline"], "ceramic-reconciliation")
            self.assertTrue(result["source_sha256"])


if __name__ == "__main__":
    unittest.main()
