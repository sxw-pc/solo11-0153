import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from weldproof import __file__ as imported_file
from weldproof.store import connect
from weldproof import qualification,inspection,repair
ROOT=Path(__file__).resolve().parents[1]
def fx(name):return json.loads((ROOT/'fixtures'/(name+'.json')).read_text(encoding='utf8'))
class WeldTests(unittest.TestCase):
    def setUp(self):self.db=connect(':memory:')
    def tearDown(self):self.db.close()
    def q(self):return self.db.execute('SELECT state FROM qualification WHERE weld_id="JOINT-1"').fetchone()[0]
    def n(self):return self.db.execute('SELECT state FROM inspection_verdict WHERE weld_id="JOINT-1"').fetchone()[0]
    def a(self):return self.db.execute('SELECT state FROM final_acceptance WHERE weld_id="JOINT-1"').fetchone()[0]
    def base(self):qualification.import_data(self.db,fx('weld_ok'))
    def good(self):self.base();inspection.import_data(self.db,fx('ndt_ok'));repair.import_data(self.db,fx('repair_ok'))
    def test_module_local(self):
        print('imported module',imported_file)
        self.assertTrue(str(Path(imported_file).resolve()).startswith(str(ROOT.resolve())))
    def test_normal_chain(self):
        self.good();self.assertEqual((self.q(),self.n(),self.a()),('qualified','accepted','accepted'))
    def test_welder_scope_and_procedure(self):
        x=fx('weld_ok');x['certificates'][0]['grade']=1
        self.assertEqual(qualification.import_data(self.db,x)[0]['state'],'unqualified')
        self.db.close();self.db=connect(':memory:')
        x=fx('weld_ok');x['procedures'][0]['approved_by']='factory'
        self.assertEqual(qualification.import_data(self.db,x)[0]['state'],'unapproved_procedure')
    def test_parallel_weld_identity_conflict(self):
        self.good();x=fx('weld_ok')['welds'][0].copy();x.update(id='WELD-CONFLICT',welder='OTHER')
        qualification.import_data(self.db,{'welds':[x]})
        self.assertEqual(self.q(),'conflict');self.assertEqual(self.a(),'qualification_hold')
    def test_independent_inspector_and_coverage(self):
        self.base();inspection.import_data(self.db,fx('ndt_bad'))
        self.assertEqual(self.n(),'self_inspection')
        self.db.close();self.db=connect(':memory:');self.base()
        x=fx('ndt_ok');x['inspections'][0]['sampled_mm']=50
        self.assertEqual(inspection.import_data(self.db,x)[0]['state'],'insufficient_coverage')
    def test_defect_zone_and_repair_required(self):
        self.base();x=fx('ndt_ok');x['inspections'][0].update(defect_position='critical',defect_mm=2.5)
        inspection.import_data(self.db,x)
        self.assertEqual(self.n(),'defect');self.assertEqual(self.a(),'repair_required')
    def test_invalid_lab_late_reinspection(self):
        self.base();x=fx('ndt_ok');x['labs'][0]['end']='2026-02-01'
        inspection.import_data(self.db,x);self.assertEqual(self.n(),'invalid_lab')
        y=fx('ndt_ok');y['labs'][0]['id']='LAB-NEW';y['inspections'][0].update(id='NDT-NEW',lab_id='LAB-NEW',revision=2)
        inspection.import_data(self.db,y)
        self.assertEqual(self.n(),'accepted')
    def test_repair_limit_and_future_welder_hold(self):
        self.good();repair.import_data(self.db,fx('repair_bad'))
        self.assertEqual(self.a(),'repair_dispute')
        self.assertEqual(self.db.execute('SELECT state FROM future_welder_hold WHERE welder="W-1"').fetchone()[0],'blocked')
        x=fx('weld_ok')['welds'][0].copy();x.update(id='WELD-REV2',revision=2)
        qualification.import_data(self.db,{'welds':[x]});self.assertEqual(self.q(),'repair_hold')
    def test_retest_coverage_and_supervisor_signature(self):
        self.base();x=fx('ndt_ok');x['inspections'][0].update(defect_position='critical',defect_mm=3)
        inspection.import_data(self.db,x)
        r={'repairs':[{'id':'REPAIR-1','weld_id':'JOINT-1','repair_index':1,'approved_by':'supervisor','at':'2026-02-03','defect_position':'critical'}]}
        repair.import_data(self.db,r);self.assertEqual(self.a(),'retest_pending')
        repair.import_data(self.db,{'retests':[{'id':'RETEST-1','weld_id':'JOINT-1','repair_id':'REPAIR-1','lab_id':'LAB-1','inspector':'I-1','at':'2026-02-04','covered_position':'general','defect_mm':1}]})
        self.assertEqual(self.a(),'retest_pending')
        repair.import_data(self.db,{'retests':[{'id':'RETEST-2','weld_id':'JOINT-1','repair_id':'REPAIR-1','lab_id':'LAB-1','inspector':'I-1','at':'2026-02-05','covered_position':'critical','defect_mm':1}]})
        self.assertEqual(self.a(),'signature_pending')
        repair.import_data(self.db,fx('repair_ok'));self.assertEqual(self.a(),'accepted')
    def test_retest_must_be_independent_and_after_repair(self):
        self.base();inspection.import_data(self.db,fx('ndt_ok'))
        repair.import_data(self.db,{'repairs':[{'id':'REPAIR-I','weld_id':'JOINT-1','repair_index':1,'approved_by':'supervisor','at':'2026-02-03','defect_position':'general'}]})
        repair.import_data(self.db,{'retests':[{'id':'RETEST-I','weld_id':'JOINT-1','repair_id':'REPAIR-I','lab_id':'LAB-1','inspector':'W-1','at':'2026-02-02','covered_position':'general','defect_mm':1}]})
        self.assertEqual(self.a(),'retest_invalid')
    def test_idempotent_and_identity_conflict(self):
        self.base();self.assertEqual(self.q(),'qualified')
        qualification.import_data(self.db,fx('weld_ok'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM evidence').fetchone()[0],3)
        bad=fx('weld_ok');bad['welds'][0]['grade']=9
        with self.assertRaises(ValueError):qualification.import_data(self.db,bad)
        self.assertEqual(self.q(),'qualified')
    def test_independent_entries_and_cli_replay(self):
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/'demo.sqlite')
            for cmd in ('demo','audit'):
                r=subprocess.run([sys.executable,'-m','weldproof',cmd,'--db',db],cwd=ROOT,capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stderr)
                if cmd=='audit':self.assertTrue(json.loads(r.stdout)['ok'])
if __name__=='__main__':unittest.main()
