"""Explicit, short-lived approval for the server's own event archive scope."""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from threading import Lock
import hashlib
import json
import secrets
import sqlite3
import time

import event_archive


class MaintenanceError(ValueError):
    def __init__(self, status, message, *, data=None):
        super().__init__(message)
        self.status = HTTPStatus(status)
        self.status_code = int(self.status)
        self.data = data


@dataclass
class _Preview:
    paths: tuple
    retention_days: int
    now: datetime
    expires: float
    signature: str
    report: dict
    used: bool = False


class MaintenanceService:
    """The provider is trusted server code; request bodies never supply paths."""
    TOKEN_TTL_SECONDS = 600

    def __init__(self, pool_paths_provider, *, clock=None, monotonic=None, token_limit=32):
        if not callable(pool_paths_provider):
            raise TypeError('pool_paths_provider must be callable')
        if type(token_limit) is not int or not 1 <= token_limit <= 256:
            raise ValueError('token_limit must be between 1 and 256')
        self._provider = pool_paths_provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._token_limit = token_limit
        self._tokens = OrderedDict()
        self._token_lock = Lock()
        self._execute_lock = Lock()

    @staticmethod
    def _days(value):
        if type(value) is not int or value < event_archive.MIN_RETENTION_DAYS:
            raise MaintenanceError(HTTPStatus.BAD_REQUEST,
                                   f'事件至少保留最近 {event_archive.MIN_RETENTION_DAYS} 天，请填写整数天数')
        return value

    def _paths(self):
        # Canonicalize only paths from the server provider. The archive engine
        # separately rejects missing databases, symlinks and archive databases.
        values = self._provider()
        if isinstance(values, (str, bytes, Path)):
            raise MaintenanceError(HTTPStatus.INTERNAL_SERVER_ERROR, '服务端任务池路径配置不正确')
        return tuple(sorted(set(Path(value).absolute() for value in values), key=str))

    @staticmethod
    def _signature(report):
        return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest()

    def _prune(self, current):
        for token, preview in list(self._tokens.items()):
            if preview.expires <= current:
                del self._tokens[token]
        while len(self._tokens) >= self._token_limit:
            self._tokens.popitem(last=False)

    def preview(self, retention_days=90):
        days = self._days(retention_days)
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise MaintenanceError(HTTPStatus.INTERNAL_SERVER_ERROR, '服务端归档时钟缺少时区')
        now = now.astimezone(timezone.utc)
        try:
            paths = self._paths()
            report = event_archive.preview_event_archives(paths, retention_days=days, now=now)
        except MaintenanceError:
            raise
        except ValueError as error:
            raise MaintenanceError(HTTPStatus.BAD_REQUEST, str(error)) from error
        except sqlite3.Error as error:
            raise MaintenanceError(HTTPStatus.SERVICE_UNAVAILABLE, '读取事件归档范围失败，请稍后重新预览') from error
        except Exception as error:
            raise MaintenanceError(HTTPStatus.INTERNAL_SERVER_ERROR, '读取服务端归档配置失败') from error
        current = self._monotonic()
        token = secrets.token_urlsafe(32)
        saved = _Preview(paths, days, now, current + self.TOKEN_TTL_SECONDS,
                         self._signature(report), report)
        with self._token_lock:
            self._prune(current)
            self._tokens[token] = saved
        # Do not expose the mutable report kept with the approval token.
        public = json.loads(json.dumps(report))
        return {**public, 'token': token,
                'expiresAt': (now + timedelta(seconds=self.TOKEN_TTL_SECONDS)).isoformat(),
                'expiresInSeconds': self.TOKEN_TTL_SECONDS}

    def execute(self, body):
        if not isinstance(body, dict) or set(body) != {'previewToken', 'retentionDays', 'confirmation'}:
            raise MaintenanceError(HTTPStatus.BAD_REQUEST, '执行归档只接受预览编号、保留天数和确认；不能指定数据库路径')
        if body['confirmation'] != 'ARCHIVE_SUCCESS_EVENTS':
            raise MaintenanceError(HTTPStatus.BAD_REQUEST, '缺少成功事件归档确认')
        days = self._days(body['retentionDays'])
        token = body['previewToken']
        if not isinstance(token, str) or not token or len(token) > 128:
            raise MaintenanceError(HTTPStatus.BAD_REQUEST, '请先预览归档范围')
        if not self._execute_lock.acquire(blocking=False):
            raise MaintenanceError(HTTPStatus.CONFLICT, '已有事件归档正在执行，请等待完成')
        try:
            with self._token_lock:
                saved = self._tokens.get(token)
                if saved is None or saved.used:
                    raise MaintenanceError(HTTPStatus.CONFLICT, '归档预览不存在或已使用，请重新预览')
                if saved.expires <= self._monotonic():
                    del self._tokens[token]
                    raise MaintenanceError(HTTPStatus.GONE, '归档预览已超过 10 分钟，请重新预览')
                if saved.retention_days != days:
                    raise MaintenanceError(HTTPStatus.BAD_REQUEST, '保留天数与预览不一致，请重新预览')
                # Consume before reading/executing, including a failed attempt:
                # subsequent attempts must obtain a new, explicit scope preview.
                saved.used = True
            try:
                paths = self._paths()
                if paths != saved.paths:
                    raise MaintenanceError(HTTPStatus.CONFLICT, '任务池范围已变化，请重新预览后确认')
                current = event_archive.preview_event_archives(paths, retention_days=days, now=saved.now)
                if self._signature(current) != saved.signature:
                    raise MaintenanceError(HTTPStatus.CONFLICT, '可归档事件范围已变化，请重新预览后确认')
                if saved.expires <= self._monotonic():
                    raise MaintenanceError(HTTPStatus.GONE, '归档预览已超过 10 分钟，请重新预览')
            except MaintenanceError:
                raise
            except Exception as error:
                raise MaintenanceError(HTTPStatus.CONFLICT, '无法再次确认原归档范围，请重新预览') from error
            try:
                result = event_archive.archive_task_events(paths, retention_days=days,
                    now=saved.now, expected_preview=saved.report)
                if result.get('scopeConflict'):
                    raise MaintenanceError(HTTPStatus.CONFLICT,
                        '归档过程中事件范围发生变化，已停止相关任务池；请核对结果后重新预览', data=result)
                return result
            except MaintenanceError:
                raise
            except ValueError as error:
                raise MaintenanceError(HTTPStatus.CONFLICT, str(error)) from error
            except sqlite3.Error as error:
                raise MaintenanceError(HTTPStatus.SERVICE_UNAVAILABLE, '事件归档未完成，请重新预览后核查') from error
            except Exception as error:
                raise MaintenanceError(HTTPStatus.INTERNAL_SERVER_ERROR, '事件归档未完成，请重新预览后核查') from error
        finally:
            self._execute_lock.release()
