"""Collection order across accounts, shards and page boundaries (isolated DBs)."""
from contextlib import closing
import json
from test_task_queries import QueryFixture
from task_queries import TaskQueries, collection_timestamp
from collection_cache import CollectionReadCache


class CollectionOrderTests(QueryFixture):
    def test_cache_refresh_and_snapshot_update_reorder_immediately(self):
        cache = CollectionReadCache(self.people.path)
        self.item(1, 1)
        self.item(1, 2)
        with closing(self.people._connect()) as db, db:
            db.executemany('INSERT INTO collection_cached_rows VALUES (?,?,?)', [
                ('scope-1', '1', '{"gmtCreate":"2026-11-04 10:00:00"}'),
                ('scope-1', '2', '{"gmtCreate":"2026-11-05 10:00:00"}')])
        query = TaskQueries(self.service)
        self.assertEqual([r['detailId'] for r in query.pool({})['items']], [2, 1])
        with closing(self.people._connect()) as db, db:
            db.execute('UPDATE collection_cached_rows SET payload=? WHERE detail_id=?',
                       ('{"gmtCreate":"2026-11-06 10:00:00"}', '1'))
        self.assertEqual([r['detailId'] for r in query.pool({})['items']], [1, 2])
        self.pools[1].put_item(1, 'site', 0, {'title': 'snapshot wins', 'gmtCreate': '2026-11-03 10:00:00'})
        self.assertEqual([r['detailId'] for r in query.pool({})['items']], [2, 1])

    def test_global_order_pagination_filters_and_missing_dates(self):
        expected = []
        with closing(self.people._connect()) as db, db:
            db.execute('CREATE TABLE collection_cached_rows (scope TEXT, detail_id TEXT, payload TEXT, '
                       'PRIMARY KEY(scope,detail_id))')
        for i in range(125):
            account = 1 + i % 2
            detail = 1000 + i
            # Arrival order is the opposite of collection order.
            value = 1700000000 + (124 - i) * 60
            payload = {'title': str(detail), 'subAppAccountId': '11'}
            if i % 3:
                payload['gmtCreate'] = value * 1000
            else:
                with closing(self.people._connect()) as db, db:
                    db.execute('INSERT INTO collection_cached_rows VALUES (?,?,?)',
                               (f'scope-{account}', str(detail), json.dumps({'gmtCreate': value})))
                    db.execute('INSERT INTO collection_cached_rows VALUES (?,?,?)',
                               ('retired', str(detail), json.dumps({'gmtCreate': value * 10})))
            self.pools[account].put_item(detail, 'site', 0, payload)
            expected.append(f'{account}:{detail}')
        self.item(1, 9999)  # No collection date: last, even though added most recently.
        expected.append('1:9999')
        query = TaskQueries(self.service)
        actual = []
        for page in (1, 2, 3):
            result = query.pool({'page': [str(page)], 'pageSize': ['50']})
            self.assertEqual(result['total'], 126)
            actual.extend(row['key'] for row in result['items'])
        self.assertEqual(actual, expected)
        all_rows = query.pool({}, all_rows=True)['items']
        self.assertEqual([r['key'] for r in all_rows], expected)
        for pool in (1, 2, 3):
            rows = query.pool({'sourceAccountId': ['1'], 'poolId': [str(pool)], 'personId': ['11']}, all_rows=True)['items']
            self.assertEqual([r['key'] for r in rows],
                             [r['key'] for r in all_rows if r['sourceAccountId'] == 1 and r['poolId'] == pool])

    def test_snapshot_precedence_and_stable_equal_or_unknown_dates(self):
        for detail, value in ((1, '2026-11-05 10:00:00'), (2, '2026-11-05T02:00:00Z'),
                              (3, 'invalid'), (4, '')):
            self.pools[1].put_item(detail, 'site', 0, {'title': str(detail), 'gmtCreate': value})
        for pool in self.pools[1].pools:
            with closing(pool._connect()) as db, db:
                db.execute("UPDATE automation_items SET created_at='2026-11-05T00:00:00Z'")
        query = TaskQueries(self.service)
        self.assertEqual([r['detailId'] for r in query.pool({})['items']], [2, 1, 4, 3])

    def test_timestamp_formats(self):
        value = collection_timestamp('2026-11-05 10:00:00')
        for item in ('2026-11-05T02:00:00Z', '2026-11-05T10:00:00+08:00', value, value * 1000, str(value)):
            self.assertEqual(collection_timestamp(item), value)
        for item in ('', None, 'bad', 'nan', 'inf', -1, True):
            self.assertEqual(collection_timestamp(item), 0)
