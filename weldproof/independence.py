"""工牌号与签认来源采录、跨册独立性凭据。

车间工牌(W-*)与实验室牌(I-*)分属两套编号。本模块只依据合成原件
(门禁候选、见证非同一、固定配对、换班重发、实名核证委托与回执)
推导同一班内两名实体能否被证明为不同的人;未记录内部候选全集时
允许暂时无法确定,绝不自行补猜。不能用最新名册改写更早签认的关系。
"""
import json
from itertools import product

from .source import record
from .store import history

# 任务限定:一班两次发放、五张牌、三个报告,全部以合成原件给出。
# 两次发放 = 同一张外观重复的牌在换班处最多经历一次重发(每人时段记录最多两段)。
LIMIT_HOLDERS_PER_BADGE = 2
LIMIT_BADGES = 5
LIMIT_DOCUMENTS = 3


def import_data(db, data):
    """采录一批工牌/签认来源原件,返回受影响班(epoch)的当前裁决。"""
    epoch = data["epoch"]
    with db:
        editions = data.get("editions")
        if editions is None:
            editions = [{"id": "ED-1", "start": None, "end": None}]
        all_badges = set(data.get("shop_badges", ())) | set(data.get("lab_badges", ()))
        docs = data.get("documents", [])
        issuances = data.get("issuances", [])
        if len(all_badges) > LIMIT_BADGES:
            raise ValueError("badge desk limit: at most %d badges per shift" % LIMIT_BADGES)
        if len(docs) > LIMIT_DOCUMENTS:
            raise ValueError("badge desk limit: at most %d reports per shift" % LIMIT_DOCUMENTS)
        holders_per_badge = {}
        for iss in issuances:
            holders_per_badge.setdefault(iss["badge"], set()).add(iss["holder"])
        if any(len(h) > LIMIT_HOLDERS_PER_BADGE for h in holders_per_badge.values()):
            raise ValueError("badge desk limit: at most %d issuances per badge per shift"
                             % LIMIT_HOLDERS_PER_BADGE)

        mandates = data.get("mandates", [])
        witness_only = any(m["kind"] == "witness" and not m.get("roster_export", False) for m in mandates)
        if witness_only and (data.get("binding_candidates") or data.get("confirmed_nonmatch") or data.get("pairings")):
            raise PermissionError("roster export not authorized under witness-only mandate")

        for m in mandates:
            if record(db, "mandate", m):
                db.execute(
                    "INSERT INTO verification_mandate VALUES(?,?,?,?,?,?)",
                    (m["id"], epoch, m["kind"], 1 if m.get("roster_export", False) else 0,
                     m.get("scope", "shift"), 1 if m.get("complete", False) else 0),
                )

        for ed in editions:
            eid = ed["id"]
            raw = {"id": "%s:ED:%s" % (epoch, eid), "epoch": epoch,
                   "edition": eid, "start": ed.get("start"), "end": ed.get("end")}
            if record(db, "edition", raw):
                db.execute("INSERT INTO badge_edition VALUES(?,?,?,?)",
                           (epoch, eid, ed.get("start"), ed.get("end")))

        for lab, shops in (data.get("binding_candidates") or {}).items():
            cid = "%s:CAND:%s" % (epoch, lab)
            raw = {"id": cid, "epoch": epoch, "edition": editions[0]["id"],
                   "lab_badge": lab, "shop_badges": shops}
            if record(db, "candidate", raw):
                db.execute("INSERT INTO badge_candidate VALUES(?,?,?,?,?)",
                           (cid, epoch, editions[0]["id"], lab, json.dumps(shops)))
            all_badges.add(lab)
            all_badges.update(shops)

        for lab, shops in (data.get("confirmed_nonmatch") or {}).items():
            for shop in shops:
                nid = "%s:NON:%s:%s" % (epoch, lab, shop)
                raw = {"id": nid, "epoch": epoch, "edition": editions[0]["id"],
                       "left": lab, "right": shop}
                if record(db, "nonmatch", raw):
                    db.execute("INSERT INTO badge_nonmatch VALUES(?,?,?,?,?)",
                               (nid, epoch, editions[0]["id"], lab, shop))
                all_badges.add(lab)
                all_badges.add(shop)

        for p in data.get("pairings", []):
            pid = "%s:PAIR:%s" % (epoch, p["lab_badge"])
            raw = dict(p, id=pid, epoch=epoch)
            if record(db, "pairing", raw):
                db.execute("INSERT INTO badge_pairing VALUES(?,?,?,?,?)",
                           (pid, epoch, p.get("edition", editions[0]["id"]),
                            p["lab_badge"], p["shop_badge"]))
            all_badges.add(p["lab_badge"])
            all_badges.add(p["shop_badge"])

        for iss in issuances:
            if record(db, "issuance", iss):
                db.execute("INSERT INTO badge_issuance VALUES(?,?,?,?,?,?)",
                           (iss["id"], epoch, iss["badge"], iss["holder"],
                            iss["start"], iss.get("end")))
            all_badges.add(iss["badge"])

        for d in docs:
            doc = dict(d)
            doc.setdefault("edition", editions[0]["id"])
            doc.setdefault("at", None)
            doc["epoch"] = epoch
            if record(db, "badge_document", doc):
                db.execute("INSERT INTO badge_document VALUES(?,?,?,?,?,?)",
                           (d["id"], epoch, doc["edition"], d["operator"],
                            d["inspector"], doc["at"]))
            all_badges.add(d["operator"])
            all_badges.add(d["inspector"])

        for r in data.get("receipts", []):
            if record(db, "receipt", r):
                db.execute("INSERT INTO witness_receipt VALUES(?,?,?,?,?,?,?)",
                           (r["id"], epoch, r["doc_id"], r["verdict"],
                            json.dumps(r.get("witnesses", [])), r["at"],
                            r.get("state", "active")))

        for w in data.get("withdrawals", []):
            if record(db, "withdrawal", w):
                db.execute("INSERT INTO receipt_withdrawal VALUES(?,?,?)",
                           (w["id"], w["receipt"], w["at"]))
                db.execute("UPDATE witness_receipt SET state='withdrawn' WHERE id=?",
                           (w["receipt"],))

        if len(all_badges) > LIMIT_BADGES:
            raise ValueError("badge desk limit: at most %d badges per shift" % LIMIT_BADGES)
        return decide(db, epoch)


def _load(db, epoch):
    rows = {}
    for key, table in (
        ("editions", "badge_edition"), ("candidates", "badge_candidate"),
        ("nonmatches", "badge_nonmatch"), ("pairings", "badge_pairing"),
        ("issuances", "badge_issuance"), ("documents", "badge_document"),
        ("mandates", "verification_mandate"), ("receipts", "witness_receipt"),
    ):
        rows[key] = [dict(r) for r in db.execute(
            "SELECT * FROM %s WHERE epoch=?" % table, (epoch,))]
    rows["withdrawals"] = [dict(r) for r in db.execute(
        "SELECT w.* FROM receipt_withdrawal w JOIN witness_receipt r ON r.id=w.receipt "
        "WHERE r.epoch=?", (epoch,))]
    rows["candidate_map"] = {c["lab_badge"]: json.loads(c["shop_badges"]) for c in rows["candidates"]}
    rows["nonmatch_ids"] = {(n["left_badge"], n["right_badge"]): n["id"] for n in rows["nonmatches"]}
    rows["candidate_ids"] = {c["lab_badge"]: c["id"] for c in rows["candidates"]}
    rows["withdrawn"] = {w["receipt"] for w in rows["withdrawals"]}
    return rows


def _intervals(issuances, badge):
    return sorted(((i["holder"], i["start"], i["end"])
                   for i in issuances if i["badge"] == badge), key=lambda x: x[1])


def _holder_at(intervals, t):
    """半开区间 [start,end):报告时间恰好等于交还界点归入再发后。"""
    for holder, start, end in intervals:
        if start <= t and (end is None or t < end):
            return holder
    return None


def _person_options(badge, at, issuances):
    """人员键选项:无重发原件时牌即身份;时刻已知取半开区间持有人;未知时枚举。"""
    iv = _intervals(issuances, badge)
    if not iv:
        return [badge]
    if at is not None:
        h = _holder_at(iv, at)
        return None if h is None else [h]
    out = []
    for h, _, _ in iv:
        if h not in out:
            out.append(h)
    return out


def _edition_worlds(edition, docs, rows):
    """枚举一个版次内全部相容解释(实验室牌映射 x 重发时序)。

    时序世界按车间牌枚举:同一张牌在一个世界里只有一个时序赋值,
    未知时刻的多份报告共享该赋值,不能各自挑有利时序。
    """
    issuances = rows["issuances"]
    cmap = {c["lab_badge"]: json.loads(c["shop_badges"])
            for c in rows["candidates"] if c["edition"] == edition}
    pair_map = {p["lab_badge"]: p for p in rows["pairings"] if p["edition"] == edition}
    nonmatches = [(n["left_badge"], n["right_badge"])
                  for n in rows["nonmatches"] if n["edition"] == edition]
    used_labs = sorted({d["inspector_badge"] for d in docs})
    domains = {}
    for lab in used_labs:
        if lab in pair_map:
            domains[lab] = [pair_map[lab]["shop_badge"]]
        elif lab in cmap:
            domains[lab] = list(cmap[lab])
        else:
            return None, []  # 候选全集未记录:允许未定,不许补猜

    # 先做静态(车间牌层面)约束过滤:见证非同一 + 同版注入性。
    static_ok = []
    for combo in product(*[domains[lab] for lab in used_labs]):
        mapping = dict(zip(used_labs, combo))
        violated = []
        for lab, shop in nonmatches:
            if lab in mapping and mapping[lab] == shop:
                violated.append(rows["nonmatch_ids"][(lab, shop)])
        seen = {}
        for lab, shop in mapping.items():
            if shop in seen:
                for colliding in (seen[shop], lab):
                    cid = rows["candidate_ids"].get(colliding)
                    if cid and cid not in violated:
                        violated.append(cid)
            else:
                seen[shop] = lab
        if not violated:
            static_ok.append(mapping)
    if not static_ok:
        originals = sorted({rows["nonmatch_ids"][pair] for pair in nonmatches} |
                           {rows["candidate_ids"][lab] for lab in used_labs if lab in rows["candidate_ids"]})
        return [], [{"docs": [d["id"] for d in docs], "originals": originals}]

    # 参与重发时序的车间牌:操作牌 + 固定配对映到且确有重发原件的牌。
    reissued = {b for b in {d["operator_badge"] for d in docs} |
                {m[d["inspector_badge"]] for m in static_ok for d in docs}
                if _intervals(issuances, b)}
    known_at = next((d["at"] for d in docs if d["at"] is not None), None)
    if any(_person_options(b, known_at, issuances) is None for b in reissued):
        return None, []
    # 时刻未知时每张重发牌是一个全局时序维度;已知时刻只取唯一持有人。
    dims = [(b, _person_options(b, None, issuances)
             if any(d["at"] is None for d in docs) else _person_options(b, known_at, issuances))
            for b in sorted(reissued)]

    feasible, seen_worlds = [], set()
    for mapping in static_ok:
        for choice in product(*[opts for _, opts in dims]):
            timing = {b: person for (b, _), person in zip(dims, choice)}
            world = {}
            for d in docs:
                op_iv = _intervals(issuances, d["operator_badge"])
                op_person = (_holder_at(op_iv, d["at"]) if d["at"] is not None
                             else timing.get(d["operator_badge"], d["operator_badge"])) if op_iv else d["operator_badge"]
                shop_badge = mapping[d["inspector_badge"]]
                insp_iv = _intervals(issuances, shop_badge)
                insp_person = (_holder_at(insp_iv, d["at"]) if d["at"] is not None
                               else timing.get(shop_badge, shop_badge)) if insp_iv else shop_badge
                if op_person is None or insp_person is None:
                    break
                world[d["id"]] = {"operator": op_person, "inspector": insp_person,
                                  "same": op_person == insp_person}
            else:
                key = tuple(sorted((k, v["operator"], v["inspector"]) for k, v in world.items()))
                if key not in seen_worlds:
                    seen_worlds.add(key)
                    feasible.append(world)
    return feasible, []


def evaluate(db, epoch):
    """对一班的全部原件求值,返回 {doc_id: 裁决 dict}。"""
    rows = _load(db, epoch)
    docs = rows["documents"]
    by_edition = {}
    for d in docs:
        by_edition.setdefault(d["edition"], []).append(d)

    structural, all_groups = {}, []
    for edition, edocs in sorted(by_edition.items()):
        worlds, groups = _edition_worlds(edition, edocs, rows)
        all_groups.extend(groups)
        if worlds is None:
            for d in edocs:
                structural[d["id"]] = {"state": "pending",
                                       "reason": "identity not determined: internal candidate set not recorded",
                                       "examples": []}
            continue
        if not worlds:
            for d in edocs:
                structural[d["id"]] = {"state": "conflict",
                                       "reason": "no consistent interpretation: authoritative originals conflict",
                                       "examples": []}
            continue
        for d in edocs:
            eq = [w[d["id"]]["same"] for w in worlds]
            if all(eq):
                structural[d["id"]] = {"state": "not_independent",
                                       "reason": "same entity in every consistent interpretation",
                                       "examples": []}
            elif not any(eq):
                structural[d["id"]] = {"state": "independent",
                                       "reason": "distinct entities in every consistent interpretation",
                                       "examples": []}
            else:
                examples = []
                for want in (True, False):
                    w = next(x for x in worlds if x[d["id"]]["same"] is want)
                    examples.append({"result": "same" if want else "different",
                                     "operator": w[d["id"]]["operator"],
                                     "inspector": w[d["id"]]["inspector"]})
                structural[d["id"]] = {"state": "pending",
                                       "reason": "compatible interpretations disagree on identity",
                                       "examples": examples}

    # 委托按采录先后叠加:同类型以最新一份为准,后到的完整委托承接先前不完整委托。
    mandates = rows["mandates"]
    roster_mandates = [m for m in mandates if m["kind"] == "roster"]
    roster = roster_mandates[-1] if roster_mandates else None
    active_receipts = {r["doc_id"]: r for r in rows["receipts"]
                       if r["id"] not in rows["withdrawn"]}

    out = {}
    for d in docs:
        v = dict(structural[d["id"]])
        v["conflict_groups"] = ([g for g in all_groups if d["id"] in g["docs"]]
                                if v["state"] == "conflict" else [])
        v["credential"] = None

        if v["state"] == "conflict":
            out[d["id"]] = v
            continue

        receipt = active_receipts.get(d["id"])
        if roster is not None and roster["roster_export"] and roster["complete"] and roster["scope"] == "shift":
            # 名册路径成立时,任何与名册结论冲突的回执都构成权威矛盾,不得覆盖。
            if receipt is not None and v["state"] in ("independent", "not_independent") \
                    and v["state"] != receipt["verdict"]:
                v.update(state="conflict",
                         reason="witness receipt contradicts roster-derived identity",
                         conflict_groups=[{"docs": [d["id"]], "originals": [receipt["id"]]}])
            else:
                v["credential"] = {"type": "roster", "id": roster["id"],
                                   "evidence": _supporting_originals(rows)}
        elif roster is not None and roster["roster_export"]:
            v.update(state="pending",
                     reason="roster mandate requires complete shift-wide coverage")
        elif receipt is not None:
            if v["state"] in ("independent", "not_independent") and v["state"] != receipt["verdict"]:
                v.update(state="conflict",
                         reason="witness receipt contradicts other authoritative originals",
                         conflict_groups=[{"docs": [d["id"]], "originals": [receipt["id"]]}])
            else:
                v["state"] = receipt["verdict"]
                v["reason"] = "established by scoped joint-witness receipt"
                v["credential"] = {"type": "receipt", "id": receipt["id"],
                                   "evidence": [receipt["id"]]}
        else:
            if any(r["doc_id"] != d["id"] for r in active_receipts.values()):
                v.update(state="pending",
                         reason="receipt scoped to another document cannot be borrowed")
            else:
                v.update(state="pending", reason="no independence credential on record")
        out[d["id"]] = v
    return out


def _supporting_originals(rows):
    ids = [c["id"] for c in rows["candidates"]]
    ids += [n["id"] for n in rows["nonmatches"]]
    ids += [p["id"] for p in rows["pairings"]]
    ids += [i["id"] for i in rows["issuances"]]
    ids += [m["id"] for m in rows["mandates"]]
    return sorted(set(ids))


def decide(db, epoch):
    """重算并持久化一班的独立性裁决,写入版本历史。"""
    verdicts = evaluate(db, epoch)
    result = []
    for doc_id in sorted(verdicts):
        v = verdicts[doc_id]
        payload = (
            epoch, v["state"], v["reason"], json.dumps(v.get("credential")),
            json.dumps(v.get("examples", []), ensure_ascii=False),
            json.dumps(v.get("conflict_groups", []), ensure_ascii=False),
        )
        old = db.execute("SELECT * FROM independence_verdict WHERE doc_id=?",
                         (doc_id,)).fetchone()
        changed = not old or (
            old["state"], old["reason"], old["credential"],
            old["examples"], old["conflict_groups"],
        ) != payload[1:]
        if changed:
            ver = old["version"] + 1 if old else 1
            db.execute(
                "INSERT INTO independence_verdict VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET epoch=excluded.epoch,state=excluded.state,"
                "reason=excluded.reason,credential=excluded.credential,examples=excluded.examples,"
                "conflict_groups=excluded.conflict_groups,version=excluded.version",
                (doc_id,) + payload + (ver,),
            )
            history(db, "independence", doc_id, v["state"], v["reason"],
                    v["credential"]["id"] if v.get("credential") else None)
        result.append(_row_to_dict(db.execute(
            "SELECT * FROM independence_verdict WHERE doc_id=?", (doc_id,)).fetchone()))
    return result


def query(db, doc_id=None, epoch=None):
    """采录查询入口:按班或按报告读取独立性凭据。"""
    if doc_id is not None:
        row = db.execute("SELECT * FROM independence_verdict WHERE doc_id=?",
                         (doc_id,)).fetchone()
        if row is not None:
            return _row_to_dict(row)
        target_epoch = epoch
        if target_epoch is None:
            d = db.execute("SELECT epoch FROM badge_document WHERE id=?",
                           (doc_id,)).fetchone()
            if d is None:
                return None
            target_epoch = d["epoch"]
        v = evaluate(db, target_epoch).get(doc_id)
        return None if v is None else {"doc_id": doc_id, **v}
    epochs = [r[0] for r in db.execute(
        "SELECT DISTINCT epoch FROM badge_document" + (" WHERE epoch=?" if epoch else ""),
        (epoch,) if epoch else ())]
    out = []
    for ep in epochs:
        for did in sorted(evaluate(db, ep)):
            row = db.execute("SELECT * FROM independence_verdict WHERE doc_id=?",
                             (did,)).fetchone()
            out.append(_row_to_dict(row) if row else {"doc_id": did, **evaluate(db, ep)[did]})
    return out


def _row_to_dict(row):
    return {
        "doc_id": row["doc_id"], "epoch": row["epoch"], "state": row["state"],
        "reason": row["reason"], "credential": json.loads(row["credential"]),
        "examples": json.loads(row["examples"]),
        "conflict_groups": json.loads(row["conflict_groups"]), "version": row["version"],
    }


def credential_status(db, epoch, doc_id):
    """供检验裁决调用:返回该报告当前可签字依据的独立性状态。"""
    v = query(db, doc_id, epoch)
    if v is None:
        return {"state": "pending", "reason": "no badge evidence on record", "credential": None}
    return {"state": v["state"], "reason": v["reason"], "credential": v.get("credential")}
