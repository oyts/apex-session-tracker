"""Explicit, non-recursive cleanup of app-owned media; never delete a chosen folder."""
import re
import tempfile
from pathlib import Path

MANAGED_NAME='ApexTrackerScreenshots'


def prepare_directory(path):
    path=Path(path).expanduser().absolute()
    path.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as probe:
        probe.write(b'write check')
    return path


def owned_photo(path,roots):
    path=Path(path)
    return bool(path.is_absolute() and re.fullmatch(r'[0-9a-f]{32}\.png',path.name) and
                not path.is_symlink() and any(path.parent.resolve()==Path(root).resolve() for root in roots)
                and path.resolve().parent==path.parent.resolve())


def cleanup_plan(directory,roots,captures,kind):
    if kind not in ('duplicates','photos','history'):
        raise ValueError('请选择清理范围。')
    photos={}; skipped=0
    referenced={c['image'] for c in captures if c['image']}
    for capture in captures:
        path=Path(capture['image'])
        if (kind=='duplicates' and not capture['merged_into']) or not path.is_file():
            continue
        if not owned_photo(path,roots):
            skipped+=1; continue
        photos[str(path)]=path.stat().st_size
    # Interrupted captures can leave orphan UUID PNGs in dedicated image folders.
    for root in roots:
        root=Path(root)
        if root.name not in ('screenshots',MANAGED_NAME) or not root.is_dir():
            continue
        for path in root.glob('*.png'):
            if str(path) not in referenced and owned_photo(path,roots) and path.is_file():
                photos[str(path)]=path.stat().st_size
    log=Path(directory)/'app.log'
    cache={}
    candidate_dir=Path(directory).resolve()/'rejected-candidates'
    if candidate_dir.is_dir() and not candidate_dir.is_symlink():
        for path in candidate_dir.iterdir():
            if (re.fullmatch(r'[0-9a-f]{32}\.(png|json)',path.name) and path.is_file()
                    and not path.is_symlink() and path.resolve().parent==candidate_dir):
                cache[str(path)]=path.stat().st_size
    cache_bytes=(log.stat().st_size if log.is_file() and not log.is_symlink() else 0)+sum(cache.values())
    return {'kind':kind,'photos':photos,'roots':list(map(str,roots)),'log':str(log),
            'cache':cache,'cache_directory':str(candidate_dir),
            'cache_bytes':cache_bytes,'bytes':sum(photos.values())+cache_bytes,'skipped':skipped,
            'pending':sum(c['status']=='pending' and not c['merged_into'] and c['image'] in photos for c in captures),
            'records':sum(not c['merged_into'] for c in captures)}


def execute_cleanup(plan):
    removed=[]; failures=[]; freed=0
    for text,size in plan['photos'].items():
        path=Path(text)
        try:
            if not owned_photo(path,plan['roots']):
                raise ValueError('文件不在程序管理的路径内')
            if path.is_file():
                size=path.stat().st_size
                path.unlink()
                freed+=size
            removed.append(text)
        except (OSError,ValueError) as error:
            failures.append((text,str(error)))
    log=Path(plan['log'])
    for text in plan.get('cache',{}):
        path=Path(text)
        try:
            if (Path(plan['cache_directory']).is_symlink() or
                    path.parent.resolve()!=Path(plan['cache_directory']).resolve() or path.is_symlink()
                    or not re.fullmatch(r'[0-9a-f]{32}\.(png|json)',path.name)):
                raise ValueError('候选缓存路径不属于程序')
            if path.is_file():
                size=path.stat().st_size;path.unlink();freed+=size
        except (OSError,ValueError) as error:failures.append((text,str(error)))
    try:
        if log.is_file() and not log.is_symlink():
            size=log.stat().st_size
            with log.open('w',encoding='utf-8'): pass
            freed+=size
    except OSError as error:
        failures.append((str(log),str(error)))
    return {'removed':removed,'failures':failures,'freed':freed,'kind':plan['kind']}
