# 测试维护

在项目根目录运行：

```powershell
python -m unittest discover -s tests -v
node tests/test_browser_adapter.js
```

测试直接导入 `src/educoder_agent`，不需要安装本项目。Python 需 3.11 或更新版本；GUI 测试使用 Tkinter，缺少图形环境时会跳过。JavaScript 测试只使用 Node.js 内置模块。

| 文件 | 验证范围 |
| --- | --- |
| `test_core.py`、`test_editor_ready.py` | 上下文、保存、评测、编辑器加载、关卡跳转 |
| `test_choices.py` | 选择题校验、提交结果、隐藏判分、进度去重 |
| `test_automation.py`、`test_codex_solver.py` | 模型协议、异常分类、自动纠错、取消与停止 |
| `test_selection.py`、`test_selection_gui.py` | 勾选范围、实际 Tk 控件、模型配置、实时列表 |
| `test_service_version.py`、`test_backend_integration.py` | 后台复用、真实本地 HTTP、旧后台升级、日志 |
| `test_project_layout.py` | 跨目录启动、配置与资源路径、版本检测 |
| `test_browser_adapter.js` | 模拟 React、Monaco 和选择题控件的网页适配 |

常规测试不访问头歌、不调用真实模型、不执行实验代码。后台集成测试只在本机启动临时服务；模拟模型进程仅返回合成数据。临时项目位于 `.state/tests/`，测试结束后清理，不纳入 Git。

可选的浏览器检查需已安装 Selenium 和 Edge，可能下载浏览器驱动：

```powershell
python tests/browser_smoke.py
```

该检查只打开本地合成页面，使用独立的 `.state/browser-smoke/` 浏览器资料，不读取真实实验代码。

增加功能时，将回归测试放入相应模块的测试文件。新增后台模块放在 `src/educoder_agent/`，后台版本检测和集成测试会自动包含包内的 Python 文件；新增其他资源时同步更新 `paths.py` 的 `backend_files()`。
