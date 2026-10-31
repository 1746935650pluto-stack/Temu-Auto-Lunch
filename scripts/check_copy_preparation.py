"""Read-only remote preparation check; optional reviewed evidence stays local."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from shared_automation import create_copy_payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('transfer_id')
    parser.add_argument('--fact')
    parser.add_argument('--value')
    parser.add_argument('--image-url')
    parser.add_argument('--evidence')
    args = parser.parse_args()
    service = server.shared_automation()
    original = server.BackgroundAutomationWorker._vendor
    def readonly(worker, path, payload):
        if path not in {server.HALF_SITE_DETAIL, server.HALF_CATEGORY_ATTRIBUTE_RULES, server.HALF_CATEGORIES,
                        server.HALF_FREIGHT, server.HALF_WAREHOUSE}:
            raise AssertionError('Remote writes are forbidden in preparation checks')
        return original(worker, path, payload)
    server.BackgroundAutomationWorker._vendor = readonly
    with service.db() as db:
        row = db.execute('SELECT payload FROM shared_transfers WHERE transfer_id=?', (args.transfer_id,)).fetchone()
    if not row:
        raise ValueError('Transfer not found')
    ref = json.loads(row['payload'])
    raw = service.worker(ref['sourceAccountId'], ref)._get_item(ref['detailId'], 'site')
    if args.fact:
        service.remember_source_evidence(ref['sourceAccountId'], ref['detailId'], raw,
            [{'name': args.fact, 'value': args.value, 'imageUrl': args.image_url, 'evidence': args.evidence}])
    item = create_copy_payload(raw, ref['shopIds'], ref['site'])
    service._prepare_copy_attributes(raw, item, ref)
    service._prepare_copy_logistics(item, ref)
    print(json.dumps({'sourceId': ref['detailId'], 'status': 'preparation passed',
        'attributes': [a for a in item['attributes'] if a.get('name') in {'包含的组件', '货源产地'}]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
