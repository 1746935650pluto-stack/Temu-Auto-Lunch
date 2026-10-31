"""Initial local account credential setup. Never log or return secret values."""
from __future__ import annotations

import codecs
import os
from pathlib import Path
import re
import tempfile


class CredentialConfigError(ValueError):
    """Only fixed, safe-to-display messages belong in this exception."""


def credential_values(account_id: int, app_key: object, app_secret: object) -> dict[str, str]:
    if type(account_id) is not int or not 2 <= account_id <= 20:
        raise CredentialConfigError('此入口仅用于新增采集箱 2—20 的独立 API 配置')
    values = []
    for value in (app_key, app_secret):
        # A single unquoted dotenv token: no line injection or shell expansion.
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.~+/:=-]{1,512}', value.strip()):
            raise CredentialConfigError('请填写有效的 App Key 和 App Secret，不得包含空格、换行或引号')
        values.append(value.strip())
    prefix = f'MIAOSHOU_ACCOUNT_{account_id}_'
    return dict(zip((prefix + 'APP_KEY', prefix + 'APP_SECRET'), values))


def collection_name_value(account_id: int, name: object) -> tuple[str, str]:
    """Validate an account label that is safe to persist as one dotenv line."""
    if type(account_id) is not int or not 1 <= account_id <= 20:
        raise CredentialConfigError('请选择有效的采集箱')
    if not isinstance(name, str):
        raise CredentialConfigError('采集箱名称不正确')
    value = ' '.join(name.strip().split())
    if not 1 <= len(value) <= 40 or any(ord(char) < 32 or char in '#=' for char in value):
        raise CredentialConfigError('采集箱名称限 1—40 个字符，不能包含换行、# 或 =')
    return f'MIAOSHOU_ACCOUNT_{account_id}_NAME', value


def persist_credentials(path: Path, values: dict[str, str]) -> None:
    """Atomically update only these keys; caller serializes configuration writes.

    Refuse differing nonempty credentials, including settings edited on disk
    since startup. Preserve comments, other settings, BOM and newline style.
    """
    if path.is_symlink():
        raise CredentialConfigError('配置文件不能是符号链接，请检查本机 .env')
    existed = path.exists()
    original = path.read_bytes() if existed else b''
    text = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines, seen = [], set()
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        key, separator, value = stripped.partition('=')
        key = key.strip()
        if separator and key in values:
            old = value.strip().strip('"').strip("'")
            if old and old != values[key]:
                raise CredentialConfigError('此账号已有不同 API 凭据；为保护账号数据，请在本机核对配置后处理')
            if key not in seen:
                lines.append(f'{key}={values[key]}{newline}')
                seen.add(key)
        else:
            lines.append(line)
    for key, value in values.items():
        if key not in seen:
            if lines and not lines[-1].endswith(('\r', '\n')):
                lines.append(newline)
            lines.append(f'{key}={value}{newline}')
    updated = ''.join(lines).encode('utf-8')
    if original.startswith(codecs.BOM_UTF8):
        updated = codecs.BOM_UTF8 + updated
    _replace_env(path, original, updated, existed)


def remove_credentials(path: Path, account_id: int) -> None:
    """Erase the pair and persist a nonsecret tombstone in the same replace.

    The marker disables inherited credentials after restart and reserves the
    account ID, so later accounts cannot inherit old products or pending tasks.
    """
    if type(account_id) is not int or not 2 <= account_id <= 20:
        raise CredentialConfigError('默认采集箱不能删除；请选择采集箱 2—20')
    if path.is_symlink():
        raise CredentialConfigError('配置文件不能是符号链接，请检查本机 .env')
    existed = path.exists()
    original = path.read_bytes() if existed else b''
    text = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    prefix = f'MIAOSHOU_ACCOUNT_{account_id}_'
    keys = {prefix + suffix for suffix in ('APP_KEY', 'APP_SECRET', 'REMOVED', 'NAME')}
    lines = [line for line in text.splitlines(keepends=True)
             if line.strip().partition('=')[0].strip() not in keys]
    if lines and not lines[-1].endswith(('\r', '\n')):
        lines.append(newline)
    lines.append(f'{prefix}REMOVED=1{newline}')
    updated = ''.join(lines).encode('utf-8')
    if original.startswith(codecs.BOM_UTF8):
        updated = codecs.BOM_UTF8 + updated
    _replace_env(path, original, updated, existed)


def persist_collection_name(path: Path, account_id: int, name: object) -> str:
    """Atomically replace only one local collection-account label."""
    key, value = collection_name_value(account_id, name)
    if path.is_symlink():
        raise CredentialConfigError('配置文件不能是符号链接，请检查本机 .env')
    existed = path.exists()
    original = path.read_bytes() if existed else b''
    text = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = [line for line in text.splitlines(keepends=True)
             if line.strip().partition('=')[0].strip() != key]
    if lines and not lines[-1].endswith(('\r', '\n')):
        lines.append(newline)
    lines.append(f'{key}={value}{newline}')
    updated = ''.join(lines).encode('utf-8')
    if original.startswith(codecs.BOM_UTF8):
        updated = codecs.BOM_UTF8 + updated
    _replace_env(path, original, updated, existed)
    return value


def _replace_env(path: Path, original: bytes, updated: bytes, existed: bool) -> None:
    if updated == original:
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.env-setup-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        if existed:
            os.chmod(temporary, path.stat().st_mode & 0o777)
        # Do not clobber a manual edit made while preparing the replacement.
        if path.is_symlink() or path.exists() != existed or (existed and path.read_bytes() != original):
            raise CredentialConfigError('保存期间 .env 已变化，请重新检查配置后再试')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def persist_deepseek_key(path: Path, api_key: str | None) -> None:
    if api_key is not None and (not isinstance(api_key, str) or not re.fullmatch(r'[A-Za-z0-9_.~+/:=-]{1,512}', api_key)):
        raise CredentialConfigError('请填写有效的 DeepSeek API Key，不得包含空格、换行或引号')
    if path.is_symlink():
        raise CredentialConfigError('配置文件不能是符号链接，请检查本机 .env')
    existed = path.exists()
    original = path.read_bytes() if existed else b''
    text = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = [line for line in text.splitlines(keepends=True)
             if line.strip().partition('=')[0].strip() not in {'DEEPSEEK_API_KEY', 'DEEPSEEK_DISABLED'}]
    if lines and not lines[-1].endswith(('\r', '\n')):
        lines.append(newline)
    if api_key is not None:
        lines.append(f'DEEPSEEK_API_KEY={api_key}{newline}')
    lines.append(f'DEEPSEEK_DISABLED={1 if api_key is None else 0}{newline}')
    updated = ''.join(lines).encode('utf-8')
    if original.startswith(codecs.BOM_UTF8):
        updated = codecs.BOM_UTF8 + updated
    _replace_env(path, original, updated, existed)
