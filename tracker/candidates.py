"""Bounded, app-owned local evidence for rejected OCR candidates."""
import json
import re
import shutil
from pathlib import Path


def retain_rejected(directory, image, result, session_id, epoch, limit=8):
    directory=Path(directory).resolve()/'rejected-candidates'
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise OSError('候选目录不能是链接')
    image=Path(image)
    if not re.fullmatch(r'[0-9a-f]{32}\.png',image.name):
        raise ValueError('候选文件名不属于程序')
    target=directory/image.name
    # A user-selected screenshot folder may be on another Windows volume.
    # Preserve the source until both the copied image and OCR evidence exist.
    shutil.copy2(image,target)
    target.with_suffix('.json').write_text(json.dumps(dict(session_id=session_id,epoch=epoch,
        reason='summary page validation failed',ocr=result),ensure_ascii=False,indent=2),encoding='utf-8')
    image.unlink()
    owned=[p for p in directory.glob('*.png') if re.fullmatch(r'[0-9a-f]{32}\.png',p.name)
           and not p.is_symlink() and p.resolve().parent==directory]
    for old in sorted(owned,key=lambda p:p.stat().st_mtime,reverse=True)[limit:]:
        old.unlink()
        sidecar=old.with_suffix('.json')
        if sidecar.is_file() and not sidecar.is_symlink():sidecar.unlink()
    return target
