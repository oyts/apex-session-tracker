from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Apex 战绩记录器")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--recognize", type=Path, help="识别指定图片并输出 JSON，不打开界面")
    parser.add_argument("--output-json", type=Path, help="将识别结果写入 UTF-8 JSON 文件")
    parser.add_argument("--self-test", type=Path, help="使用 --recognize 的样例执行隔离诊断，结果写入此文件")
    parser.add_argument("--self-test-roster", type=Path, help="隐藏窗口验证名单与历史补算，不采集屏幕或操作游戏")
    parser.add_argument('--self-test-window',type=Path,help='验证本程序的非激活测试窗口捕获，不读取游戏画面')
    args = parser.parse_args()
    from tracker.windows import enable_dpi
    enable_dpi()
    if args.self_test_window:
        from tracker.diagnostics import run_window_capture_smoke
        raise SystemExit(0 if run_window_capture_smoke(args.self_test_window) else 1)
    if args.self_test_roster:
        from tracker.diagnostics import run_roster_smoke
        raise SystemExit(0 if run_roster_smoke(args.self_test_roster) else 1)
    if args.self_test:
        if not args.recognize:
            parser.error("--self-test 需要 --recognize 指定验证样例")
        from tracker.diagnostics import run_smoke
        raise SystemExit(0 if run_smoke(args.recognize, args.self_test) else 1)
    if args.recognize:
        from tracker.ocr import Recognizer
        result = json.dumps(Recognizer().recognize(args.recognize), ensure_ascii=False, indent=2)
        if args.output_json:
            args.output_json.write_text(result, encoding="utf-8")
        elif os.sys.stdout:
            print(result)
        return
    directory = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ApexSessionTracker"
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / "app.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    mutex = None
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\ApexSessionTracker_" +
                                   hashlib.sha256(str(directory.resolve()).lower().encode()).hexdigest()[:16])
        if ctypes.get_last_error() == 183:
            ctypes.windll.user32.MessageBoxW(None, "程序已经在运行，请使用已打开的窗口。", "Apex 战绩记录器", 0)
            return
    import tkinter as tk
    from tkinter import messagebox
    from tracker.app import App
    root = tk.Tk()
    def report_exception(exc, value, traceback):
        logging.error("UI error", exc_info=(exc, value, traceback))
        messagebox.showerror("程序遇到问题", f"{value}\n详细记录已保存到数据目录的 app.log。", parent=root)
    root.report_callback_exception = report_exception
    try:
        App(root, directory)
        root.mainloop()
    except Exception:
        logging.exception("Startup failed")
        raise
    finally:
        if mutex:
            kernel.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
