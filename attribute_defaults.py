"""User-approved defaults shared by copy preparation, saves and the editor.

Only category-provided options/units are used. Source facts take precedence;
this service performs no remote writes and never guesses unknown measurements.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from attribute_ai import (attribute_for, constrained_rule, hydrate_attributes_from_source,
                          needs_completion, normalize_values, ordered_rules, user_choice_values)
from item_editor import apply_required_choice_default, remove_inactive_numeric_attributes

CHOICE_DEFAULTS = {
    '适用年龄段': '6+', '木材类型': '原木', '木种': '樟子松',
    '织物材质成分': '人造纤维', '刀片材质': '不锈钢', '形状': '不规则形',
    '材质类型': '非纺织品材质', '面料纹理1': '光面', '货源产地': '广州产区',
    '与食品接触部位材质': '不锈钢', '是否可用于食品接触': '是',
}


def default_rule(item, rule, forced=False, *, source=None, rules=()):
    name = str(rule.get('name') or '').strip()
    number = ('10' if name.startswith('平方克重') else
              '5' if name.startswith(('面料克重', '里料克重')) or name == '刀刃长度' else
              '3' if name.startswith('厚度') else None)
    if number is not None and rule.get('valueType') in ('STRING', 'NUMBER', None):
        numeric_rule = {**rule, 'valueType': 'NUMBER'}
        attribute = attribute_for(item, rule)
        units = rule.get('valueUnit') or ['']
        units = units if isinstance(units, list) else [units]
        for value in (attribute or {}).get('values') or []:
            if value.get('valueUnit') and value['valueUnit'] not in units:
                raise ValueError(f'{name}源数值单位与当前类目不一致，不能直接换算')
            for candidate in (value.get('numberValue'), value.get('name')):
                try:
                    amount = Decimal(str(candidate))
                    if not amount.is_finite():
                        continue
                    normalize_values([{'vid': 0, 'name': str(candidate), 'numberValue': str(candidate)}], numeric_rule)
                    number = str(candidate)
                    break
                except (InvalidOperation, ValueError):
                    continue
        try:
            values = normalize_values([{'vid': 0, 'name': number, 'numberValue': number}], numeric_rule)
        except ValueError as error:
            raise ValueError(f'{name}默认值 {number} 超出当前类目范围或精度要求：{error}') from error
    elif not needs_completion(item, rule):
        return False
    elif name == '纺织纤维成分' and rule.get('valueType') == 'TABLE':
        values = user_choice_values(item, source or item, rule)
        if not values:
            return False
    elif name in CHOICE_DEFAULTS and rule.get('valueType') in ('SELECT', None):
        value = CHOICE_DEFAULTS[name]
        if name == '材质类型' and any(r.get('name') == '纺织纤维成分' and r.get('valueType') == 'TABLE' for r in rules):
            value = '纺织品材质'
        return apply_required_choice_default(item, [rule], name, value,
            required_by_vendor=forced or name in {'面料纹理1', '与食品接触部位材质', '是否可用于食品接触'},
            random_fallback=name not in {'面料纹理1', '货源产地', '与食品接触部位材质', '是否可用于食品接触'})
    else:
        return False
    attribute = attribute_for(item, rule)
    if attribute and attribute.get('values') == values:
        return False
    if attribute is None:
        attribute = {}
        item.setdefault('attributes', []).append(attribute)
    attribute.update({k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule})
    attribute['values'] = values
    return True


def apply_attribute_defaults(item, source, rules, *, targets=None):
    working = deepcopy(item)
    # Hydration normalizes response units. Reject incompatible numeric source
    # units before that normalization so a value cannot silently change scale.
    for raw in rules:
        name = str(raw.get('name') or '').strip()
        if raw.get('valueType') not in ('NUMBER', 'STRING', None) or not (name.startswith(('面料克重', '里料克重', '平方克重', '厚度')) or name == '刀刃长度'):
            continue
        units = raw.get('valueUnit') or ['']
        units = units if isinstance(units, list) else [units]
        for original in (item, source or {}):
            for value in (attribute_for(original, raw) or {}).get('values') or []:
                if value.get('valueUnit') and value['valueUnit'] not in units:
                    raise ValueError(f'{name}源数值单位与当前类目不一致，不能直接换算')
    changed = hydrate_attributes_from_source(working, source or item, rules)
    for raw in ordered_rules(rules):
        rule = constrained_rule(working, raw)
        if rule and default_rule(working, rule, bool(targets and rule.get('name') in targets), source=source, rules=rules):
            changed.append(rule.get('name', '属性'))
    remove_inactive_numeric_attributes(working, rules)
    item['attributes'] = working.get('attributes') or []
    return list(dict.fromkeys(changed))
