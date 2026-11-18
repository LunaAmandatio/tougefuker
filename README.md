# 头歌网页实验助手

Selenium 操作网页，Tkinter 查看状态，agent 通过 JSON 输入答案。实验代码只写入网页编辑器，运行和评测使用平台环境。本工具不添加或运行本地题目代码，不导出或备份源码。

## 启动

```powershell
cd D:\Project\something\educoder_agent
python .\edu.py gui
```

默认使用无头 Edge。“登录”显示浏览器供手动登录，随后获取题目或自动答题时切回无头并复用登录状态。已有 Selenium 和 Tkinter 可直接运行；首次可能下载驱动，必要时在 `config.json` 填写 `driver_path`。

此次更新后重新打开新版窗口一次。窗口标题和每次命令结果都会显示后台版本；以后工具文件更新时自动检测并升级旧后台，保留登录资料，无需手动执行 `stop`。升级会关闭旧浏览器，并在下次网页操作时重新打开。

## 勾选要完成的题目

窗口只保留四个操作按钮：**登录、获取题目列表、自动答题、暂停**。点击“获取题目列表”，上方表格列出各实验的标题、完成状态、进度、提交状态及是否可进入。点击左侧方框或选中一行后按空格勾选；点击表头“全选”可切换全选和清空。

勾选后直接点击“自动答题”，程序会自动保存范围。自动前进按课程列表顺序执行，只进入勾选范围。每完成一关立即更新实验进度、当前关卡及状态，整个实验完成后显示“已完成”，然后进入下个勾选实验；无需刷新整个课程网页。全部勾选实验完成后返回 `action: selection_complete`。未勾选的实验不能通过 `input` 写入或通过 `test` 评测。

点击“自动答题”后，勾选按课程保存在本地，重启后保留，只包含实验 ID。GUI 和 CLI/agent 共用此范围。“获取题目列表”可重新核对平台实际状态。

CLI 也可设置范围：先 `python edu.py list`，再 `python edu.py select --ids 4115214 4115215`。加 `--start` 立即进入。`python edu.py select` 查看范围，`select --ids` 清空，`select --unrestricted` 取消范围限制。尚未设置过范围时，CLI 保持原有按课程列表选择的行为。

## Codex 自动提交和纠错

需要本机已安装 Codex CLI，并配置好模型服务和认证（使用 OpenAI 登录时可在终端运行 `codex login`；自定义服务使用其密钥配置）。本工具沿用 CLI 已有配置，不要求把密钥填进本工具。Windows 上自动识别 npm 安装的 Codex，优先调用原生程序，其次调用 Node 入口，避免 PowerShell 的脚本执行策略阻止 `.ps1` 启动。

1. 登录头歌，点击“获取题目列表”，勾选要完成的实验。
2. 在“Codex 模型”填入你的服务实际支持的准确模型 ID，点击“自动答题”。此按钮会自动应用当前勾选。
3. 底部左、右两栏各占一半：左栏保留网页操作和评测结果，右栏显示模型输出、读取文件、纠错进度与错误提示。
4. 失败时把实际评测反馈交给 Codex，生成修改后再在网页提交；通过后进入下个关卡或勾选实验。全部完成后停止。点击“暂停”可终止模型子进程并停止新的提交，保留网页状态；再次点击“自动答题”从网页现状继续。

通过后使用网页提供的下一关地址跳转，保留当前课程和实验编号，避免评测结果面板遮挡链接。跳转失败时保留本关通过结果；重新继续时先检查网页状态，已通过的当前关卡直接前进。

同时支持普通代码关卡和单选/多选关卡，自动识别类型。选择题直接读取题干和选项，填写后点击网页评测，无需等待代码编辑器。若平台隐藏判分，只确认“已提交”，列表显示“已提交（未公开判分）”并继续；不会声称答案正确，也无法在没有失败反馈时自动纠错。

保存后直接等待评测按钮出现并启用，使用 JS 在同一次网页操作中检查并点击一次。不会为核对代码而强制刷新环境，以平台实际评测结果为准。未加载、禁用或存在多个候选按钮时继续等待，默认最多 45 秒（`evaluation_ready_timeout` 可单独配置）。点击回执不明确时停止，避免重复评测；按钮等待超时不计入提交次数。

按你的要求预填 `gpt-6-luna`。不可用时会报错停止，不会自动换模型。可以直接在窗口改成账号支持的 ID，或修改 `config.json` 的 `codex.model`。

“Codex 模型”是可编辑的下拉框，既可输入新 ID，也可选择最近使用的模型。点击“自动答题”时保存到 `.state/ui.json`，下次打开窗口自动恢复；不增加操作按钮。运行中也可修改输入框，但当前任务继续使用启动时指定的模型，新值在下次点击“自动答题”时生效。要立即切换，请先暂停再启动。空白或格式无效的模型 ID 会直接报 `invalid_model`，不会悄悄使用默认模型。

模型调用失败时，在状态栏和两侧输出中显示模型 ID、异常代码及对应原因；能识别时附带 HTTP 状态与服务错误代码，并写入报错日志。例如 `model_not_found`（模型不存在或不支持）、`model_unavailable`（模型暂无可用服务）、`model_access_denied`（无权限）、`model_auth_failed`（认证失败）、`model_quota_exceeded`（额度不足）、`model_rate_limited`（限流）、`model_connection_failed`（连接失败）及 `model_timeout`（超时）。明确的 `turn.failed` 会立即抛出对应 `ToolError` 并停止自动流程，失败调用不会继续写入或评测。原始服务输出可能含题目或凭据，不直接回显；无法识别的原因返回 `model_failed`，不会伪造具体原因。

Codex 使用 `exec --ephemeral --json`，题目、代码和结果经进程标准输入/输出传递；不创建答案文件、会话副本或模型输出文件。关闭本次调用中的本地执行、MCP、插件与 hooks，使用只读沙箱；模型只返回 JSON 修改，控制器负责调用网页接口。当前模块和配置参数已按本机 Codex CLI 0.169.0 帮助核对，旧版 CLI 可能需要更新。

需要多个文件时，模型可以请求读取网页文件；所有修改保存成功后才评测。单关默认最多 5 次提交，不自动重复相同答案；浏览器错误、代码冲突、评测超时和通过后导航未确认均停止。再次启动可读取网页现状继续，但不会自动重置平台评测尝试计数。

每次模型请求只包含当前题目、已读取的文件和最近一次实际失败反馈，不携带全部历史对话；默认 reasoning effort 为 `low`。`config.json` 中还可设置 `codex.timeout`、`max_attempts` 和 `max_tasks`。`codex.command` 默认为空数组，自动发现 CLI；必要时填写程序及参数的数组（不使用 shell 字符串）。

CLI 控制：

```powershell
python .\edu.py auto-start --model gpt-6-luna
python .\edu.py auto-status
python .\edu.py auto-status --since 事件游标
python .\edu.py auto-stop
```

`auto-status` 返回状态、累计 token 用量和有界的内存输出；用 `--since` 仅获取新事件。自动运行期间锁定普通网页操作，避免 GUI、CLI 和自动解题相互覆盖。关闭 GUI 后后台会继续运行；再次打开窗口会显示运行状态。模型事件和评测反馈仅留在内存，报错日志不写入源码或原始 CLI 输出。

## agent 输入接口

`python edu.py context` 读取当前关卡；如果当前仍在课程列表，会自动打开配置中的实验网址。从返回内容读取题目、代码、关卡 ID 和网页文件路径。也可先用 `open` 或 `next --homework-id` 选择实验。

已保存勾选范围时，从课程列表调用 `context` 会打开范围内的未完成实验。读取代码时会等待网页编辑器，必要时自动打开文件树中的文件；超时返回加载诊断，避免返回尚未加载的空代码。

运行 `python edu.py input`，通过进程标准输入发送以下 **UTF-8 JSON**，不要创建答案文件：

```json
{
  "task_id": "context 返回的关卡 ID",
  "path": "current_file.path",
  "expected": "current_file.sha256（可省略）",
  "code": "agent 编写的完整代码，换行使用 JSON 转义",
  "evaluate": true,
  "advance": true
}
```

接口检查关卡、文件和代码冲突，直接写入网页编辑器，调用网页保存，随即发起网页评测并等待结果。不会因换行或缩进格式被网页调整而进行保存后的逐字拦截。`evaluate` 和 `advance` 默认为 `true`。

若 `context.task_type` 为 `choice`，使用同一输入接口发送选择答案，不提供 `code` 或 `path`：

```json
{
  "task_id": "context 返回的关卡 ID",
  "answers": [{"question_id": "choices 中的 id", "positions": [0]}],
  "evaluate": true,
  "advance": true
}
```

每道题都要提供答案，`positions` 使用选项原始 `position`，单选选一个、多选可选多个。模型返回 `action: choose` 和 `answers`。选择题评测仅接受本次 `choose_build` 的响应，避免把旧页面状态当作新结果。隐藏判分时 `evaluation.result.feedback_visible=false`，`passed=true` 仅表示平台已接受完整提交并允许前进。

- 不通过：返回 `action: revise` 和 `evaluation.result`，agent 根据结果修改后再次调用 `input`。返回的 `sha256` 可用于下次 `expected`。不会拿相同答案自动重复评测。
- 通过：进入尚未完成的下一关并返回新上下文；实验没有下一关时查找下个未完成实验。
- 导航无法确认：返回 `passed_navigation_pending` 和原因，不能视为课程已全部完成。

设置 `evaluate: false` 可只保存；`advance: false` 可在评测后停留当前关。可由外部 agent 调用接口，也可使用上面的 Codex 自动模式完成推理和纠错。这里的自动提交指网页代码保存和评测；网页独立的作业提交按钮使用 `submit --confirm`。

## 省 token 和其他命令

已有上下文时，`context --since REVISION` 只返回变化，本地版本记录只保存字段哈希。一次 `input` 自动完成保存、等待结果和通过后的导航，不需要反复截图或读取整页。

题目和完整代码一次提取。已有 React 题目和文件列表时跳过 Markdown、文件树的重复扫描，自动模式复用刚读取的上下文。正常提取不再额外拉取网络响应，评测期间仍收集本次结果。页面就绪轮询默认 0.15 秒，可用 `ready_poll_interval` 调整。

```powershell
python .\edu.py list
python .\edu.py next --homework-id 4115214
python .\edu.py context --path "网页文件路径"
python .\edu.py test
python .\edu.py next-level
python .\edu.py logs --tail 30
python .\edu.py status
python .\edu.py pause
python .\edu.py close
python .\edu.py stop
```

`next` 按课程页面顺序查找未完成实验，可指定作业 ID。列表从实际学生卡片读取，不把左侧目录当作业。默认连续评测上限为 5 次，分析原因后用 `test --reset-attempts` 重置。

## 调试日志

操作错误同时返回窗口/CLI，并写入 `.state/errors.log`。记录时间、错误 ID、操作、错误类型、简短消息和工具调用位置；单文件约 300 KB，保留两个轮转日志。CLI 运行 `logs --tail 30` 查看。

不记录输入 JSON、完整答案、Cookie、认证头或局部变量。评测失败的具体详情返回 agent，日志仅记录失败状态。底层 WebDriver 协议日志关闭，防止认证信息落入日志。

`.state/` 还保存专用浏览器资料和进度/哈希，不生成题目源码副本。浏览器自身可能按其机制保存站点数据。关闭窗口保留服务，`stop` 关闭服务和浏览器。

工具检查使用 `python -m unittest discover -s tests -v`，网页适配模拟检查使用 `node tests/test_browser_adapter.js`；仅使用内存中的合成数据，不执行实际实验代码。支持普通代码编辑器和选择题，Jupyter、远程桌面、报告附件等需另外适配。
