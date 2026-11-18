"""Bounded solve/evaluate/revise loop; source lives only in memory and the webpage."""
from __future__ import annotations

import concurrent.futures
import json
import threading
from collections import deque

from .codex_solver import CodexSolver, DEFAULT_MODEL, normalize_model
from .core import ToolError


class AutoController:
    def __init__(self, worker, solver_factory=CodexSolver):
        self.worker = worker
        self.solver_factory = solver_factory
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.events = deque()
        self.event_chars = 0
        self.seq = 0
        self.state = {'running': False, 'phase': 'idle', 'message': '尚未启动自动答题',
                      'completed_tasks': 0, 'usage': {}}

    def emit(self, kind, text):
        if not isinstance(text, str):
            return
        text = text[:20000] + ('\n[显示内容已截断]' if len(text) > 20000 else '')
        with self.lock:
            self.seq += 1
            self.events.append({'seq': self.seq, 'kind': kind, 'text': text})
            self.event_chars += len(text)
            while len(self.events) > 100 or self.event_chars > 150000:
                self.event_chars -= len(self.events.popleft()['text'])

    def update(self, phase, message, **fields):
        with self.lock:
            self.state.update(phase=phase, message=message, **fields)
        self.emit('error' if phase == 'error' else 'progress',
                  '[' + fields['error'] + '] ' + message if phase == 'error' and fields.get('error') else message)

    def status(self, since=0):
        if not isinstance(since, int) or since < 0:
            raise ToolError('since 必须为非负整数', 'invalid_input')
        with self.lock:
            return {'automation': dict(self.state), 'events': [dict(e) for e in self.events if e['seq'] > since],
                    'cursor': self.seq, 'experiments': self.worker.engine.progress_snapshot()
                    if hasattr(self.worker.engine, 'progress_snapshot') else {}}

    def stop(self):
        self.cancel.set()
        self.worker.engine.cancel.set()
        with self.lock:
            active = self.state['running']
        if active:
            self.update('stopping', '正在停止：不再开始新的模型调用或提交')
        return self.status()

    def start(self, model=None, ids=None, on_done=lambda: None):
        config = dict(self.worker.engine.config.get('codex', {}))
        model = normalize_model(config.get('model', DEFAULT_MODEL) if model is None else model)
        with self.lock:
            if self.state['running']:
                raise ToolError('自动答题已在运行', 'busy')
            self.cancel.clear()
            self.state = {'running': True, 'phase': 'starting', 'message': '正在检查勾选范围',
                          'model': model, 'completed_tasks': 0, 'attempt': 0, 'usage': {}}
            self.events.clear()
            self.event_chars = 0
        self.thread = threading.Thread(target=self.run, args=(config, model, ids, on_done),
                                       name='codex-autosolver', daemon=True)
        self.thread.start()
        return self.status()

    def check_cancel(self):
        if self.cancel.is_set():
            raise ToolError('自动答题已停止', 'cancelled')

    def call(self, command, args=None):
        self.check_cancel()
        future = self.worker.submit(command, args or {})
        while True:
            try:
                result = future.result(timeout=0.15)
                break
            except concurrent.futures.TimeoutError:
                if self.cancel.is_set():
                    self.worker.engine.cancel.set()
        self.check_cancel()
        if not result.get('ok'):
            # BrowserWorker already logged the error. Keep feedback only in memory.
            raise ToolError(result.get('message', '网页操作失败'), result.get('error', 'operation_failed'))
        return result

    @staticmethod
    def add_file(payload, context):
        current = context.get('current_file') or {}
        if context.get('task_id') != payload['task_id']:
            raise ToolError('读取文件时关卡已改变，停止自动答题', 'task_mismatch')
        if payload.get('task_type') == 'choice':
            return
        if not current.get('path') or not isinstance(context.get('code'), str):
            raise ToolError('网页代码未加载完整，未调用模型', 'missing_code')
        payload['files_context'][current['path']] = {
            'code': context['code'], 'sha256': current['sha256'], 'read_only': bool(current.get('read_only'))}

    def run(self, config, model, ids, on_done):
        try:
            selected = self.call('select', {'ids': ids} if ids is not None else {})['selection']
            if not selected['restricted'] or not selected['ids']:
                raise ToolError('请先勾选并应用要完成的实验', 'empty_selection')
            context = self.call('auto-context')
            solver = self.solver_factory(config, self.emit, self.worker.engine.base)
            completed = 0
            while completed < config.get('max_tasks', 100):
                self.check_cancel()
                tid = context['task_id']
                payload = {'task_id': tid, 'subject': context.get('subject', ''),
                           'description': context.get('description', ''),
                           'task_type': context.get('task_type', 'code'),
                           'choices': context.get('choices', []),
                           'available_files': context.get('available_files', []), 'files_context': {},
                           'feedback': None}
                self.add_file(payload, context)
                attempt, reads, last_submission = 0, 0, None
                while True:
                    self.check_cancel()
                    if attempt >= config.get('max_attempts', 5):
                        raise ToolError('当前关卡已到自动纠错上限，请查看评测结果后处理', 'auto_attempt_limit')
                    self.update('thinking', f'正在解答 {payload["subject"]}，第 {attempt + 1} 次提交前分析',
                                task_id=tid, subject=payload['subject'], attempt=attempt + 1,
                                homework_id=(context.get('experiment') or {}).get('homework_id'))
                    answer, usage = solver.solve(payload, model, self.cancel)
                    self.check_cancel()
                    with self.lock:
                        totals = dict(self.state['usage'])
                        for name, count in usage.items():
                            totals[name] = totals.get(name, 0) + count
                        self.state['usage'] = totals
                    if answer['action'] == 'read':
                        reads += 1
                        if reads > 10 or all(p in payload['files_context'] for p in answer['read_files']):
                            raise ToolError('模型重复请求文件或读取次数超限', 'model_read_limit')
                        for path in answer['read_files']:
                            if path not in payload['files_context']:
                                self.add_file(payload, self.call('context', {'path': path}))
                        continue
                    # Require a changed answer after failed evaluation; do not blindly resubmit.
                    from .core import digest
                    choosing = answer['action'] == 'choose'
                    submission = sorted([{'question_id': a['question_id'], 'positions': sorted(a['positions'])}
                                         for a in answer['answers']], key=lambda a: a['question_id']) if choosing else answer['files']
                    fingerprint = digest(submission)
                    if last_submission is not None and (fingerprint == last_submission or (not choosing and all(
                            f['code'] == payload['files_context'][f['path']]['code'] for f in answer['files']))):
                        raise ToolError('模型未修改答案，已停止重复提交', 'unchanged_answer')
                    last_submission = fingerprint
                    attempt += 1
                    result = None
                    if choosing:
                        self.update('submitting', '正在网页选择答案，随后评测')
                        result = self.call('input', {'task': tid, 'answers': answer['answers'],
                                                     'evaluate': True, 'advance': True})
                    for i, file in enumerate(answer['files']):
                        last = i == len(answer['files']) - 1
                        self.update('submitting', f'正在网页保存 {file["path"]}' + ('，随后评测' if last else ''))
                        result = self.call('input', {'task': tid, 'path': file['path'], 'code': file['code'],
                            'expected': payload['files_context'][file['path']]['sha256'],
                            'evaluate': last, 'advance': last})
                        payload['files_context'][file['path']] = {
                            'code': file['code'], 'sha256': result['sha256']}
                    evaluation = (result.get('evaluation') or {}).get('result') or {}
                    if result.get('action') == 'revise':
                        payload['feedback'] = evaluation
                        self.emit('feedback', json.dumps(evaluation, ensure_ascii=False))
                        self.update('revising', '网页评测未通过，将实际反馈交给 Codex 纠错')
                        continue
                    if not evaluation.get('passed'):
                        raise ToolError('没有明确的评测通过结果，停止自动前进', 'evaluation_unknown')
                    completed += 1
                    with self.lock:
                        self.state['completed_tasks'] = completed
                    self.emit('feedback', '本关网页已提交，平台未公开判分；继续下一关' if
                              evaluation.get('feedback_visible') is False else '本关网页评测已通过')
                    if result.get('action') == 'selection_complete':
                        self.update('complete', '勾选的实验已全部完成')
                        return
                    if result.get('action') in ('next_level', 'next_experiment') and result.get('context'):
                        context = result['context']
                        break
                    raise ToolError(result.get('navigation_error', '通过后导航尚未确认，请检查网页'),
                                    'passed_navigation_pending')
            self.update('limit', '已到本次自动处理关卡上限，已停止')
        except ToolError as error:
            if error.code == 'cancelled':
                self.update('stopped', '自动答题已停止')
            elif error.code == 'selection_complete':
                self.update('complete', '勾选的实验已全部完成')
            else:
                from .diagnostics import record_error
                diagnostic = record_error('auto', error)
                self.update('error', str(error), error=error.code,
                            error_details={k: v for k, v in error.details.items()
                                           if k in ('model', 'provider_code', 'http_status', 'exit_code')}, **diagnostic)
        except Exception:
            # Never let arbitrary exception reprs leak prompts, generated source, or credentials.
            from .diagnostics import record_error
            error = ToolError('自动答题遇到内部错误，请检查工具日志', 'auto_internal_error')
            diagnostic = record_error('auto', error)
            self.update('error', str(error), error=error.code, **diagnostic)
        finally:
            with self.lock:
                self.state['running'] = False
            on_done()
