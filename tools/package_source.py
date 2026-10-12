"""Build an allowlisted source archive; never traverse private evidence."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
root=Path(__file__).resolve().parents[1]
files=[root/name for name in ('main.py','AGENTS.md','启动战绩记录器.cmd','requirements.txt','build.ps1','ApexTracker.spec','.gitignore','.gitattributes','LICENSE',
       'README.md','VALIDATION.md','ARCHITECTURE.md','RESEARCH.md','DEVELOPMENT.md','CONTRIBUTING.md','CHANGELOG.md','THIRD_PARTY_NOTICES.md')]
files += list((root/'tracker').glob('*.py'))+[root/'tracker'/'summary_templates.json',root/'tracker'/'windows.manifest']
files += list((root/'tests').glob('*.py'))
files += [p for p in (root/'.github').rglob('*') if p.is_file()]
files += [root/'tools'/n for n in ('package_release.py','package_source.py','collect_licenses.py')]
archive=root/'dist'/'ApexTracker-Source-v14.zip'
with ZipFile(archive,'w',ZIP_DEFLATED) as z:
    for p in sorted(files):
        assert p.is_file(),p
        relative=p.relative_to(root)
        assert not any(part in ('samples','private-diagnostics','test-output','.venv','screenshots') for part in relative.parts)
        assert p.suffix not in ('.png','.sqlite3','.log','.jpg')
        z.write(p,Path('apex-session-tracker')/relative)
    z.writestr('apex-session-tracker/samples/README.md','Private screenshots are not redistributed. See DEVELOPMENT.md for fixture requirements.\n')
with ZipFile(archive) as z:
    assert z.testzip() is None
    names=z.namelist()
report=dict(ok=True,files=len(names),bytes=archive.stat().st_size,private_screenshots_included=False,remote_published=False)
archive.with_suffix('.zip.sha256').write_text(hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()+'  '+archive.name+'\n','ascii')
(root/'test-output'/'source-package-v14.json').write_text(json.dumps(report,indent=2),'utf-8')
print(json.dumps(report))
