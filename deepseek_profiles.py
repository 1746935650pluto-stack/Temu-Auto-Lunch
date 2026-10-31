"""Local, secret-safe DeepSeek API profile and account assignment storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock


class DeepSeekProfileError(ValueError):
    pass


class DeepSeekProfileStore:
    """Stores named keys locally; list operations never expose a key."""

    _KEY = re.compile(r'[A-Za-z0-9_.~+/:=-]{1,512}')
    _NAME = re.compile(r'[^\r\n]{1,40}')
    _ID = re.compile(r'[a-z][a-z0-9_-]{0,31}')

    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def _read(self):
        if not self.path.exists():
            return {'profiles': [], 'assignments': {}, 'revision': 0}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise DeepSeekProfileError('本机 DeepSeek API 配置无法读取，请检查配置文件') from error
        if not isinstance(data, dict) or not isinstance(data.get('profiles'), list) or not isinstance(data.get('assignments'), dict):
            raise DeepSeekProfileError('本机 DeepSeek API 配置格式无效')
        if type(data.get('revision', 0)) is not int or data.get('revision', 0) < 0:
            raise DeepSeekProfileError('本机 DeepSeek API 配置版本无效')
        if any(not isinstance(row, dict) or not all(isinstance(row.get(key), str)
               for key in ('id', 'name', 'apiKey')) for row in data['profiles']):
            raise DeepSeekProfileError('本机 DeepSeek API 配置格式无效')
        return data

    @classmethod
    def _profile(cls, profile_id, name, api_key):
        if not isinstance(profile_id, str) or not cls._ID.fullmatch(profile_id):
            raise DeepSeekProfileError('API 标识仅可使用小写字母、数字、连字符或下划线')
        if not isinstance(name, str) or not cls._NAME.fullmatch(name.strip()):
            raise DeepSeekProfileError('API 名称限 1—40 个字符，不能包含换行')
        if not isinstance(api_key, str) or not cls._KEY.fullmatch(api_key.strip()):
            raise DeepSeekProfileError('请填写有效的 DeepSeek API Key，不得包含空格、换行或引号')
        return {'id': profile_id, 'name': name.strip(), 'apiKey': api_key.strip()}

    def _write(self, data):
        data['revision'] = data.get('revision', 0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='deepseek-profiles-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
                json.dump(data, output, ensure_ascii=False, separators=(',', ':'))
                output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _public(data):
        profiles = []
        for row in data['profiles']:
            if isinstance(row, dict) and isinstance(row.get('id'), str) and isinstance(row.get('name'), str) and isinstance(row.get('apiKey'), str):
                profiles.append({'id': row['id'], 'name': row['name'], 'configured': bool(row['apiKey'])})
        assignments = {str(account): profile for account, profile in data['assignments'].items()
                       if str(account).isdigit() and isinstance(profile, str)}
        return {'profiles': profiles, 'assignments': assignments, 'revision': data.get('revision', 0)}

    def public(self):
        with self._lock:
            return self._public(self._read())

    def resolve(self, account_id: int | None):
        with self._lock:
            data = self._read()
            profile_id = data['assignments'].get(str(account_id)) if account_id else None
            if not profile_id:
                return None
            profile = next((row for row in data['profiles'] if row.get('id') == profile_id), None)
            if not profile or not isinstance(profile.get('apiKey'), str):
                return None
            return dict(profile)

    def save(self, profile_id, name, api_key):
        """Legacy create-only alias. Updating a key requires explicit replace."""
        return self.create(profile_id, name, api_key)

    def create(self, profile_id, name, api_key):
        profile = self._profile(profile_id, name, api_key)
        with self._lock:
            data = self._read()
            if any(row['id'] == profile['id'] for row in data['profiles']):
                raise DeepSeekProfileError('该 API 标识已存在；请使用替换操作更新已有配置')
            data['profiles'].append(profile)
            self._write(data)
            return self._public(data)

    def replace(self, profile_id, name, api_key):
        profile = self._profile(profile_id, name, api_key)
        with self._lock:
            data = self._read()
            if not any(row['id'] == profile['id'] for row in data['profiles']):
                raise DeepSeekProfileError('指定的 API 配置不存在，请刷新后重试')
            data['profiles'] = [profile if row['id'] == profile['id'] else row for row in data['profiles']]
            self._write(data)
            return self._public(data)

    def delete(self, profile_id):
        if not isinstance(profile_id, str) or not self._ID.fullmatch(profile_id):
            raise DeepSeekProfileError('请选择有效的 API 配置')
        with self._lock:
            data = self._read()
            if not any(row.get('id') == profile_id for row in data['profiles']):
                raise DeepSeekProfileError('指定的 API 配置不存在')
            data['profiles'] = [row for row in data['profiles'] if row.get('id') != profile_id]
            data['assignments'] = {account: value for account, value in data['assignments'].items() if value != profile_id}
            self._write(data)
            return self._public(data)

    def assign(self, account_id, profile_id):
        if type(account_id) is not int or not 1 <= account_id <= 20:
            raise DeepSeekProfileError('请选择有效的妙手采集箱')
        if profile_id is not None and (not isinstance(profile_id, str) or not self._ID.fullmatch(profile_id)):
            raise DeepSeekProfileError('请选择有效的 API 配置')
        with self._lock:
            data = self._read()
            if profile_id and not any(row.get('id') == profile_id for row in data['profiles']):
                raise DeepSeekProfileError('指定的 API 配置不存在')
            if profile_id: data['assignments'][str(account_id)] = profile_id
            else: data['assignments'].pop(str(account_id), None)
            self._write(data)
            return self._public(data)
