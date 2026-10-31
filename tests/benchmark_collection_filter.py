"""Compare the former full-JSON filter with SQL paging on disposable data.

Run: py -3 tests/benchmark_collection_filter.py --items 30000 --repeat 5
Never imports server; production configuration/data and external I/O are denied.
"""
from contextlib import closing
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_tests import install_io_guard

install_io_guard()
from collection_cache import CollectionReadCache


def former_filter(cache, scope, person_id='', page=1, page_size=20, detail_id='', title_keyword=''):
    """The previous implementation, retained only as a synthetic benchmark."""
    snapshot = cache.snapshot_info(scope)
    if not snapshot:
        return None
    with closing(cache._connect()) as db:
        rows = db.execute('SELECT payload FROM collection_snapshot_members '
                          'WHERE scope=? AND generation=? ORDER BY position',
                          (scope, snapshot['generation'])).fetchall()
    keyword = str(title_keyword or '').strip().casefold()
    matches = []
    for (payload,) in rows:
        item = json.loads(payload)
        if ((not person_id or str(item.get('subAppAccountId', '')).strip() == str(person_id))
                and (not detail_id or str(item.get('collectBoxDetailId')) == str(detail_id))
                and (not keyword or keyword in str(item.get('title') or '').casefold())):
            matches.append(item)
    start = max(0, (max(1, int(page)) - 1) * int(page_size))
    return {'rows': matches[start:start + int(page_size)], 'total': len(matches), 'snapshot': snapshot}


def summary(timings):
    ordered = sorted(timings)
    return {'p50_ms': round(statistics.median(timings), 2),
            'p95_ms': round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--items', type=int, default=30000)
    parser.add_argument('--payload-bytes', type=int, default=512)
    args = parser.parse_args()
    if min(args.repeat, args.items) < 1 or args.payload_bytes < 0:
        parser.error('positive item/repetition counts and nonnegative payload size required')
    with tempfile.TemporaryDirectory(prefix='temu-collection-filter-') as directory:
        cache = CollectionReadCache(Path(directory) / 'cache.sqlite3')
        rows = [{'collectBoxDetailId': str(9007199254740993 + i),
                 'subAppAccountId': str(i % 20),
                 'title': ('冬季 Straße 100%_手套 ' if i % 7 == 0 else '夏季帽子 ') + str(i),
                 'thumbnail': f'https://invalid.example/fixture/{i}.jpg',
                 'fixturePadding': 'x' * args.payload_bytes} for i in range(args.items)]
        cache.store_listing('fixture', rows)
        del rows
        cases = {
            'person_first_page': {'person_id': '11', 'page_size': 50},
            'person_page_10': {'person_id': '11', 'page': 10, 'page_size': 50},
            'unicode_literal_title': {'title_keyword': 'STRASSE 100%_', 'page_size': 50},
            'person_and_title': {'person_id': '11', 'title_keyword': '手套', 'page_size': 50},
            'exact_detail': {'detail_id': str(9007199254740993 + args.items // 2), 'page_size': 50},
        }
        report = {'fixture': {'items': args.items, 'people': 20, 'pageSize': 50,
                              'paddingBytesPerItem': args.payload_bytes, 'repetitions': args.repeat},
                  'measurements': {}}
        for name, filters in cases.items():
            before = former_filter(cache, 'fixture', **filters)
            after = cache.filtered_listing('fixture', **filters)
            if before != after:
                raise AssertionError(f'filter results differ: {name}')
            timings = {'before': [], 'after': []}
            for repetition in range(args.repeat):
                operations = [('before', former_filter), ('after', CollectionReadCache.filtered_listing)]
                if repetition % 2:
                    operations.reverse()
                for label, operation in operations:
                    start = time.perf_counter()
                    result = operation(cache, 'fixture', **filters)
                    timings[label].append((time.perf_counter() - start) * 1000)
                    if result != after:
                        raise AssertionError(f'unstable filter result: {name}')
            report['measurements'][name] = {
                'before': summary(timings['before']), 'after': summary(timings['after']),
                'p50_speedup': round(statistics.median(timings['before']) / statistics.median(timings['after']), 1),
                'matched': after['total'], 'returned': len(after['rows']),
            }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
