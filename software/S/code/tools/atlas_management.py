"""Folder/day classification and exact, scoped style relationships.

Organization is virtual; ResourceStore identities and existing marks stay authoritative.
"""
import collections
import datetime
import hashlib
import os
import uuid

from atlas_resources import Conflict


RULE = 1


def produced_at(record):
    for obj in (record, record.get('generation') or {}, record.get('metadata') or {}):
        for key in ('produced_at', 'generated_at', 'completed_at'):
            value = obj.get(key)
            if not isinstance(value, str):
                continue
            try:
                date = datetime.datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
                return date.isoformat()
            except ValueError:
                pass
    return ''


def fields(record, styles=()):
    raw = record.get('style_fields') or (record.get('generation') or {}).get('style_fields') or []
    if isinstance(raw, dict):
        raw = [{'key': k, 'value': v} for k, v in raw.items()]
    result = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key, value = item.get('key'), item.get('value')
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            result.append({'key': key.strip(), 'value': value, 'norm': value.strip(),
                           'label': str(item.get('label') or key).strip()})
    if not result:
        generation = record.get('generation') or {}
        structured = generation.get('styles') or record.get('styles') or []
        if not structured and generation.get('style_name'):
            structured = [{'catalog': generation.get('style_library'), 'name': generation['style_name']}]
        for item in structured if isinstance(structured, list) else []:
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            catalog = item.get('catalog') or item.get('library') or ''
            if isinstance(name, str) and name.strip() and isinstance(catalog, str):
                result.append({'key': 'catalog:' + catalog.strip() if catalog.strip() else 'style',
                               'value': name, 'norm': name.strip(), 'label': catalog.strip() or '风格词条'})
    if not result:
        for name in styles:
            if isinstance(name, str) and name.strip():
                result.append({'key': 'style', 'value': name, 'norm': name.strip(), 'label': '风格词条'})
    return result


def source_folder(path):
    parent = os.path.dirname(path.replace('\\', '/')).replace('\\', '/')
    key = os.path.normpath(parent).replace('\\', '/').casefold()
    return 'disk:' + hashlib.sha256(key.encode('utf8')).hexdigest()[:24]


def decorate(data, store):
    document = store.load('_organization.json', {'folders': [], 'assignments': {}, 'metadata': {}})
    known = {f['id']: dict(f, count=0) for f in document['folders']}
    metadata = dict(document.get('metadata') or {})
    observed = {}
    for im in data.get('images') or []:
        im.setdefault('uid', im.get('path', '') + '#' + str(im.get('run', '')) + '#' + str(im.get('index', '')))
        rid = im.get('resource_id') or im['uid']
        old = metadata.get(rid, {})
        stamp, style = produced_at(im), fields(im, im.get('styles') or [])
        observed[rid] = {**({'produced_at': stamp} if stamp else {}),
                         **({'style_fields': style} if style else {})}
        im['produced_at'] = stamp or old.get('produced_at', '')
        im['day'] = im['produced_at'][:10]
        im['style_fields'] = style or old.get('style_fields', [])
        metadata[rid] = {k: im[k] for k in ('produced_at', 'style_fields')}
        fid = source_folder(im['path'])
        known.setdefault(fid, {'id': fid, 'name': im.get('folder') or os.path.dirname(im['path']),
                               'kind': 'disk', 'count': 0})
        assigned = document['assignments'].get(rid)
        if assigned in known:
            fid = assigned
        im['folder_id'] = fid
        im['image_uid'] = rid
        known[fid]['count'] += 1
    folders = [{k: v for k, v in f.items() if k != 'count'} for f in known.values()]
    if metadata != document.get('metadata') or folders != document['folders']:
        # Index reconstruction may overlap organization edits: merge against latest revision.
        for _ in range(8):
            current = store.load('_organization.json')
            current.setdefault('metadata', {})
            for rid, values in observed.items():
                current['metadata'][rid] = {**current['metadata'].get(rid, {}), **values}
            current_folders = {f['id']: f for f in current['folders']}
            for f in folders:
                current_folders.setdefault(f['id'], f)
            current['folders'] = list(current_folders.values())
            try:
                store.save('_organization.json', current, expected=current['revision'])
                break
            except Conflict:
                continue
        else:
            raise Conflict('图片组织更新繁忙，请重试')
    data['management_rule'] = RULE
    data['management_revision'] = store.load('_organization.json')['revision']
    data['management_base_id'] = data.get('management_base_id') or data.get('build_id', '')
    data['build_id'] = data['management_base_id'] + '.m' + str(data['management_revision'])
    data['management_folders'] = sorted(known.values(), key=lambda f: (f['kind'] != 'virtual', f['name']))
    data.setdefault('facets', {})['day'] = sorted(collections.Counter(im['day'] for im in data['images']).items(), reverse=True)
    return data


def organize(data, store, body):
    action = body.get('action')
    if action == 'create':
        name = body.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError('文件夹名称需为1到100个字符')
        folder = {'id': 'virtual:' + uuid.uuid4().hex, 'name': name.strip(), 'kind': 'virtual'}
    elif action == 'assign':
        ids = body.get('image_uids')
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
            raise ValueError('请先选定图片')
        valid = {im.get('resource_id') or im['uid'] for im in data['images']}
        if not set(ids) <= valid:
            raise ValueError('部分图片已不在索引中，请刷新后重试')
        if body.get('folder_id') not in {f['id'] for f in data['management_folders']}:
            raise ValueError('文件夹不存在')
    else:
        raise ValueError('未知文件夹操作')
    for _ in range(8):
        current = store.load('_organization.json', {'folders': [], 'assignments': {}, 'metadata': {}})
        if action == 'create':
            current['folders'].append(folder)
        else:
            current['assignments'].update({uid: body['folder_id'] for uid in ids})
        try:
            store.save('_organization.json', current, expected=current['revision'])
            return folder['id'] if action == 'create' else body['folder_id']
        except Conflict:
            continue
    raise Conflict('文件夹更新繁忙，请重试')


def related(data, folder_id, key, value, day=None, page=1, page_size=48):
    if not folder_id or not key or not isinstance(value, str) or not value.strip():
        raise ValueError('同类查询需要文件夹、字段和值')
    if folder_id not in {f['id'] for f in data['management_folders']}:
        raise ValueError('文件夹不存在')
    if day not in (None, ''):
        datetime.date.fromisoformat(day)
    hits, seen = [], set()
    for im in data['images']:
        if im['folder_id'] != folder_id or (day is not None and im['day'] != day):
            continue
        if not any(f['key'] == key and f['norm'] == value.strip() for f in im['style_fields']):
            continue
        content = im.get('content_hash') or im['image_uid']
        if content not in seen:
            seen.add(content)
            hits.append(im)
    page_size = min(100, max(1, int(page_size)))
    page = min(max(1, int(page)), max(1, (len(hits) + page_size - 1) // page_size))
    return {'total': len(hits), 'page': page, 'page_size': page_size,
            'matched_ids': [im['image_uid'] for im in hits],
            'images': hits[(page-1)*page_size:page*page_size]}
