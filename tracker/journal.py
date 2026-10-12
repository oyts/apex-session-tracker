"""Durable bounded-dispatch spool. Files survive before the UI/database commit."""
import json
import os
import uuid
from pathlib import Path
from .dedup import image_digest


class EvidenceSpool:
    def __init__(self,directory):
        self.directory=Path(directory)/'capture-spool'
        self.directory.mkdir(parents=True,exist_ok=True)

    def preserve(self,image,session_id,context):
        key=uuid.uuid4().hex
        path=self.directory/(key+'.png')
        temporary=path.with_suffix('.tmp')
        meta=dict(session_id=session_id,hash=image_digest(image),**context)
        sidecar=path.with_suffix('.json')
        tmp=sidecar.with_suffix('.tmp')
        with tmp.open('w',encoding='utf-8') as stream:
            json.dump(meta,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        tmp.replace(sidecar)
        # Write intent first: a committed PNG always has its recovery context.
        with temporary.open('wb') as stream:
            image.save(stream,format='PNG');stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
        return dict(path=str(path),**meta)

    def pending(self):
        for sidecar in sorted(self.directory.glob('*.json'),key=lambda p:p.stat().st_mtime):
            try:
                path=sidecar.with_suffix('.png')
                if path.is_file() and not path.is_symlink():
                    yield dict(path=str(path),**json.loads(sidecar.read_text('utf-8')))
            except (OSError,ValueError):
                continue

    def acknowledge(self,path):
        path=Path(path)
        if path.parent.resolve()!=self.directory.resolve() or path.is_symlink():
            raise ValueError('不是本程序的待处理截图')
        path.unlink(missing_ok=True)
        path.with_suffix('.json').unlink(missing_ok=True)
