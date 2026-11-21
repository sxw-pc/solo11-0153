import sqlite3
SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,kind TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS file_journal(id INTEGER PRIMARY KEY,command TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS welder_certificate(id TEXT PRIMARY KEY,welder TEXT NOT NULL,grade INTEGER NOT NULL,material TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS procedure_approval(id TEXT PRIMARY KEY,material TEXT NOT NULL,grade INTEGER NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,approved_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS weld_record(id TEXT PRIMARY KEY,weld_id TEXT NOT NULL,revision INTEGER NOT NULL,welder TEXT NOT NULL,material TEXT NOT NULL,grade INTEGER NOT NULL,at TEXT NOT NULL,length_mm REAL NOT NULL,procedure_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS qualification(weld_id TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL,record_id TEXT,version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lab_license(id TEXT PRIMARY KEY,lab TEXT NOT NULL,method TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inspection(id TEXT PRIMARY KEY,weld_id TEXT NOT NULL,revision INTEGER NOT NULL,inspector TEXT NOT NULL,lab_id TEXT NOT NULL,at TEXT NOT NULL,method TEXT NOT NULL,sampled_mm REAL NOT NULL,defect_mm REAL NOT NULL,defect_position TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inspection_verdict(weld_id TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL,inspection_id TEXT,version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS repair_order(id TEXT PRIMARY KEY,weld_id TEXT NOT NULL,repair_index INTEGER NOT NULL,approved_by TEXT NOT NULL,at TEXT NOT NULL,defect_position TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retest(id TEXT PRIMARY KEY,weld_id TEXT NOT NULL,repair_id TEXT NOT NULL,lab_id TEXT NOT NULL,inspector TEXT NOT NULL,at TEXT NOT NULL,covered_position TEXT NOT NULL,defect_mm REAL NOT NULL);
CREATE TABLE IF NOT EXISTS supervisor_acceptance(id TEXT PRIMARY KEY,weld_id TEXT NOT NULL,signer TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS final_acceptance(weld_id TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL,version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS future_welder_hold(welder TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_history(id INTEGER PRIMARY KEY,subsystem TEXT NOT NULL,weld_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,source_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""
def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
def history(db,subsystem,weld_id,state,reason,source_id=None):
    db.execute('INSERT INTO decision_history(subsystem,weld_id,state,reason,source_id) VALUES(?,?,?,?,?)',(subsystem,weld_id,state,reason,source_id))
