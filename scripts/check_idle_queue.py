"""Read-only guard for an explicitly approved idle-scheduler recovery restart."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parents[1]
result = []
now = datetime.now(timezone.utc).timestamp()
for path in root.glob('automation_pool*.sqlite3'):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        counts = dict(connection.execute('SELECT status,COUNT(*) FROM automation_jobs GROUP BY status'))
        latest = connection.execute('SELECT MAX(created_at) FROM automation_job_events').fetchone()[0]
        recent = connection.execute('SELECT MAX(updated_at) FROM automation_jobs').fetchone()[0]
        for stamp in (latest, recent):
            if stamp and now - datetime.fromisoformat(stamp).timestamp() < 120:
                raise SystemExit('Recent task activity found; no restart allowed.')
        if connection.execute("SELECT 1 FROM automation_publish_batches WHERE status='running' LIMIT 1").fetchone():
            raise SystemExit('Manual publishing is in progress; no restart allowed.')
        result.append({'database': path.name, 'counts': counts, 'lastEvent': latest})
path = root / 'shared_automation.sqlite3'
if path.exists():
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        if connection.execute("SELECT 1 FROM shared_transfers WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise SystemExit('Shared transfer pending; no restart allowed.')
print(json.dumps(result))
