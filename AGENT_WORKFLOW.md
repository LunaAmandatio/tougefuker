# 网页答题调用规则

1. `python edu.py select` 查看用户在窗口应用的勾选范围；遵守该范围。`python edu.py context` 读取题目和网页代码。仅在 agent 上下文中编写解答。
2. 不创建本地题目代码文件，不在本地执行题目代码，不复制源码作备份。
3. 启动 `python edu.py input`，把含 `task_id`、网页 `path`、完整 `code` 的 UTF-8 JSON 通过进程标准输入发送。不要创建答案临时文件，不要用 shell 命令替换拼接代码。
4. 查看 `evaluation.result`。`action=revise` 时据实际错误修改代码，再次调用输入接口。可用返回的 `sha256` 更新 `expected`。
5. `next_level` 或 `next_experiment` 时，使用新 `context` 继续。`passed_navigation_pending` 表示已通过但导航尚未确认，需处理导航错误，不能宣称全部完成。
6. 工具错误返回 `error_id`，运行 `python edu.py logs --tail 30` 查看。先处理登录或页面限制，不重复盲点按钮。
7. `action=selection_complete` 表示勾选实验全部完成，应停止。`empty_selection` 表示没有选中实验，不要自行取消范围限制或加入其他实验。
8. 内置 Codex 模式使用 `auto-start`、`auto-status --since 游标`、`auto-stop`。运行期间不要从外部重复写入和评测；其输出只在进程内存和 GUI 中显示。
9. `context.task_type=choice` 时不等待代码编辑器；向 `input` 发送 `task_id` 和 `answers:[{"question_id":"choices 中的 id","positions":[选项原始 position]}]`，每道题都须回答。题目和答案只经内存、标准输入与网页传递。若 `feedback_visible=false`，平台只确认提交、未公开判分，不能声称答案正确；按接口返回的导航继续。
