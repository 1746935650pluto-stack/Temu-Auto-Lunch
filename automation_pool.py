"""Local staging pool for MiaoShou collect-box item snapshots.

Adding an item here does not edit, claim, or publish it on MiaoShou.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from item_editor import fingerprint
from account_runtime import RUNTIME, has_unknown_write_outcome
from event_archive import read_job_events, read_events_for_jobs


_GENERATED_TITLE_EVENT = re.compile(r'^中英文标题已生成：中文「(.+?)」；英文「.+?」；尚未写入妙手$')


class _SerializedPoolConnection(sqlite3.Connection):
    """Release the owning pool's short local-database gate when closed.

    WAL permits readers beside a writer, but it still permits only one writer.
    An edit worker records several progress events in quick succession, so a
    large local edit batch could otherwise make two workers spend their whole
    SQLite timeout fighting over those tiny transactions. The gate covers
    only an open SQLite connection; no MiaoShou or AI call is made while held.
    """

    def attach_pool_lock(self, lock: RLock, account_id=None) -> None:
        started = time.monotonic()
        waited = not lock.acquire(blocking=False)
        if waited:
            lock.acquire()
        self._pool_lock = lock
        self._pool_lock_released = False
        if waited:
            RUNTIME.sqlite_wait(account_id, (time.monotonic() - started) * 1000)

    def close(self) -> None:
        try:
            super().close()
        finally:
            lock = getattr(self, '_pool_lock', None)
            if lock is not None and not getattr(self, '_pool_lock_released', True):
                self._pool_lock_released = True
                lock.release()


def normalize_edit_concurrency(value: Any) -> int:
    """Accept positive exact integers without the former 50-worker limit."""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2 ** 53 - 1:
        raise ValueError('编辑并发数应为大于 0 的整数')
    return value


class AutomationPool:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._claim_states_lock = Lock()
        self._claim_states: dict[int, dict[str, Any]] = {}
        # SQLite allows a single writer even in WAL mode. Keep each physical
        # pool's short reads/writes orderly; remote work runs after its
        # connection closes, so this does not lower MiaoShou parallelism.
        self._connection_lock = RLock()
        self._schema_lock = Lock()
        self._initialized = False

    def claim_state(self, detail_id: int) -> dict[str, Any]:
        """Process-local coordination only; remote state is always read afresh."""
        with self._claim_states_lock:
            return self._claim_states.setdefault(detail_id, {
                'lock': Lock(), 'submitted': set(), 'submittedAt': None,
            })

    def pending_claim_shop_ids(self, detail_id: int) -> set[int]:
        """Include queued siblings, even when the edit worker limit is one."""
        with closing(self._connect()) as connection:
            rows = connection.execute("""
                SELECT DISTINCT shop_id FROM automation_jobs WHERE detail_id = ?
                AND status IN ('queued', 'running', 'needs_input', 'needs_review', 'interrupted', 'ready_publish')
            """, (detail_id,)).fetchall()
        return {int(row['shop_id']) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, factory=_SerializedPoolConnection)
        try:
            connection.attach_pool_lock(self._connection_lock, getattr(self, 'account_id', None))
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            if not self._initialized:
                with self._schema_lock:
                    if not self._initialized:
                        connection.execute("PRAGMA journal_mode=WAL")
                        self._initialize_schema(connection)
                        connection.commit()
                        self._initialized = True
            return connection
        except Exception:
            connection.close()
            raise

    def _initialize_schema(self, connection):
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_items (
                detail_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                shop_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                thumbnail TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_claims (
                detail_id INTEGER NOT NULL REFERENCES automation_items(detail_id) ON DELETE CASCADE,
                shop_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (detail_id, shop_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_jobs (
                job_id TEXT PRIMARY KEY,
                detail_id INTEGER NOT NULL,
                shop_id INTEGER NOT NULL,
                site TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT '160',
                sku_prices_json TEXT NOT NULL DEFAULT '{}',
                publish_after_save INTEGER NOT NULL DEFAULT 1,
                interval_seconds INTEGER NOT NULL DEFAULT 10,
                auto_override_attempts INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                step TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        job_columns = {row['name'] for row in connection.execute("PRAGMA table_info(automation_jobs)")}
        if 'price' not in job_columns:
            connection.execute("ALTER TABLE automation_jobs ADD COLUMN price TEXT NOT NULL DEFAULT '160'")
        if 'sku_prices_json' not in job_columns:
            connection.execute("ALTER TABLE automation_jobs ADD COLUMN sku_prices_json TEXT NOT NULL DEFAULT '{}'")
        if 'publish_after_save' not in job_columns:
            # Existing local batch tasks predate the manual publish queue, so
            # migrate them to the safer held-for-publication behavior.
            connection.execute("ALTER TABLE automation_jobs ADD COLUMN publish_after_save INTEGER NOT NULL DEFAULT 0")
        if 'interval_seconds' not in job_columns:
            connection.execute("ALTER TABLE automation_jobs ADD COLUMN interval_seconds INTEGER NOT NULL DEFAULT 10")
        if 'auto_override_attempts' not in job_columns:
            connection.execute("ALTER TABLE automation_jobs ADD COLUMN auto_override_attempts INTEGER NOT NULL DEFAULT 0")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES automation_jobs(job_id) ON DELETE CASCADE,
                step TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_job_media (
                job_id TEXT PRIMARY KEY REFERENCES automation_jobs(job_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                thumbnail TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_job_choices (
                job_id TEXT NOT NULL REFERENCES automation_jobs(job_id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                options_json TEXT NOT NULL,
                selected_id TEXT NOT NULL DEFAULT '',
                refreshed_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (job_id, kind)
            )
        """)
        choice_columns = {row['name'] for row in connection.execute('PRAGMA table_info(automation_job_choices)')}
        if 'refreshed_at' not in choice_columns:
            connection.execute("ALTER TABLE automation_job_choices ADD COLUMN refreshed_at TEXT NOT NULL DEFAULT ''")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_job_deletions (
                job_id TEXT PRIMARY KEY REFERENCES automation_jobs(job_id) ON DELETE CASCADE,
                deleted_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_publish_batches (
                batch_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS automation_publish_batch_items (
                batch_id TEXT NOT NULL REFERENCES automation_publish_batches(batch_id),
                job_id TEXT NOT NULL REFERENCES automation_jobs(job_id),
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (batch_id, job_id)
            )
        """)
        connection.execute('CREATE INDEX IF NOT EXISTS automation_jobs_detail_idx ON automation_jobs(detail_id)')
        connection.execute('CREATE INDEX IF NOT EXISTS automation_jobs_status_idx ON automation_jobs(status, created_at)')
        connection.execute('CREATE INDEX IF NOT EXISTS automation_job_events_job_idx ON automation_job_events(job_id, event_id)')
        connection.execute('CREATE INDEX IF NOT EXISTS automation_job_events_day_idx ON automation_job_events(created_at, step, status, job_id)')
        connection.execute('CREATE INDEX IF NOT EXISTS automation_jobs_target_idx ON automation_jobs(detail_id, site, shop_id, status)')
        connection.execute('CREATE INDEX IF NOT EXISTS automation_publish_items_job_idx ON automation_publish_batch_items(job_id, status, batch_id)')
        connection.execute('CREATE INDEX IF NOT EXISTS automation_items_created_idx ON automation_items(created_at DESC, CAST(detail_id AS TEXT) DESC)')
        return connection

    def _read_connect(self):
        # Pure reads coexist with the physical pool writer under WAL.
        if not self._initialized:
            with closing(self._connect()): pass
        connection = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    def get_job_state(self, job_id):
        return self.job_states([job_id]).get(job_id)

    def read_rows(self, sql, parameters=()):
        with closing(self._read_connect()) as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    @staticmethod
    def _summary(row: sqlite3.Row, claims: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "detailId": row["detail_id"],
            "mode": row["mode"],
            "shopId": row["shop_id"],
            "title": row["title"],
            "thumbnail": row["thumbnail"],
            "version": row["version"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "status": "待认领",
            "claims": claims or [],
        }

    @staticmethod
    def _claim_rows(connection: sqlite3.Connection, detail_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not detail_ids:
            return {}
        claims: dict[int, list[dict[str, Any]]] = {}
        ids = list(dict.fromkeys(detail_ids))
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            rows = connection.execute('SELECT * FROM automation_claims WHERE detail_id IN (' +
                ','.join('?' for _ in batch) + ') ORDER BY detail_id, shop_id', batch)
            for row in rows:
                claims.setdefault(row["detail_id"], []).append({
                    "shopId": row["shop_id"], "status": row["status"],
                    "error": row["error"], "updatedAt": row["updated_at"],
                })
        return claims

    def item_ids(self) -> set[int]:
        """Check incremental imports without loading large detail snapshots."""
        with closing(self._connect()) as connection:
            return {row[0] for row in connection.execute('SELECT detail_id FROM automation_items')}

    def list_items(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM automation_items ORDER BY created_at DESC, detail_id DESC"
            ).fetchall()
            claims = self._claim_rows(connection, [row["detail_id"] for row in rows])
        return [self._summary(row, claims.get(row["detail_id"])) for row in rows]

    def get_item(self, detail_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM automation_items WHERE detail_id = ?", (detail_id,)
            ).fetchone()
            claims = self._claim_rows(connection, [detail_id]) if row else {}
        if row is None:
            return None
        return {**self._summary(row, claims.get(detail_id)), "item": json.loads(row["snapshot_json"])}

    def put_item(self, detail_id: int, mode: str, shop_id: int, item: dict[str, Any], *, only_if_new: bool = False) -> dict[str, Any]:
        if detail_id <= 0 or mode not in {"site", "shop"} or (mode == "shop" and shop_id <= 0):
            raise ValueError("商品 ID 或编辑模式不正确")
        if not isinstance(item, dict):
            raise ValueError("妙手商品资料不正确")
        snapshot = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if len(snapshot.encode("utf-8")) > 4_000_000:
            raise ValueError("商品资料过大，无法加入自动化池")
        images = item.get("imgUrls") or []
        thumbnail = images[0] if isinstance(images, list) and images and isinstance(images[0], str) else ""
        title = str(item.get("title") or "未命名商品")[:500]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conflict = 'DO NOTHING' if only_if_new else '''DO UPDATE SET
            mode = excluded.mode, shop_id = excluded.shop_id, title = excluded.title,
            thumbnail = excluded.thumbnail, snapshot_json = excluded.snapshot_json,
            version = excluded.version, updated_at = excluded.updated_at'''
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(f"""
                INSERT INTO automation_items
                    (detail_id, mode, shop_id, title, thumbnail, snapshot_json, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(detail_id) {conflict}
            """, (detail_id, mode, shop_id, title, thumbnail, snapshot, fingerprint(item), now, now))
            row = connection.execute(
                "SELECT * FROM automation_items WHERE detail_id = ?", (detail_id,)
            ).fetchone()
            claims = self._claim_rows(connection, [detail_id])
        result = self._summary(row, claims.get(detail_id))
        if only_if_new:
            result['skipped'] = cursor.rowcount == 0
        return result

    def record_claim(self, detail_id: int, shop_id: int, status: str, error: str = "") -> None:
        if detail_id <= 0 or shop_id <= 0 or status not in {"claimed", "failed"}:
            raise ValueError("认领结果不正确")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                INSERT INTO automation_claims (detail_id, shop_id, status, error, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(detail_id, shop_id) DO UPDATE SET
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = excluded.updated_at
            """, (detail_id, shop_id, status, error[:500], now))

    def remove_item(self, detail_id: int) -> bool:
        return bool(self.remove_items([detail_id]))

    def remove_items(self, detail_ids: list[int]) -> list[int]:
        """Remove several staged items atomically; never orphan an active queue job."""
        ids = sorted({int(detail_id) for detail_id in detail_ids})
        if not ids or any(detail_id <= 0 for detail_id in ids):
            raise ValueError("请选择有效的采集箱商品")
        placeholders = ",".join("?" for _ in ids)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            active_rows = connection.execute(f"""
                SELECT DISTINCT detail_id FROM automation_jobs
                WHERE detail_id IN ({placeholders}) AND status IN ('queued', 'running', 'needs_input', 'ready_publish')
            """, ids).fetchall()
            if active_rows:
                active_ids = "、".join(str(row['detail_id']) for row in active_rows)
                raise ValueError(f"采集箱 ID {active_ids} 正在排队、处理或等待选择，不能移出池子")
            existing = connection.execute(
                f"SELECT detail_id FROM automation_items WHERE detail_id IN ({placeholders})", ids
            ).fetchall()
            removed = [row['detail_id'] for row in existing]
            if removed:
                connection.execute(f"DELETE FROM automation_items WHERE detail_id IN ({placeholders})", ids)
            return removed

    def recover_interrupted_jobs(self) -> None:
        """A process restart must never silently resume a remote write."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE automation_jobs SET status = 'interrupted', message = '服务重启；请到妙手核查后再操作', updated_at = ? WHERE status = 'running'",
                (now,),
            )
            connection.execute("UPDATE automation_publish_batch_items SET status = 'needs_review' WHERE status = 'publishing'")
            connection.execute("UPDATE automation_publish_batches SET status = 'interrupted', updated_at = ? WHERE status = 'running'", (now,))

    def start_job(self, job_id: str, detail_id: int, shop_id: int, site: str, *, price: str = '160',
                  publish_after_save: bool = True, interval_seconds: int = 10) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            item = connection.execute(
                "SELECT title, thumbnail FROM automation_items WHERE detail_id = ?", (detail_id,)
            ).fetchone()
            if item is None:
                raise ValueError("商品不在自动化池中")
            if connection.execute("SELECT 1 FROM automation_jobs WHERE status = 'running'").fetchone():
                raise ValueError("已有自动任务正在运行，请等待完成后再提交下一件")
            if connection.execute("SELECT 1 FROM automation_jobs WHERE detail_id = ? AND status = 'needs_input'", (detail_id,)).fetchone():
                raise ValueError("该商品已有暂停任务，请先继续或结束上次任务")
            connection.execute("""
                INSERT INTO automation_jobs (job_id, detail_id, shop_id, site, price, publish_after_save, interval_seconds, status, step, message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 'queued', '等待后台处理', ?, ?)
            """, (job_id, detail_id, shop_id, site, str(price), int(publish_after_save), interval_seconds, now, now))
            connection.execute(
                'INSERT INTO automation_job_media (job_id, title, thumbnail) VALUES (?, ?, ?)',
                (job_id, item['title'], item['thumbnail']),
            )
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'queued', 'done', '任务已创建', ?)
            """, (job_id, now))
        return self.get_job(job_id)

    def queue_jobs(self, detail_ids: list[int], shop_id: int | list[int], site: str, *, price: str | list[str] = '160',
                   sku_prices: list[dict[str, str]] | None = None,
                   shops_by_detail: dict[int, list[int]] | None = None,
                   personnel_by_detail: dict[int, str] | None = None,
                   interval_seconds: int = 10, dedupe_pairs: bool = False,
                   existing_pairs: set[tuple[int, int, str]] | None = None,
                   validate_only: bool = False) -> list[dict[str, Any]]:
        """Create a local edit-and-save queue; publishing is released separately."""
        ids = list(dict.fromkeys(int(detail_id) for detail_id in detail_ids))
        if not ids or len(ids) > 50 or any(detail_id <= 0 for detail_id in ids):
            raise ValueError('请选择 1 到 50 件有效商品')
        shops = shop_id if isinstance(shop_id, list) else [shop_id]
        if not shops or any(type(value) is not int or value <= 0 for value in shops):
            raise ValueError('请选择有效的目标店铺')
        shops = list(dict.fromkeys(shops))
        if shops_by_detail is not None:
            if set(shops_by_detail) != set(ids) or any(not isinstance(values, list) or not values or any(
                    type(value) is not int or value <= 0 for value in values) for values in shops_by_detail.values()):
                raise ValueError('每件商品必须有明确的目标店铺')
            shops_by_detail = {detail: list(dict.fromkeys(values)) for detail, values in shops_by_detail.items()}
        if not isinstance(site, str) or not site or not site.isalnum() or len(site) > 20:
            raise ValueError('目标店铺或发布站点不正确')
        site = site.upper()
        if not 1 <= interval_seconds <= 600:
            raise ValueError('发布间隔应为 1 到 600 秒')
        prices = [str(price)] * len(ids) if isinstance(price, str) else [str(item) for item in price]
        if len(prices) != len(ids):
            raise ValueError('每件商品必须有一个申报价')
        item_sku_prices = [{} for _ in ids] if sku_prices is None else [dict(item) for item in sku_prices]
        if len(item_sku_prices) != len(ids) or any(
                any(not isinstance(key, str) or not isinstance(value, str) for key, value in item.items())
                for item in item_sku_prices):
            raise ValueError('SKU 申报价数据不正确')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        job_ids: list[str] = []
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            placeholders = ','.join('?' for _ in ids)
            items = connection.execute(
                f"SELECT detail_id, title, thumbnail FROM automation_items WHERE detail_id IN ({placeholders})", ids
            ).fetchall()
            item_by_id = {row['detail_id']: row for row in items}
            missing = [detail_id for detail_id in ids if detail_id not in item_by_id]
            if missing:
                raise ValueError('采集箱 ID ' + '、'.join(map(str, missing)) + ' 不在自动化池中')
            active = connection.execute(f"""
                SELECT DISTINCT detail_id FROM automation_jobs
                WHERE detail_id IN ({placeholders}) AND status IN ('queued', 'running', 'needs_input', 'ready_publish')
            """, ids).fetchall()
            if active and not dedupe_pairs:
                raise ValueError('以下商品已有排队或未完成任务：' + '、'.join(str(row['detail_id']) for row in active))
            if validate_only:
                return []
            # All product/shop pairs are inserted in one transaction. A bad
            # product or an unfinished earlier batch cannot leave half a batch.
            pairs = ((detail_id, item_price, item_sku_price_map, target_shop)
                     for detail_id, item_price, item_sku_price_map in zip(ids, prices, item_sku_prices)
                     for target_shop in (shops_by_detail[detail_id] if shops_by_detail is not None else shops))
            for detail_id, item_price, item_sku_price_map, target_shop in pairs:
                if dedupe_pairs and ((detail_id, target_shop, site) in (existing_pairs or set())
                        or connection.execute('''SELECT 1 FROM automation_jobs
                        WHERE detail_id=? AND shop_id=? AND UPPER(site)=? AND status!='canceled' LIMIT 1''',
                        (detail_id, target_shop, site)).fetchone()):
                    # Shared orchestration may add another shop to a confirmed
                    # copy. Keep every existing pair, including hidden history.
                    continue
                job_id = uuid.uuid4().hex
                job_ids.append(job_id)
                item = item_by_id[detail_id]
                connection.execute("""
                    INSERT INTO automation_jobs (job_id, detail_id, shop_id, site, price, sku_prices_json, publish_after_save, interval_seconds, status, step, message, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'queued', 'queued', ?, ?, ?)
                """, (job_id, detail_id, target_shop, site, item_price,
                      json.dumps(item_sku_price_map, ensure_ascii=False, sort_keys=True), interval_seconds,
                      '已加入批量编辑保存队列，完成后等待统一发布', now, now))
                connection.execute(
                    'INSERT INTO automation_job_media (job_id, title, thumbnail) VALUES (?, ?, ?)',
                    (job_id, item['title'], item['thumbnail']),
                )
                routing_note = ('；按所属人员账号 ID ' + personnel_by_detail[detail_id] + ' 的店铺绑定保存'
                                if personnel_by_detail and personnel_by_detail.get(detail_id) else '')
                connection.execute("""
                    INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                    VALUES (?, 'queued', 'done', ?, ?)
                """, (job_id, '已加入批量编辑保存队列，完成后等待统一发布' + routing_note, now))
        RUNTIME.work_available.set()
        return [self.get_job(job_id) for job_id in job_ids]

    def start_next_queued_job(self) -> dict[str, Any] | None:
        """Compatibility wrapper for callers that need one edit job."""
        jobs = self.start_next_queued_jobs(1)
        return jobs[0] if jobs else None

    def start_next_queued_jobs(self, limit: int = 8, *, light=False) -> list[dict[str, Any]]:
        """Atomically promote a concurrent edit-and-save group from the local queue."""
        limit = normalize_edit_concurrency(limit)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            running = connection.execute("SELECT COUNT(*) FROM automation_jobs WHERE status = 'running' AND step != 'publish'").fetchone()[0]
            available = max(0, limit - running)
            if not available:
                return []
            rows = connection.execute("""
                SELECT job_id FROM automation_jobs
                WHERE status = 'queued'
                ORDER BY created_at, rowid
                LIMIT ?
            """, (available,)).fetchall()
            if not rows:
                return []
            job_ids = [row['job_id'] for row in rows]
            placeholders = ','.join('?' for _ in job_ids)
            connection.execute(f"""
                UPDATE automation_jobs SET status = 'running', message = '已进入并发编辑保存队列，正在开始处理', updated_at = ?
                WHERE job_id IN ({placeholders}) AND status = 'queued'
            """, [now, *job_ids])
            connection.executemany("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'queued', 'running', '已进入并发编辑保存队列', ?)
            """, [(job_id, now) for job_id in job_ids])
            # Hydration is part of the claim transaction: a failed read must
            # not leave a running job which was never handed to an executor.
            jobs = ([{'jobId':job_id} for job_id in job_ids] if light else
                    [self._read_job(connection, job_id) for job_id in job_ids])
        return jobs

    def has_queued_jobs(self) -> bool:
        with closing(self._read_connect()) as connection:
            return connection.execute("SELECT 1 FROM automation_jobs WHERE status = 'queued'").fetchone() is not None

    def recover_orphaned_edit_jobs(self, cutoff: str) -> dict[str, int]:
        """Caller must hold the scheduler lock. Never replay possible remote writes."""
        result = {'requeued': 0, 'needsReview': 0}
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("""SELECT * FROM automation_jobs j
                WHERE status='running' AND step!='publish' AND updated_at<=?
                AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                AND NOT EXISTS (SELECT 1 FROM automation_publish_batch_items i
                    JOIN automation_publish_batches b ON b.batch_id=i.batch_id
                    WHERE i.job_id=j.job_id AND b.status='running')""", (cutoff,)).fetchall()
            for row in rows:
                touched = connection.execute("SELECT 1 FROM automation_job_events WHERE job_id=? AND step!='queued' LIMIT 1", (row['job_id'],)).fetchone()
                safe = row['step'] == 'queued' and not touched and not row['publish_after_save']
                status = 'queued' if safe else 'needs_review'
                message = ('自动检测：调度器已退出，尚未执行的任务重新排队，保存后等待手动发布' if safe else
                           '自动检测：执行器已退出，已有操作记录，结果需核查；未自动重复执行')
                connection.execute('UPDATE automation_jobs SET status=?,message=?,updated_at=? WHERE job_id=?',
                                   (status, message, now, row['job_id']))
                # Keep the queued-only proof for a later executor-launch failure.
                connection.execute('INSERT INTO automation_job_events(job_id,step,status,message,created_at) VALUES(?,?,?,?,?)',
                                   (row['job_id'], row['step'], status, message, now))
                result['requeued' if safe else 'needsReview'] += 1
        return result

    def ready_publish_jobs(self, shop_id: int | None = None, *, shop_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Return saved jobs in queue order, restricted to the selected stores."""
        if shop_id is not None and (isinstance(shop_id, bool) or not isinstance(shop_id, int) or shop_id <= 0):
            raise ValueError('发布店铺不正确')
        shop_filter = '' if shop_id is None else ' AND shop_id = ?'
        parameters: tuple[Any, ...] = () if shop_id is None else (shop_id,)
        if shop_ids is not None:
            if shop_id is not None or not isinstance(shop_ids, list) or not shop_ids or any(
                    type(value) is not int or value <= 0 for value in shop_ids):
                raise ValueError('请至少选择一个有效发布店铺')
            parameters = tuple(dict.fromkeys(shop_ids))
            shop_filter = ' AND shop_id IN (' + ','.join('?' for _ in parameters) + ')'
        with closing(self._connect()) as connection:
            rows = connection.execute("""
                SELECT job_id FROM automation_jobs j
                WHERE status = 'ready_publish' AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                )""" + shop_filter + """
                ORDER BY created_at, rowid
            """, parameters).fetchall()
        return [self.get_job(row['job_id']) for row in rows]

    def begin_publish_batch(self, job_ids: list[str], interval_seconds: int) -> str:
        """Persist the exact set explicitly released by the user, not later arrivals."""
        ids = list(dict.fromkeys(job_ids))
        if not ids or not 1 <= interval_seconds <= 600:
            raise ValueError('没有待发布商品或发布间隔不正确')
        batch_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute("SELECT 1 FROM automation_publish_batches WHERE status = 'running'").fetchone():
                raise ValueError('统一发布任务正在运行')
            placeholders = ','.join('?' for _ in ids)
            count = connection.execute(f"""
                SELECT COUNT(*) FROM automation_jobs j WHERE job_id IN ({placeholders})
                AND status = 'ready_publish' AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                )
            """, ids).fetchone()[0]
            if count != len(ids):
                raise ValueError('待发布队列已变化，请刷新后重新发布')
            connection.execute('INSERT INTO automation_publish_batches VALUES (?, ?, ?, ?, ?)',
                               (batch_id, 'running', interval_seconds, now, now))
            connection.executemany('INSERT INTO automation_publish_batch_items (batch_id, job_id) VALUES (?, ?)',
                                   [(batch_id, job_id) for job_id in ids])
        return batch_id

    def append_to_running_publish_batch(self, job_ids: list[str], interval_seconds: int) -> str:
        """Append a confirmed release to this account's active publish run.

        A shared publisher is deliberately single-filed at the MiaoShou API,
        but confirmation must not fail merely because it is already working.
        Adding rows to the persisted batch lets that same worker pick them up
        after its current item without starting a competing publisher.
        """
        ids = list(dict.fromkeys(job_ids))
        if not ids or not 1 <= interval_seconds <= 600:
            raise ValueError('没有待发布商品或发布间隔不正确')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            batch = connection.execute("""SELECT batch_id FROM automation_publish_batches
                WHERE status='running' ORDER BY rowid DESC LIMIT 1""").fetchone()
            if batch is None:
                raise ValueError('当前发布任务已结束，请刷新后重新发布')
            placeholders = ','.join('?' for _ in ids)
            count = connection.execute(f"""
                SELECT COUNT(*) FROM automation_jobs j WHERE job_id IN ({placeholders})
                AND status = 'ready_publish' AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                )
            """, ids).fetchone()[0]
            if count != len(ids):
                raise ValueError('待发布队列已变化，请刷新后重新发布')
            batch_id = str(batch['batch_id'])
            if connection.execute(f'''SELECT 1 FROM automation_publish_batch_items
                WHERE batch_id=? AND job_id IN ({placeholders}) LIMIT 1''', [batch_id, *ids]).fetchone():
                raise ValueError('所选商品已在发布队列中，未重复提交；请刷新后仅选择尚未入队的商品')
            connection.executemany('INSERT INTO automation_publish_batch_items (batch_id, job_id) VALUES (?, ?)',
                                   [(batch_id, job_id) for job_id in ids])
            # The latest confirmed setting applies to the remaining items;
            # it never interrupts the request that is currently in progress.
            connection.execute("""UPDATE automation_publish_batches
                SET interval_seconds=?, updated_at=? WHERE batch_id=?""", (interval_seconds, now, batch_id))
        return batch_id

    def rollback_publish_additions(self, batch_id, ids, previous):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.executemany("DELETE FROM automation_publish_batch_items WHERE batch_id=? AND job_id=? AND status='pending'",[(batch_id,j) for j in ids])
            old = next((r for r in previous if r['batch_id']==batch_id),None)
            if old:
                connection.execute('UPDATE automation_publish_batches SET interval_seconds=? WHERE batch_id=?',(old['interval_seconds'],batch_id))
            else:
                connection.execute("UPDATE automation_publish_batches SET status='interrupted' WHERE batch_id=?",(batch_id,))

    def finish_publish_batch(self, batch_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            # An unexpected worker exit during a request is uncertain, never success.
            connection.execute("""
                UPDATE automation_publish_batch_items SET status = 'needs_review'
                WHERE batch_id = ? AND status = 'publishing'
            """, (batch_id,))
            unfinished = connection.execute("""
                SELECT 1 FROM automation_publish_batch_items
                WHERE batch_id = ? AND status IN ('pending', 'publishing')
            """, (batch_id,)).fetchone()
            connection.execute("""
                UPDATE automation_publish_batches SET status = ?, updated_at = ? WHERE batch_id = ?
            """, ('interrupted' if unfinished else 'completed',
                  datetime.now(timezone.utc).isoformat(timespec='seconds'), batch_id))

    def update_running_publish_intervals(self, interval_seconds: int) -> list[str]:
        """Update pacing only for batches that are still actively publishing."""
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not 1 <= interval_seconds <= 600:
            raise ValueError('发布间隔应为 1 到 600 秒')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("SELECT batch_id FROM automation_publish_batches WHERE status='running'").fetchall()
            if not rows:
                return []
            connection.execute("UPDATE automation_publish_batches SET interval_seconds=?, updated_at=? WHERE status='running'",
                               (interval_seconds, now))
        return [str(row['batch_id']) for row in rows]

    def running_publish_interval(self, batch_id: str) -> int | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT interval_seconds FROM automation_publish_batches WHERE batch_id=? AND status='running'",
                                     (batch_id,)).fetchone()
        return int(row['interval_seconds']) if row else None

    def wait_for_running_publish_interval(self, batch_id: str) -> bool:
        """Wait between submissions while accepting a change to the interval.

        The revised interval is measured from the preceding submission.  It
        therefore never interrupts the request already in progress.
        """
        started = time.monotonic()
        while True:
            interval = self.running_publish_interval(batch_id)
            if interval is None:
                return False
            remaining = interval - (time.monotonic() - started)
            if remaining <= 0:
                return True
            time.sleep(min(0.5, remaining))

    def publish_progress(self) -> dict[str, Any] | None:
        """Read the latest persisted batch, even if its task records were hidden."""
        with closing(self._connect()) as connection:
            batch = connection.execute('SELECT * FROM automation_publish_batches ORDER BY rowid DESC LIMIT 1').fetchone()
            if batch is None:
                return None
            rows = connection.execute("""
                SELECT b.status, j.detail_id FROM automation_publish_batch_items b
                JOIN automation_jobs j ON j.job_id = b.job_id WHERE b.batch_id = ?
            """, (batch['batch_id'],)).fetchall()
        counts = {name: sum(row['status'] == name for row in rows)
                  for name in ('pending', 'publishing', 'submitted', 'needs_review', 'removed')}
        handled = counts['submitted'] + counts['needs_review'] + counts['removed']
        return {'batchId': batch['batch_id'], 'status': batch['status'], 'total': len(rows),
                'submitted': counts['submitted'], 'needsReview': counts['needs_review'], 'removed': counts['removed'],
                'remaining': counts['pending'] + counts['publishing'], 'handled': handled,
                'currentDetailId': next((row['detail_id'] for row in rows if row['status'] == 'publishing'), None),
                'intervalSeconds': batch['interval_seconds'], 'createdAt': batch['created_at']}

    def remove_publish_queue_jobs(self, job_ids: list[str]) -> list[str]:
        """Cancel pending publication only; preserve saved goods and audit history."""
        if not isinstance(job_ids, list) or not job_ids or len(job_ids) > 100 or any(
            not isinstance(value, str) or len(value) != 32 or
            any(char not in '0123456789abcdef' for char in value) for value in job_ids
        ):
            raise ValueError('请选择 1 到 100 条有效待发布任务')
        ids = list(dict.fromkeys(job_ids))
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        message = '用户已移出发布队列；妙手商品及保存记录保留'
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            placeholders = ','.join('?' for _ in ids)
            rows = connection.execute(f"""
                SELECT job_id, status FROM automation_jobs j WHERE job_id IN ({placeholders})
                AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id)
            """, ids).fetchall()
            if len(rows) != len(ids) or any(row['status'] != 'ready_publish' for row in rows):
                raise ValueError('部分任务已不在待发布队列，未删除任何项目；请刷新后重选，正在发布的任务不能删除')
            connection.executemany("""
                UPDATE automation_jobs SET status = 'canceled', step = 'publish',
                    message = ?, updated_at = ? WHERE job_id = ? AND status = 'ready_publish'
            """, [(message, now, job_id) for job_id in ids])
            connection.executemany("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'publish', 'canceled', ?, ?)
            """, [(job_id, message, now) for job_id in ids])
            connection.executemany("""
                UPDATE automation_publish_batch_items SET status = 'removed' WHERE job_id = ? AND status = 'pending'
            """, [(job_id,) for job_id in ids])
        return ids

    def start_next_ready_publish_job(self, batch_id: str | None = None) -> dict[str, Any] | None:
        """Promote one explicitly released saved job for a single publish request."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("""
                SELECT job_id FROM automation_jobs
                WHERE status = 'ready_publish' AND (? IS NULL OR EXISTS (
                    SELECT 1 FROM automation_publish_batch_items b
                    JOIN automation_publish_batches p ON p.batch_id = b.batch_id
                    WHERE b.job_id = automation_jobs.job_id AND b.batch_id = ?
                      AND b.status = 'pending' AND p.status = 'running'
                ))
                ORDER BY created_at, rowid
                LIMIT 1
            """, (batch_id, batch_id)).fetchone()
            if row is None:
                return None
            connection.execute("""
                UPDATE automation_jobs SET status = 'running', step = 'publish',
                    message = '已开始统一发布，正在提交妙手发布任务', updated_at = ?
                WHERE job_id = ? AND status = 'ready_publish'
            """, (now, row['job_id']))
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'publish', 'running', '已开始统一发布，正在提交妙手发布任务', ?)
            """, (row['job_id'], now))
            job_id = row['job_id']
            if batch_id:
                connection.execute("""
                    UPDATE automation_publish_batch_items SET status = 'publishing'
                    WHERE batch_id = ? AND job_id = ? AND status = 'pending'
                """, (batch_id, job_id))
        return self.get_job(job_id)

    def update_job(self, job_id: str, step: str, status: str, message: str) -> None:
        if status not in {'running', 'done', 'failed', 'needs_review', 'ready_publish'}:
            raise ValueError('任务状态不正确')
        job_status = status if status in {'failed', 'needs_review', 'ready_publish'} else 'done' if step == 'complete' else 'running'
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        completion = None
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("""
                UPDATE automation_jobs SET step = ?, status = ?, message = ?, updated_at = ? WHERE job_id = ?
                AND status != 'canceled' AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = automation_jobs.job_id
                )
            """, (step, job_status, message[:1000], now, job_id))
            if cursor.rowcount != 1:
                return
            if step in {'save', 'publish'} and status == 'done' and not connection.execute(
                    "SELECT 1 FROM automation_job_events WHERE job_id=? AND step=? AND status='done' LIMIT 1",
                    (job_id, step)).fetchone():
                completion = 'edit' if step == 'save' else 'publish'
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (job_id, step, status, message[:1000], now))
            if step == 'publish' and status in {'done', 'needs_review', 'failed'}:
                connection.execute("""
                    UPDATE automation_publish_batch_items SET status = ?
                    WHERE job_id = ? AND status = 'publishing'
                """, ('submitted' if status == 'done' else 'needs_review', job_id))
        # Count only committed, first successful task steps. A later event or
        # a failed/rolled-back transaction cannot inflate the throughput.
        if completion:
            RUNTIME.metrics.completed(getattr(self, 'account_id', None), completion)

    def update_job_title(self, job_id: str, title: str) -> None:
        """Use the generated Chinese title in task and publish-queue displays."""
        title = str(title).strip()
        if not title or len(title) > 500:
            raise ValueError('生成的商品标题不正确')
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE automation_job_media SET title = ? WHERE job_id = ? AND EXISTS (
                    SELECT 1 FROM automation_jobs j WHERE j.job_id = automation_job_media.job_id
                    AND j.status != 'canceled' AND NOT EXISTS (
                        SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                    )
                )""", (title, job_id)
            )
            if cursor.rowcount != 1:
                row = connection.execute("""
                    SELECT j.status FROM automation_jobs j WHERE j.job_id = ? AND (
                        j.status = 'canceled' OR EXISTS (
                            SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                        )
                    )
                """, (job_id,)).fetchone()
                if row is not None:
                    return
                raise ValueError('自动化任务不存在')

    def is_job_active(self, job_id: str) -> bool:
        """Whether a worker may still make a new remote request for this job."""
        with closing(self._connect()) as connection:
            return connection.execute("""
                SELECT 1 FROM automation_jobs j WHERE j.job_id = ? AND j.status != 'canceled'
                AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id)
            """, (job_id,)).fetchone() is not None

    @staticmethod
    def _generated_title(events: list[sqlite3.Row], fallback: str) -> str:
        """Read historical title events so existing queued goods self-repair on load."""
        for event in reversed(events):
            if event['step'] != 'titles' or event['status'] != 'done':
                continue
            matched = _GENERATED_TITLE_EVENT.fullmatch(str(event['message']))
            if matched:
                return matched.group(1)
        return fallback

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._read_connect()) as connection:
            return self._read_job(connection, job_id)

    def job_states(self, job_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Lightweight exact-ID polling without loading events or opening per job."""
        result = {}
        ids = list(dict.fromkeys(job_ids))
        if not ids:
            return result
        with closing(self._read_connect()) as connection:
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                rows = connection.execute('''SELECT j.job_id,j.detail_id,j.shop_id,j.site,j.status,j.message
                    FROM automation_jobs j WHERE j.job_id IN (''' + ','.join('?' for _ in batch) + ''')
                    AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)''', batch)
                for row in rows:
                    result[row['job_id']] = {'jobId': row['job_id'], 'detailId': row['detail_id'],
                        'shopId': row['shop_id'], 'site': row['site'], 'status': row['status'], 'message': row['message']}
        return result

    def _read_job(self, connection, job_id):
        row = connection.execute("""
                SELECT j.*, COALESCE(m.title, i.title, '') AS item_title,
                    COALESCE(m.thumbnail, i.thumbnail, '') AS item_thumbnail
                FROM automation_jobs j
                LEFT JOIN automation_job_media m ON m.job_id = j.job_id
                LEFT JOIN automation_items i ON i.detail_id = j.detail_id
                WHERE j.job_id = ? AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                )
            """, (job_id,)).fetchone()
        if row is None:
            return None
        choices = connection.execute(
            "SELECT kind, options_json, selected_id, refreshed_at FROM automation_job_choices WHERE job_id = ?", (job_id,)
        ).fetchall()
        events = read_job_events(self.path, job_id, connection=connection)
        return self._serialize_job(row, choices, events)

    def _serialize_job(self, row: sqlite3.Row, choices: list[sqlite3.Row], events: list[sqlite3.Row]) -> dict[str, Any]:
        """Format a job after its related records have been fetched in one batch."""
        display_title = self._generated_title(events, row['item_title'])
        return {
            'jobId': row['job_id'], 'detailId': row['detail_id'], 'shopId': row['shop_id'],
            'title': display_title, 'thumbnail': row['item_thumbnail'],
            'site': row['site'], 'price': row['price'],
            'skuPrices': json.loads(row['sku_prices_json'] or '{}'),
            'status': row['status'], 'step': row['step'],
            'publishAfterSave': bool(row['publish_after_save']), 'intervalSeconds': row['interval_seconds'],
            'autoOverrideAttempts': row['auto_override_attempts'],
            'message': row['message'], 'createdAt': row['created_at'], 'updatedAt': row['updated_at'],
            'choices': {choice['kind']: {'options': json.loads(choice['options_json']),
                                        'selectedId': choice['selected_id'],
                                        'refreshedAt': choice['refreshed_at']} for choice in choices},
            'events': [{'step': event['step'], 'status': event['status'], 'message': event['message'],
                        'createdAt': event['created_at']} for event in events],
        }

    def pause_for_choice(self, job_id: str, kind: str, options: list[dict[str, str]]) -> None:
        if kind not in {'freight', 'warehouse'}:
            raise ValueError('物流配置类型不正确')
        label = '运费模板' if kind == 'freight' else '发货仓库'
        if not options:
            raise ValueError(f'当前店铺与站点没有可选的{label}；请先在妙手配置，再点击重新检查')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                INSERT INTO automation_job_choices (job_id, kind, options_json) VALUES (?, ?, ?)
                ON CONFLICT(job_id, kind) DO UPDATE SET options_json = excluded.options_json
            """, (job_id, kind, json.dumps(options, ensure_ascii=False)))
            cursor = connection.execute("""
                UPDATE automation_jobs SET status = 'needs_input', step = ?,
                    message = ?, updated_at = ? WHERE job_id = ?
                    AND status != 'canceled' AND NOT EXISTS (
                        SELECT 1 FROM automation_job_deletions d WHERE d.job_id = automation_jobs.job_id
                    )
            """, (kind, f'未找到唯一默认{label}，请从可用选项中确认', now, job_id))
            if cursor.rowcount != 1:
                return
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, ?, 'needs_input', ?, ?)
            """, (job_id, kind, f'等待选择{label}', now))

    def refresh_choice(self, job_id: str, kind: str, options: list[dict[str, str]]) -> dict[str, Any]:
        """Replace a paused task's options without resuming or changing its target."""
        if kind not in {'freight', 'warehouse'}:
            raise ValueError('物流配置类型不正确')
        label, default = ('运费模板', '通用') if kind == 'freight' else ('发货仓库', '发货')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            job = self._read_job(connection, job_id)
            if not job or job['status'] != 'needs_input' or job['step'] != kind:
                raise ValueError('任务状态已变化，请重新打开任务详情')
            selected = (job['choices'].get(kind) or {}).get('selectedId', '')
            if not any(option['id'] == selected for option in options):
                selected = ''
            defaults = [option for option in options if option['name'].strip() == default]
            if not options:
                message = f'当前店铺与站点没有可选的{label}，请在店铺配置后刷新'
            elif len(defaults) == 1:
                message = f'已同步店铺最新{label}，找到默认“{default}”，可继续保存'
            else:
                message = f'已同步店铺最新{label}，请从可用选项中确认'
            connection.execute("""
                INSERT INTO automation_job_choices (job_id, kind, options_json, selected_id, refreshed_at)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id, kind) DO UPDATE SET
                    options_json=excluded.options_json, selected_id=excluded.selected_id,
                    refreshed_at=excluded.refreshed_at
            """, (job_id, kind, json.dumps(options, ensure_ascii=False), selected, now))
            connection.execute('UPDATE automation_jobs SET message=?, updated_at=? WHERE job_id=?',
                               (message, now, job_id))
        return self.get_job(job_id)

    def refresh_choices_for_shop(self, shop_id: int, site: str, kind: str, options: list[dict[str, str]],
                                 *, queue_unique_default: bool = False) -> dict[str, int]:
        """Refresh every paused task sharing one store/site choice.

        A store-level logistics change applies to all of its paused edit tasks,
        not only the card the operator happened to open.  When MiaoShou now
        has exactly one default option, those tasks can safely return to the
        local edit queue; this does not submit a publish request.
        """
        if kind not in {'freight', 'warehouse'}:
            raise ValueError('物流配置类型不正确')
        label, default = ('运费模板', '通用') if kind == 'freight' else ('发货仓库', '发货')
        defaults = [option for option in options if str(option.get('name', '')).strip() == default]
        chosen_default = str(defaults[0]['id']) if queue_unique_default and len(defaults) == 1 else ''
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        refreshed = queued = 0
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = list(connection.execute("""SELECT j.job_id,j.detail_id,COALESCE(c.selected_id,'') AS selected_id
                FROM automation_jobs j LEFT JOIN automation_job_choices c ON c.job_id=j.job_id AND c.kind=?
                WHERE j.shop_id=? AND j.site=? AND j.status='needs_input' AND j.step=?
                  AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)""",
                (kind, shop_id, site, kind)))
            for row in rows:
                selected = chosen_default or row['selected_id']
                if not any(str(option.get('id')) == selected for option in options):
                    selected = ''
                connection.execute("""INSERT INTO automation_job_choices (job_id, kind, options_json, selected_id, refreshed_at)
                    VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id, kind) DO UPDATE SET
                    options_json=excluded.options_json, selected_id=excluded.selected_id,
                    refreshed_at=excluded.refreshed_at""",
                    (row['job_id'], kind, json.dumps(options, ensure_ascii=False), selected, now))
                refreshed += 1
                if chosen_default:
                    conflict = connection.execute("""SELECT 1 FROM automation_jobs
                        WHERE job_id != ? AND detail_id=? AND shop_id=? AND status IN ('queued','running')""",
                        (row['job_id'], row['detail_id'], shop_id)).fetchone()
                    if conflict:
                        continue
                    cursor = connection.execute("""UPDATE automation_jobs SET status='queued',publish_after_save=0,
                        auto_override_attempts=0,message=?,updated_at=? WHERE job_id=? AND status='needs_input' AND step=?""",
                        (f'已同步店铺最新{label}，自动选择默认“{default}”并重新加入编辑队列', now, row['job_id'], kind))
                    if cursor.rowcount:
                        connection.execute("""INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                            VALUES (?, ?, 'queued', ?, ?)""",
                            (row['job_id'], kind, f'已同步店铺配置，自动选择默认“{default}”并重新排队', now))
                        queued += 1
                else:
                    message = (f'当前店铺与站点没有可选的{label}，请在店铺配置后刷新' if not options else
                               f'已同步店铺最新{label}，请从可用选项中确认')
                    connection.execute('UPDATE automation_jobs SET message=?, updated_at=? WHERE job_id=?',
                                       (message, now, row['job_id']))
        return {'refreshed': refreshed, 'queued': queued}

    def pause_for_problem(self, job_id: str, step: str, message: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("""
                UPDATE automation_jobs SET status = 'needs_input', step = ?, message = ?, updated_at = ? WHERE job_id = ?
                    AND status != 'canceled' AND NOT EXISTS (
                        SELECT 1 FROM automation_job_deletions d WHERE d.job_id = automation_jobs.job_id
                    )
            """, (step, message[:1000], now, job_id))
            if cursor.rowcount != 1:
                return
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, ?, 'needs_input', ?, ?)
            """, (job_id, step, message[:1000], now))

    def requeue_local_database_busy_jobs(self) -> int:
        """Safely resume jobs paused before any MiaoShou write was attempted.

        ``run_automation_job`` calls :meth:`pause_for_problem` only while its
        ``wrote_remote`` flag is false. Therefore a paused exact SQLite busy
        message proves that no claim, save, or publish request was sent.
        """
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        message = '本机数据库占用已恢复，自动重新加入编辑保存队列'
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("""SELECT job_id,detail_id,shop_id FROM automation_jobs
                WHERE status='needs_input' AND lower(message) IN ('database is locked','database is busy')
                  AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=automation_jobs.job_id)
                ORDER BY created_at,rowid""").fetchall()
            job_ids = []
            for row in rows:
                conflict = connection.execute("""SELECT 1 FROM automation_jobs
                    WHERE job_id != ? AND detail_id=? AND shop_id=? AND status IN ('queued','running')""",
                    (row['job_id'], row['detail_id'], row['shop_id'])).fetchone()
                if conflict:
                    continue
                cursor = connection.execute("""UPDATE automation_jobs
                    SET status='queued',step='queued',publish_after_save=0,message=?,updated_at=?
                    WHERE job_id=? AND status='needs_input' AND lower(message) IN ('database is locked','database is busy')""",
                    (message, now, row['job_id']))
                if cursor.rowcount:
                    job_ids.append(row['job_id'])
            connection.executemany("""INSERT INTO automation_job_events (job_id,step,status,message,created_at)
                VALUES (?, 'queued', 'queued', ?, ?)""", [(job_id, message, now) for job_id in job_ids])
        return len(job_ids)

    def requeue_missing_snapshot_jobs(self, *, blocked_pairs=None) -> int:
        """Repair only false snapshot misses proven to precede remote work.

        Keep the same job identity, history and dedupe records. A snapshot
        must still exist locally, and any claim/save/publish attempt, hidden
        record, duplicate pair or publication membership makes recovery unsafe.
        """
        original = '商品已从自动化池移除'
        message = '已核对本地商品快照仍在，恢复分池读取误报暂停的编辑任务'
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("""SELECT j.job_id,j.detail_id,j.shop_id,j.site FROM automation_jobs j
                WHERE j.status='needs_input' AND j.step='claim' AND j.message=?
                  AND EXISTS (SELECT 1 FROM automation_items i WHERE i.detail_id=j.detail_id)
                  AND EXISTS (SELECT 1 FROM automation_job_events e WHERE e.job_id=j.job_id AND e.step='queued')
                  AND EXISTS (SELECT 1 FROM automation_job_events e WHERE e.job_id=j.job_id
                              AND e.step='claim' AND e.status='needs_input' AND e.message=?)
                  AND NOT EXISTS (SELECT 1 FROM automation_job_events e WHERE e.job_id=j.job_id
                      AND e.step!='queued' AND NOT (e.step='claim' AND e.status='needs_input' AND e.message=?))
                  AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                  AND NOT EXISTS (SELECT 1 FROM automation_publish_batch_items b WHERE b.job_id=j.job_id)
                  AND NOT EXISTS (SELECT 1 FROM automation_jobs other WHERE other.job_id!=j.job_id
                      AND other.detail_id=j.detail_id AND other.shop_id=j.shop_id
                      AND UPPER(other.site)=UPPER(j.site) AND other.status!='canceled')
                ORDER BY j.created_at,j.rowid""", (original, original, original)).fetchall()
            job_ids = []
            for row in rows:
                if (row['detail_id'], row['shop_id'], row['site'].upper()) in (blocked_pairs or ()):
                    continue
                cursor = connection.execute("""UPDATE automation_jobs
                    SET status='queued',step='queued',publish_after_save=0,message=?,updated_at=?
                    WHERE job_id=? AND status='needs_input' AND step='claim' AND message=?""",
                    (message, now, row['job_id'], original))
                if cursor.rowcount:
                    job_ids.append(row['job_id'])
            connection.executemany("""INSERT INTO automation_job_events (job_id,step,status,message,created_at)
                VALUES (?, 'queued', 'queued', ?, ?)""", [(job_id, message, now) for job_id in job_ids])
        return len(job_ids)

    @staticmethod
    def _is_definite_save_rejection(failed_step: str, reason: str) -> bool:
        """Only known validation rejections prove that a save was not accepted."""
        if failed_step != 'save':
            return False
        text = str(reason or '')
        lower = text.casefold()
        if has_unknown_write_outcome(reason):
            return False
        return (('产品属性【' in text and '必填' in text) or '承诺发货时效错误' in text
                or '英文标题不能超过250个字符' in text
                or ('groupIdAndValueMap' in text and ('必填' in text or 'required' in lower))
                or ('店铺【' in text and '请选择店铺运费模板' in text))

    def begin_auto_override(self, job_id: str, failed_step: str, reason: str) -> bool:
        """Claim one retry only when the save received a definite rejection."""
        if not self._is_definite_save_rejection(failed_step, reason):
            return False
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.execute("""
                UPDATE automation_jobs
                SET status = 'running', step = 'preflight', auto_override_attempts = auto_override_attempts + 1,
                    message = '妙手已明确拒绝保存，后台正在重新检查后重试', updated_at = ?
                WHERE job_id = ? AND auto_override_attempts < 1 AND step='save'
                    AND status IN ('running','needs_review') AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = automation_jobs.job_id
                )
            """, (now, job_id))
            if cursor.rowcount != 1:
                return False
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, ?, 'running', ?, ?)
            """, (job_id, failed_step,
                  f'妙手明确拒绝保存，重新检查后重试第 1 次：{reason[:700]}', now))
        return True

    def claim_pending_auto_overrides(self, limit: int = 50) -> list[dict[str, Any]]:
        """Claim only explicit save rejections; unknown outcomes stay in review."""
        if not 1 <= limit <= 50:
            raise ValueError('自动重试数量不正确')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("""
                SELECT job_id,step,message FROM automation_jobs j
                WHERE status = 'needs_review' AND step = 'save' AND auto_override_attempts < 1
                  AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                ORDER BY created_at, rowid
            """).fetchall()
            job_ids = [row['job_id'] for row in rows
                       if self._is_definite_save_rejection(row['step'], row['message'])][:limit]
            if not job_ids:
                return []
            placeholders = ','.join('?' for _ in job_ids)
            connection.execute(f"""
                UPDATE automation_jobs
                SET status = 'running', step = 'preflight', auto_override_attempts = auto_override_attempts + 1,
                    message = '服务启动后重新检查已被妙手明确拒绝的保存任务', updated_at = ?
                WHERE job_id IN ({placeholders}) AND status = 'needs_review' AND step = 'save' AND auto_override_attempts < 1
            """, [now, *job_ids])
            connection.executemany("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'preflight', 'running', '服务启动后重新检查明确拒绝的保存，第 1 次重试', ?)
            """, [(job_id, now) for job_id in job_ids])
        return [job for job_id in job_ids if (job := self.get_job(job_id)) is not None]

    def claim_pending_direct_edit_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Resume only old tasks paused by the retired snapshot-version guard."""
        if not 1 <= limit <= 50:
            raise ValueError('自动继续数量不正确')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("""
                SELECT job_id FROM automation_jobs
                WHERE status = 'needs_input' AND message LIKE '%商品资料已变化%'
                ORDER BY created_at, rowid LIMIT ?
            """, (limit,)).fetchall()
            job_ids = [row['job_id'] for row in rows]
            if not job_ids:
                return []
            placeholders = ','.join('?' for _ in job_ids)
            connection.execute(f"""
                UPDATE automation_jobs SET status = 'running', step = 'preflight',
                    message = '已取消资料版本判断，后台正在直接编辑并覆盖保存', updated_at = ?
                WHERE job_id IN ({placeholders}) AND status = 'needs_input'
                    AND message LIKE '%商品资料已变化%'
            """, [now, *job_ids])
            connection.executemany("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'preflight', 'running', '已取消资料版本判断，后台自动继续直接编辑', ?)
            """, [(job_id, now) for job_id in job_ids])
        return [job for job_id in job_ids if (job := self.get_job(job_id)) is not None]

    def resume_job(self, job_id: str, selected_id: str = '', *, queue_only: bool = False) -> dict[str, Any]:
        """Resume different products concurrently; atomically reject duplicate work."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            job = connection.execute("""SELECT * FROM automation_jobs j WHERE job_id = ?
                AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)""", (job_id,)).fetchone()
            if not job or job['status'] != 'needs_input':
                raise ValueError('任务并未暂停等待处理')
            if not connection.execute('SELECT 1 FROM automation_items WHERE detail_id = ?',
                                      (job['detail_id'],)).fetchone():
                raise ValueError('商品已移出自动化池，请重新加入后再继续')
            if connection.execute("""
                SELECT 1 FROM automation_jobs WHERE job_id != ? AND detail_id = ? AND shop_id = ?
                AND status IN ('queued', 'running')
            """, (job_id, job['detail_id'], job['shop_id'])).fetchone():
                raise ValueError('该商品在此店铺已有任务正在处理，请完成后再继续')
            choice = connection.execute(
                "SELECT options_json FROM automation_job_choices WHERE job_id = ? AND kind = ?",
                (job_id, job['step']),
            ).fetchone()
            if choice:
                options = json.loads(choice['options_json'])
                if not any(option['id'] == selected_id for option in options):
                    raise ValueError('所选配置不在当前可选列表中')
                connection.execute(
                    "UPDATE automation_job_choices SET selected_id = ? WHERE job_id = ? AND kind = ?",
                    (selected_id, job_id, job['step']),
                )
            elif selected_id:
                raise ValueError('当前阶段不需要选择配置')
            connection.execute("""
                UPDATE automation_jobs SET status = ?, step = ?, publish_after_save = 0, auto_override_attempts = 0,
                    message = '已确认，重新检查并继续编辑保存', updated_at = ? WHERE job_id = ?
            """, ('queued' if queue_only else 'running', 'queued' if queue_only else job['step'], now, job_id))
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, ?, ?, '用户已确认，重新检查', ?)
            """, (job_id, 'queued' if queue_only else job['step'], 'queued' if queue_only else 'done', now))
        return self.get_job(job_id)

    def cancel_paused_job(self, job_id: str, *, message: str = '用户结束暂停任务，可重新选择店铺或站点') -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("""
                UPDATE automation_jobs SET status = 'canceled', message = ?,
                    updated_at = ? WHERE job_id = ? AND status = 'needs_input'
            """, (message[:1000], now, job_id))
            if cursor.rowcount != 1:
                raise ValueError('只有暂停的任务可以结束')
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'canceled', 'done', ?, ?)
            """, (job_id, message[:1000], now))
        return self.get_job(job_id)

    def cancel_edit_job(self, job_id: str) -> dict[str, Any]:
        """Stop one editable task without touching its or anyone else's publication."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        message = '用户已停止此条编辑保存任务；发布队列未受影响'
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("""
                UPDATE automation_jobs SET status = 'canceled', step = 'canceled', message = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('queued', 'running', 'needs_input')
                  AND NOT (status = 'running' AND step = 'publish')
                  AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = automation_jobs.job_id)
            """, (message, now, job_id))
            if cursor.rowcount != 1:
                raise ValueError('只有编辑保存中的任务可以停止；发布队列不会被此操作影响')
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'canceled', 'canceled', ?, ?)
            """, (job_id, message, now))
        job = self.get_job(job_id)
        if job is None:
            raise ValueError('任务不存在或已删除')
        return job

    def retry_job(self, job_id: str, *, queue_only: bool = False) -> dict[str, Any]:
        """Retry editing in the same record; publication always needs manual release."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        message = '用户重试：重新编辑保存，完成后进入发布队列'
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            job = connection.execute("""
                SELECT j.* FROM automation_jobs j WHERE j.job_id = ? AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                )
            """, (job_id,)).fetchone()
            if not job or job['status'] not in {'failed', 'needs_review', 'interrupted'}:
                raise ValueError('只有失败、需核查或中断的任务可以重试')
            if not connection.execute('SELECT 1 FROM automation_items WHERE detail_id = ?',
                                      (job['detail_id'],)).fetchone():
                raise ValueError('商品已移出自动化池，请重新加入后再重试')
            if connection.execute("""
                SELECT 1 FROM automation_jobs WHERE job_id != ? AND detail_id = ? AND shop_id = ?
                AND status IN ('queued', 'running')
            """, (job_id, job['detail_id'], job['shop_id'])).fetchone():
                raise ValueError('该商品在此店铺已有任务正在处理，请完成后再重试')
            connection.execute("""
                UPDATE automation_jobs SET status = ?, step = ?,
                    publish_after_save = 0, auto_override_attempts = 0, message = ?, updated_at = ?
                WHERE job_id = ?
            """, ('queued' if queue_only else 'running', 'queued' if queue_only else 'preflight', message, now, job_id))
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (job_id, 'queued' if queue_only else 'preflight', 'queued' if queue_only else 'running', message, now))
        return self.get_job(job_id)

    def force_retry_for_publish(self, job_id: str) -> dict[str, Any]:
        """Resume only a reviewed task after an explicit force-publish confirmation."""
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            job = connection.execute("""
                SELECT j.status FROM automation_jobs j
                WHERE j.job_id = ? AND NOT EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                )
            """, (job_id,)).fetchone()
            if not job or job['status'] != 'needs_review':
                raise ValueError('只有需核查的任务可以强制覆盖并继续处理')
            if connection.execute("SELECT 1 FROM automation_jobs WHERE status = 'running'").fetchone():
                raise ValueError('已有自动任务正在运行，请等待完成后再继续')
            connection.execute("""
                UPDATE automation_jobs SET status = 'running', step = 'preflight',
                    message = '用户已确认强制覆盖妙手并继续处理', updated_at = ? WHERE job_id = ?
            """, (now, job_id))
            connection.execute("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'preflight', 'done', '用户已确认强制覆盖妙手并继续处理', ?)
            """, (job_id, now))
        return self.get_job(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("""
                SELECT j.*, COALESCE(m.title, i.title, '') AS item_title,
                    COALESCE(m.thumbnail, i.thumbnail, '') AS item_thumbnail
                FROM automation_jobs j
                LEFT JOIN automation_job_media m ON m.job_id = j.job_id
                LEFT JOIN automation_items i ON i.detail_id = j.detail_id
                WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id)
                ORDER BY j.created_at DESC, j.rowid DESC
            """).fetchall()
            if not rows:
                return []
            choices = connection.execute("""
                SELECT c.job_id, c.kind, c.options_json, c.selected_id, c.refreshed_at
                FROM automation_job_choices c
                JOIN automation_jobs j ON j.job_id = c.job_id
                WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id)
            """).fetchall()
            events_by_job = read_events_for_jobs(self.path, [row['job_id'] for row in rows], connection=connection)
        choices_by_job: dict[str, list[sqlite3.Row]] = {}
        for choice in choices:
            choices_by_job.setdefault(choice['job_id'], []).append(choice)
        return [self._serialize_job(row, choices_by_job.get(row['job_id'], []),
                                    events_by_job.get(row['job_id'], [])) for row in rows]

    def delete_job_records(self, job_ids: list[str], *, allow_pending: bool = False) -> list[str]:
        """Soft-delete records; optionally cancel pending jobs in the same transaction."""
        ids = list(dict.fromkeys(job_ids))
        if not ids or len(ids) > 100 or any(not isinstance(job_id, str) or len(job_id) != 32 for job_id in ids):
            raise ValueError('请选择 1 到 100 条有效任务记录')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        placeholders = ','.join('?' for _ in ids)
        terminal = {'done', 'failed', 'needs_review', 'interrupted', 'canceled'}
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute(f"""
                SELECT j.job_id, j.status, EXISTS (
                    SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id
                ) AS deleted FROM automation_jobs j WHERE j.job_id IN ({placeholders})
            """, ids).fetchall()
            found = {row['job_id']: row['status'] for row in rows}
            if any(job_id not in found for job_id in ids) or (not allow_pending and any(row['deleted'] for row in rows)):
                raise ValueError('部分任务不存在或已删除')
            if allow_pending:
                terminal |= {'needs_input', 'queued'}
            if any(found[job_id] not in terminal for job_id in ids):
                if allow_pending:
                    raise ValueError('运行中的任务不能删除；待发布任务请先移出发布队列')
                raise ValueError('运行中、待处理或待发布的任务不能删除')
            pending_ids = [row['job_id'] for row in rows if not row['deleted'] and row['status'] in {'needs_input', 'queued'}]
            if pending_ids:
                message = '用户删除本地任务记录，已结束排队或待处理任务；未删除妙手商品'
                connection.executemany("""UPDATE automation_jobs SET status='canceled', step='canceled',
                    message=?, updated_at=? WHERE job_id=?""", [(message, now, job_id) for job_id in pending_ids])
                connection.executemany("""INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                    VALUES (?, 'canceled', 'canceled', ?, ?)""", [(job_id, message, now) for job_id in pending_ids])
            connection.executemany(
                'INSERT OR IGNORE INTO automation_job_deletions (job_id, deleted_at) VALUES (?, ?)',
                [(job_id, now) for job_id in ids],
            )
        return ids

    def delete_job_record(self, job_id: str) -> bool:
        """Hide a finished task while retaining the local audit trail."""
        try:
            return bool(self.delete_job_records([job_id]))
        except ValueError as error:
            if str(error) == '部分任务不存在或已删除':
                return False
            raise

    def stop_and_clear_jobs(self) -> dict[str, int]:
        """Cancel edit work and hide its records without changing publication.

        The cancellation flag is persisted before the rows are hidden.  Workers
        check it before each MiaoShou request, so queued work stops immediately
        and a worker already between requests cannot continue to the next one.
        Ready or already-running publication stays in the publish queue.
        """
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        message = '用户已停止并清空编辑任务列表；发布队列保持不变'
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("""
                SELECT j.job_id, j.status FROM automation_jobs j
                WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = j.job_id)
                  AND NOT (j.status = 'ready_publish' OR (j.status = 'running' AND j.step = 'publish'))
            """).fetchall()
            job_ids = [row['job_id'] for row in rows]
            active_statuses = {'queued', 'running', 'needs_input'}
            stopped = sum(row['status'] in active_statuses for row in rows)
            if not job_ids:
                return {'cleared': 0, 'stopped': 0}
            connection.execute("""
                UPDATE automation_jobs SET status = 'canceled', step = 'canceled',
                    message = ?, updated_at = ?
                WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id = automation_jobs.job_id)
                  AND NOT (status = 'ready_publish' OR (status = 'running' AND step = 'publish'))
            """, (message, now))
            connection.executemany("""
                INSERT INTO automation_job_events (job_id, step, status, message, created_at)
                VALUES (?, 'canceled', 'canceled', ?, ?)
            """, [(job_id, message, now) for job_id in job_ids])
            connection.executemany(
                'INSERT INTO automation_job_deletions (job_id, deleted_at) VALUES (?, ?)',
                [(job_id, now) for job_id in job_ids],
            )
        return {'cleared': len(job_ids), 'stopped': stopped}
