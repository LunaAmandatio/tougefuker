"""Tkinter stays on the main thread; Selenium lives in the local service."""
import json
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from core import BASE, DEFAULT_COURSE, DEFAULT_TASK, ToolError, load_json, write_json
from service import ensure_server, error_result, rpc
from service import build_id
from codex_solver import DEFAULT_MODEL, normalize_model

MODEL_SETTINGS = BASE / '.state' / 'ui.json'


def model_preferences():
    try:
        value = load_json(MODEL_SETTINGS, {})
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def experiment_state(item):
    if item.get('complete') is True:
        return '已完成'
    if item.get('complete') is False:
        return '未完成'
    return '状态未知'


def experiment_progress(item):
    finished, total = item.get('finished'), item.get('total')
    return f'{finished}/{total}' if finished is not None and total is not None else '未知'


class App:
    def __init__(self, root):
        self.root = root
        root.title('头歌实验助手 · Selenium · ' + build_id())
        root.geometry('1120x900')
        root.minsize(880, 760)
        config = load_json(BASE / 'config.json', {})
        preferences = model_preferences()
        self.course = tk.StringVar(value=config.get('course_url', DEFAULT_COURSE))
        self.task = tk.StringVar(value=config.get('task_url', DEFAULT_TASK))
        self.path = tk.StringVar()
        initial_model = preferences.get('model') or config.get('codex', {}).get('model', DEFAULT_MODEL)
        self.model = tk.StringVar(value=initial_model)
        self.status = tk.StringVar(value='先登录、获取题目列表，再勾选并自动答题。')
        self.results = queue.Queue()
        self.busy = False
        self.auto_running = False
        self.auto_poll_pending = False
        self.auto_cursor = 0
        self.auto_discover = True
        self.buttons = []
        self.experiments = []
        self.live_progress = {}
        self.automation = {}
        self.checked = set()
        self.selection_dirty = False
        self.selection_status = tk.StringVar(value='勾选后直接点击“自动答题”，自动应用范围。')
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate((('课程网址', self.course),)):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=4)
            ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=4)
        ttk.Label(frame, text='Codex 模型').grid(row=1, column=0, sticky='w', padx=(0, 8), pady=4)
        recent_models = preferences.get('models', [])
        if not isinstance(recent_models, list):
            recent_models = []
        self.model_entry = ttk.Combobox(frame, textvariable=self.model, state='normal',
                                       values=list(dict.fromkeys([initial_model, DEFAULT_MODEL] +
                                           [m for m in recent_models if isinstance(m, str) and len(m) <= 120]))[:8])
        self.model_entry.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(frame, text='模型 ID 可直接输入；点击“自动答题”生效。运行中修改用于下次启动，模型调用失败会显示异常并停止。').grid(
            row=2, column=0, columnspan=3, sticky='w', pady=5)
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=3, sticky='ew', pady=8)
        specs = [
            ('登录', lambda: self.call('login', {'url': self.course.get()})),
            ('获取题目列表', lambda: self.call('list')),
            ('自动答题', self.start_auto),
        ]
        for i, (label, callback) in enumerate(specs):
            button = ttk.Button(actions, text=label, command=callback)
            button.grid(row=0, column=i, sticky='ew', padx=2, pady=3)
            actions.columnconfigure(i, weight=1)
            self.buttons.append(button)
        self.pause_button = ttk.Button(actions, text='暂停', command=self.pause)
        self.pause_button.grid(row=0, column=3, sticky='ew', padx=2, pady=3)
        actions.columnconfigure(3, weight=1)
        ttk.Label(frame, textvariable=self.status, wraplength=830).grid(row=4, column=0, columnspan=3, sticky='w', pady=5)
        selection_frame = ttk.LabelFrame(frame, text='选择要完成的实验（点击方框或按空格勾选）', padding=6)
        selection_frame.grid(row=5, column=0, columnspan=3, sticky='nsew', pady=(0, 8))
        selection_frame.columnconfigure(0, weight=1)
        table = ttk.Frame(selection_frame)
        table.grid(row=0, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table, columns=('title', 'state', 'progress', 'status', 'entry', 'level'),
                                  show='tree headings', height=9, selectmode='browse')
        for key, label, width in (('#0', '勾选', 48), ('title', '题目标题', 420), ('state', '完成状态', 90),
                                   ('progress', '进度', 65), ('status', '提交状态', 90), ('entry', '可进入', 65),
                                   ('level', '当前关卡 / 状态', 240)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=width if key != 'title' else 220,
                              stretch=key == 'title', anchor='w' if key == 'title' else 'center')
        self.table.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.table.bind('<Button-1>', self.toggle_click)
        self.table.bind('<space>', self.toggle_space)
        self.table.heading('#0', text='全选', command=self.toggle_all)
        ttk.Label(selection_frame, textvariable=self.selection_status).grid(row=1, column=0, sticky='w', pady=(5, 0))
        self.output_panes = ttk.Panedwindow(frame, orient='horizontal')
        self.output_panes.grid(row=6, column=0, columnspan=3, sticky='nsew')
        operation_pane = ttk.LabelFrame(self.output_panes, text='网页操作 / 评测结果', padding=4)
        model_pane = ttk.LabelFrame(self.output_panes, text='Codex 模型输出（仅内存）', padding=4)
        self.output_panes.add(operation_pane, weight=1)
        self.output_panes.add(model_pane, weight=1)
        self.output = scrolledtext.ScrolledText(operation_pane, wrap='word', font=('Consolas', 10), width=1)
        self.output.pack(fill='both', expand=True)
        self.model_output = scrolledtext.ScrolledText(model_pane, wrap='word', font=('Consolas', 10), width=1)
        self.model_output.pack(fill='both', expand=True)
        self.output_panes.bind('<Configure>', self.split_output)
        frame.rowconfigure(6, weight=1)
        self.write('实验代码只在网页编写和运行，不导出、不备份。agent 接口：python edu.py input（标准输入 JSON）。\n首次启动驱动可能需要下载，请等待。')
        root.after(100, self.poll)
        root.after(1000, self.poll_auto)

    def split_output(self, event):
        self.output_panes.sashpos(0, event.width // 2)

    def start_auto(self):
        if self.busy or self.auto_running:
            return
        if not self.experiments or not self.checked:
            self.status.set('请先获取题目列表，并勾选要完成的实验。')
            return
        try:
            model = normalize_model(self.model.get())
            models = list(dict.fromkeys([model] + list(self.model_entry.cget('values'))))[:8]
            write_json(MODEL_SETTINGS, {'model': model, 'models': models})
        except (ToolError, OSError) as error:
            code = getattr(error, 'code', 'model_settings_failed')
            message = str(error) if isinstance(error, ToolError) else '无法保存模型设置，请检查本地目录权限'
            self.status.set('[' + code + '] ' + message)
            self.write('[' + code + '] ' + message)
            return
        self.model.set(model)
        self.model_entry.configure(values=models)
        ids = [i['id'] for i in self.experiments if i['id'] in self.checked]
        self.call('auto-start', {'model': model, 'ids': ids})

    def pause(self):
        self.auto_discover = True
        self.call('pause', interrupt=True)

    def toggle_all(self):
        if not self.busy and not self.auto_running:
            self.check_many('none' if len(self.checked) == len(self.experiments) else 'all')

    def poll_auto(self):
        if (self.auto_running or self.auto_discover) and not self.auto_poll_pending:
            self.auto_discover = False
            self.call('auto-status', {'since': self.auto_cursor}, interrupt=True)
        self.root.after(750, self.poll_auto)

    def show_automation(self, result):
        automation = result.get('automation')
        if not automation:
            return
        self.auto_running = automation.get('running', False)
        for event in result.get('events', []):
            if event['seq'] <= self.auto_cursor:
                continue
            self.write_to(self.model_output, '[' + event['kind'] + '] ' + event['text'])
            if event['kind'] in ('feedback', 'error'):
                self.write(event['text'])
        self.auto_cursor = result.get('cursor', self.auto_cursor)
        usage = automation.get('usage') or {}
        tokens = ''
        if usage:
            tokens = f"；输入 {usage.get('input_tokens', 0)} / 输出 {usage.get('output_tokens', 0)} tokens"
        error_prefix = '[' + automation['error'] + '] ' if automation.get('phase') == 'error' and automation.get('error') else ''
        self.status.set(error_prefix + automation.get('message', '') + tokens)
        self.live_progress = result.get('experiments', self.live_progress)
        self.automation = automation
        self.show_live_progress(self.live_progress, automation)

    def show_live_progress(self, progress, automation):
        phases = {'thinking': '解答中', 'submitting': '评测中', 'revising': '纠错中',
                  'stopping': '正在暂停', 'stopped': '已暂停', 'error': '需处理'}
        for item in self.experiments:
            live = progress.get(item['id'])
            if not live:
                continue
            item.update({k: live[k] for k in ('finished', 'total', 'complete') if live.get(k) is not None})
            state = experiment_state(item)
            level_state = live.get('level_status', '')
            if automation.get('homework_id') == item['id'] and automation.get('task_id') == live.get('task_id'):
                if level_state != '已通过' and not level_state.startswith('已提交'):
                    level_state = phases.get(automation.get('phase'), level_state)
                if not item.get('complete'):
                    state = phases.get(automation.get('phase'), state)
            level = live.get('subject', '')
            if level_state:
                level += ' · ' + level_state
            item['live_level'] = level
            if self.table.exists(item['id']):
                self.table.set(item['id'], 'state', state)
                self.table.set(item['id'], 'progress', experiment_progress(item))
                self.table.set(item['id'], 'level', level)

    def mark_selection_changed(self):
        for item in self.experiments:
            self.table.item(item['id'], text='☑' if item['id'] in self.checked else '☐')
        self.selection_dirty = True
        self.selection_status.set(f'已勾选 {len(self.checked)} / {len(self.experiments)} 个实验；点击“自动答题”即应用。')

    def toggle_click(self, event):
        if self.busy or self.auto_running:
            return
        row = self.table.identify_row(event.y)
        if row and self.table.identify_column(event.x) == '#0':
            self.toggle_row(row)

    def toggle_space(self, event):
        if not self.busy and not self.auto_running and self.table.focus():
            self.toggle_row(self.table.focus())
        return 'break'

    def toggle_row(self, row):
        if row in self.checked:
            self.checked.remove(row)
        else:
            self.checked.add(row)
        self.mark_selection_changed()

    def check_many(self, mode):
        self.checked = {i['id'] for i in self.experiments if mode == 'all' or
                        (mode == 'unfinished' and i.get('complete') is False and i.get('can_enter'))}
        self.mark_selection_changed()

    def show_experiments(self, items, selection):
        self.experiments = items
        known = {i['id'] for i in items}
        if not self.selection_dirty:
            self.checked = set(selection.get('ids', []))
        self.checked &= known
        self.table.delete(*self.table.get_children())
        for item in items:
            status = item.get('status') or []
            if isinstance(status, list):
                status = '、'.join(map(str, status))
            self.table.insert('', 'end', iid=item['id'], text='☑' if item['id'] in self.checked else '☐',
                              values=(item['title'], experiment_state(item), experiment_progress(item),
                                      str(status), '是' if item.get('can_enter') else '否', item.get('live_level', '')))
        self.selection_status.set(f'共 {len(items)} 个实验，已勾选 {len(self.checked)} 个；' + (
            '点击“自动答题”应用勾选并开始。'))
        self.show_live_progress(self.live_progress, self.automation)

    def apply_selection(self, start=False):
        if not self.experiments:
            self.status.set('请先点击“获取题目列表”。')
            return
        if start and not self.checked:
            self.status.set('请至少勾选一个要完成的实验。')
            return
        ids = [i['id'] for i in self.experiments if i['id'] in self.checked]
        self.call('select', {'ids': ids, 'start': start})

    def write(self, value):
        self.write_to(self.output, value)

    @staticmethod
    def write_to(widget, value):
        widget.insert('end', value + '\n')
        if int(widget.index('end-1c').split('.')[0]) > 1500:
            widget.delete('1.0', '500.0')
        # Also bound compact one-line JSON/model responses.
        if int(widget.count('1.0', 'end', 'chars')[0]) > 200000:
            widget.delete('1.0', 'end-100000c')
        widget.see('end')

    def call(self, command, args=None, interrupt=False):
        if (self.busy or self.auto_running) and not interrupt:
            return
        if command == 'auto-status':
            if self.auto_poll_pending:
                return
            self.auto_poll_pending = True
        if not interrupt:
            self.busy = True
            for button in self.buttons:
                button.state(['disabled'])
            self.status.set('正在执行: ' + command)

        def run():
            try:
                if command not in ('pause', 'shutdown', 'auto-stop'):
                    ensure_server()
                result = rpc(command, args or {}, timeout=300)
            except Exception as e:
                result = error_result(e)
            self.results.put((command, result, interrupt))

        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        while True:
            try:
                command, result, interrupt = self.results.get_nowait()
            except queue.Empty:
                break
            if command == 'auto-status':
                self.auto_poll_pending = False
            if command in ('auto-start', 'auto-status', 'auto-stop') and result.get('ok'):
                self.show_automation(result)
                if command == 'auto-start':
                    self.selection_dirty = False
                    self.selection_status.set(f'自动答题范围：已勾选 {len(self.checked)} 个实验。')
            elif command == 'list' and result.get('ok'):
                self.show_experiments(result['experiments'], result.get('selection', {}))
                self.write(f"已读取 {len(result['experiments'])} 个实验，标题和状态见上方列表。")
            else:
                self.write(command + '\n' + json.dumps(result, ensure_ascii=False, indent=2))
            if command == 'select' and 'selection' in result:
                self.selection_dirty = False
                self.selection_status.set(f'已应用 {len(self.checked)} 个实验；' + (
                    'agent 只处理勾选范围。' if result.get('ok') else '打开实验未完成，原因见下方输出。'))
            context = result.get('context', result)
            current = context.get('current_file') or {}
            if current:
                self.path.set(current.get('path', ''))
            if context.get('url') and '/tasks/' in context['url']:
                self.task.set(context['url'])
            if not interrupt:
                self.busy = False
                if command not in ('auto-start', 'auto-stop') or not result.get('ok'):
                    self.status.set('完成: ' + command if result.get('ok') else result.get('message', '操作失败'))
            for button in self.buttons:
                button.state(['disabled'] if self.busy or self.auto_running else ['!disabled'])
        self.root.after(120, self.poll)


def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
