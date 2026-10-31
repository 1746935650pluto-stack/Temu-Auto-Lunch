"""Local empty account entries; never holds credentials or remote products."""
from contextlib import closing
from pathlib import Path
import sqlite3
from threading import Lock


class AccountSlotStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._schema_lock = Lock()
        self._initialized_path = None

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            if self._initialized_path != self.path.resolve():
                with self._schema_lock:
                    if self._initialized_path != self.path.resolve():
                        connection.execute('CREATE TABLE IF NOT EXISTS account_slots (id INTEGER PRIMARY KEY, deleted INTEGER NOT NULL)')
                        connection.execute('CREATE TABLE IF NOT EXISTS account_history_cleanup (account_id INTEGER PRIMARY KEY, cleared_at TEXT NOT NULL, backup_path TEXT NOT NULL)')
                        connection.commit()
                        self._initialized_path = self.path.resolve()
        except Exception:
            connection.close()
            raise
        return connection

    def _list(self, connection, configured):
        slots = {slot['id']: dict(slot) for slot in configured}
        for row in connection.execute('SELECT id, deleted FROM account_slots'):
            slot_id = row['id']
            if slot_id == 1:
                continue
            # A newly configured API always wins over an earlier local deletion.
            if slot_id in slots and not slots[slot_id]['deletable']:
                continue
            if row['deleted']:
                slots.pop(slot_id, None)
            else:
                slots.setdefault(slot_id, {'id': slot_id, 'name': f'妙手采集箱 {slot_id}',
                                          'configured': False, 'active': False, 'deletable': True})
        return [slots[key] for key in sorted(slots)]

    def list(self, configured):
        with closing(self._connect()) as connection:
            return self._list(connection, configured)

    def history_cleared_ids(self):
        with closing(self._connect()) as connection:
            return {row[0] for row in connection.execute('SELECT account_id FROM account_history_cleanup')}

    def add(self, configured, limit=20):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            used = {slot['id'] for slot in self._list(connection, configured)}
            slot_id = next((value for value in range(2, limit + 1) if value not in used), None)
            if slot_id is None:
                raise ValueError(f'最多支持 {limit} 个采集箱，已无空余账号位置')
            connection.execute('INSERT INTO account_slots (id, deleted) VALUES (?, 0) '
                               'ON CONFLICT(id) DO UPDATE SET deleted = 0', (slot_id,))
            return slot_id, self._list(connection, configured)

    def delete(self, slot_id, configured):
        if type(slot_id) is not int or slot_id <= 1:
            raise ValueError('默认采集箱不能删除')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            slot = next((row for row in self._list(connection, configured) if row['id'] == slot_id), None)
            if slot is None:
                raise ValueError('采集箱不存在或已删除')
            if not slot['deletable']:
                raise ValueError('只能删除未配置 API 的空采集箱；已有凭证或正在使用的账号不能删除')
            connection.execute('INSERT INTO account_slots (id, deleted) VALUES (?, 1) '
                               'ON CONFLICT(id) DO UPDATE SET deleted = 1', (slot_id,))
            return self._list(connection, configured)
