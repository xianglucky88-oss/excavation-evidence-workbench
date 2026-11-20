import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from excavation_evidence_workbench import PIPELINES, process
from excavation_evidence_workbench.storage import store

ROOT = Path(__file__).resolve().parent.parent


class BaselineTests(unittest.TestCase):
    def test_all_pipeline_outputs(self):
        self.assertEqual(len(PIPELINES), 6)
        for name in PIPELINES:
            with self.subTest(pipeline=name):
                source = json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
                expected = json.loads((ROOT / "tests" / "expected" / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(process(name, source), expected)

    def test_cli_and_sqlite_replay(self):
        name = next(iter(PIPELINES))
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "facts.sqlite"
            cmd = [sys.executable, "-m", "excavation_evidence_workbench", name, str(ROOT / "examples" / f"{name}.json"), "--sqlite", str(dest)]
            first = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            second = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            with sqlite3.connect(dest) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone(), (1,))
                stored = db.execute("SELECT fact_json FROM facts ORDER BY fact_id").fetchall()
                self.assertEqual([json.loads(raw) for raw, in stored], sorted(result["facts"], key=lambda f: f["fact_id"]))

    def test_duplicate_fact_rolls_back_new_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "facts.sqlite"
            store(dest, "test", {"batch": 1}, [{"fact_id": "ok"}])
            with self.assertRaises(sqlite3.IntegrityError):
                store(dest, "test", {"batch": 2}, [{"fact_id": "dup"}, {"fact_id": "dup"}])
            with sqlite3.connect(dest) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone(), (1,))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM facts").fetchone(), (1,))

    def test_reject_invalid_input_before_database_write(self):
        name = next(iter(PIPELINES))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"records": [], "records": []}', encoding="utf-8")
            dest = Path(tmp) / "facts.sqlite"
            run = subprocess.run([sys.executable, "-m", "excavation_evidence_workbench", name, str(bad), "--sqlite", str(dest)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn("duplicate JSON key", run.stderr)
            self.assertFalse(dest.exists())
            with self.assertRaises(ValueError):
                process(name, [])
            with self.assertRaises((ValueError, KeyError)):
                process(name, {"records": [{"id": "missing-required-fields"}]})

    def test_release_audit_rules(self):
        name = "fieldwalking-release-audit"
        source = json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
        facts = {fact["fact_id"]: fact for fact in process(name, source)}
        # 与旧表差分会唯一确定 e2：east 合并到更粗的 east-garden 后仍可公开
        self.assertEqual(facts["item:east"]["status"], "adjusted")
        self.assertEqual(facts["item:east"]["small_zones"], ["e1", "e2", "g1", "g2"])
        self.assertEqual(facts["item:east"]["value"], 14)
        self.assertEqual(facts["item:east"]["public_sources"], ["release-2023"])
        # garden 与调整后的 east 重叠，无法继续公开，只能暂缓
        self.assertEqual(facts["item:garden"]["status"], "deferred")
        self.assertIsNone(facts["item:garden"]["value"])
        # 两份旧表同名 north 覆盖不同，合并已唯一确定 n2，属既有泄露
        self.assertEqual(facts["inference:n2"]["reader_range"], [3, 3])
        self.assertTrue(facts["inference:n2"]["pre_existing"])
        self.assertEqual(facts["inference:n2"]["public_sources"], ["release-2023", "release-2024"])
        # 2024 表 east 空白格不代表零，e2 在旧表中不被锁定
        self.assertEqual(facts["inference:e2"]["reader_range"], [0, 9])
        self.assertFalse(facts["inference:e2"]["exact"])
        # 草稿拿掉的 south-rim 仍可由保留的总数与明细推出，列入风险
        self.assertEqual(facts["risk:south-rim"]["determined_value"], 3)
        self.assertIn("south", facts["risk:south-rim"]["joint_items"])
        self.assertIn("south-core", facts["risk:south-rim"]["joint_items"])
        self.assertEqual(facts["summary"]["deferred"], ["garden"])
        self.assertEqual(facts["summary"]["withheld"], ["south-rim"])

    def test_release_audit_rejects_inconsistent_numbers(self):
        name = "fieldwalking-release-audit"
        source = json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
        # 草稿数字与踏查带计数不符
        draft_off = json.loads(json.dumps(source))
        draft_off["draft"]["rows"][0]["finds"] += 1
        with self.assertRaises(ValueError):
            process(name, draft_off)
        # 明确代表零的空白格与踏查计数冲突
        blank_conflict = json.loads(json.dumps(source))
        blank_conflict["published"][0]["rows"][2]["finds"] = None
        with self.assertRaises(ValueError):
            process(name, blank_conflict)
        # 同名分区必须按各自版本边界核对
        wrong_version = json.loads(json.dumps(source))
        wrong_version["published"][0]["boundary"] = "v2024"
        with self.assertRaises(ValueError):
            process(name, wrong_version)
