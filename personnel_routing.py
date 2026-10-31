"""Resolve personnel-bound targets without trusting browser ownership or names."""
from collection_people import collector_id


def personnel_targets(detail_ids, owners, bindings, valid_shops):
    expected, targets, skipped = [], {}, []
    for detail_id in detail_ids:
        person_id = collector_id(owners.get(str(detail_id)))
        shops = sorted(set(bindings.get(person_id, []))) if person_id else []
        if any(shop not in valid_shops for shop in shops):
            raise ValueError(f'人员账号 ID {person_id} 的绑定包含不可用店铺，请重新设置绑定')
        expected.append({'detailId': detail_id, 'collectorId': person_id, 'shopIds': shops})
        if shops:
            targets[detail_id] = shops
        else:
            skipped.append({'detailId': detail_id, 'collectorId': person_id,
                            'reason': '所属人员未绑定店铺' if person_id else '当前采集箱未返回该商品的所属人员'})
    return expected, targets, skipped
