"""Consistent SQLite snapshots, verified manifests, and read-only retention preview.

Run after workers drain for an application-wide restore point. Online snapshots
are individually consistent but separate databases may reflect different instants.
Credentials are never included. No command deletes existing backups.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

SOURCE_ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def read_only(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)


def database_check(path):
    with closing(read_only(path)) as connection:
        result = [row[0] for row in connection.execute('PRAGMA integrity_check')]
    if result != ['ok']:
        raise ValueError(f'Backup integrity check failed: {path.name}')


def source_version():
    match = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", (SOURCE_ROOT / 'server.py').read_text(encoding='utf-8'), re.M)
    return match.group(1) if match else 'unknown'


def verify_backup(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('formatVersion') != 1 or manifest.get('status') != 'complete' or not isinstance(manifest.get('databases'), list):
        raise ValueError('Backup manifest is incomplete or unsupported')
    seen = set()
    for entry in manifest['databases']:
        if not isinstance(entry, dict):
            raise ValueError('Backup manifest contains an invalid database record')
        name = entry.get('name', '')
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.sqlite3') or name in seen:
            raise ValueError('Backup manifest contains an unsafe or duplicate filename')
        seen.add(name)
        path = directory / name
        if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
            raise ValueError(f'Backup database is missing or unsafe: {name}')
        if path.stat().st_size != entry.get('sizeBytes') or digest(path) != entry.get('sha256'):
            raise ValueError(f'Backup checksum does not match: {name}')
        database_check(path)
    if set(path.name for path in directory.glob('*.sqlite3')) != seen:
        raise ValueError('Backup contains databases absent from its manifest')
    return {'verified': True, 'databaseCount': len(seen), 'version': manifest.get('applicationVersion')}


def create_backup(data_root, *, label='manual', version=None, pinned=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', label):
        raise ValueError('Backup label must use letters, digits, periods, underscores or hyphens')
    data_root = Path(data_root).resolve()
    sources = sorted(path for path in data_root.glob('*.sqlite3') if not path.name.endswith('.archive.sqlite3'))
    if any(path.is_symlink() or not path.is_file() for path in sources):
        raise ValueError('Backup sources must be regular local SQLite files')
    backup_root = data_root / 'backups'
    backup_root.mkdir(parents=True, exist_ok=True)
    if backup_root.is_symlink() or backup_root.resolve().parent != data_root:
        raise ValueError('Backup directory must stay inside the data directory')
    now = datetime.now(BEIJING)
    destination = Path(tempfile.mkdtemp(prefix=f'before-{label}-{now:%Y%m%d-%H%M%S}-', dir=backup_root))
    manifest = {'formatVersion': 1, 'status': 'complete', 'createdAt': now.isoformat(),
                'applicationVersion': version or source_version(), 'label': label, 'pinned': bool(pinned),
                'consistency': 'sqlite-backup-per-database', 'containsCredentials': False, 'databases': []}
    # Failed copies have no complete manifest and cannot pass verification.
    def copy_database(source):
        if source.is_symlink() or not source.is_file():
            raise ValueError('Backup sources must be regular local SQLite files')
        target = destination / source.name
        with closing(read_only(source)) as connection, closing(sqlite3.connect(target)) as output:
            connection.backup(output)
        database_check(target)
        manifest['databases'].append({'name': source.name, 'sizeBytes': target.stat().st_size,
                                      'sha256': digest(target), 'integrity': 'ok'})
    for source in sources:
        copy_database(source)
    # Archival commits cold rows before deleting hot rows. Back up hot first,
    # then discover/backup cold files (including newly created archives), so
    # even an online archive operation cannot create a gap in the backup union.
    for source in sorted(data_root.glob('*.archive.sqlite3')):
        copy_database(source)
    temporary = destination / 'manifest.json.tmp'
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, destination / 'manifest.json')
    return {'path': str(destination), **verify_backup(destination)}


def retention_preview(data_root, *, keep=10):
    if type(keep) is not int or keep < 1:
        raise ValueError('At least one backup must be retained')
    backup_root = Path(data_root).resolve() / 'backups'
    records = []
    for directory in sorted(backup_root.iterdir() if backup_root.exists() else [], key=lambda path: path.name, reverse=True):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}
        complete = manifest.get('formatVersion') == 1 and manifest.get('status') == 'complete'
        records.append({'name': directory.name, 'createdAt': manifest.get('createdAt', ''),
                        'pinned': (directory / 'PINNED').exists() or manifest.get('pinned') is True,
                        'complete': complete})
    recent = sorted((row for row in records if row['complete'] and not row['pinned']),
                    key=lambda row: row['createdAt'], reverse=True)
    retained = {row['name'] for row in recent[:keep]}
    for row in records:
        row['action'] = ('keep-pinned' if row['pinned'] else 'review-legacy-or-incomplete' if not row['complete']
                         else 'keep-recent' if row['name'] in retained else 'candidate-after-verification')
    return {'previewOnly': True, 'keepRecent': keep, 'deleted': 0, 'backups': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=Path(os.getenv('TEMU_DATA_ROOT', SOURCE_ROOT)))
    parser.add_argument('--label', default='manual')
    parser.add_argument('--version')
    parser.add_argument('--pin', action='store_true')
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--retention-preview', action='store_true')
    parser.add_argument('--keep', type=int, default=10)
    args = parser.parse_args()
    if args.verify:
        result = verify_backup(args.verify)
    elif args.retention_preview:
        result = retention_preview(args.data_root, keep=args.keep)
    else:
        result = create_backup(args.data_root, label=args.label, version=args.version, pinned=args.pin)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
