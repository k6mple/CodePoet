"""Upgrade populated Canvas databases without losing work/job references."""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('migration_runner', ROOT / 'scripts/migrate.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RendererMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_jobs_references_indexes_and_is_atomic(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('PRAGMA foreign_keys=ON; CREATE TABLE users(id TEXT PRIMARY KEY);'
                         'CREATE TABLE generations(id TEXT PRIMARY KEY);')
        source = (ROOT / 'backend/migrations/004_works.sql').read_text(encoding='utf-8')
        db.executescript(source[source.index('CREATE TABLE render_jobs ('):source.index('CREATE TABLE works (')])
        db.executescript("CREATE TABLE work_versions(id TEXT, render_job_id TEXT REFERENCES render_jobs(id));"
                         "INSERT INTO users VALUES('u'); INSERT INTO generations VALUES('g');"
                         "INSERT INTO render_jobs(id,owner_id,generation_id,backend,execution_mode,status,phase,"
                         "times_ms_json,renderer_version,created_at,updated_at) VALUES"
                         "('j','u','g','canvas2d','browser_upload','succeeded','completed','[0]','p0-03.1','now','now');"
                         "INSERT INTO work_versions VALUES('w','j');")
        before = db.execute('SELECT * FROM render_jobs').fetchall()
        runner.ensure_registry(db)
        runner.apply_one(db, 12, ROOT / 'backend/migrations/012_browser_render_backends.sql')
        self.assertEqual(db.execute('SELECT * FROM render_jobs').fetchall(), before)
        self.assertEqual(db.execute('SELECT * FROM work_versions').fetchall(), [('w', 'j')])
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(len(db.execute("PRAGMA index_list('render_jobs')").fetchall()), 3)
        db.execute("UPDATE render_jobs SET backend='css'")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE render_jobs SET backend='turtle'")
        db.rollback()
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / '013_bad.sql'
            bad.write_text('-- migrate: foreign_keys_off\nPRAGMA foreign_keys=OFF;\n'
                           "UPDATE work_versions SET render_job_id='missing';\nPRAGMA foreign_keys=ON;\n",
                           encoding='utf-8')
            with self.assertRaises(sqlite3.IntegrityError):
                runner.apply_one(db, 13, bad)
        self.assertEqual(db.execute('SELECT * FROM work_versions').fetchall(), [('w', 'j')])
        self.assertEqual(runner.applied_versions(db), {12})
        self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
