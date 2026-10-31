"""Preview old successful task events; --execute explicitly enables archival.

Example: py -3 scripts/archive_task_events.py --data-root C:\\path\\to\\data
Only named physical automation pool files in that directory are considered.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_archive import archive_task_events, preview_event_archives


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not args.data_root.is_dir():
        parser.error('--data-root must name an existing directory')
    paths = sorted(path for path in args.data_root.glob('automation_pool*.sqlite3')
                   if not path.name.endswith('.archive.sqlite3'))
    operation = archive_task_events if args.execute else preview_event_archives
    result = operation(paths, retention_days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get('errors') else 0


if __name__ == '__main__':
    raise SystemExit(main())
