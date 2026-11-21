from .source import record
from .store import history
def decide(db,weld_id):
    qual=db.execute('SELECT * FROM qualification WHERE weld_id=?',(weld_id,)).fetchone()
    inspection=db.execute('SELECT * FROM inspection_verdict WHERE weld_id=?',(weld_id,)).fetchone()
    orders=db.execute('SELECT * FROM repair_order WHERE weld_id=? ORDER BY repair_index DESC,id',(weld_id,)).fetchall()
    sign=db.execute('SELECT * FROM supervisor_acceptance WHERE weld_id=? ORDER BY at DESC',(weld_id,)).fetchone()
    latest=orders[0] if orders else None
    retest=db.execute('SELECT * FROM retest WHERE repair_id=? ORDER BY at DESC',(latest['id'],)).fetchone() if latest else None
    weld=db.execute('SELECT * FROM weld_record WHERE id=?',(qual['record_id'],)).fetchone() if qual and qual['record_id'] else None
    retest_lab=db.execute('SELECT * FROM lab_license WHERE id=?',(retest['lab_id'],)).fetchone() if retest else None
    if not qual or qual['state']!='qualified':state,reason='qualification_hold','weld qualification not valid'
    elif not inspection or inspection['state'] in ('invalid_lab','self_inspection','insufficient_coverage'):state,reason='inspection_hold','independent inspection evidence missing'
    elif latest and latest['repair_index']>2:state,reason='repair_dispute','more than two repair attempts'
    elif latest and latest['approved_by']!='supervisor':state,reason='repair_unapproved','repair order needs supervisor authorization'
    elif retest and (not retest_lab or not(retest_lab['start']<=retest['at']<=retest_lab['end']) or retest['at']<latest['at'] or (weld and retest['inspector']==weld['welder'])):state,reason='retest_invalid','retest lab, sequence or independence invalid'
    elif latest and (not retest or retest['covered_position']!=latest['defect_position'] or retest['defect_mm']>2):state,reason='retest_pending','retest must cover original defect and pass'
    elif inspection['state']=='defect' and not latest:state,reason='repair_required','defect requires repair order'
    elif not sign or sign['signer']!='supervisor':state,reason='signature_pending','monitor supervisor has not accepted weld'
    else:state,reason='accepted','qualification, inspection, repair and signoff complete'
    old=db.execute('SELECT * FROM final_acceptance WHERE weld_id=?',(weld_id,)).fetchone()
    if not old or (old['state'],old['reason'])!=(state,reason):
        ver=old['version']+1 if old else 1
        db.execute('INSERT INTO final_acceptance VALUES(?,?,?,?) ON CONFLICT(weld_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,version=excluded.version',(weld_id,state,reason,ver))
        history(db,'acceptance',weld_id,state,reason,latest['id'] if latest else sign['id'] if sign else None)
    if state=='repair_dispute' and qual:
        welder=db.execute('SELECT welder FROM weld_record WHERE id=?',(qual['record_id'],)).fetchone()['welder']
        db.execute('INSERT INTO future_welder_hold VALUES(?,?,?) ON CONFLICT(welder) DO UPDATE SET state=excluded.state,reason=excluded.reason',(welder,'blocked','excess repair attempts require reauthorization'))
    return dict(db.execute('SELECT * FROM final_acceptance WHERE weld_id=?',(weld_id,)).fetchone())
def import_data(db,data):
    touched=set()
    with db:
        for r in data.get('repairs',[]):
            if record(db,'repair',r):db.execute('INSERT INTO repair_order VALUES(?,?,?,?,?,?)',(r['id'],r['weld_id'],r['repair_index'],r['approved_by'],r['at'],r['defect_position']))
            touched.add(r['weld_id'])
        for t in data.get('retests',[]):
            if record(db,'retest',t):db.execute('INSERT INTO retest VALUES(?,?,?,?,?,?,?,?)',(t['id'],t['weld_id'],t['repair_id'],t['lab_id'],t['inspector'],t['at'],t['covered_position'],t['defect_mm']))
            touched.add(t['weld_id'])
        for s in data.get('signoffs',[]):
            if record(db,'signoff',s):db.execute('INSERT INTO supervisor_acceptance VALUES(?,?,?,?)',(s['id'],s['weld_id'],s['signer'],s['at']))
            touched.add(s['weld_id'])
        return [decide(db,w) for w in sorted(touched)]
