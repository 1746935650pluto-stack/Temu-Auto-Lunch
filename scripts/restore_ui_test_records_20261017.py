"""One-time, narrowly scoped undo of the 02:04:03 UTC UI validation deletion.

Restores only the 43 formerly needs_input rows from the verified upgrade backup.
Retains an after-deletion snapshot for audit; refuses any unexpected difference.
"""
import sqlite3
from contextlib import closing
from pathlib import Path

root = Path(__file__).resolve().parents[1]
backup = root / 'backups' / 'before-v1.2.95-20261017-100048'
timestamp = '2026-10-17T02:04:03+00:00'
with closing(sqlite3.connect((root / 'automation_pool.sqlite3').as_uri() + '?mode=rw', uri=True)) as db:
    db.row_factory = sqlite3.Row
    db.execute('ATTACH DATABASE ? AS prior', ((backup / 'automation_pool.sqlite3').as_uri() + '?mode=ro',))
    rows = list(db.execute('''SELECT j.* FROM automation_jobs j
        JOIN automation_job_deletions d USING(job_id)
        LEFT JOIN prior.automation_job_deletions old USING(job_id)
        WHERE d.deleted_at=? AND old.job_id IS NULL''', (timestamp,)))
    if not rows:
        print('No matching deletion remains; nothing changed.')
    else:
        assert len(rows) == 43, f'Unexpected target count: {len(rows)}'
        changed = {'status', 'step', 'message', 'updated_at'}
        originals = []
        for row in rows:
            original = db.execute('SELECT * FROM prior.automation_jobs WHERE job_id=?', (row['job_id'],)).fetchone()
            assert original and original['status'] == 'needs_input'
            assert row['status'] == row['step'] == 'canceled' and row['updated_at'] == timestamp
            assert all(row[key] == original[key] for key in row.keys() if key not in changed)
            originals.append(original)
        audit = backup / 'automation_pool.after-ui-deletion.sqlite3'
        assert not audit.exists(), 'Audit snapshot already exists; inspect before retrying.'
        with closing(sqlite3.connect(audit)) as target:
            db.backup(target)
        with db:
            db.execute('BEGIN IMMEDIATE')
            for original in originals:
                job_id = original['job_id']
                cursor = db.execute('''UPDATE automation_jobs SET status=?, step=?, message=?, updated_at=?
                    WHERE job_id=? AND status='canceled' AND step='canceled' AND updated_at=?''',
                    (original['status'], original['step'], original['message'], original['updated_at'], job_id, timestamp))
                assert cursor.rowcount == 1
                cursor = db.execute('DELETE FROM automation_job_deletions WHERE job_id=? AND deleted_at=?', (job_id, timestamp))
                assert cursor.rowcount == 1
                cursor = db.execute("DELETE FROM automation_job_events WHERE job_id=? AND step='canceled' AND status='canceled' AND created_at=?",
                                    (job_id, timestamp))
                assert cursor.rowcount == 1
            assert db.execute('SELECT count(*) FROM automation_job_deletions WHERE deleted_at=?', (timestamp,)).fetchone()[0] == 0
        print('Restored exactly 43 original task rows, visibility and event history. Remote products were untouched.')
