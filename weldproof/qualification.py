from .source import record
from .store import history
def decide(db,weld_id):
    records=db.execute('SELECT * FROM weld_record WHERE weld_id=? ORDER BY revision DESC,id',(weld_id,)).fetchall()
    if not records:return None
    top=records[0];parallel=[r for r in records if r['revision']==top['revision']]
    cert=db.execute('SELECT * FROM welder_certificate WHERE welder=? AND material=? AND grade>=? AND start<=? AND end>=? ORDER BY grade DESC',(top['welder'],top['material'],top['grade'],top['at'],top['at'])).fetchone()
    procedure=db.execute('SELECT * FROM procedure_approval WHERE id=?',(top['procedure_id'],)).fetchone()
    hold=db.execute('SELECT * FROM future_welder_hold WHERE welder=?',(top['welder'],)).fetchone()
    if len({(r['welder'],r['material'],r['procedure_id']) for r in parallel})>1:state,reason='conflict','parallel weld records disagree on worker or procedure'
    elif not cert:state,reason='unqualified','welder certificate does not cover material grade and date'
    elif not procedure or procedure['material']!=top['material'] or procedure['grade']<top['grade'] or not(procedure['start']<=top['at']<=procedure['end']) or procedure['approved_by']!='engineer':state,reason='unapproved_procedure','procedure not approved at welding time'
    elif hold and hold['state']=='blocked' and top['revision']>1:state,reason='repair_hold','repeated repair requires welder reauthorization'
    else:state,reason='qualified','certificate and procedure cover weld'
    old=db.execute('SELECT * FROM qualification WHERE weld_id=?',(weld_id,)).fetchone()
    if not old or (old['state'],old['reason'],old['record_id'])!=(state,reason,top['id']):
        ver=old['version']+1 if old else 1
        db.execute('INSERT INTO qualification VALUES(?,?,?,?,?) ON CONFLICT(weld_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,record_id=excluded.record_id,version=excluded.version',(weld_id,state,reason,top['id'],ver))
        history(db,'qualification',weld_id,state,reason,top['id'])
    from .inspection import decide as inspect
    inspect(db,weld_id)
    from .lot_source import refresh
    refresh(db,weld_id)
    return dict(db.execute('SELECT * FROM qualification WHERE weld_id=?',(weld_id,)).fetchone())
def import_data(db,data):
    touched=set()
    with db:
        for c in data.get('certificates',[]):
            if record(db,'certificate',c):db.execute('INSERT INTO welder_certificate VALUES(?,?,?,?,?,?)',(c['id'],c['welder'],c['grade'],c['material'],c['start'],c['end']))
            touched.update(x['weld_id'] for x in db.execute('SELECT weld_id FROM weld_record WHERE welder=?',(c['welder'],)))
        for p in data.get('procedures',[]):
            if record(db,'procedure',p):db.execute('INSERT INTO procedure_approval VALUES(?,?,?,?,?,?)',(p['id'],p['material'],p['grade'],p['start'],p['end'],p['approved_by']))
            touched.update(x['weld_id'] for x in db.execute('SELECT weld_id FROM weld_record WHERE procedure_id=?',(p['id'],)))
        for w in data.get('welds',[]):
            if record(db,'weld',w):db.execute('INSERT INTO weld_record VALUES(?,?,?,?,?,?,?,?,?)',(w['id'],w['weld_id'],w['revision'],w['welder'],w['material'],w['grade'],w['at'],w['length_mm'],w['procedure_id']))
            touched.add(w['weld_id'])
        return [decide(db,w) for w in sorted(touched)]

