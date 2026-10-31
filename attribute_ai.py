"""Constrained AI completion of missing category attributes."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import re
import random

from account_runtime import has_unknown_write_outcome


class AttributeInferenceError(ValueError):
    """Stop without blindly repeating a save when evidence is insufficient."""


def category_path(tree, cid):
    for node in (tree or {}).values():
        if not isinstance(node, dict):
            continue
        name = str(node.get('nameChinese') or node.get('name') or node.get('cid') or '')
        if str(node.get('cid')) == str(cid):
            return [name]
        path = category_path(node.get('children') or {}, cid)
        if path:
            return [name, *path]
    return []


def attribute_for(item, rule):
    attributes = [a for a in item.get('attributes') or [] if isinstance(a, dict)]
    template_pid = rule.get('templatePid')
    if template_pid not in (None, '', 0, '0'):
        exact = next((a for a in attributes if str(a.get('templatePid')) == str(template_pid)), None)
        if exact is not None:
            return exact
    # Older detail payloads can carry a stale template ID.  A matching name is
    # a safe fallback, while matching only pid is not: categories may expose
    # optional sibling fields with the same pid (for example, 面料纹理1/2).
    name = rule.get('name')
    if name not in (None, ''):
        named = next((a for a in attributes if a.get('name') == name), None)
        if named is not None:
            return named
    pid = rule.get('pid')
    if pid not in (None, '', 0, '0'):
        return next((a for a in attributes if str(a.get('pid')) == str(pid)
                     and a.get('name') in (None, '', name)), None)
    return None


def constrained_rule(item, rule):
    result = deepcopy(rule)
    for condition in rule.get('showCondition') or []:
        selected = {str(v.get('vid')) for a in item.get('attributes') or []
                    if str(a.get('refPid')) == str(condition.get('parentRefPid'))
                    for v in a.get('values') or []}
        if not selected.intersection(map(str, condition.get('parentVids') or [])):
            return None
    parent = rule.get('parentTemplatePid')
    if parent not in (None, 0, '0', ''):
        selected = {str(v.get('vid')) for a in item.get('attributes') or []
                    if str(a.get('templatePid')) == str(parent) for v in a.get('values') or []}
        if not selected:
            return None
        result['values'] = [v for v in rule.get('values') or []
                            if not v.get('parentVidList') or selected.intersection(map(str, v['parentVidList']))]
        if rule.get('valueType') in ('SELECT', 'TABLE') and not result['values']:
            return None
    return result


def normalize_values(values, rule):
    if not isinstance(values, list) or not values or len(values) > int(rule.get('chooseMaxNum') or 1):
        raise ValueError('选项数量不符合类目规则')
    choices = {str(v['vid']): v for v in rule.get('values') or [] if v.get('vid') not in (None, '', 0, '0')}
    result, seen, total = [], set(), Decimal(0)
    units = rule.get('valueUnit') or ['']
    unit = units[0] if isinstance(units, list) else units
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('属性值结构不正确')
        vid = str(value.get('vid'))
        free_input = rule.get('valueType') in ('STRING', 'NUMBER')
        # Attribute choice IDs belong to an account's category configuration.
        # When a product is copied to another account, a source ID can be
        # different even though its returned choice name is the same.  Prefer
        # that exact, human-readable option name and rebind it to the target
        # ID.  A name that conflicts with an otherwise matching ID is rejected
        # rather than silently changing the source product's meaning.
        source_name = _normalized_choice_name(value.get('name'))
        choice = choices.get(vid)
        named_choice = next((candidate for candidate in choices.values()
                             if source_name and _normalized_choice_name(candidate.get('name')) == source_name), None)
        if named_choice is not None:
            choice = named_choice
        elif choice is not None and source_name and _normalized_choice_name(choice.get('name')) != source_name:
            raise ValueError('源属性选项与目标类目选项不一致')
        if not free_input and (choice is None or str(choice.get('vid')) in seen):
            raise ValueError('AI 选项编号不在当前类目及父属性允许范围内')
        seen.add(str(choice.get('vid')) if choice is not None else vid)
        choice = {'vid': 0, 'name': str(value.get('name') or '').strip()} if free_input else choice
        if free_input and (not choice['name'] or len(choice['name']) > 500):
            raise ValueError('自由填写内容为空或过长')
        number = None
        # A fixed SELECT may expose propertyValueType=1 (for example, a
        # "140-160g" fabric-weight band).  Its numeric information belongs to
        # the selected option itself, so it must not be treated as a free
        # composition percentage.  Only free numeric inputs and TABLE rows
        # require a numberValue.
        if (rule.get('valueType') in ('TABLE', 'NUMBER')
                or (rule.get('valueType') == 'STRING' and rule.get('propertyValueType') == 1)):
            try:
                amount = Decimal(str(value.get('numberValue')))
                if not amount.is_finite():
                    raise ValueError('属性数值必须是有限数字')
                if rule.get('minValue') not in (None, '') and amount < Decimal(str(rule['minValue'])):
                    raise ValueError('属性数值低于类目下限')
                if rule.get('maxValue') not in (None, '') and amount > Decimal(str(rule['maxValue'])):
                    raise ValueError('属性数值超过类目上限')
                precision = min(int(rule.get('valuePrecision') or 0), 12)
                if amount != amount.quantize(Decimal(1).scaleb(-precision)):
                    raise ValueError('成分比例小数位数超出限制')
                number = format(amount, 'f')
                total += amount
            except InvalidOperation as error:
                raise ValueError('成分比例不是有效数字') from error
        result.append({'vid': choice['vid'], 'name': choice['name'], 'numberValue': number, 'valueUnit': unit})
    if rule.get('valueType') == 'TABLE' and rule.get('valueInputSum') is not None and total != Decimal(str(rule['valueInputSum'])):
        raise ValueError('成分比例合计不符合类目规则')
    return result


def needs_completion(item, rule):
    current = attribute_for(item, rule)
    if not current or str(current.get('templatePid')) != str(rule.get('templatePid')):
        return True
    if not rule.get('valueType') and any(v.get('vid') not in (None, '', 0, '0')
                                        or str(v.get('name') or '').strip() for v in current.get('values') or []):
        return False
    try:
        normalize_values(current.get('values'), rule)
        return False
    except (ValueError, TypeError):
        return True


def ordered_rules(rules):
    pending, ordered = list(rules), []
    while pending:
        ids = {str(r.get('templatePid')) for r in pending}
        ready = [r for r in pending if r.get('parentTemplatePid') in (None, '', 0, '0')
                 or str(r['parentTemplatePid']) not in ids]
        if not ready:
            raise AttributeInferenceError('类目属性父子关系存在循环，不能自动补全')
        ordered.extend(ready)
        pending = [r for r in pending if r not in ready]
    return ordered


def missing_attributes(item, rules):
    return [r for r in rules if (r.get('required') or attribute_for(item, r))
            and (active := constrained_rule(item, r)) is not None and needs_completion(item, active)]


def _normalized_choice_name(value):
    return str(value or '').strip().rstrip('，,;；').casefold()


def _choice_named(rule, name):
    normalized = _normalized_choice_name(name)
    return next((value for value in rule.get('values') or []
                 if _normalized_choice_name(value.get('name')) == normalized), None)


def _source_values(source, names):
    values = []
    for attribute in source.get('attributes') or []:
        if not isinstance(attribute, dict) or str(attribute.get('name') or '').strip() not in names:
            continue
        values.extend(str(value.get('name') or '').strip() for value in attribute.get('values') or []
                      if isinstance(value, dict) and str(value.get('name') or '').strip())
    return values


def _semantic_source_values(source, rule):
    """Return an exactly supported option when another MiaoShou field proves it."""
    name = str(rule.get('name') or '').strip()
    if name == '材质类型':
        material = ' '.join(_source_values(source, {'面料材质', '主体材质', '材质'})).casefold()
        non_textile = ('pu', 'pvc', '皮革', '塑料', '树脂', '硅胶', '金属', '合金', '橡胶', '乳胶', '玻璃')
        textile = ('涤纶', '聚酯', '棉', '麻', '毛', '丝', '氨纶', '尼龙', '腈纶', '粘胶', '纤维', '无纺布', '蕾丝')
        choice = (_choice_named(rule, '非纺织品材质') if any(token in material for token in non_textile)
                  else _choice_named(rule, '纺织品材质') if any(token in material for token in textile) else None)
        if choice:
            return [choice]
    if name == '无线属性':
        power = ' '.join(_source_values(source, {'电源方式', '电池属性'})).casefold()
        no_power = any(token in power for token in ('不带电', '不带电池', '无电池', '无需电池'))
        wireless = ('蓝牙', 'wifi', 'wi-fi', '无线充', '2.4g', '3g', '4g', '5g', 'rfid', 'nfc', 'app', '红外')
        if no_power and not any(token in power for token in wireless):
            choice = _choice_named(rule, '无')
            if choice:
                return [choice]
    return None


def hydrate_attributes_from_source(item, source, rules):
    """Copy valid facts returned by MiaoShou into the outgoing item.

    The site and shop detail endpoints can expose different subsets of the
    same product's attributes. These are first-party facts, so a value that
    still validates against the current category rule is safer than asking AI
    to infer it from a text representation of that same value. Invalid or
    stale detail values are deliberately ignored and remain eligible for the
    normal AI/manual completion path.
    """
    if not isinstance(item, dict) or not isinstance(source, dict):
        return []
    working = deepcopy(item)
    copied = []
    for raw in ordered_rules(rules):
        rule = constrained_rule(working, raw)
        if not rule:
            continue
        source_attribute = attribute_for(source, rule)
        if not needs_completion(working, rule):
            continue
        try:
            values = (normalize_values(source_attribute.get('values'), rule) if source_attribute
                      else normalize_values(_semantic_source_values(source, rule), rule))
        except (ValueError, TypeError):
            continue
        target = attribute_for(working, rule)
        if target is None:
            target = {}
            working.setdefault('attributes', []).append(target)
        target.update({key: rule[key] for key in ('templatePid', 'pid', 'refPid', 'name') if key in rule})
        target['values'] = values
        copied.append(str(rule.get('name') or rule.get('templatePid') or rule.get('pid') or '属性'))
    item['attributes'] = working.get('attributes') or []
    return copied


def missing_from_error(message, rules, item=None):
    # A transport/unknown-outcome marker wins even when an error also quotes
    # a missing attribute. Such a save may already have reached the vendor.
    if has_unknown_write_outcome(message):
        return []
    message = str(message or '')
    explicit_choice = re.fullmatch(r'\s*(.+?)[，,]\s*必选\s*\d+\s*个[！!。]?\s*', message)
    if explicit_choice and any(r.get('name') == explicit_choice.group(1).strip() for r in rules):
        return [explicit_choice.group(1).strip()]
    if not any(word in message for word in ('必填', '必须设置', '必须输入', '不能为空')):
        return []
    names = re.findall(r'【([^】]+)】', message)
    indexed = re.search(r'attributes\[(\d+)\]', message)
    if not names and indexed and item:
        attributes = item.get('attributes') or []
        index = int(indexed.group(1))
        if index < len(attributes):
            rule = next((r for r in rules if attribute_for({'attributes': [attributes[index]]}, r)), None)
            if rule:
                names = [rule['name']]
    # In parent/child errors it is the last (child) attribute that is missing.
    return [names[-1]] if names and any(r.get('name') == names[-1] for r in rules) else []


def user_choice_values(item, source, rule):
    """Apply the user's option/100-percent defaults within the active category rule."""
    choices = [v for v in rule.get('values') or []
               if isinstance(v, dict) and v.get('vid') not in (None, '', 0, '0') and v.get('name')]
    if not choices:
        return None
    if rule.get('valueType') == 'SELECT':
        if int(rule.get('chooseMinNum') or 1) > 1:
            return None
        return normalize_values([random.choice(choices)], rule)
    units = rule.get('valueUnit') or []
    if isinstance(units, str):
        units = [units]
    if (rule.get('valueType') != 'TABLE' or '成分' not in str(rule.get('name') or '')
            or not (rule.get('valueInputSum') in (100, '100') or '%' in units or '％' in units)):
        return None
    if rule.get('valueInputSum') not in (None, '', 100, '100'):
        return None
    current = attribute_for(item, rule)
    if not current or not current.get('values'):
        current = next((attribute for attribute in source.get('attributes') or []
                        if isinstance(attribute, dict) and attribute.get('values')
                        and attribute_for({'attributes': [attribute]}, rule)), None)
    existing = [v for v in (current or {}).get('values') or []
                if not (isinstance(v, dict) and v.get('vid') in (None, '', 0, '0')
                        and not v.get('name') and v.get('numberValue') in (None, ''))]
    if not existing:
        if int(rule.get('chooseMinNum') or 1) > 1:
            return None
        candidates = []
        for choice in choices:
            try:
                candidates.append(normalize_values([{**choice, 'numberValue': '100'}], rule))
            except (ValueError, TypeError):
                continue
        return random.choice(candidates) if candidates else None
    values = []
    total = Decimal(0)
    for value in existing:
        if not isinstance(value, dict):
            return None
        choice = next((v for v in choices if str(v['vid']) == str(value.get('vid'))
                       or v['name'] == value.get('name')), None)
        if choice is None:
            return None
        try:
            raw_number = value.get('numberValue')
            amount = Decimal(str(raw_number if raw_number not in (None, '') else 0))
            if not amount.is_finite() or amount < 0:
                return None
        except InvalidOperation:
            return None
        values.append({**choice, 'numberValue': amount})
        total += amount
    if total > 100:
        return None
    remainder = Decimal(100) - total
    try:
        maximum = Decimal(str(rule.get('maxValue') if rule.get('maxValue') not in (None, '') else 100))
        if not maximum.is_finite() or maximum < 0 or len(values) < int(rule.get('chooseMinNum') or 1):
            return None
    except InvalidOperation:
        return None
    for value in values:
        extra = min(remainder, max(Decimal(0), maximum - value['numberValue']))
        value['numberValue'] += extra
        remainder -= extra
        value['numberValue'] = format(value['numberValue'], 'f')
    if remainder:
        return None
    try:
        return normalize_values(values, rule)
    except (ValueError, TypeError):
        return None


def _set_completed_values(item, rule, values):
    current = attribute_for(item, rule)
    if current is None:
        current = {}
        item.setdefault('attributes', []).append(current)
    current.update({k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule})
    current['values'] = values


def complete_attributes(item, source, rules, category, infer, log, targets=None, attempted=None, default_resolver=None,
                        allow_user_choice_defaults=False):
    working = deepcopy(item)
    rules = ordered_rules(rules)
    attempted = attempted if attempted is not None else set()
    evidence = str(source.get('title') or '') + '\n' + json.dumps(source.get('attributes') or [], ensure_ascii=False)
    failures = []
    for raw in rules:
        rule = constrained_rule(working, raw)
        if not rule and targets is not None and raw.get('name') in targets:
            failures.append(f'【{raw["name"]}】父属性未填写或不满足类目启用条件')
        if not rule or (targets is not None and rule.get('name') not in targets):
            continue
        if default_resolver and default_resolver(working, rule, targets is not None):
            actual = attribute_for(working, rule)['values']
            log(f'【{rule["name"]}】已按用户默认设置填写：' + '、'.join(v['name'] for v in actual) + '；尚未保存到妙手')
            continue
        if targets is None and (not (rule.get('required') or attribute_for(working, rule)) or not needs_completion(working, rule)):
            continue
        name = rule['name']
        can_fallback = False
        try:
            key = str(rule.get('templatePid') or rule.get('pid') or name)
            if key in attempted:
                raise ValueError('本次任务已识别过该字段，接口仍拒绝，停止重复重试')
            attempted.add(key)
            can_fallback = allow_user_choice_defaults
            if rule.get('valueType') not in ('SELECT', 'TABLE', 'STRING', 'NUMBER'):
                raise ValueError('暂不支持该类目属性输入类型')
            if rule.get('valueType') in ('SELECT', 'TABLE') and not rule.get('values'):
                raise ValueError('类目未提供可用选项')
            if allow_user_choice_defaults and rule.get('valueType') == 'TABLE':
                defaults = user_choice_values(working, source, rule)
                if defaults:
                    _set_completed_values(working, rule, defaults)
                    log(f'【{name}】按用户设置补足至100%：' + '、'.join(
                        f"{v['name']} {v['numberValue']}%" for v in defaults) + '；尚未保存到妙手')
                    continue
            if allow_user_choice_defaults and rule.get('valueType') == 'SELECT' and len(rule.get('values') or []) == 1:
                defaults = user_choice_values(working, source, rule)
                if defaults:
                    _set_completed_values(working, rule, defaults)
                    log(f'【{name}】按已有选项兜底规则采用当前父属性允许的唯一选项；未调用AI，尚未保存到妙手')
                    continue
            log(f'AI 属性识别：正在根据原标题、类目和现有属性补全【{name}】')
            context = {'originalTitle': source.get('title'), 'category': category,
                       'cid': item.get('cid'), 'evidenceText': evidence,
                       'existingAttributes': working.get('attributes') or [],
                       'rule': {k: rule.get(k) for k in ('name', 'valueType', 'propertyValueType', 'chooseMaxNum', 'minValue', 'maxValue', 'valuePrecision', 'valueUnit', 'valueInputSum')},
                       'options': [{'vid': v['vid'], 'name': v.get('name')} for v in rule.get('values') or []]}
            answer = infer(context)
            if not isinstance(answer, dict) or answer.get('uncertain') is not False:
                raise ValueError('资料不足，无法可靠判断；请补充商品资料')
            values = normalize_values(answer.get('values'), rule)
            proof = answer.get('evidence')
            if not isinstance(proof, str) or not proof.strip() or proof not in evidence:
                raise ValueError('AI 未提供原标题或现有属性中的有效依据')
            if rule.get('valueType') == 'TABLE':
                # The model may translate material names, but cannot invent percentages.
                numbers = {Decimal(a or b) for a, b in re.findall(
                    r'(\d+(?:\.\d+)?)\s*%|"numberValue"\s*:\s*"?(\d+(?:\.\d+)?)', proof)}
                if any(Decimal(v['numberValue']) not in numbers for v in values):
                    raise ValueError('缺少明确的成分比例依据，不能自动猜测百分比')
            elif any(v['numberValue'] is not None for v in values):
                numbers = {Decimal(n) for n in re.findall(r'-?\d+(?:\.\d+)?', proof)}
                if any(Decimal(v['numberValue']) not in numbers for v in values if v['numberValue'] is not None):
                    raise ValueError('缺少明确数值依据，不能自动猜测规格')
            current = attribute_for(working, rule)
            if current is None:
                current = {}
                working.setdefault('attributes', []).append(current)
            current.update({k: rule[k] for k in ('templatePid', 'pid', 'refPid', 'name') if k in rule})
            current['values'] = values
            description = '、'.join(v['name'] + (f" {v['numberValue']}{v['valueUnit']}" if v['numberValue'] is not None else '') for v in values)
            log(f'AI 属性识别完成：【{name}】{description}；依据：{proof[:250]}；尚未保存到妙手')
        except (ValueError, TypeError, KeyError, RuntimeError) as error:
            if can_fallback and rule.get('valueType') == 'SELECT':
                try:
                    defaults = user_choice_values(working, source, rule)
                except (ValueError, TypeError, KeyError):
                    defaults = None
                if defaults:
                    _set_completed_values(working, rule, defaults)
                    log(f'【{name}】无法识别，已按用户设置从有效选项随机选择：'
                        + '、'.join(v['name'] for v in defaults) + '；尚未保存到妙手')
                    continue
            failures.append(f'【{name}】{error}')
            log(f'AI 属性识别待确认：【{name}】{error}')
    if failures:
        raise AttributeInferenceError('产品属性自动识别未完成：' + '；'.join(failures))
    item['attributes'] = working.get('attributes') or []
