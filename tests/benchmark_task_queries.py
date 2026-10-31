"""Synthetic read-path benchmark. Never imports server or opens workspace data.

Run: py -3 tests/benchmark_task_queries.py --repeat 5
All fixture databases are created inside TemporaryDirectory and removed on exit.
"""
from contextlib import closing
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_task_queries import QueryFixture
from task_queries import reader


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--items', type=int, default=30000)
    parser.add_argument('--jobs', type=int, default=15000)
    parser.add_argument('--events', type=int, default=260000)
    args = parser.parse_args()
    if min(args.repeat, args.items, args.jobs, args.events) < 1 or args.jobs > args.items:
        parser.error('positive sizes required; jobs must not exceed items')
    fixture = QueryFixture()
    fixture.setUp()
    try:
        pool = fixture.pools[1].pools[0]
        when = '2026-10-21T00:00:00+00:00'
        snapshot = json.dumps({'title': 'Synthetic item', 'subAppAccountId': '11', 'padding': 'x' * 4096})
        with closing(pool._connect()) as db, db:
            db.executemany('INSERT INTO automation_items VALUES (?,?,?,?,?,?,?,?,?)',
                ((i, 'site', 0, f'Synthetic item {i}', '', snapshot, 'fixture', when, when) for i in range(1, args.items + 1)))
            db.executemany('INSERT INTO automation_jobs(job_id,detail_id,shop_id,site,status,step,message,created_at,updated_at,publish_after_save) '
                'VALUES (?,?,?,?,?,?,?,?,?,0)', ((f'job{i:07}', i, 7, 'US', 'ready_publish', 'save', 'synthetic ' * 50, when, when)
                for i in range(1, args.jobs + 1)))
            db.executemany('INSERT INTO automation_job_media VALUES (?,?,?)',
                ((f'job{i:07}', f'Synthetic item {i}', '') for i in range(1, args.jobs + 1)))
            db.executemany('INSERT INTO automation_job_events(job_id,step,status,message,created_at) VALUES (?,?,?,?,?)',
                ((f'job{(i % args.jobs) + 1:07}', 'save' if i % 16 == 0 else 'prepare', 'done', 'fixture', when)
                for i in range(args.events)))
        fixture.people.observe('scope-1', [{'collectBoxDetailId': i, 'subAppAccountId': '11'} for i in range(1, args.items + 1)])
        with fixture.service.db() as db:
            db.executemany('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ((f'copy{i}', 1, 'scope-1', i, 2, 'scope-2', 9000000 + i, 'ready', '{}', '')
                for i in range(1, min(2000, args.items) + 1)))

        def old_pool_page():
            # The former hot path: all snapshots parsed, and all copies
            # scanned again for every item, before the first 50 are sliced.
            with reader(fixture.service.path) as db:
                copies = [dict(r) for r in db.execute('SELECT * FROM shared_copies')]
            rows = []
            with reader(pool.path) as db:
                for row in db.execute("SELECT detail_id,title,thumbnail,version,created_at,updated_at,"
                        "json_extract(snapshot_json,'$.subAppAccountId') AS person,"
                        "json_extract(snapshot_json,'$.gmtCreate') AS collected_at FROM automation_items"):
                    rows.append({**dict(row), 'copies': [c for c in copies if c['source_id'] == row['detail_id']]})
            return sorted(rows, key=lambda r: (r['created_at'], str(r['detail_id'])), reverse=True)[:50]

        def old_job_page():
            with reader(pool.path) as db:
                # The old personnel resolver loaded the complete source box
                # even when a page needed only 50 display records.
                owners = {str(r['detail_id']): r['person'] for r in db.execute(
                    "SELECT detail_id,json_extract(snapshot_json,'$.subAppAccountId') AS person FROM automation_items")}
                rows = [{**dict(r), 'person': owners.get(str(r['detail_id']))} for r in db.execute(
                    'SELECT j.*,m.title,m.thumbnail FROM automation_jobs j LEFT JOIN automation_job_media m ON m.job_id=j.job_id')]
            return sorted(rows, key=lambda r: (r['created_at'], r['job_id']), reverse=True)[:50]

        def event_lookup(indexed):
            with reader(pool.path) as db:
                return list(db.execute('SELECT step,status,message,created_at FROM automation_job_events ' +
                    ('' if indexed else 'NOT INDEXED ') + 'WHERE job_id=? ORDER BY event_id', ('job0000001',)))

        operations = {
            'pool_before': old_pool_page,
            'pool_after': lambda: fixture.service.listing('pool', {})['items'],
            'tasks_before': old_job_page,
            'tasks_after': lambda: fixture.service.listing('queue', {})['entries'],
            'event_before_scan': lambda: event_lookup(False),
            'event_after_index': lambda: event_lookup(True),
        }
        # Warm the independent daily cache; cold/expired nonblocking behavior
        # is covered by the Event-gated regression test instead of a timing guess.
        fixture.service.today_store_stats()
        operations['light_activity_after'] = lambda: fixture.service.activity()['pools']
        result = {'fixture': {'items': args.items, 'jobs': args.jobs, 'events': args.events,
                              'copies': min(2000, args.items), 'repetitions': args.repeat}, 'measurements': {}}
        for name, operation in operations.items():
            timings = []
            count = None
            for _ in range(args.repeat):
                start = time.perf_counter()
                rows = operation()
                timings.append((time.perf_counter() - start) * 1000)
                count = len(rows)
            ordered = sorted(timings)
            result['measurements'][name] = {'p50_ms': round(statistics.median(timings), 2),
                'p95_ms': round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 2), 'returned': count}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
