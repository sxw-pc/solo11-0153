"""工牌号跨册独立性凭据的验收测试。

合成原件、一班最多两次发放/五张牌/三个报告;不接密码学或网络服务。
覆盖:版次演绎与注入性、相容冲突例证、两种核证委托及不可互借、
换班重发半开边界与权威矛盾、版次不得回溯、检验裁决集成与 CLI 重放。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weldproof import independence, inspection, qualification
from weldproof.store import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def roster_mandate(mid="M-ROSTER", complete=True):
    return {"id": mid, "kind": "roster", "roster_export": True,
            "scope": "shift", "complete": complete}


def witness_mandate(mid="M-WITNESS"):
    return {"id": mid, "kind": "witness", "roster_export": False, "scope": "document"}


def states(rows):
    return {r["doc_id"]: r["state"] for r in rows}


class EditionDeductionTests(unittest.TestCase):
    """fixtures/badge_edition.json 的全局演绎,及逐份判断的错误。"""

    def setUp(self):
        self.db = connect(":memory:")
        self.data = read_fixture("badge_edition.json")

    def tearDown(self):
        self.db.close()

    def test_global_deduction_pins_both_mappings(self):
        # I-2 经独立原件排除 W-3 后固定为 W-2;注入性迫使 I-1 只能是 W-1。
        self.data["mandates"] = [roster_mandate()]
        rows = independence.import_data(self.db, self.data)
        self.assertEqual(
            states(rows), {"D1": "not_independent", "D2": "independent"})
        d1 = next(r for r in rows if r["doc_id"] == "D1")
        self.assertEqual(d1["credential"]["type"], "roster")
        # 凭据必须列出支撑它的全部原件。
        evidence = set(d1["credential"]["evidence"])
        self.assertIn("night-shift:CAND:I-1", evidence)
        self.assertIn("night-shift:NON:I-2:W-3", evidence)

    def test_judging_documents_separately_would_wrongly_sign_d1(self):
        # 只看 D1 自身候选(I-1 -> W-1|W-2)且操作人是 W-1 时,
        # 逐份判断会得到“可能不同人”而放行;全局注入性才能否掉它。
        only_d1 = {
            "epoch": "night-shift",
            "shop_badges": ["W-1", "W-2"],
            "lab_badges": ["I-1"],
            "binding_candidates": {"I-1": ["W-1", "W-2"]},
            "documents": [{"id": "D1", "operator": "W-1", "inspector": "I-1"}],
            "mandates": [roster_mandate()],
        }
        rows = independence.import_data(connect(":memory:"), only_d1)
        self.assertEqual(rows[0]["state"], "pending")  # 单份证据不足,不能签
        # 而在完整全局约束下 D1 被确定为同一人(不得签认)。
        full = read_fixture("badge_edition.json")
        full["mandates"] = [roster_mandate()]
        full_rows = independence.import_data(self.db, full)
        self.assertEqual(states(full_rows)["D1"], "not_independent")

    def test_missing_exclusion_original_gives_compatible_conflict_examples(self):
        # 抽掉 I-2≠W-3 的独立原件后,D1 同时存在同一人/不同人两种相容解释,
        # 必须保持待核并给出双向例证,而不是二选一擅断。
        self.data["confirmed_nonmatch"] = {}
        self.data["mandates"] = [roster_mandate()]
        rows = independence.import_data(self.db, self.data)
        d1 = next(r for r in rows if r["doc_id"] == "D1")
        self.assertEqual(d1["state"], "pending")
        results = {ex["result"] for ex in d1["examples"]}
        self.assertEqual(results, {"same", "different"})
        same = next(ex for ex in d1["examples"] if ex["result"] == "same")
        diff = next(ex for ex in d1["examples"] if ex["result"] == "different")
        self.assertEqual(same["inspector"], "W-1")
        self.assertEqual(diff["inspector"], "W-2")

    def test_injectivity_conflict_is_grouped_with_originals(self):
        # 同版两张经确认发给不同人员的牌不能映给同一人。
        data = {
            "epoch": "inject", "shop_badges": ["W-1"], "lab_badges": ["I-1", "I-2"],
            "binding_candidates": {"I-1": ["W-1"], "I-2": ["W-1"]},
            "documents": [
                {"id": "D1", "operator": "W-1", "inspector": "I-1"},
                {"id": "D2", "operator": "W-1", "inspector": "I-2"},
            ],
            "mandates": [roster_mandate()],
        }
        rows = independence.import_data(self.db, data)
        self.assertTrue(all(r["state"] == "conflict" for r in rows))
        groups = rows[0]["conflict_groups"]
        self.assertTrue(groups)
        self.assertEqual(set(groups[0]["docs"]), {"D1", "D2"})
        self.assertIn("inject:CAND:I-1", groups[0]["originals"])

    def test_unrecorded_candidate_set_stays_undetermined(self):
        data = {
            "epoch": "nocand", "shop_badges": ["W-9"], "lab_badges": ["I-9"],
            "documents": [{"id": "DX", "operator": "W-9", "inspector": "I-9"}],
            "mandates": [roster_mandate()],
        }
        row = independence.import_data(self.db, data)[0]
        self.assertEqual(row["state"], "pending")
        self.assertIn("not recorded", row["reason"])


class MandateRoutesTests(unittest.TestCase):
    """两种独立核证委托:全班名册 vs 单报告实名在场,不可互借。"""

    def setUp(self):
        self.db = connect(":memory:")
        self.base = read_fixture("badge_edition.json")
        # 先在无委托状态采录名册类原件(数据尚未出口授权)。
        independence.import_data(self.db, json.loads(json.dumps(self.base)))

    def tearDown(self):
        self.db.close()

    def test_roster_route_requires_complete_shift_coverage(self):
        incomplete = {"epoch": "night-shift",
                      "mandates": [roster_mandate("M-PARTIAL", complete=False)]}
        rows = independence.import_data(self.db, incomplete)
        self.assertTrue(all(r["state"] == "pending" for r in rows))
        self.assertIn("complete shift-wide coverage", rows[0]["reason"])

        complete = {"epoch": "night-shift", "mandates": [roster_mandate()]}
        rows = independence.import_data(self.db, complete)
        self.assertEqual(
            states(rows), {"D1": "not_independent", "D2": "independent"})

    def test_scoped_receipt_certifies_only_its_document(self):
        independence.import_data(self.db, {
            "epoch": "night-shift", "mandates": [witness_mandate()],
            "receipts": [{
                "id": "R-D1", "doc_id": "D1", "verdict": "not_independent",
                "witnesses": ["authority-a", "authority-b"],
                "at": "2026-03-02T23:40",
            }],
        })
        rows = independence.query(self.db, epoch="night-shift")
        by_id = {r["doc_id"]: r for r in rows}
        self.assertEqual(by_id["D1"]["state"], "not_independent")
        self.assertEqual(by_id["D1"]["credential"]["type"], "receipt")
        # D2 不得从 D1 的回执推导任何名单关系。
        self.assertEqual(by_id["D2"]["state"], "pending")
        self.assertIsNone(by_id["D2"]["credential"])
        self.assertIn("cannot be borrowed", by_id["D2"]["reason"])

    def test_receipt_withdrawal_only_affects_dependent_verdict(self):
        independence.import_data(self.db, {
            "epoch": "night-shift", "mandates": [witness_mandate()],
            "receipts": [{
                "id": "R-D1", "doc_id": "D1", "verdict": "not_independent",
                "witnesses": ["authority-a", "authority-b"],
                "at": "2026-03-02T23:40",
            }],
        })
        evidence_before = self.db.execute("SELECT count(*) FROM evidence").fetchone()[0]
        independence.import_data(self.db, {
            "epoch": "night-shift",
            "withdrawals": [{"id": "W-REC", "receipt": "R-D1",
                             "at": "2026-03-03T08:00"}],
        })
        rows = {r["doc_id"]: r for r in
                independence.query(self.db, epoch="night-shift")}
        self.assertEqual(rows["D1"]["state"], "pending")
        self.assertIsNone(rows["D1"]["credential"])
        # 已签收旧件原件与技术读数不受撤回影响。
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM evidence").fetchone()[0],
            evidence_before + 1)
        self.assertEqual(
            self.db.execute("SELECT state FROM witness_receipt WHERE id='R-D1'")
            .fetchone()[0], "withdrawn")

    def test_witness_only_mandate_refuses_roster_export(self):
        with self.assertRaises(PermissionError):
            independence.import_data(self.db, {
                "epoch": "blocked",
                "binding_candidates": {"I-1": ["W-1"]},
                "mandates": [witness_mandate()],
            })

    def test_receipt_contradicting_authoritative_originals_conflicts(self):
        # 全局名册证据已确定 D2 独立,一份相反回执不能覆盖,只能判冲突。
        independence.import_data(self.db, {
            "epoch": "night-shift", "mandates": [roster_mandate(), witness_mandate()],
            "receipts": [{
                "id": "R-BAD", "doc_id": "D2", "verdict": "not_independent",
                "witnesses": ["a", "b"], "at": "2026-03-02T23:45",
            }],
        })
        d2 = next(r for r in independence.query(self.db, epoch="night-shift")
                  if r["doc_id"] == "D2")
        self.assertEqual(d2["state"], "conflict")
        self.assertEqual(d2["conflict_groups"][0]["originals"], ["R-BAD"])


class ReissueBoundaryTests(unittest.TestCase):
    """换班处重发:半开区间、全局时序世界、权威矛盾分组。"""

    ISSUANCES = [
        {"id": "ISS-W1", "badge": "W-1", "holder": "P-A",
         "start": "2026-03-02T20:00", "end": None},
        {"id": "ISS-W2A", "badge": "W-2", "holder": "P-A",
         "start": "2026-03-02T20:00", "end": "2026-03-02T22:00"},
        {"id": "ISS-W2B", "badge": "W-2", "holder": "P-B",
         "start": "2026-03-02T22:00", "end": None},
    ]

    def _doc(self, at):
        return {
            "epoch": "reissue", "shop_badges": ["W-1", "W-2"],
            "lab_badges": ["I-1"], "issuances": self.ISSUANCES,
            "pairings": [{"lab_badge": "I-1", "shop_badge": "W-2"}],
            "documents": [{"id": "D1", "operator": "W-1",
                           "inspector": "I-1", "at": at}],
            "mandates": [roster_mandate()],
        }

    def test_half_open_boundary_assigns_exact_instant_to_reissue(self):
        db = connect(":memory:")
        before = independence.import_data(db, self._doc("2026-03-02T21:59"))[0]
        self.assertEqual(before["state"], "not_independent")
        db.close()
        db = connect(":memory:")
        boundary = independence.import_data(db, self._doc("2026-03-02T22:00"))[0]
        self.assertEqual(boundary["state"], "independent")
        db.close()
        db = connect(":memory:")
        after = independence.import_data(db, self._doc("2026-03-02T22:30"))[0]
        self.assertEqual(after["state"], "independent")
        db.close()

    def test_unknown_timing_keeps_both_worlds_and_global_consistency(self):
        data = self._doc(None)
        # 再加一份使用同一重发牌的报告;两份报告必须共享同一时序赋值。
        data["documents"].append({"id": "D2", "operator": "W-1",
                                  "inspector": "I-1", "at": None})
        db = connect(":memory:")
        rows = {r["doc_id"]: r for r in independence.import_data(db, data)}
        for doc in ("D1", "D2"):
            self.assertEqual(rows[doc]["state"], "pending")
            self.assertEqual(
                {ex["result"] for ex in rows[doc]["examples"]},
                {"same", "different"})
        # 同一时序世界里两份文档结论一致(不存在 D1 同一人而 D2 不同人的世界)。
        loaded = independence._load(db, "reissue")
        worlds, _ = independence._edition_worlds(
            "ED-1", loaded["documents"], loaded)
        self.assertTrue(
            all(w["D1"]["same"] == w["D2"]["same"] for w in worlds))
        db.close()

    def test_no_common_interpretation_groups_reports_with_originals(self):
        db = connect(":memory:")
        data = {
            "epoch": "dead", "shop_badges": ["W-2"], "lab_badges": ["I-1"],
            "pairings": [{"lab_badge": "I-1", "shop_badge": "W-2"}],
            "confirmed_nonmatch": {"I-1": ["W-2"]},
            "documents": [{"id": "D3", "operator": "W-2",
                           "inspector": "I-1"}],
            "mandates": [roster_mandate()],
        }
        row = independence.import_data(db, data)[0]
        self.assertEqual(row["state"], "conflict")
        self.assertEqual(row["conflict_groups"][0]["docs"], ["D3"])
        self.assertIn("dead:NON:I-1:W-2",
                      row["conflict_groups"][0]["originals"])
        # 绝不允许“全都独立”的空集结论。
        self.assertNotEqual(row["state"], "independent")
        db.close()


class EditionHistoryTests(unittest.TestCase):
    def test_later_edition_cannot_rewrite_earlier_signoff(self):
        db = connect(":memory:")
        data = {
            "epoch": "ed", "shop_badges": ["W-1", "W-2"],
            "lab_badges": ["I-1"],
            "editions": [
                {"id": "ED-1", "start": "2026-01-01", "end": "2026-02-01"},
                {"id": "ED-2", "start": "2026-02-01", "end": "2026-03-01"},
            ],
            "binding_candidates": {"I-1": ["W-1", "W-2"]},
            "pairings": [{"lab_badge": "I-1", "shop_badge": "W-2",
                          "edition": "ED-2"}],
            "documents": [
                {"id": "OLD", "operator": "W-1", "inspector": "I-1",
                 "edition": "ED-1", "at": "2026-01-15"},
                {"id": "NEW", "operator": "W-1", "inspector": "I-1",
                 "edition": "ED-2", "at": "2026-02-15"},
            ],
            "mandates": [roster_mandate()],
        }
        rows = {r["doc_id"]: r for r in independence.import_data(db, data)}
        # 旧报告仍按 ED-1 候选演绎(两种解释 -> 待核),未被 ED-2 固定配对改写。
        self.assertEqual(rows["OLD"]["state"], "pending")
        # 新报告在 ED-2 下 I-1 固定 W-2,与 W-1 不同人。
        self.assertEqual(rows["NEW"]["state"], "independent")
        db.close()


class InspectionIntegrationTests(unittest.TestCase):
    """独立性凭据是声明外部名册报告的必要条件;老报告路径不变。"""

    def setUp(self):
        self.weld = read_fixture("weld_ok.json")
        self.ndt = read_fixture("ndt_ok.json")

    def _badge(self, shop_badge, doc_id="JOINT-1"):
        return {
            "epoch": "night", "shop_badges": ["W-1", "W-2"],
            "lab_badges": ["I-1"],
            "pairings": [{"lab_badge": "I-1", "shop_badge": shop_badge}],
            "documents": [{"id": doc_id, "operator": "W-1",
                           "inspector": "I-1"}],
            "mandates": [roster_mandate()],
        }

    def _roster_ndt(self):
        ndt = json.loads(json.dumps(self.ndt))
        ndt["inspections"][0].update(
            roster_epoch="night", roster_doc="JOINT-1")
        return ndt

    def test_external_roster_report_requires_independence_credential(self):
        db = connect(":memory:")
        qualification.import_data(db, self.weld)
        # 先采录名册原件但不给委托:没有凭据,检验必须挂起。
        badge = self._badge("W-2")
        badge["mandates"] = []
        independence.import_data(db, badge)
        verdict = inspection.import_data(db, self._roster_ndt())[0]
        self.assertEqual(verdict["state"], "independence_pending")
        # 委托补齐后获证,技术检测全部通过。
        independence.import_data(
            db, {"epoch": "night", "mandates": [roster_mandate()]})
        verdict = inspection.decide(db, "JOINT-1")
        self.assertEqual(verdict["state"], "accepted")
        db.close()

    def test_not_independent_becomes_self_inspection(self):
        db = connect(":memory:")
        qualification.import_data(db, self.weld)
        independence.import_data(db, self._badge("W-1"))
        verdict = inspection.import_data(db, self._roster_ndt())[0]
        self.assertEqual(verdict["state"], "self_inspection")
        db.close()

    def test_technical_checks_unchanged_for_roster_reports(self):
        db = connect(":memory:")
        qualification.import_data(db, self.weld)
        independence.import_data(db, self._badge("W-2"))
        ndt = self._roster_ndt()
        ndt["inspections"][0]["sampled_mm"] = 50  # 低于 70% 长度
        self.assertEqual(
            inspection.import_data(db, ndt)[0]["state"],
            "insufficient_coverage")
        db.close()
        db = connect(":memory:")
        qualification.import_data(db, self.weld)
        independence.import_data(db, self._badge("W-2"))
        ndt = self._roster_ndt()
        ndt["inspections"][0].update(
            defect_position="critical", defect_mm=2.5)
        self.assertEqual(
            inspection.import_data(db, ndt)[0]["state"], "defect")
        db.close()

    def test_legacy_report_keeps_raw_string_comparison(self):
        db = connect(":memory:")
        qualification.import_data(db, self.weld)
        # 无 roster 字段:沿用原入口,字符串相等即自检,不等即通过。
        self.assertEqual(
            inspection.import_data(db, self.ndt)[0]["state"], "accepted")
        db.close()
        db = connect(":memory:")
        qualification.import_data(db, self.weld)
        bad = read_fixture("ndt_bad.json")
        self.assertEqual(
            inspection.import_data(db, bad)[0]["state"], "self_inspection")
        db.close()


class CaptureLimitsAndReplayTests(unittest.TestCase):
    def test_desk_limits_enforced_on_synthetic_originals(self):
        db = connect(":memory:")
        with self.assertRaises(ValueError):
            independence.import_data(db, {
                "epoch": "big",
                "shop_badges": ["W-1", "W-2", "W-3", "W-4", "W-5", "W-6"],
                "documents": [],
            })
        with self.assertRaises(ValueError):
            independence.import_data(db, {
                "epoch": "manydocs",
                "documents": [{"id": d, "operator": "W-1", "inspector": "I-1"}
                              for d in ("D1", "D2", "D3", "D4")],
            })
        with self.assertRaises(ValueError):
            independence.import_data(db, {
                "epoch": "threeholders",
                "issuances": [
                    {"id": str(i), "badge": "W-1", "holder": h,
                     "start": "2026-03-0%dT20:00" % i, "end": None}
                    for i, h in enumerate(("P-A", "P-B", "P-C"), start=1)],
                "documents": [],
            })
        db.close()

    def test_idempotent_capture_keeps_evidence_identity(self):
        db = connect(":memory:")
        data = read_fixture("badge_edition.json")
        data["mandates"] = [roster_mandate()]
        independence.import_data(db, data)
        first = db.execute("SELECT count(*) FROM evidence").fetchone()[0]
        independence.import_data(db, json.loads(json.dumps(data)))
        second = db.execute("SELECT count(*) FROM evidence").fetchone()[0]
        self.assertEqual(first, second)
        tampered = json.loads(json.dumps(data))
        tampered["documents"][0]["operator"] = "W-2"
        with self.assertRaises(ValueError):
            independence.import_data(db, tampered)
        db.close()

    def test_cli_badge_import_query_and_audit_replay(self):
        doc = {
            "epoch": "cli", "shop_badges": ["W-1", "W-2"],
            "lab_badges": ["I-1"],
            "pairings": [{"lab_badge": "I-1", "shop_badge": "W-2"}],
            "documents": [{"id": "D1", "operator": "W-1",
                           "inspector": "I-1"}],
            "mandates": [roster_mandate()],
        }
        with tempfile.TemporaryDirectory() as d:
            db_path = str(Path(d) / "badge.sqlite")
            input_path = Path(d) / "badge.json"
            input_path.write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            imp = subprocess.run(
                [sys.executable, "-m", "weldproof", "badge-import",
                 "--db", db_path, "--input", str(input_path)],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(imp.returncode, 0, imp.stderr)
            self.assertEqual(
                json.loads(imp.stdout)[0]["state"], "independent")
            q = subprocess.run(
                [sys.executable, "-m", "weldproof", "badge-query",
                 "--db", db_path, "--epoch", "cli", "--doc", "D1"],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(q.returncode, 0, q.stderr)
            self.assertEqual(json.loads(q.stdout)["state"], "independent")
            audit = subprocess.run(
                [sys.executable, "-m", "weldproof", "audit", "--db", db_path],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertTrue(json.loads(audit.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
