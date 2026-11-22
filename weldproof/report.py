import json
from .store import connect
from . import qualification,inspection,repair,lot_source
def build(db):
    ids=[r[0] for r in db.execute('SELECT DISTINCT weld_id FROM weld_record UNION SELECT DISTINCT weld_id FROM inspection UNION SELECT DISTINCT weld_id FROM repair_order ORDER BY weld_id')]
    return [{'weld_id':i,'qualification':dict(x) if (x:=db.execute('SELECT * FROM qualification WHERE weld_id=?',(i,)).fetchone()) else None,'inspection':dict(x) if (x:=db.execute('SELECT * FROM inspection_verdict WHERE weld_id=?',(i,)).fetchone()) else None,'acceptance':dict(x) if (x:=db.execute('SELECT * FROM final_acceptance WHERE weld_id=?',(i,)).fetchone()) else None,'history':[dict(x) for x in db.execute('SELECT subsystem,state,reason,source_id FROM decision_history WHERE weld_id=? ORDER BY id',(i,))]} for i in ids]
def audit(db):
    replay=connect(':memory:');handlers={'weld-import':qualification.import_data,'ndt-import':inspection.import_data,'repair-import':repair.import_data,'lot-import':lot_source.import_data}
    for r in db.execute('SELECT command,raw FROM file_journal ORDER BY id'):handlers[r['command']](replay,json.loads(r['raw']))
    equal=build(db)==build(replay) and lot_source.snapshot(db)==lot_source.snapshot(replay);replay.close();return {'ok':equal,'files':db.execute('SELECT count(*) FROM file_journal').fetchone()[0]}
