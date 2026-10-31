"""An old credential scope must not hide current source products."""
from test_task_queries import QueryFixture


class DedupeScopeTests(QueryFixture):
    def add_copy(self, source_scope, target_scope, source=1, target=2, source_id=101, target_id=202):
        with self.service.db() as db:
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('copy-key', source, source_scope, source_id, target, target_scope,
                 target_id, 'ready', '{}', ''))

    def test_old_target_scope_does_not_hide_a_current_product(self):
        self.item(1, 101)
        self.add_copy('scope-2', 'retired-scope-1', source=2, target=1, source_id=999, target_id=101)
        self.assertEqual([r['detailId'] for r in self.service.listing('pool', {})['items']], [101])

    def test_current_target_copy_remains_excluded_from_source_pool(self):
        self.item(1, 101)
        self.add_copy('scope-2', 'scope-1', source=2, target=1, source_id=999, target_id=101)
        self.assertEqual(self.service.listing('pool', {})['items'], [])

    def test_retired_source_scope_is_not_shown_as_current_copy_evidence(self):
        self.item(1, 101)
        self.add_copy('retired-scope-1', 'scope-2')
        self.assertEqual(self.service.listing('pool', {})['items'][0]['copies'], [])
