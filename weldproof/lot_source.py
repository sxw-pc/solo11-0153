"""Original-label intake for synthetic incoming filler material."""

from .source import record


def initialize(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS lot_candidate (
             id TEXT PRIMARY KEY,
             weld_id TEXT NOT NULL,
             lot_claim TEXT NOT NULL,
             state TEXT NOT NULL
        )"""
    )


def refresh(db, weld_id):
    initialize(db)
    qualification = db.execute(
        "SELECT state FROM qualification WHERE weld_id=?", (weld_id,)
    ).fetchone()
    state = "collect_for_issue" if qualification and qualification["state"] == "qualified" else "trace_only"
    db.execute("UPDATE lot_candidate SET state=? WHERE weld_id=?", (state,weld_id))


def import_data(db, doc):
    welds = set()
    with db:
        initialize(db)
        for item in doc["requests"]:
            if record(db, "lot-candidate", item):
                db.execute(
                    "INSERT INTO lot_candidate VALUES (?,?,?,?)",
                    (item["id"], item["weld_id"], item["lot_claim"], "trace_only"),
                )
            welds.add(item["weld_id"])
        for weld_id in sorted(welds):
            refresh(db, weld_id)
    return [dict(row) for row in db.execute("SELECT * FROM lot_candidate ORDER BY id")]
