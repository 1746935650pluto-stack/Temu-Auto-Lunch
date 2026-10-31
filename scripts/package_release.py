"""Preview or create a release from an explicit source-only allowlist.

Default is a read-only preview. Pass --output dist/workbench.zip to create a
new archive; existing archives are never overwritten. Local data and credentials
cannot enter a release merely because they exist beside the source files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = (
    'server.py', 'launcher_support.py', 'activity_stream.py', 'account_credentials.py', 'account_history.py', 'account_runtime.py',
    'account_slots.py', 'attribute_ai.py', 'attribute_defaults.py', 'automatic_tasks.py', 'automation_pool.py',
    'automation_read_cache.py', 'automation_watchdog.py', 'collection_cache.py',
    'collection_people.py', 'deepseek_profiles.py', 'deepseek_efficiency.py', 'edit_scheduler.py', 'hourly_sync.py', 'item_editor.py',
    'event_archive.py', 'maintenance_service.py', 'performance_metrics.py',
    'zhihou.py', 'zhihou.js', 'order_sync.py', 'partitioned_pool.py', 'personnel_routing.py', 'shared_automation.py', 'task_queries.py',
    'index.html', 'styles.css', 'app.js', 'account-context.js', 'virtual-list.js', 'diagnostics-panel.js', 'shared-automation.js',
    'local-view-cache.js', 'live-status.js', 'desktop-ui.js', 'pricing-workbench.js',
    'start-workbench.cmd', 'start-client.cmd', 'start-web.cmd', 'desktop/launcher.py',
    'desktop/TemuWorkbench.cs', 'desktop/build-client.ps1', 'desktop/README.md', 'desktop/app.manifest', 'desktop/app.config',
    'desktop/bin/TemuWorkbench.exe.config', 'desktop/bin/TemuWorkbench.ico', 'desktop/app.ico', 'desktop/make-icon.py',
    'desktop/bin/TemuWorkbench.exe', 'desktop/bin/Microsoft.Web.WebView2.Core.dll',
    'desktop/bin/Microsoft.Web.WebView2.WinForms.dll', 'desktop/bin/WebView2Loader.dll',
    'desktop/bin/WebView2-LICENSE.txt', 'desktop/bin/client-manifest.json',
    'collection-sync.js', 'automatic-tasks.js', 'live-editor.js',
    'restart-backend.cmd', 'restart-backend.ps1', 'VERSIONS.md', 'INTEGRATION.md',
    'COMFYUI_QWEN_IMAGE_2_1.md', 'comfyui-qwen-image-2.1-edit-api.json',
    'comfyui-qwen-image-2.1-product-edit-api.json',
    'scripts/backup_local_databases.py', 'scripts/audit_legacy_backups.py', 'scripts/package_release.py', 'scripts/archive_task_events.py',
)


def release_manifest(root=ROOT):
    root = Path(root).resolve()
    files = []
    for name in RELEASE_FILES:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f'Release source is missing or unsafe: {name}')
        content = path.read_bytes()
        files.append({'path': name, 'sizeBytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    return {'formatVersion': 1, 'containsLocalData': False, 'files': files}


def create_release(output, root=ROOT):
    root = Path(root).resolve()
    output = Path(output).resolve()
    if output.suffix.lower() != '.zip':
        raise ValueError('Release output must be a new .zip file')
    manifest = release_manifest(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for row in manifest['files']:
            content = (root / row['path']).read_bytes()
            if hashlib.sha256(content).hexdigest() != row['sha256']:
                raise ValueError(f'Release source changed while packaging: {row["path"]}')
            archive.writestr(row['path'], content)
        archive.writestr('release-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Release archive verification failed')
    return {'path': str(output), 'fileCount': len(manifest['files']), 'verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = create_release(args.output) if args.output else {'previewOnly': True, **release_manifest()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

