"""Copy dependency notices into the portable distribution."""
from importlib import metadata
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
destination = (Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'dist' / 'v14-beta3' / 'ApexTracker') / 'licenses'
destination.mkdir(parents=True, exist_ok=True)
for distribution in metadata.distributions():
    name = distribution.metadata.get("Name", "unknown")
    if name.lower() in {"pip", "pytest", "pyinstaller", "setuptools", "pyinstaller-hooks-contrib"}:
        continue
    for item in distribution.files or []:
        if any(token in item.name.lower() for token in ("license", "licence", "copying", "notice")):
            source = Path(distribution.locate_file(item))
            if source.is_file():
                output = destination / name / str(item)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output)
for name, source in (
    ("Python-LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt"),
    ("Tk-license.terms", Path(sys.base_prefix) / "tcl" / "tk8.6" / "license.terms"),
):
    if source.exists():
        shutil.copy2(source, destination / name)
print("Notices collected:", len(list(destination.rglob("*"))), "entries")
