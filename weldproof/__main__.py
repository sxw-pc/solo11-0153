import argparse,json
from pathlib import Path
from .store import connect
from .source import read,file_record
from . import qualification,inspection,repair,independence,report
HANDLERS={'weld-import':qualification.import_data,'ndt-import':inspection.import_data,'repair-import':repair.import_data,'badge-import':independence.import_data}
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=[*HANDLERS,'demo','report','audit','badge-query']);p.add_argument('--db',default='state.sqlite');p.add_argument('--input');p.add_argument('--epoch');p.add_argument('--doc');a=p.parse_args();db=connect(a.db)
    if a.command=='report':print(json.dumps(report.build(db),ensure_ascii=False,indent=2));return
    if a.command=='audit':print(json.dumps(report.audit(db),ensure_ascii=False,indent=2));return
    if a.command=='badge-query':
        print(json.dumps(independence.query(db,doc_id=a.doc,epoch=a.epoch),ensure_ascii=False,indent=2));return
    if a.command=='demo':
        for cmd,name in [('weld-import','weld_ok.json'),('ndt-import','ndt_ok.json'),('repair-import','repair_ok.json')]:
            doc,raw=read(Path(__file__).resolve().parents[1]/'fixtures'/name)
            HANDLERS[cmd](db,doc)
            with db:file_record(db,cmd,raw)
        print(json.dumps(report.build(db),ensure_ascii=False,indent=2));return
    if not a.input:p.error('--input required')
    doc,raw=read(a.input);result=HANDLERS[a.command](db,doc)
    with db:file_record(db,a.command,raw)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
