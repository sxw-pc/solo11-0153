import json
import unittest
from pathlib import Path

from weldproof import inspection, qualification
from weldproof.store import connect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class BadgeBaseline(unittest.TestCase):
    def test_qualification_revision_changes_inspection_verdict(self):
        db = connect(":memory:")
        try:
            weld = read("weld_ok.json")
            ndt = read("ndt_ok.json")
            weld_id = weld["welds"][0]["weld_id"]
            qualification.import_data(db, weld)
            self.assertEqual("accepted", inspection.import_data(db, ndt)[0]["state"])
            rev = dict(weld["welds"][0])
            rev.update(id="WELD-LATE", welder="UNLICENSED", revision=2)
            result = qualification.import_data(db, {"welds": [rev]})[0]
            self.assertEqual("unqualified", result["state"])
            state = db.execute(
                "SELECT state FROM inspection_verdict WHERE weld_id=?", (weld_id,)
            ).fetchone()[0]
            self.assertEqual("qualification_hold", state)
        finally:
            db.close()
