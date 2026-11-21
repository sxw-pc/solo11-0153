import json
import unittest
from pathlib import Path

from weldproof import lot_source, qualification
from weldproof.store import connect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class LotBaseline(unittest.TestCase):
    def test_qualification_changes_actual_lot_evidence_work(self):
        db = connect(":memory:")
        try:
            source = read("weld_ok.json")
            self.assertEqual("qualified", qualification.import_data(db, source)[0]["state"])
            self.assertEqual(
                "collect_for_issue", lot_source.import_data(db, read("lot_supplier_box.json"))[0]["state"]
            )
            superseding = dict(source["welds"][0])
            superseding.update(id="WELD-LATER", revision=2, welder="UNLICENSED")
            result = qualification.import_data(db, {"welds":[superseding]})
            self.assertEqual("unqualified", result[0]["state"])
            self.assertEqual("trace_only", db.execute("SELECT state FROM lot_candidate").fetchone()[0])
        finally:
            db.close()
