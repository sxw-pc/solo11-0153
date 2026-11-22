import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weldproof import lot_source, qualification
from weldproof.store import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class CaptureBase(unittest.TestCase):
    """Qualified weld plus the sealed supplier carton intake."""

    def setUp(self):
        self.db = connect(":memory:")
        qualification.import_data(self.db, read("weld_ok.json"))
        lot_source.import_data(self.db, read("lot_supplier_box.json"))

    def tearDown(self):
        self.db.close()

    def observe(self, *events):
        lot_source.import_data(self.db, {"box_id": "BOX-7", "events": list(events)})
        return {
            e["id"]: self.db.execute(
                "SELECT verdict FROM lot_event WHERE id=?", (e["id"],)
            ).fetchone()[0]
            for e in events
        }

    def issue(self, issue_id, request, slot):
        lot_source.import_data(
            self.db,
            {"box_id": "BOX-7", "issues": [{"id": issue_id, "request": request, "slot": slot}]},
        )
        return self.db.execute(
            "SELECT verdict FROM lot_issue WHERE id=?", (issue_id,)
        ).fetchone()[0]

    def status(self):
        return lot_source.box_status(self.db, "BOX-7")


class JointConstraint(CaptureBase):
    def test_finite_contents_yield_exactly_two_explanations(self):
        status = self.status()
        self.assertEqual(
            [{"x": "A", "y": "B", "z": "C"}, {"x": "B", "y": "C", "z": "A"}],
            status["explanations"],
        )
        self.assertEqual(2, len(status["authorizations"]))
        # x 在两种解释下都属于 A/B 代料池，但不能写成确切 A。
        self.assertTrue(status["slots"]["x"]["pool_provable"])
        self.assertIsNone(status["slots"]["x"]["exact_lot"])
        # y、z 各自只在一种解释里属于代料池，候选池本身不能证明它们。
        self.assertFalse(status["slots"]["y"]["pool_provable"])
        self.assertFalse(status["slots"]["z"]["pool_provable"])
        self.assertEqual([{"kind": "pool", "slot": "x"}], status["continuable_certificates"])

    def test_candidate_pool_alone_cannot_issue(self):
        # 不能给 x 写 A 下发；两个摊位也不能把 y、z 录为 A/B 实际领料。
        self.assertEqual("rejected_unproven", self.issue("I-XE", "LOT-JOB", "x"))
        self.assertEqual("rejected_unproven", self.issue("I-YP", "LOT-POOL-1", "y"))
        self.assertEqual("rejected_unproven", self.issue("I-ZP", "LOT-POOL-2", "z"))
        # x 的代料池证明来自封签联合约束，可以直接领。
        self.assertEqual("admitted_pool", self.issue("I-XP", "LOT-POOL-1", "x"))
        row = self.db.execute(
            "SELECT slot, mode, lot FROM lot_issue WHERE id='I-XP'"
        ).fetchone()
        self.assertEqual(("x", "pool", "A/B"), tuple(row))


class EvidenceSeparation(CaptureBase):
    def test_witnessed_engraving_supports_exact_slip(self):
        verdicts = self.observe(
            {"id": "ENG-X", "kind": "core_engraving", "slot": "x", "lot": "A",
             "witness": "WIT-1", "auth": "AUTH-CORE-7", "original_core": True}
        )
        self.assertEqual("admitted", verdicts["ENG-X"])
        status = self.status()
        # 消除一条完整解释后，确切炉批沿刻印证据链传导。
        self.assertEqual([{"x": "A", "y": "B", "z": "C"}], status["exact_explanations"])
        self.assertEqual("A", status["slots"]["x"]["exact_lot"])
        self.assertEqual("C", status["slots"]["z"]["exact_lot"])
        self.assertEqual("admitted_exact", self.issue("I-X", "LOT-JOB", "x"))
        row = self.db.execute(
            "SELECT slot, mode, lot, basis FROM lot_issue WHERE id='I-X'"
        ).fetchone()
        self.assertEqual(("x", "exact", "A"), tuple(row)[:3])
        self.assertIn("ENG-X", json.loads(row["basis"]))
        # 已领出的卷不能再次签发。
        self.assertEqual("rejected_consumed", self.issue("I-X2", "LOT-POOL-1", "x"))
        self.assertEqual("admitted_pool", self.issue("I-Y", "LOT-POOL-1", "y"))

    def test_pool_verification_never_writes_heat_lot(self):
        # 验池把解释收窄到一条，但确切炉批仍不能据此补写。
        verdicts = self.observe(
            {"id": "VER-Y", "kind": "pool_verification", "slot": "y",
             "card": "CARD-1", "auth": "AUTH-POOL-7"}
        )
        self.assertEqual("admitted", verdicts["VER-Y"])
        status = self.status()
        self.assertEqual([{"x": "A", "y": "B", "z": "C"}], status["explanations"])
        self.assertEqual(2, len(status["exact_explanations"]))
        self.assertEqual("rejected_unproven", self.issue("I-XE", "LOT-JOB", "x"))
        # 验池结论只覆盖被检的这一卷。
        self.assertEqual("admitted_pool", self.issue("I-YP", "LOT-POOL-1", "y"))
        self.assertEqual("rejected_not_in_pool", self.issue("I-ZP", "LOT-POOL-2", "z"))

    def test_verification_covers_exactly_one_roll_of_one_box(self):
        self.observe(
            {"id": "VER-X", "kind": "pool_verification", "slot": "x",
             "card": "CARD-1", "auth": "AUTH-POOL-7"}
        )
        # x 的验池不能覆盖 y，也不能覆盖附近箱同时取出的另一卷。
        self.assertEqual("rejected_unproven", self.issue("I-YP", "LOT-POOL-1", "y"))
        verdicts = self.observe(
            {"id": "VER-FAR", "kind": "pool_verification", "box_id": "BOX-ELSE",
             "slot": "x", "card": "CARD-2", "auth": "AUTH-POOL-7"}
        )
        self.assertEqual("rejected_unknown_box", verdicts["VER-FAR"])

    def test_empty_core_blocks_exact_but_pool_fallback_issues(self):
        # 换过空芯的卷不能只靠新芯刻印，旧卷芯路线始终不能给炉批。
        verdicts = self.observe(
            {"id": "ENG-X", "kind": "core_engraving", "slot": "x", "lot": "A",
             "witness": "WIT-1", "auth": "AUTH-CORE-7", "original_core": False}
        )
        self.assertEqual("rejected_empty_core", verdicts["ENG-X"])
        self.assertEqual("rejected_unproven", self.issue("I-XE", "LOT-JOB", "x"))
        # 验池在旧卷芯刻印不可得时仍合法支持按代料池领用。
        verdicts = self.observe(
            {"id": "VER-X", "kind": "pool_verification", "slot": "x",
             "card": "CARD-1", "auth": "AUTH-POOL-7"}
        )
        self.assertEqual("admitted", verdicts["VER-X"])
        # 验池之后依旧不能补写确切炉批。
        self.assertEqual("rejected_unproven", self.issue("I-XE2", "LOT-JOB", "x"))
        self.assertEqual("admitted_pool", self.issue("I-XP", "LOT-POOL-1", "x"))

    def test_observations_require_preauthorization_and_card(self):
        verdicts = self.observe(
            {"id": "ENG-NOAUTH", "kind": "core_engraving", "slot": "x", "lot": "A",
             "witness": "WIT-1", "auth": "AUTH-ELSE", "original_core": True},
            {"id": "VER-NOCARD", "kind": "pool_verification", "slot": "x",
             "auth": "AUTH-POOL-7"},
            {"id": "ENG-BADLOT", "kind": "core_engraving", "slot": "x", "lot": "C",
             "witness": "WIT-1", "auth": "AUTH-CORE-7", "original_core": True},
        )
        self.assertEqual("rejected_unauthorized", verdicts["ENG-NOAUTH"])
        self.assertEqual("rejected_no_card", verdicts["VER-NOCARD"])
        self.assertEqual("rejected_inconsistent", verdicts["ENG-BADLOT"])
        self.assertEqual(2, len(self.status()["explanations"]))


class SealCutRecovery(CaptureBase):
    def setUp(self):
        super().setUp()
        lot_source.import_data(
            self.db,
            {"box_id": "BOX-7", "events": [
                {"id": "CUT-X", "kind": "seal_cut", "slot": "x", "seal": "SEAL-7",
                 "fixture": "FIXTURE-1", "status": "pending"}]},
        )

    def test_disconnect_window_reports_safe_observations_and_certificates(self):
        status = self.status()
        # 掉线窗口内既不能确认已剪签，也不能确认还在原箱。
        self.assertEqual("cut_pending", status["slots"]["x"]["custody"])
        self.assertEqual([], status["executable_observations"]["x"])
        self.assertEqual(
            ["core_engraving", "pool_verification"], status["executable_observations"]["y"]
        )
        self.assertEqual(
            ["core_engraving", "pool_verification"], status["executable_observations"]["z"]
        )
        # 剪签本身不是炉批证据，解释不变；x 的代料池证明可以继续发。
        self.assertEqual(2, len(status["explanations"]))
        self.assertIn({"kind": "pool", "slot": "x"}, status["continuable_certificates"])
        # 失联期间对 x 的两条观察路线都不能当作已执行。
        verdicts = self.observe(
            {"id": "ENG-EARLY", "kind": "core_engraving", "slot": "x", "lot": "A",
             "witness": "WIT-1", "auth": "AUTH-CORE-7", "original_core": True},
            {"id": "VER-EARLY", "kind": "pool_verification", "slot": "x",
             "card": "CARD-1", "auth": "AUTH-POOL-7"},
        )
        self.assertEqual("rejected_custody", verdicts["ENG-EARLY"])
        self.assertEqual("rejected_not_intact", verdicts["VER-EARLY"])
        self.assertEqual("cut_pending", self.status()["slots"]["x"]["custody"])

    def test_station_claims_cannot_piecetogether_three_slips(self):
        verdicts = self.observe(
            {"id": "CLAIM-Y", "kind": "station_claim", "slot": "y", "claim": "A/B",
             "station": "STALL-1", "seal": "SEAL-7"},
            {"id": "CLAIM-Z", "kind": "station_claim", "slot": "z", "claim": "A/B",
             "station": "STALL-2", "seal": "SEAL-7"},
        )
        # 同一封签的一组，但任何单一解释都不能同时满足 y、z 都属于代料池。
        self.assertEqual("recorded_unproven", verdicts["CLAIM-Y"])
        self.assertEqual("recorded_unproven", verdicts["CLAIM-Z"])
        status = self.status()
        self.assertFalse(status["claims_compatible"])
        # 只写“A/B”的台面记录不是授权证据，拼不出能签的单。
        self.assertEqual("rejected_unproven", self.issue("I-YP", "LOT-POOL-1", "y"))
        self.assertEqual("rejected_unproven", self.issue("I-ZP", "LOT-POOL-2", "z"))
        # 不属于该封签一组的记录直接拒收。
        verdicts = self.observe(
            {"id": "CLAIM-Z2", "kind": "station_claim", "slot": "z", "claim": "A/B",
             "station": "STALL-2", "seal": "SEAL-ELSE"}
        )
        self.assertEqual("rejected_seal_mismatch", verdicts["CLAIM-Z2"])

    def test_custody_original_restores_exact_route_and_keeps_cut_history(self):
        verdicts = self.observe(
            {"id": "CUSTODY-X", "kind": "custody_original", "slot": "x",
             "seal": "SEAL-7", "fixture": "FIXTURE-1"}
        )
        self.assertEqual("confirmed", verdicts["CUSTODY-X"])
        self.assertEqual("cut_confirmed", self.status()["slots"]["x"]["custody"])
        # 开芯后的卷不再完好，验池路线关闭；有见证的原刻印仍可采信。
        verdicts = self.observe(
            {"id": "VER-LATE", "kind": "pool_verification", "slot": "x",
             "card": "CARD-1", "auth": "AUTH-POOL-7"},
            {"id": "ENGRAVE-X", "kind": "core_engraving", "slot": "x", "lot": "A",
             "witness": "WIT-1", "auth": "AUTH-CORE-7", "original_core": True},
        )
        self.assertEqual("rejected_not_intact", verdicts["VER-LATE"])
        self.assertEqual("admitted", verdicts["ENGRAVE-X"])
        # 同一货箱同一领用机会：确切单能签，另一卷的代料只能等待。
        self.assertEqual("admitted_exact", self.issue("ISSUE-X", "LOT-JOB", "x"))
        self.assertEqual("admitted_pool", self.issue("ISSUE-Y", "LOT-POOL-1", "y"))
        self.assertEqual("rejected_not_in_pool", self.issue("ISSUE-Z", "LOT-POOL-2", "z"))
        # 已实施剪签的历史保留在账上。
        cut = self.db.execute(
            "SELECT verdict FROM lot_event WHERE id='CUT-X'"
        ).fetchone()
        self.assertEqual("recorded_pending", cut[0])


class WitnessFlow(CaptureBase):
    def test_supplier_box_and_witness_acceptance_flow(self):
        lot_source.import_data(self.db, read("lot_witness_acceptance.json"))
        events = {
            row["id"]: row["verdict"]
            for row in self.db.execute("SELECT id, verdict FROM lot_event")
        }
        self.assertEqual(
            {
                "CUT-X": "recorded_pending",
                "CLAIM-Y": "recorded_unproven",
                "CLAIM-Z": "recorded_unproven",
                "CUSTODY-X": "confirmed",
                "ENGRAVE-X": "admitted",
            },
            events,
        )
        issues = {
            row["id"]: row["verdict"]
            for row in self.db.execute("SELECT id, verdict FROM lot_issue")
        }
        self.assertEqual(
            {"ISSUE-X": "admitted_exact", "ISSUE-Y": "admitted_pool",
             "ISSUE-Z": "rejected_not_in_pool"},
            issues,
        )
        status = self.status()
        self.assertEqual([{"x": "A", "y": "B", "z": "C"}], status["explanations"])
        self.assertEqual("issued", status["slots"]["x"]["custody"])
        self.assertEqual("issued", status["slots"]["y"]["custody"])
        self.assertEqual("sealed", status["slots"]["z"]["custody"])
        self.assertFalse(status["claims_compatible"])


class QualificationLinkage(CaptureBase):
    def test_trace_only_blocks_new_issue_but_still_collects_sources(self):
        source = read("weld_ok.json")
        superseding = dict(source["welds"][0])
        superseding.update(id="WELD-LATER", revision=2, welder="UNLICENSED")
        qualification.import_data(self.db, {"welds": [superseding]})
        self.assertEqual(
            {"trace_only"},
            {row[0] for row in self.db.execute("SELECT state FROM lot_candidate")},
        )
        # trace_only 只能采集来源做追踪。
        verdicts = self.observe(
            {"id": "VER-X", "kind": "pool_verification", "slot": "x",
             "card": "CARD-1", "auth": "AUTH-POOL-7"}
        )
        self.assertEqual("admitted", verdicts["VER-X"])
        # 不能继续新领料。
        self.assertEqual("rejected_trace_only", self.issue("I-XP", "LOT-POOL-1", "x"))
        # 资格恢复后同一卷仍可凭代料池证明领用。
        restored = dict(source["welds"][0])
        restored.update(id="WELD-FINAL", revision=3)
        qualification.import_data(self.db, {"welds": [restored]})
        self.assertEqual(
            {"collect_for_issue"},
            {row[0] for row in self.db.execute("SELECT state FROM lot_candidate")},
        )
        self.assertEqual("admitted_pool", self.issue("I-XP2", "LOT-POOL-1", "x"))


class ReplayConsistency(unittest.TestCase):
    def test_cli_replay_matches_live_and_keeps_cut_history(self):
        with tempfile.TemporaryDirectory() as d:
            dbpath = str(Path(d) / "state.sqlite")
            for command, name in [
                ("weld-import", "weld_ok.json"),
                ("lot-import", "lot_supplier_box.json"),
                ("lot-import", "lot_witness_acceptance.json"),
            ]:
                r = subprocess.run(
                    [sys.executable, "-m", "weldproof", command, "--db", dbpath,
                     "--input", str(FIXTURES / name)],
                    cwd=ROOT, capture_output=True, text=True,
                )
                self.assertEqual(0, r.returncode, r.stderr)
            r = subprocess.run(
                [sys.executable, "-m", "weldproof", "lot-status", "--db", dbpath],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual("BOX-7", json.loads(r.stdout)["box_id"])
            r = subprocess.run(
                [sys.executable, "-m", "weldproof", "audit", "--db", dbpath],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertTrue(json.loads(r.stdout)["ok"])
            db = connect(dbpath)
            try:
                # 回传失败不抹账：从采录文件重放后剪签历史仍在。
                replay = connect(":memory:")
                handlers = {
                    "weld-import": qualification.import_data,
                    "lot-import": lot_source.import_data,
                }
                for row in db.execute("SELECT command, raw FROM file_journal ORDER BY id"):
                    handlers[row["command"]](replay, json.loads(row["raw"]))
                self.assertEqual(lot_source.snapshot(db), lot_source.snapshot(replay))
                cut = replay.execute(
                    "SELECT verdict FROM lot_event WHERE id='CUT-X'"
                ).fetchone()
                self.assertEqual("recorded_pending", cut[0])
                replay.close()
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
