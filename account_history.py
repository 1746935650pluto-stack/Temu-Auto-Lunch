"""Backed-up, account-scoped local history cleanup. No vendor API calls."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from event_archive import archive_path


POOL_TABLES = ('automation_publish_batch_items', 'automation_publish_batches',
               'automation_job_choices', 'automation_job_media', 'automation_job_events',
               'automation_job_deletions', 'automation_jobs', 'automation_claims', 'automation_items')


def _validate_archive_source(connection, alias, source_file):
    # Aliases are generated locally; the source filename must exactly match
    # this physical pool before any cold history can be removed.
    try:
        objects = connection.execute(
            f"SELECT name FROM {alias}.sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if not objects:
            # A failed initial archive transaction can leave an empty file.
            return False
        rows = connection.execute(
            f"SELECT value FROM {alias}.event_archive_metadata WHERE key='source_file'").fetchall()
        connection.execute(f'SELECT event_id,job_id,step,status,message,created_at '
                           f'FROM {alias}.automation_job_events LIMIT 0')
    except sqlite3.DatabaseError as exc:
        raise ValueError('事件归档缺少有效的物理任务池来源，未清理历史') from exc
    if len(rows) != 1 or rows[0][0] != source_file:
        raise ValueError('事件归档不属于当前物理任务池，未清理历史')
    return True


def clear_history(account_id, slot_store, pool_path, order_path, shared_path, backup_root, remove_credentials,
                  *, extra_pool_paths=()):
    if type(account_id) is not int or not 2 <= account_id <= 20:
        raise ValueError('只能清理采集箱 2—20')
    if account_id in slot_store.history_cleared_ids():
        remove_credentials()
        return
    paths = {'main': slot_store.path, 'pool': Path(pool_path),
             'orders': Path(order_path), 'shared': Path(shared_path)}
    paths.update({f'pool_{index}': Path(path) for index, path in enumerate(extra_pool_paths, 2)})
    archives = {}
    for alias, path in list(paths.items()):
        if alias == 'pool' or alias.startswith('pool_'):
            cold = archive_path(path)
            if cold.is_symlink() or (cold.exists() and not cold.is_file()):
                raise ValueError('事件归档必须是同目录的普通数据库文件')
            if cold.exists():
                cold_alias = 'archive_' + alias
                paths[cold_alias] = cold
                archives[cold_alias] = path.name
    paths = {alias: path for alias, path in paths.items() if path.exists()}
    backup = Path(backup_root) / ('remove-account-%s-%s-%s' % (
        account_id, datetime.now().strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:8]))
    backup.mkdir(parents=True)
    for alias, path in paths.items():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, \
                closing(sqlite3.connect(backup / path.name)) as target:
            if alias in archives:
                _validate_archive_source(source, 'main', archives[alias])
            source.backup(target)
    # All local history changes and the completion marker commit together.
    # Credentials are retired first: on failure cleanup can be retried safely.
    remove_credentials()
    with closing(slot_store._connect()) as connection, connection:
        for alias, path in paths.items():
            if alias != 'main':
                connection.execute(f'ATTACH DATABASE ? AS {alias}', (str(path),))
        connection.execute('BEGIN IMMEDIATE')
        populated_archives = [alias for alias, source_file in archives.items()
                              if _validate_archive_source(connection, alias, source_file)]
        for alias in (name for name in paths if name == 'pool' or name.startswith('pool_')):
            tables = {r[0] for r in connection.execute(f"SELECT name FROM {alias}.sqlite_master WHERE type='table'")}
            for table in POOL_TABLES:
                if table in tables:
                    connection.execute(f'DELETE FROM {alias}.{table}')
        for alias in populated_archives:
            connection.execute(f'DELETE FROM {alias}.automation_job_events')
        if 'orders' in paths:
            connection.execute('DELETE FROM orders.synced_orders')
        if 'shared' in paths:
            # Retain cross-account copy provenance for OTHER accounts and their
            # duplicate protection; delete only this account's task history.
            batches = [r[0] for r in connection.execute("""SELECT DISTINCT batch_id FROM shared.shared_transfers
                WHERE json_extract(payload,'$.sourceAccountId')=? OR json_extract(payload,'$.targetAccountId')=?""", (account_id, account_id))]
            connection.execute("""DELETE FROM shared.shared_transfers
                WHERE json_extract(payload,'$.sourceAccountId')=? OR json_extract(payload,'$.targetAccountId')=?""", (account_id, account_id))
            for batch in batches:
                connection.execute('DELETE FROM shared.shared_batches WHERE batch_id=? AND NOT EXISTS (SELECT 1 FROM shared.shared_transfers WHERE batch_id=?)', (batch, batch))
            connection.execute('DELETE FROM shared.shared_job_origins WHERE account_id=?', (account_id,))
            connection.execute('DELETE FROM shared.shared_copies WHERE target_account=?', (account_id,))
        connection.execute('INSERT INTO account_history_cleanup VALUES (?,?,?)',
                           (account_id, datetime.now(timezone.utc).isoformat(), str(backup)))
