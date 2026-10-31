"""Validate and shape half-managed collect-box items for MiaoShou's save APIs.

Only fields documented for create/save are forwarded. This avoids sending
response-only IDs and statuses back to MiaoShou while preserving all editable
fields (including attributes and variant structures) from the detail response.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import random
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from attribute_ai import attribute_for, constrained_rule

SITE_FIELDS = frozenset("""
collectBoxDetailShopList cid title multiLanguageTitleMap translateLanguages itemNum
productOriginCountry productOriginProvince productOriginCertFiles imgUrls
multiLanguageImgUrlsMap shopIdAndGoodsModelReqMap productMaterialImageUrl
mainImgVideoUrl goodsLayerDecorationReqs productGuideFileUrl productGuideFileName
outerGoodsUrl collectShowSizeTemplateIds sizeAttributeValueGroupId sizeCharts
vehicleLibraryRelationList attributes saleAttributes skuMap publishSiteList
publishEuSiteList siteAndSkuPriceMap siteShipmentLimitDayMap
materialMultiLanguageType materialMultiLanguages discreetShipping currency
shipmentLimitDay manufacturingLocationRegionShortNameList
""".split())

SHOP_FIELDS = frozenset("""
detailId cid shopId title itemNum imgUrls multiLanguageImgUrlsMap
goodsLayerDecorationReqs productOriginCountry productOriginProvince
productOriginCertFiles currency mainImgVideoUrl productGuideFileUrl
productGuideFileName site editModel brandId multiLanguageTitleMap
sizeAttributeValueGroupId sizeCharts vehicleLibraryRelationList
collectShowSizeTemplateIds
translateLanguages goodsModelReqs attributes saleAttributes skuMap
publishSiteList publishEuSiteList siteShipmentLimitDayMap
materialMultiLanguageType materialMultiLanguages isSetSitePrice isSetSiteStock
shipmentLimitDay siteFreightTemplateList siteWarehouseList
manufacturingLocationRegionShortNameList
""".split())

SHOP_ROW_FIELDS = frozenset("""
shopId publishSiteList publishEuSiteList
siteWarehouseList siteFreightTemplateList materialMultiLanguageType
materialMultiLanguages
""".split())

SKU_FIELDS = frozenset("""
itemNum length width height imgUrl price weight suggestedPrice
suggestedPriceCurrencyType numberOfPieces skuClassification pieceUnitCode
individuallyPacked netContentUnitCode netContentNumber numberOfPiecesNew
pieceNewUnitCode mixedType totalNetContentNumber totalNetContentUnitCode
siteAndWarehouseIdAndStockMap multiLanguageImgUrlMap productSkuAccessories
""".split())


def apply_food_contact_default(item: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Fill an empty food-contact material using the category's actual choice ID."""
    rule = next((r for r in rules if r.get('name') == '与食品接触部位材质'), None)
    if rule and rule.get('parentTemplatePid') not in (None, '', 0, '0'):
        selected = {str(v.get('vid')) for a in item.get('attributes') or []
                    if str(a.get('templatePid')) == str(rule['parentTemplatePid'])
                    for v in a.get('values') or []}
        choice = next((v for v in rule.get('values') or [] if v.get('name') == '不锈钢'), None)
        if selected and choice and choice.get('parentVidList') and not selected.intersection(map(str, choice['parentVidList'])):
            return False
    return _apply_select_default(item, rules, '与食品接触部位材质', '不锈钢')


def apply_food_contact_usage_default(item: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Default the empty food-contact suitability attribute to the real Yes option."""
    return _apply_select_default(item, rules, '是否可用于食品接触', '是')


def _apply_select_default(item: dict[str, Any], rules: list[dict[str, Any]],
                          attribute_name: str, choice_name: str) -> bool:
    for rule in rules:
        if not isinstance(rule, dict) or str(rule.get('name', '')).strip() != attribute_name:
            continue
        attributes = item.setdefault('attributes', [])
        attribute = next((entry for entry in attributes if isinstance(entry, dict) and (
            str(entry.get('name', '')).strip() == attribute_name or any(
                rule.get(key) is not None and entry.get(key) is not None
                and str(entry[key]) == str(rule[key]) for key in ('templatePid', 'pid')
            )
        )), None)
        if attribute and any(isinstance(value, dict) and (
            value.get('vid') not in (None, '', 0, '0') or str(value.get('name') or '').strip()
        ) for value in attribute.get('values') or []):
            return False
        choice = next((value for value in rule.get('values') or []
                       if isinstance(value, dict) and str(value.get('name', '')).strip() == choice_name
                       and value.get('vid') not in (None, '', 0, '0')), None)
        if choice is None:
            raise ValueError(f'产品属性【{attribute_name}】没有可用的“{choice_name}”选项，请手动选择')
        if attribute is None:
            attribute = {key: rule[key] for key in ('templatePid', 'pid', 'refPid', 'name') if key in rule}
            attributes.append(attribute)
        unit = rule.get('valueUnit') or ''
        attribute['values'] = [{'vid': choice['vid'], 'name': choice['name'],
                                'valueUnit': (unit[0] if unit else '') if isinstance(unit, list) else unit,
                                'numberValue': None}]
        return True
    return False


def apply_source_origin_default(item: dict[str, Any], rules: list[dict[str, Any]], *,
                                required_by_vendor: bool = False) -> bool:
    """Use the user's Guangzhou default only when an origin needs filling."""
    return apply_required_choice_default(item, rules, '货源产地', '广州产区', required_by_vendor=required_by_vendor)


def apply_required_choice_default(item: dict[str, Any], rules: list[dict[str, Any]],
                                  name: str, default: str, *, required_by_vendor: bool = False,
                                  force_choice: bool = False, random_fallback: bool = False) -> bool:
    rule = next((r for r in rules if isinstance(r, dict) and r.get('name') == name), None)
    if rule is None:
        return False
    rule = constrained_rule(item, rule)
    if rule is None:
        return False
    attributes = item.setdefault('attributes', [])
    attribute = attribute_for(item, rule)
    if not rule.get('required') and attribute is None and not required_by_vendor:
        return False
    choices = [v for v in rule.get('values') or [] if isinstance(v, dict) and v.get('vid') not in (None, '', 0, '0')]
    if rule.get('parentTemplatePid') not in (None, '', 0, '0'):
        selected = {str(v.get('vid')) for a in attributes if str(a.get('templatePid')) == str(rule['parentTemplatePid'])
                    for v in a.get('values') or []}
        if not selected:
            return False
        choices = [v for v in choices if not v.get('parentVidList') or selected.intersection(map(str, v['parentVidList']))]
        if not choices or (not random_fallback and not any(v.get('name') == default for v in choices)):
            return False
    choice = None
    for value in ([] if force_choice else (attribute or {}).get('values') or []):
        if isinstance(value, dict):
            choice = next((v for v in choices if str(v['vid']) == str(value.get('vid'))
                           or (value.get('name') and v.get('name') == value['name'])), None)
            if choice:
                break
    choice = choice or next((v for v in choices if v.get('name') == default), None)
    if choice is None and random_fallback and choices:
        choice = random.choice(choices)
    if not choice:
        raise ValueError(f'产品属性【{name}】当前类目没有“{default}”选项，请手动选择')
    unit = rule.get('valueUnit') or ''
    updated = {**(attribute or {}), **{k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule},
               'values': [{'vid': choice['vid'], 'name': choice['name'], 'numberValue': None,
                           'valueUnit': (unit[0] if unit else '') if isinstance(unit, list) else unit}]}
    if updated == attribute:
        return False
    if attribute is None:
        attributes.append(updated)
    else:
        attribute.update(updated)
    return True


def apply_blade_length_default(item: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Default a missing/invalid numeric blade length to 5 in the category's unit."""
    rule = next((entry for entry in rules if isinstance(entry, dict)
                 and str(entry.get('name', '')).strip() == '刀刃长度'), None)
    if rule is None:
        return False
    attributes = item.setdefault('attributes', [])
    attribute = next((entry for entry in attributes if isinstance(entry, dict) and (
        str(entry.get('name', '')).strip() == '刀刃长度' or any(
            rule.get(key) is not None and entry.get(key) is not None
            and str(entry[key]) == str(rule[key]) for key in ('templatePid', 'pid')
        )
    )), None)
    for value in (attribute or {}).get('values') or []:
        try:
            if isinstance(value, dict) and Decimal(str(value.get('numberValue'))).is_finite():
                return False
        except InvalidOperation:
            pass
    if attribute is None:
        attribute = {key: rule[key] for key in ('templatePid', 'pid', 'refPid', 'name') if key in rule}
        attributes.append(attribute)
    unit = rule.get('valueUnit') or ''
    # Free-input attributes still require the API's vid sentinel, as returned
    # by MiaoShou for the existing blade-length field.
    attribute['values'] = [{'vid': 0, 'name': '5', 'numberValue': '5',
                            'valueUnit': (unit[0] if unit else '') if isinstance(unit, list) else unit}]
    return True


def apply_fabric_weight_default(item: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Fill known fabric-weight fields, retaining a valid vendor-provided value."""
    changed = False
    for rule in rules:
        if isinstance(rule, dict) and str(rule.get('name') or '').strip().startswith(('平方克重', '面料克重', '里料克重')):
            changed = _apply_fabric_weight_rule(item, rule) or changed
    return changed


def _apply_fabric_weight_rule(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    name = str(rule.get('name') or '').strip()
    # Do not create a weight when its coverage/texture conditions are inactive.
    if constrained_rule(item, rule) is None:
        return False
    default_number = '10' if name.startswith('平方克重') else '5'
    attributes = item.setdefault('attributes', [])
    attribute = attribute_for(item, rule)
    number = None
    for value in (attribute or {}).get('values') or []:
        if not isinstance(value, dict):
            continue
        for candidate in (value.get('numberValue'), value.get('name')):
            try:
                parsed = Decimal(str(candidate).strip())
                if (parsed.is_finite() and parsed >= Decimal(str(rule.get('minValue') or 0))
                        and (not rule.get('maxValue') or parsed <= Decimal(str(rule['maxValue'])))):
                    number = format(parsed, 'f')
                    break
            except InvalidOperation:
                pass
        if number is not None:
            break
    number = number if number is not None else default_number
    if Decimal(number) < Decimal(str(rule.get('minValue') or 0)) or (rule.get('maxValue') and Decimal(number) > Decimal(str(rule['maxValue']))):
        raise ValueError(f'{name}默认值 {default_number} 超出当前类目范围，请手动填写')
    unit = rule.get('valueUnit') or ''
    updated = {**(attribute or {}), **{k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule},
               'values': [{'vid': 0, 'name': number, 'numberValue': number,
                           'valueUnit': (unit[0] if unit else '') if isinstance(unit, list) else unit}]}
    if updated == attribute:
        return False
    if attribute is None:
        attributes.append(updated)
    else:
        attribute.update(updated)
    return True


def fill_empty_sale_options(item: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    """Use an explicit Other option for empty specs, preserving every SKU row."""
    working = deepcopy(item)
    groups = working.setdefault('saleAttributes', [])
    original_groups = deepcopy(groups)
    changes = []
    for raw in rules:
        context = {'attributes': [*(working.get('attributes') or []), *groups]}
        rule = constrained_rule(context, raw)
        if rule is None:
            continue
        group = next((g for g in groups if g.get('name') == rule.get('name')
                      or (rule.get('templatePid') and g.get('templatePid') == rule['templatePid'])), None)
        if (group is None and not rule.get('required')) or (group and group.get('values')):
            continue
        choice = next((v for v in rule.get('values') or [] if v.get('vid') not in (None, '', 0, '0')
                       and str(v.get('name') or '').strip().casefold() in {'其他', '其它', 'other'}), None)
        if rule.get('valueType') != 'SELECT' or not choice:
            raise ValueError(f'规格【{rule.get("name")}】为空，类目没有可用的“其他”选项，请补充真实规格')
        if group is None:
            group = {}
            groups.append(group)
        group.update({k: rule[k] for k in ('name', 'pid', 'templatePid', 'refPid', 'parentSpecId') if k in rule})
        group['values'] = [{'vid': choice['vid'], 'name': choice['name'], 'skuKey': uuid.uuid4().hex[:8]}]
        changes.append(f'{rule["name"]}＝{choice["name"]}')
    if not changes:
        return []
    active_original = [g for g in original_groups if g.get('values')]
    new_map = {}
    for old_key, sku in (working.get('skuMap') or {}).items():
        if not (old_key.startswith(';') and old_key.endswith(';')):
            raise ValueError('规格补全时发现无法识别的 SKU 键，已保留原资料，请手动核查')
        tokens = old_key[1:-1].split(';')
        if not active_original and all(not token for token in tokens):
            selected = {}
        else:
            layout = original_groups if len(tokens) == len(original_groups) else active_original
            if len(tokens) != len(layout):
                raise ValueError('规格与 SKU 数量不匹配，已保留原资料，请手动核查')
            selected = {}
            for group, token in zip(layout, tokens):
                if group.get('values'):
                    if token not in {str(v.get('skuKey')) for v in group['values']}:
                        raise ValueError('SKU 键不属于原规格，已保留原资料，请手动核查')
                    selected[group['name']] = token
                elif token:
                    raise ValueError('空规格仍带有未知 SKU 键，请手动核查')
        new_key = ';' + ';'.join(selected.get(g['name'], str(g['values'][0]['skuKey']))
                                  for g in groups if g.get('values')) + ';'
        if new_key in new_map:
            raise ValueError('规格补全会合并不同 SKU，已停止并保留原资料')
        new_map[new_key] = sku
    if not new_map:
        raise ValueError('商品没有可保留的 SKU，请补充规格资料')
    item['saleAttributes'], item['skuMap'] = groups, new_map
    return changes


def remove_inactive_numeric_attributes(item: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    """Omit inactive conditional numeric fields only from the outgoing copy."""
    removed = []
    for rule in rules:
        if not isinstance(rule, dict) or not str(rule.get('name') or '').startswith(
                ('厚度', '平方克重', '面料克重', '里料克重')):
            continue
        if constrained_rule(item, rule) is None:
            attribute = attribute_for(item, rule)
            if attribute is not None:
                item['attributes'].remove(attribute)
                removed.append(rule['name'])
    return removed


def apply_thickness_default(item: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Use 3 cm for an absent thickness field when the class range permits it."""
    rule = next((r for r in rules if isinstance(r, dict)
                 and str(r.get('name') or '').strip().startswith('厚度')), None)
    if rule is None or constrained_rule(item, rule) is None:
        return False
    name = str(rule.get('name') or '').strip()
    attributes = item.setdefault('attributes', [])
    attribute = attribute_for(item, rule)
    number = None
    for value in (attribute or {}).get('values') or []:
        if not isinstance(value, dict):
            continue
        for candidate in (value.get('numberValue'), value.get('name')):
            try:
                parsed = Decimal(str(candidate).strip())
                if (parsed.is_finite() and parsed >= Decimal(str(rule.get('minValue') or 0))
                        and (not rule.get('maxValue') or parsed <= Decimal(str(rule['maxValue'])))):
                    number = format(parsed, 'f')
                    break
            except InvalidOperation:
                pass
        if number is not None:
            break
    number = number if number is not None else '3'
    if Decimal(number) < Decimal(str(rule.get('minValue') or 0)) or (rule.get('maxValue') and Decimal(number) > Decimal(str(rule['maxValue']))):
        raise ValueError(f'{name}默认值 3 超出当前类目范围，请手动填写')
    unit = rule.get('valueUnit') or ''
    updated = {**(attribute or {}), **{k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule},
               'values': [{'vid': 0, 'name': number, 'numberValue': number,
                           'valueUnit': (unit[0] if unit else '') if isinstance(unit, list) else unit}]}
    if updated == attribute:
        return False
    if attribute is None:
        attributes.append(updated)
    else:
        attribute.update(updated)
    return True


def apply_textile_fiber_composition_default(item: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    """Default an empty textile-composition table to the real Cotton 100% choice."""
    rule = next((r for r in rules if isinstance(r, dict)
                 and str(r.get('name') or '').strip() == '纺织纤维成分'
                 and r.get('valueType') == 'TABLE'), None)
    if rule is None:
        return False
    attributes = item.setdefault('attributes', [])
    attribute = next((a for a in attributes if isinstance(a, dict) and (
        str(a.get('name') or '').strip() == rule['name'] or any(
            rule.get(k) is not None and str(a.get(k)) == str(rule[k]) for k in ('templatePid', 'pid')))), None)
    if attribute and any(isinstance(value, dict) and value.get('vid') not in (None, '', 0, '0')
                         for value in attribute.get('values') or []):
        return False
    choice = next((value for value in rule.get('values') or []
                   if isinstance(value, dict) and str(value.get('name') or '').strip() == '棉Cotton'
                   and value.get('vid') not in (None, '', 0, '0')), None)
    if choice is None:
        return False
    unit = rule.get('valueUnit') or ''
    updated = {**(attribute or {}), **{k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule},
               'values': [{'vid': choice['vid'], 'name': choice['name'], 'numberValue': '100',
                           'valueUnit': (unit[0] if unit else '%') if isinstance(unit, list) else (unit or '%')}]}
    if updated == attribute:
        return False
    if attribute is None:
        attributes.append(updated)
    else:
        attribute.update(updated)
    return True


def fingerprint(item: dict[str, Any]) -> str:
    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sites(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("请至少选择一个发布站点")
    result = []
    for site in value:
        if not isinstance(site, str) or not site.isalnum() or len(site) > 20:
            raise ValueError("发布站点格式不正确")
        if site not in result:
            result.append(site)
    return result


def _image_urls(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("请至少填写一张商品图片")
    for url in value:
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise ValueError("商品图片必须是完整的 HTTP(S) 地址")
    return value


def _manufacturing_locations(value: Any, origin_country: str) -> list[str]:
    """Normalize manufacturing countries, defaulting only to the stated origin.

    MiaoShou stores this as country/region short names (for example ``CN``),
    not as provincial administrative codes.  Older collect-box records often
    omit the field even though the save endpoint now requires it.
    """
    locations = value if isinstance(value, list) else []
    result: list[str] = []
    for location in locations:
        normalized = str(location or '').strip().upper()
        if not normalized:
            continue
        if not normalized.isalnum() or not 2 <= len(normalized) <= 3:
            raise ValueError('制造地点必须使用国家或地区简称，例如 CN')
        if normalized not in result:
            result.append(normalized)
    return result or [origin_country]


def normalize_show_size_template_ids(item: dict[str, Any]) -> bool:
    """Keep only real positive integer size-template IDs for MiaoShou saves.

    The detail API occasionally returns a UUID in this field even though the
    save API accepts only numeric template IDs.  Send an empty list in that
    case so a stale, invalid template is explicitly cleared; never invent an
    ID by coercing a UUID.
    """
    if "collectShowSizeTemplateIds" not in item:
        return False
    raw = item.get("collectShowSizeTemplateIds")
    candidates = raw if isinstance(raw, list) else []
    normalized: list[int] = []
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            numeric = value
        elif isinstance(value, str) and value.strip().isdecimal():
            numeric = int(value.strip())
        else:
            continue
        if numeric > 0 and numeric not in normalized:
            normalized.append(numeric)
    if raw == normalized:
        return False
    item["collectShowSizeTemplateIds"] = normalized
    return True


def normalize_item(raw: Any, mode: str, *, detail_id: int | None = None,
                   shop_id: int | None = None) -> dict[str, Any]:
    """Return a documented request body item or raise a user-readable error."""
    if not isinstance(raw, dict) or mode not in {"site", "shop"}:
        raise ValueError("商品资料或编辑模式不正确")
    fields = SITE_FIELDS if mode == "site" else SHOP_FIELDS
    item = {key: deepcopy(value) for key, value in raw.items() if key in fields}
    normalize_show_size_template_ids(item)
    if detail_id is not None and mode == "shop":
        item["detailId"] = detail_id
    if shop_id is not None and mode == "shop":
        item["shopId"] = shop_id
        item["editModel"] = "shop"
    if not isinstance(item.get("title"), str) or not item["title"].strip():
        raise ValueError("请填写商品标题")
    if not isinstance(item.get("cid"), str) or not item["cid"].strip():
        raise ValueError("请填写妙手类目 ID")
    if not isinstance(item.get("productOriginCountry"), str) or not item["productOriginCountry"].strip():
        raise ValueError("请填写原产国编码")
    item["productOriginCountry"] = item["productOriginCountry"].strip().upper()
    if item["productOriginCountry"] == "CN" and not item.get("productOriginProvince"):
        raise ValueError("原产国为中国时，请填写原产省编码")
    item['manufacturingLocationRegionShortNameList'] = _manufacturing_locations(
        item.get('manufacturingLocationRegionShortNameList'), item['productOriginCountry']
    )
    item["imgUrls"] = _image_urls(item.get("imgUrls"))
    for key in ("attributes", "saleAttributes", "goodsLayerDecorationReqs"):
        if not isinstance(item.get(key), list):
            raise ValueError(f"{key} 必须是数组，请检查高级资料")
    sku_map = item.get("skuMap")
    if not isinstance(sku_map, dict) or not sku_map or len(sku_map) > 200:
        raise ValueError("请至少填写一个 SKU，最多 200 个")
    cleaned_skus = {}
    for sku_key, sku in sku_map.items():
        if not isinstance(sku_key, str) or not sku_key or not isinstance(sku, dict):
            raise ValueError("SKU 结构不正确")
        clean = {key: deepcopy(value) for key, value in sku.items() if key in SKU_FIELDS}
        for key in ("length", "width", "height", "price", "weight"):
            if clean.get(key) in (None, ""):
                raise ValueError(f"SKU {sku_key} 缺少 {key}")
        cleaned_skus[sku_key] = clean
    item["skuMap"] = cleaned_skus
    item["publishSiteList"] = _sites(item.get("publishSiteList"))
    if mode == "site":
        rows = item.get("collectBoxDetailShopList")
        if not isinstance(rows, list) or not rows:
            raise ValueError("请为商品选择至少一个目标店铺")
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("shopId", "")).isdigit():
                raise ValueError("店铺资料不正确")
            clean_row = {key: deepcopy(value) for key, value in row.items() if key in SHOP_ROW_FIELDS}
            clean_row["shopId"] = int(row["shopId"])
            clean_row["publishSiteList"] = _sites(row.get("publishSiteList"))
            clean_rows.append(clean_row)
        item["collectBoxDetailShopList"] = clean_rows
    elif not item.get("shopId") or not item.get("detailId"):
        raise ValueError("缺少店铺模式的商品或店铺 ID")
    return item
