"""Package the complete portable folder, with an integrity check and SHA-256."""
import hashlib
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
folder = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'dist' / 'v14-beta8' / 'ApexTracker'
assert folder.name == 'ApexTracker'
archive = root / 'dist' / 'ApexTracker-Windows.zip'
assert (folder / 'ApexTracker.exe').is_file()
assert (folder / '_internal').is_dir()
with ZipFile(archive, 'w', ZIP_DEFLATED, compresslevel=6) as zipped:
    for path in sorted(folder.rglob('*')):
        if path.is_file():
            assert path.suffix.lower() not in ('.sqlite3','.log','.png','.jpg','.jpeg'), path
            assert not any(part in ('private-diagnostics','screenshots','test-output') for part in path.parts), path
            zipped.write(path, path.relative_to(folder.parent))
with ZipFile(archive) as zipped:
    assert zipped.testzip() is None
    assert 'ApexTracker/ApexTracker.exe' in zipped.namelist()
    assert 'ApexTracker/_internal/tracker/summary_templates.json' in zipped.namelist()
digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
print(f'{archive.name}: {archive.stat().st_size} bytes, SHA-256 {digest}')
