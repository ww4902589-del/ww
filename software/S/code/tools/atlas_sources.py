"""Persistent artwork roots and direct enumeration independent of reviews."""
import json
import os
import tempfile
import threading

_lock = threading.Lock()

def defaults():
    desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
    return [os.path.join(desktop, name).replace('\\', '/') for name in
            ('c', 'D', '认可', '风格LoRA_其他生成候选', '风格LoRA_认可样图与本组20张')] + ['D:/liu/Images']

def normalize(roots, *, require_exists=False):
    if not isinstance(roots, list) or not 1 <= len(roots) <= 32:
        raise ValueError('请填写1到32个作品目录，每行一个')
    result = []
    for value in roots:
        if not isinstance(value, str) or not os.path.isabs(value.strip()):
            raise ValueError('请使用完整的目录路径')
        path = os.path.normpath(value.strip()).replace('\\', '/')
        if require_exists and not os.path.isdir(path):
            raise ValueError('目录不存在或无法访问：' + path)
        if path.lower() not in {p.lower() for p in result}:
            result.append(path)
    return result

def load(data_dir):
    path = os.path.join(data_dir, '_image_sources.json')
    if not os.path.isfile(path):
        return {'roots': normalize(defaults())}
    with open(path, encoding='utf8') as f:
        return {'roots': normalize(json.load(f)['roots'])}

def save(data_dir, roots):
    value = {'roots': normalize(roots, require_exists=True)}
    os.makedirs(data_dir, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(prefix='sources-', suffix='.tmp', dir=data_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(data_dir, '_image_sources.json'))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return value

def scan(roots, seen_paths, skip_dir, extensions, valid_image):
    seen = {os.path.normcase(os.path.abspath(p)) for p in seen_paths}
    found = []
    reports = []
    for directory in normalize(roots):
        report = {'path': directory, 'exists': os.path.isdir(directory),
                  'images': 0, 'added': 0, 'already_indexed': 0, 'invalid': 0,
                  'excluded_directories': 0, 'unsupported': 0, 'errors': []}
        reports.append(report)
        if not report['exists']:
            continue
        for root, dirs, files in os.walk(directory, onerror=lambda e: report['errors'].append(str(e))):
            kept = [d for d in dirs if not skip_dir(d) and d.lower() != '_style_atlas_thumbs'
                    and not os.path.isjunction(os.path.join(root, d))]
            report['excluded_directories'] += len(dirs)-len(kept)
            dirs[:] = kept
            for name in files:
                suffix = os.path.splitext(name)[1].lower()
                if suffix not in extensions:
                    if suffix in ('.tif', '.tiff', '.avif', '.heic', '.svg'):
                        report['unsupported'] += 1
                    continue
                path = os.path.join(root, name).replace('\\', '/')
                report['images'] += 1
                key = os.path.normcase(os.path.abspath(path))
                if not valid_image(path):
                    report['invalid'] += 1
                    continue
                if key in seen:
                    report['already_indexed'] += 1
                    continue
                seen.add(key)
                found.append(path)
                report['added'] += 1
    return found, reports
