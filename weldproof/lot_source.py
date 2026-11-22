"""Sealed-carton intake for synthetic incoming filler material.

The original-label registration is extended with box-original intake: a
sealed supplier carton guarantees a finite content multiset and every
slot inside carries an inner-label candidate set.  Together they define
a finite set of joint explanations (worlds) for what each physical roll
actually is.  Two pre-authorized on-site observation kinds may narrow
those explanations:

* ``core_engraving`` — a witnessed read of the original spool core.  It
  is exact evidence and can support slips that name an exact heat lot.
* ``pool_verification`` — an authorized checker that only states whether
  the one intact roll inside belongs to the approved replacement pool.
  It never yields an exact heat lot and covers exactly that one roll.

Exact and pool evidence are never mixed: pool verification may narrow
the live explanations but is never used to write an exact heat lot, and
a bare station claim of "A/B" proves nothing by itself.  Sign-out
records are admitted only when the requested proof holds and the linked
requisition is in ``collect_for_issue`` state; ``trace_only``
requisitions keep collecting sources for tracing but cannot start new
issues.  All intake is journaled so an offline replay rebuilds the same
state, including an already implemented seal cut.
"""

import json

from .source import record

POOL_CLAIM = "A/B"

_EVENT_KINDS = (
    "seal_cut",
    "custody_original",
    "core_engraving",
    "pool_verification",
    "station_claim",
)


def initialize(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS lot_candidate (
             id TEXT PRIMARY KEY,
             weld_id TEXT NOT NULL,
             lot_claim TEXT NOT NULL,
             state TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS lot_box (
             id TEXT PRIMARY KEY,
             seal TEXT NOT NULL,
             contents TEXT NOT NULL,
             pool TEXT NOT NULL,
             authorizations TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS lot_slot (
             box_id TEXT NOT NULL,
             slot TEXT NOT NULL,
             candidates TEXT NOT NULL,
             custody TEXT NOT NULL,
             PRIMARY KEY (box_id, slot)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS lot_event (
             id TEXT PRIMARY KEY,
             box_id TEXT NOT NULL,
             kind TEXT NOT NULL,
             slot TEXT,
             verdict TEXT NOT NULL,
             detail TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS lot_issue (
             id TEXT PRIMARY KEY,
             request_id TEXT NOT NULL,
             box_id TEXT NOT NULL,
             slot TEXT NOT NULL,
             mode TEXT NOT NULL,
             lot TEXT NOT NULL,
             verdict TEXT NOT NULL,
             basis TEXT NOT NULL
        )"""
    )


def refresh(db, weld_id):
    initialize(db)
    qualification = db.execute(
        "SELECT state FROM qualification WHERE weld_id=?", (weld_id,)
    ).fetchone()
    state = "collect_for_issue" if qualification and qualification["state"] == "qualified" else "trace_only"
    db.execute("UPDATE lot_candidate SET state=? WHERE weld_id=?", (state,weld_id))


def _joint_explanations(contents, candidates):
    """Every slot assignment matching the inner-label candidates and the
    guaranteed finite carton contents at the same time."""
    slots = sorted(candidates)
    worlds = []

    def walk(index, remaining, assigned):
        if index == len(slots):
            if all(left == 0 for left in remaining.values()):
                worlds.append(dict(assigned))
            return
        slot = slots[index]
        for lot in candidates[slot]:
            if remaining.get(lot, 0) > 0:
                remaining[lot] -= 1
                assigned[slot] = lot
                walk(index + 1, remaining, assigned)
                del assigned[slot]
                remaining[lot] += 1

    walk(0, dict(contents), {})
    return worlds


def _load_box(db, box_id):
    box = db.execute("SELECT * FROM lot_box WHERE id=?", (box_id,)).fetchone()
    if not box:
        return None
    slots = {
        row["slot"]: {"candidates": json.loads(row["candidates"]), "custody": row["custody"]}
        for row in db.execute("SELECT * FROM lot_slot WHERE box_id=?", (box_id,))
    }
    return {
        "id": box["id"],
        "seal": box["seal"],
        "contents": json.loads(box["contents"]),
        "pool": json.loads(box["pool"]),
        "authorizations": json.loads(box["authorizations"]),
        "slots": slots,
    }


def _admitted_events(db, box_id, kind):
    return [
        json.loads(row["detail"])
        for row in db.execute(
            "SELECT detail FROM lot_event WHERE box_id=? AND kind=? AND verdict='admitted' ORDER BY rowid",
            (box_id, kind),
        )
    ]


def _explanations(db, box):
    """Split explanations: exact evidence alone vs. all admitted evidence.

    ``exact`` worlds only respect witnessed core engravings, so an exact
    heat lot can never be derived from a pool verification; ``remaining``
    worlds respect both authorized observation kinds.
    """
    candidates = {slot: s["candidates"] for slot, s in box["slots"].items()}
    exact = _joint_explanations(box["contents"], candidates)
    for ev in _admitted_events(db, box["id"], "core_engraving"):
        exact = [w for w in exact if w.get(ev["slot"]) == ev.get("lot")]
    remaining = list(exact)
    for ev in _admitted_events(db, box["id"], "pool_verification"):
        remaining = [w for w in remaining if w.get(ev["slot"]) in box["pool"]]
    return exact, remaining


def _slot_proof(box, exact_worlds, remaining_worlds, slot):
    exact_values = {w[slot] for w in exact_worlds}
    return {
        "exact_lot": next(iter(exact_values)) if len(exact_values) == 1 else None,
        "pool_provable": bool(remaining_worlds) and all(w[slot] in box["pool"] for w in remaining_worlds),
        "pool_excluded": bool(remaining_worlds) and all(w[slot] not in box["pool"] for w in remaining_worlds),
    }


def _executable_observations(custody):
    """Observations still executable under every current explanation of
    the roll's physical state."""
    if custody == "sealed":
        return ["core_engraving", "pool_verification"]
    if custody == "cut_confirmed":
        return ["core_engraving"]
    return []


def _set_custody(db, box_id, slot, custody):
    db.execute(
        "UPDATE lot_slot SET custody=? WHERE box_id=? AND slot=?",
        (custody, box_id, slot),
    )


def _authorized(box, ev, kind):
    return ev.get("auth") in {a.get("id") for a in box["authorizations"] if a.get("kind") == kind}


def _seal_cut(db, box, ev, custody):
    if ev.get("seal") != box["seal"]:
        return "rejected_seal_mismatch"
    if custody != "sealed":
        return "rejected_state"
    pending = ev.get("status") == "pending"
    _set_custody(db, box["id"], ev["slot"], "cut_pending" if pending else "cut_confirmed")
    return "recorded_pending" if pending else "recorded_confirmed"


def _custody_original(db, box, ev, custody):
    if ev.get("seal") != box["seal"]:
        return "rejected_seal_mismatch"
    if custody == "cut_pending":
        _set_custody(db, box["id"], ev["slot"], "cut_confirmed")
        return "confirmed"
    return "recorded"


def _core_engraving(db, box, ev, custody):
    if not _authorized(box, ev, "core_engraving"):
        return "rejected_unauthorized"
    if not ev.get("witness"):
        return "rejected_unwitnessed"
    if ev.get("original_core") is not True:
        return "rejected_empty_core"
    if ev.get("lot") not in box["slots"][ev["slot"]]["candidates"]:
        return "rejected_inconsistent"
    if custody == "cut_pending":
        return "rejected_custody"
    if custody == "issued":
        return "rejected_state"
    return "admitted"


def _pool_verification(db, box, ev, custody):
    if not _authorized(box, ev, "pool_verification"):
        return "rejected_unauthorized"
    if not ev.get("card"):
        return "rejected_no_card"
    if custody != "sealed":
        return "rejected_not_intact"
    return "admitted"


def _station_claim(db, box, ev):
    if ev.get("seal") != box["seal"]:
        return "rejected_seal_mismatch"
    _, remaining = _explanations(db, box)
    slot, claim = ev["slot"], ev.get("claim")
    if claim == POOL_CLAIM:
        satisfiable = any(w[slot] in box["pool"] for w in remaining)
    else:
        satisfiable = any(w[slot] == claim for w in remaining)
    return "recorded_unproven" if satisfiable else "rejected_inconsistent"


def _apply_event(db, box_id, ev):
    kind, slot = ev.get("kind"), ev.get("slot")
    box = _load_box(db, box_id)
    if box is None:
        verdict = "rejected_unknown_box"
    elif kind not in _EVENT_KINDS:
        verdict = "rejected_kind"
    elif slot not in box["slots"]:
        verdict = "rejected_unknown_slot"
    else:
        custody = box["slots"][slot]["custody"]
        if kind == "seal_cut":
            verdict = _seal_cut(db, box, ev, custody)
        elif kind == "custody_original":
            verdict = _custody_original(db, box, ev, custody)
        elif kind == "core_engraving":
            verdict = _core_engraving(db, box, ev, custody)
        elif kind == "pool_verification":
            verdict = _pool_verification(db, box, ev, custody)
        else:
            verdict = _station_claim(db, box, ev)
    db.execute(
        "INSERT INTO lot_event VALUES (?,?,?,?,?,?)",
        (ev["id"], box_id or "", kind or "", slot, verdict, json.dumps(ev, ensure_ascii=False, sort_keys=True)),
    )
    return verdict


def _apply_issue(db, box_id, iss):
    box = _load_box(db, box_id)
    request = db.execute(
        "SELECT * FROM lot_candidate WHERE id=?", (iss.get("request"),)
    ).fetchone()
    slot = iss.get("slot")
    mode, lot, basis = "", "", []
    if box is None:
        verdict = "rejected_unknown_box"
    elif request is None:
        verdict = "rejected_unknown_request"
    elif slot not in box["slots"]:
        verdict = "rejected_unknown_slot"
    elif db.execute(
        "SELECT 1 FROM lot_issue WHERE request_id=? AND verdict LIKE 'admitted%'",
        (request["id"],),
    ).fetchone():
        verdict = "rejected_already_filled"
    elif box["slots"][slot]["custody"] == "issued":
        verdict = "rejected_consumed"
    elif request["state"] != "collect_for_issue":
        verdict = "rejected_trace_only"
    else:
        exact_worlds, remaining_worlds = _explanations(db, box)
        proof = _slot_proof(box, exact_worlds, remaining_worlds, slot)
        claim = request["lot_claim"]
        if claim == POOL_CLAIM:
            mode = "pool"
            if proof["pool_provable"]:
                verdict, lot = "admitted_pool", POOL_CLAIM
                basis = ["sealed-carton:" + box["seal"]] + sorted(
                    ev["id"]
                    for kind in ("core_engraving", "pool_verification")
                    for ev in _admitted_events(db, box["id"], kind)
                )
            elif proof["pool_excluded"]:
                verdict = "rejected_not_in_pool"
            else:
                verdict = "rejected_unproven"
        else:
            mode = "exact"
            if proof["exact_lot"] == claim:
                verdict, lot = "admitted_exact", claim
                basis = ["sealed-carton:" + box["seal"]] + sorted(
                    ev["id"] for ev in _admitted_events(db, box["id"], "core_engraving")
                )
            elif proof["exact_lot"] is None:
                verdict = "rejected_unproven"
            else:
                verdict = "rejected_wrong_lot"
    if verdict.startswith("admitted"):
        _set_custody(db, box_id, slot, "issued")
    db.execute(
        "INSERT INTO lot_issue VALUES (?,?,?,?,?,?,?,?)",
        (
            iss["id"],
            iss.get("request") or "",
            box_id or "",
            slot or "",
            mode,
            lot or (request["lot_claim"] if request else ""),
            verdict,
            json.dumps(basis),
        ),
    )
    return verdict


def _import_box(db, doc):
    box_id = doc.get("box_id")
    if "sealed_carton_contents" not in doc:
        return box_id
    box_id = box_id or "BOX-1"
    boxdoc = {
        "id": box_id,
        "seal": doc.get("seal", box_id),
        "contents": doc["sealed_carton_contents"],
        "slots": doc["coil_slot_candidates"],
        "pool": doc["eligible_replacement_pool"],
        "preauthorizations": doc.get("preauthorizations", []),
    }
    if record(db, "lot-box", boxdoc):
        db.execute(
            "INSERT INTO lot_box VALUES (?,?,?,?,?)",
            (
                box_id,
                boxdoc["seal"],
                json.dumps(boxdoc["contents"], sort_keys=True),
                json.dumps(boxdoc["pool"], sort_keys=True),
                json.dumps(boxdoc["preauthorizations"], sort_keys=True),
            ),
        )
        for slot, candidates in sorted(boxdoc["slots"].items()):
            db.execute(
                "INSERT INTO lot_slot VALUES (?,?,?,?)",
                (box_id, slot, json.dumps(candidates, sort_keys=True), "sealed"),
            )
    return box_id


def import_data(db, doc):
    welds = set()
    with db:
        initialize(db)
        for item in doc.get("requests", []):
            if record(db, "lot-candidate", item):
                db.execute(
                    "INSERT INTO lot_candidate VALUES (?,?,?,?)",
                    (item["id"], item["weld_id"], item["lot_claim"], "trace_only"),
                )
            welds.add(item["weld_id"])
        box_id = _import_box(db, doc)
        for ev in doc.get("events", []):
            if record(db, "lot-event", ev):
                _apply_event(db, ev.get("box_id") or box_id, ev)
        for iss in doc.get("issues", []):
            if record(db, "lot-issue", iss):
                _apply_issue(db, iss.get("box_id") or box_id, iss)
        for weld_id in sorted(welds):
            refresh(db, weld_id)
    return [dict(row) for row in db.execute("SELECT * FROM lot_candidate ORDER BY id")]


def box_status(db, box_id=None):
    """Current explanations, still-executable observations and the
    certificates that can continue to be issued for one sealed carton."""
    initialize(db)
    if box_id is None:
        ids = [row[0] for row in db.execute("SELECT id FROM lot_box ORDER BY id")]
        if len(ids) != 1:
            raise ValueError("box id required")
        box_id = ids[0]
    box = _load_box(db, box_id)
    if box is None:
        raise ValueError("unknown box: " + str(box_id))
    exact_worlds, remaining_worlds = _explanations(db, box)
    slots = {}
    for slot in sorted(box["slots"]):
        proof = _slot_proof(box, exact_worlds, remaining_worlds, slot)
        slots[slot] = {
            "custody": box["slots"][slot]["custody"],
            "candidates": box["slots"][slot]["candidates"],
            "exact_lot": proof["exact_lot"],
            "pool_provable": proof["pool_provable"],
        }
    continuable = []
    for slot in sorted(slots):
        if slots[slot]["custody"] == "issued":
            continue
        if slots[slot]["exact_lot"] is not None:
            continuable.append({"slot": slot, "kind": "exact", "lot": slots[slot]["exact_lot"]})
        if slots[slot]["pool_provable"]:
            continuable.append({"slot": slot, "kind": "pool"})
    claims = [
        {
            "id": row["id"],
            "slot": row["slot"],
            "claim": json.loads(row["detail"]).get("claim"),
            "verdict": row["verdict"],
        }
        for row in db.execute(
            "SELECT * FROM lot_event WHERE box_id=? AND kind='station_claim' ORDER BY rowid",
            (box_id,),
        )
    ]
    open_claims = [c for c in claims if c["verdict"] == "recorded_unproven" and c["claim"] == POOL_CLAIM]
    claims_compatible = (
        any(all(w[c["slot"]] in box["pool"] for c in open_claims) for w in remaining_worlds)
        if open_claims
        else True
    )
    return {
        "box_id": box["id"],
        "seal": box["seal"],
        "pool": box["pool"],
        "authorizations": box["authorizations"],
        "explanations": remaining_worlds,
        "exact_explanations": exact_worlds,
        "slots": slots,
        "executable_observations": {
            slot: _executable_observations(info["custody"]) for slot, info in slots.items()
        },
        "continuable_certificates": continuable,
        "station_claims": claims,
        "claims_compatible": claims_compatible,
        "requests": [dict(row) for row in db.execute("SELECT * FROM lot_candidate ORDER BY id")],
        "events": [
            dict(row)
            for row in db.execute(
                "SELECT id,kind,slot,verdict FROM lot_event WHERE box_id=? ORDER BY rowid",
                (box_id,),
            )
        ],
        "issues": [
            dict(row)
            for row in db.execute("SELECT * FROM lot_issue WHERE box_id=? ORDER BY rowid", (box_id,))
        ],
    }


def snapshot(db):
    """All lot-source tables, for offline replay comparison."""
    initialize(db)
    return {
        table: [dict(row) for row in db.execute("SELECT * FROM %s ORDER BY 1, 2" % table)]
        for table in ("lot_candidate", "lot_box", "lot_slot", "lot_event", "lot_issue")
    }
