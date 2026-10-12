"""Keep one Tk interpreter, like production, while testing independent windows."""
import tkinter as tk
import pytest


@pytest.fixture(scope='session')
def tk_host():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def pytest_collection_modifyitems(items):
    from pathlib import Path
    if (Path(__file__).resolve().parents[1]/'samples'/'match-summary.png').exists():
        return
    for item in items:
        if item.path.name in ('test_ocr_integration.py','test_app.py') or item.name=='test_detector_summary_sizes_partial_and_non_summary':
            item.add_marker(pytest.mark.skip(reason='Private local screenshot fixture is not redistributed; see DEVELOPMENT.md'))
