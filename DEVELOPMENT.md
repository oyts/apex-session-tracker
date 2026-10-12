# 开发、构建与故障排查

支持环境：Windows 10／11，Python3.11，CPU本地OCR。开发时不要使用正式数据目录测试迁移或清理。核心规则不需要启动游戏；Windows截图和Tk/DPI集成需要交互桌面。

## 安装与运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest pyinstaller
.\.venv\Scripts\python.exe main.py --data-dir .\test-output\dev-data
```

`requirements.txt`固定已验证运行时版本；测试／构建工具可通过本地安装版本记录复现。OCR模型随RapidOCR安装／打包提供，不上传图片或昵称。更换推理依赖须重新验证实际OCR；不能只测试解析JSON。

## 测试

```powershell
$env:RUN_OCR_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests -q
```

公开源码不附带用户截图 `samples/match-summary.png`；缺少时依赖该图的OCR和GUI导入测试会明确skip。纯领域、合成页面文字、恢复队列、数据迁移、名单、累计、照片清理、DPI规则等照常执行。不能把skipped写成真实OCR通过。

本地要扩展真实OCR集成，请使用你有权处理的脱敏截图，调整自己的预期数字并保存在私有诊断目录；不要以公开问题附件暴露队友昵称／完整桌面。当前集成样例预期174／430／182，不能随意换图却保留预期值。

```powershell
# 不采集屏幕的隔离名单／字体／自身窗口显示器诊断
.\.venv\Scripts\python.exe main.py --self-test-roster .\test-output\roster.json
# 真实本地OCR，输出文件不公开提交
.\.venv\Scripts\python.exe main.py --recognize .\private-diagnostics\your-summary.png --output-json .\test-output\ocr.json
```

完整 `--self-test` 需要专用本地样例，包含真实Windows截图与独立临时数据库；请在交互桌面运行，不用于无桌面的CI。诊断不会自动操作Apex。CI工作流为Windows隔离测试，不执行游戏，也不称为反作弊验证。

## 构建

```powershell
.\build.ps1
.\.venv\Scripts\python.exe tools\package_release.py
```

输出 `dist/v14/ApexTracker/ApexTracker.exe` 和 `dist/ApexTracker-Windows.zip`。README、第三方许可与依赖许可随完整文件夹交付，不能只搬exe。`tools/package_source.py` 用允许清单输出 `dist/ApexTracker-Source-v14.zip`，排除数据库、图片、日志、诊断、环境和构建产物；发布前仍需审阅文件清单及许可边界。

## 定位问题

1. 页面没被采集：看自动状态里的显示器、前台情况。窗口客户区是否完整、是否黑屏？自动跟随通过公开窗口位置确定，不轮流拍全桌面。
2. 有原图但待确认：历史“原因”区分比赛归属、名单不足和数字低置信；不要先调低数字阈值。归属不明可明确选新比赛／已有比赛。
3. 识别页是unknown：检查 `pages.classify_lines` 的位置和组合证据，只有一个职业词不能新建场。阈值／时间集中在domain，修改需有正负例。
4. OCR失败：原图和spool保留；原图路径是否可用、模型依赖是否一致？重启可以恢复尚未提交的PNG＋JSON，已提交的精确像素不再累计。
5. SQLite迁移：正式库先backup；隔离副本上做quick_check、foreign_key_check，比较原stats及手工数据。不在运行中替换库，不让旧版本混写v14库。
6. 磁盘积压：恢复队列保护尚未提交的数据，故障项本次运行停止无限重试。处理失败时先检查日志并修复原因，再重启。默认照片清理不删迁移备份或未提交spool；手动备份后再处理故障材料。

日志和数据库包含私有信息。提交bug时先描述版本、系统缩放、模式、语言、出现的状态及期望场数；具体原图需脱敏与确认授权。不要直接上传整个数据目录。

## Git 同步

本项目拥有独立Git仓库，远端为 `git@github.com:oyts/apex-session-tracker.git`。每次完成更新后，检查变更和私有资料排除项，运行适当验证，提交并推送；确认本地与远端HEAD一致并检查GitHub Actions。维护规则也保存于AGENTS.md。不要使用父目录仓库提交本项目，不要强制推送。

`requirements-lock.txt`记录已验证开发环境的完整版本，可用于重现构建；`requirements.txt`为运行时直接依赖。

系统屏幕截图测试需要交互桌面和屏幕捕获权限，默认跳过。确认当前桌面适合测试时设置 `$env:APEX_RUN_DESKTOP_TESTS='1'` 再运行测试；CI不把规则回归冒充真实屏幕捕获。快捷键注册与消息循环测试仍单独执行。
