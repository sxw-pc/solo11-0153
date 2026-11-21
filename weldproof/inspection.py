from .source import record
from .store import history
def decide(db,weld_id):
    inspections=db.execute('SELECT * FROM inspection WHERE weld_id=? ORDER BY revision DESC,id',(weld_id,)).fetchall()
    if not inspections:return None
    latest=inspections[0]
    qual=db.execute('SELECT * FROM qualification WHERE weld_id=?',(weld_id,)).fetchone()
    weld=db.execute('SELECT * FROM weld_record WHERE id=?',(qual['record_id'],)).fetchone() if qual else None
    license=db.execute('SELECT * FROM lab_license WHERE id=?',(latest['lab_id'],)).fetchone()
    if not license or license['method']!=latest['method'] or not(license['start']<=latest['at']<=license['end']):state,reason='invalid_lab','lab license or method not valid at inspection'
    elif weld and latest['roster_epoch']:
        from .independence import credential_status
        badge=credential_status(db,latest['roster_epoch'],latest['roster_doc'])
        if badge['state']=='not_independent':state,reason='self_inspection','inspector must be independent from welder'
        elif badge['state']=='conflict':state,reason='independence_conflict','authoritative originals give no consistent identity interpretation'
        elif badge['state']!='independent':state,reason='independence_pending',badge['reason']
        else:state,reason=None,None
    elif weld and latest['inspector']==weld['welder']:state,reason='self_inspection','inspector must be independent from welder'
    else:state,reason=None,None
    if state is None:
        if weld and latest['sampled_mm']<weld['length_mm']*0.7:state,reason='insufficient_coverage','contract requires at least 70 percent length'
        elif latest['defect_position']=='critical' and latest['defect_mm']>2:state,reason='defect','critical zone tolerance exceeded'
        elif latest['defect_mm']>3:state,reason='defect','general zone tolerance exceeded'
        elif not qual or qual['state']!='qualified':state,reason='qualification_hold','inspection cannot accept unqualified weld'
        else:state,reason='accepted','independent inspection within defect tolerance'
    old=db.execute('SELECT * FROM inspection_verdict WHERE weld_id=?',(weld_id,)).fetchone()
    if not old or (old['state'],old['reason'],old['inspection_id'])!=(state,reason,latest['id']):
        ver=old['version']+1 if old else 1
        db.execute('INSERT INTO inspection_verdict VALUES(?,?,?,?,?) ON CONFLICT(weld_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,inspection_id=excluded.inspection_id,version=excluded.version',(weld_id,state,reason,latest['id'],ver))
        history(db,'inspection',weld_id,state,reason,latest['id'])
    from .repair import decide as repair
    repair(db,weld_id)
    return dict(db.execute('SELECT * FROM inspection_verdict WHERE weld_id=?',(weld_id,)).fetchone())
def import_data(db,data):
    touched=set()
    with db:
        for l in data.get('labs',[]):
            if record(db,'lab',l):db.execute('INSERT INTO lab_license VALUES(?,?,?,?,?)',(l['id'],l['lab'],l['method'],l['start'],l['end']))
            touched.update(x['weld_id'] for x in db.execute('SELECT weld_id FROM inspection WHERE lab_id=?',(l['id'],)))
        for x in data.get('inspections',[]):
            if record(db,'inspection',x):db.execute('INSERT INTO inspection VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(x['id'],x['weld_id'],x['revision'],x['inspector'],x['lab_id'],x['at'],x['method'],x['sampled_mm'],x['defect_mm'],x['defect_position'],x.get('roster_epoch'),x.get('roster_doc')))
            touched.add(x['weld_id'])
        return [decide(db,w) for w in sorted(touched)]
