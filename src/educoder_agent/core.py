"""Selenium operations and compact task snapshots. No model/API dependency."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from .paths import BASE, ProjectPaths
DEFAULT_COURSE = 'https://www.educoder.net/classrooms/J2F8SUKL/shixun_homework'
DEFAULT_TASK = 'https://www.educoder.net/tasks/J2F8SUKL/4115214/s7ka9e3cl5b2'


class ToolError(Exception):
    def __init__(self, message, code='operation_failed', **details):
        super().__init__(message)
        self.code, self.details = code, details


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def validate_url(url):
    u = urlparse(url)
    if u.scheme != 'https' or u.hostname != 'www.educoder.net' or u.username or u.password:
        raise ToolError('只接受 https://www.educoder.net/ 下的网址', 'invalid_url')
    if not (u.path.startswith('/tasks/') or u.path.startswith('/classrooms/')):
        raise ToolError('请输入课程或实验网址', 'invalid_url')
    return url


def task_id(url):
    # /tasks/<id>, /tasks/<course>/<homework>/<id>; exclude Jupyter/exercise variants.
    parts = urlparse(url).path.strip('/').split('/')
    if parts[0] != 'tasks' or len(parts) not in (2, 4):
        raise ToolError('当前页面不是支持的代码关卡；请打开普通代码编辑器关卡', 'unsupported_task')
    value = parts[-1]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value) or value in ('start', 'jupyter'):
        raise ToolError('请等待实验启动并打开代码关卡', 'task_not_ready')
    return value


def compact_result(data, text_limit=4500):
    """Preserve failure evidence; status=2 is the site's explicit pass status."""
    cases = data.get('test_sets') or []
    failed = [c for c in cases if c.get('result') is False or c.get('result') == 0]
    detail = {k: data[k] for k in ('status', 'message', 'error', 'error_message',
              'compile_success', 'compile_result', 'running_code_message',
              'submitted', 'feedback_visible', 'correct') if data.get(k) is not None}
    detail['passed'] = data.get('status') == 2
    detail['case_count'] = len(cases)
    if cases:
        detail['passed_cases'] = sum(c.get('result') is True or c.get('result') == 1 for c in cases)
    # Results with an unknown schema must remain unknown, not become "all passed".
    detail['failed_cases'] = failed[:3]
    if data.get('visible_result'):
        detail['visible_result'] = data['visible_result']
    original = json.dumps(detail, ensure_ascii=False, separators=(',', ':'))
    if len(original) > text_limit:
        return {'passed': detail['passed'], 'status': data.get('status'),
                'case_count': len(cases), 'excerpt': original[:text_limit], 'truncated': True,
                **{k: detail[k] for k in ('submitted', 'feedback_visible', 'correct') if k in detail}}
    return detail


def choice_result(data, visible_result=''):
    """A hidden grade confirms submission, never correctness. Use this submission's response."""
    if data.get('had_all_submmit') is not True:
        return None
    cases = data.get('test_sets') or []
    count, correct_count = data.get('challenge_chooses_count'), data.get('choose_correct_num')
    correct = None
    if type(correct_count) is int and type(count) is int and count > 0:
        correct = correct_count == count
    elif cases and all(type(c.get('result')) in (bool, int) and c['result'] in (0, 1) for c in cases):
        correct = all(bool(c['result']) for c in cases)
    return {'status': 0 if correct is False else 2, 'submitted': True,
            'feedback_visible': correct is not None, 'correct': correct, 'test_sets': cases,
            'visible_result': visible_result,
            'message': '网页已提交，平台未公开判分，无法据此自动纠错' if correct is None else
                       '选择题评测通过' if correct else '选择题评测未通过'}


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root)

    def directory(self, tid):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', tid):
            raise ToolError('无效关卡标识', 'invalid_task')
        return self.root / 'tasks' / tid

    def checkpoint(self, tid):
        return load_json(self.directory(tid) / 'checkpoint.json', {})

    def context(self, snapshot, since=None):
        tid = task_id(snapshot['url'])
        directory = self.directory(tid)
        previous = self.checkpoint(tid)
        files = {name: {key: value for key, value in info.items()
                       if key in ('path', 'sha256', 'language', 'read_only')}
                 for name, info in previous.get('files', {}).items()}
        code = snapshot.get('content')
        path = snapshot.get('current_path')
        file_info = None
        if code is not None and path:
            file_info = {'path': path, 'sha256': digest(code),
                         'language': snapshot.get('language', ''), 'read_only': snapshot.get('read_only')}
            files[path] = file_info
        context = {
            'task_id': tid, 'url': snapshot['url'], 'subject': snapshot.get('subject') or snapshot.get('title'),
            'description': snapshot.get('description', ''),
            'game_status': snapshot.get('game_status'), 'current_file': file_info,
            'available_files': snapshot.get('files') or snapshot.get('tree') or [],
            'code': code,
        }
        if snapshot.get('experiment'):
            context['experiment'] = snapshot['experiment']
        if snapshot.get('task_type') == 'choice':
            context.update(task_type='choice', choices=snapshot.get('choices', []))
            if 'feedback_visible' in snapshot:
                context['feedback_visible'] = snapshot['feedback_visible']
        revision = digest(context)[:16]
        history_path = directory / 'revisions.json'
        saved = load_json(history_path, {})
        history = saved.get('hashes', {})
        baseline = history.get(since) if since else None
        hashes = {key: digest(value) for key, value in context.items()}
        history[revision] = hashes
        history = dict(list(history.items())[-8:])
        write_json(history_path, {'hashes': history})
        write_json(directory / 'checkpoint.json', previous | {'task_id': tid, 'url': snapshot['url'], 'files': files,
                   'experiment': snapshot.get('experiment', {}), 'subject': snapshot.get('subject', '')})
        if baseline is not None:
            changes = {k: v for k, v in context.items() if baseline.get(k) != hashes[k]}
            return {'task_id': tid, 'revision': revision, 'delta': True, 'changed': changes}
        return context | {'revision': revision, 'delta': False}


class Engine:
    def __init__(self, base=BASE):
        self.base = Path(base).resolve()
        self.paths = ProjectPaths(self.base)
        self.config = load_json(self.paths.config, {})
        self.root = self.paths.state
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = SnapshotStore(self.root)
        self.browser = None
        self.browser_headless = None
        self.cancel = threading.Event()
        self.script = self.paths.browser_script.read_text(encoding='utf-8')
        self.script_stamp = self.paths.browser_script.stat().st_mtime_ns
        self.events = deque(maxlen=100)
        self.requests = {}
        self.seq = 0
        self.progress_lock = threading.Lock()
        self.live_progress = {}

    def check_cancel(self):
        if self.cancel.is_set():
            raise ToolError('操作已暂停；浏览器和已保存代码保留', 'cancelled')

    def sleep(self, seconds):
        if self.cancel.wait(seconds):
            self.check_cancel()

    def start(self, headless=None):
        headless = self.config.get('headless', True) if headless is None else headless
        if self.browser is not None:
            try:
                self.browser.current_window_handle
                if self.browser_headless == headless:
                    return
                self.close()
            except Exception:
                self.close()
        from selenium import webdriver
        is_edge = self.config.get('browser', 'edge') == 'edge'
        if is_edge:
            from selenium.webdriver.edge.options import Options
            from selenium.webdriver.edge.service import Service
            factory = webdriver.Edge
        else:
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            factory = webdriver.Chrome
        options = Options()
        profile = self.root / ('profile-edge' if is_edge else 'profile-chrome')
        options.add_argument('--user-data-dir=' + str(profile))
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--disable-background-mode')
        options.add_argument('--disable-gpu')
        for argument in self.config.get('browser_arguments', []):
            options.add_argument(argument)
        options.add_argument('--window-size=1360,920')
        options.page_load_strategy = 'eager'
        if headless:
            options.add_argument('--headless=new')
        if self.config.get('browser_binary'):
            options.binary_location = self.config['browser_binary']
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        # Keep Selenium Manager's downloads inside this project too.
        os.environ.setdefault('SE_CACHE_PATH', str(self.root / 'selenium-cache'))
        # Driver protocol logs may contain performance-log authentication headers.
        service_args = {'log_output': subprocess.DEVNULL}
        if self.config.get('driver_path'):
            service_args['executable_path'] = self.config['driver_path']
        try:
            self.browser = factory(options=options, service=Service(**service_args))
            self.browser.set_page_load_timeout(self.config.get('page_timeout', 45))
            self.browser.set_script_timeout(45)
            self.browser.execute_cdp_cmd('Network.enable', {'maxTotalBufferSize': 8000000, 'maxResourceBufferSize': 2000000})
            self.browser_headless = headless
        except Exception as e:
            self.close()
            raise ToolError('浏览器启动失败，请检查 driver_path、浏览器版本及驱动下载网络: ' + str(e)[:1000], 'browser_start_failed') from e

    def close(self):
        if self.browser:
            try:
                self.browser.quit()
            except Exception:
                pass
        self.browser = None
        self.browser_headless = None
        self.events.clear()
        self.requests.clear()

    def js(self, op, **args):
        self.check_cancel()
        if not self.browser:
            raise ToolError('请先运行 login 或 open 打开浏览器', 'browser_not_open')
        path = self.paths.browser_script
        stamp = path.stat().st_mtime_ns
        if stamp != self.script_stamp:
            self.script = path.read_text(encoding='utf-8')
            self.script_stamp = stamp
        return self.browser.execute_script(self.script, op, args)

    def collect(self):
        """Read only task request bodies/responses, never authentication headers."""
        if not self.browser:
            return
        try:
            logs = self.browser.get_log('performance')
        except Exception:
            return
        for entry in logs:
            try:
                message = json.loads(entry['message'])['message']
                p = message.get('params', {})
                rid = p.get('requestId')
                if message['method'] == 'Network.requestWillBeSent':
                    u = urlparse(p['request']['url'])
                    if u.hostname and (u.hostname == 'educoder.net' or u.hostname.endswith('.educoder.net')) and re.search(
                        r'/api/(?:tasks/[\w-]+(?:\.json|/(?:game_build|game_status|choose_build|rep_content)\.json)|myshixuns/[\w-]+/update_file\.json)$', u.path):
                        body = p['request'].get('postData', '')
                        self.requests[rid] = {'path': u.path, 'request_body': body, 'query': u.query}
                        if len(self.requests) > 200:
                            self.requests.pop(next(iter(self.requests)))
                elif message['method'] == 'Network.loadingFinished' and rid in self.requests:
                    request = self.requests.pop(rid)
                    raw = self.browser.execute_cdp_cmd('Network.getResponseBody', {'requestId': rid})
                    body = base64.b64decode(raw['body']).decode('utf-8') if raw.get('base64Encoded') else raw['body']
                    data = json.loads(body)
                    self.seq += 1
                    self.events.append(request | {'seq': self.seq, 'data': data})
                elif message['method'] == 'Network.loadingFailed':
                    self.requests.pop(rid, None)
            except Exception:
                # CDP may evict a body. The DOM adapter still works; never log a request/header.
                continue

    def snapshot(self):
        snap = self.js('snapshot', **self.config.get('selectors', {}))
        tid = task_id(snap['url'])
        # A captured task response is a fallback if React's internal shape changes.
        if not snap.get('task_available'):
            self.collect()
            for event in reversed(self.events):
                if event['path'] == '/api/tasks/' + tid + '.json':
                    data = event['data']
                    if isinstance(data, dict) and isinstance(data.get('data'), dict):
                        data = data['data']
                    challenge = data.get('challenge', {}) if isinstance(data, dict) else {}
                    snap['description'] = challenge.get('task_pass') or snap['description']
                    snap['subject'] = challenge.get('subject') or snap['subject']
                    if isinstance(data, dict):
                        snap['game_status'] = data.get('game', {}).get('status', snap['game_status'])
                    break
        return snap

    def wait_snapshot(self, code=False):
        if code:
            return self.select_file(None)
        deadline = time.monotonic() + self.config.get('page_timeout', 45)
        last = None
        while time.monotonic() < deadline:
            self.check_cancel()
            try:
                last = self.snapshot()
                if (code and last.get('editor_available') and last.get('current_path')) or (
                        not code and (last.get('description') or last.get('choices'))):
                    return last
            except ToolError as e:
                if e.code not in ('task_not_ready', 'unsupported_task'):
                    raise
            self.sleep(self.config.get('ready_poll_interval', 0.15))
        raise ToolError('页面尚未准备好；请检查登录、弹窗，并打开题目和代码文件', 'page_not_ready',
                        notice=self.js('notice'), last_url=last.get('url') if last else '')

    def navigate(self, url, headless=None):
        self.start(headless=headless)
        self.check_cancel()
        validate_url(url)
        self.browser.get(url)
        self.collect()

    def select_file(self, path):
        snap = self.wait_snapshot()
        last_clicked = None
        deadline = time.monotonic() + self.config.get('page_timeout', 45)
        while time.monotonic() < deadline:
            self.check_cancel()
            if (snap.get('task_type') == 'choice' and snap.get('choices') and
                    snap.get('choice_ready', True) and path is None):
                return snap
            if snap.get('editor_available') and snap.get('current_path') and (
                    path is None or snap['current_path'] == path):
                return snap
            target = path or snap.get('current_path') or next((p for p in snap.get('files', [])
                                                             if isinstance(p, str) and p), None)
            if target and (snap.get('current_path') != target or not snap.get('editor_available')):
                element = self.js('select_file', path=target)
                if element and element != last_clicked:
                    element.click()
                    last_clicked = element
            self.sleep(self.config.get('ready_poll_interval', 0.15))
            snap = self.snapshot()
        raise ToolError('等待网页代码编辑器超时；已尝试自动打开文件，请检查环境加载或弹窗',
                        'file_not_open', path=path, available_files=snap.get('files', []),
                        editor_available=snap.get('editor_available'), notice=self.js('notice'))

    def context(self, since=None, path=None):
        if not self.browser:
            if self.selection()['restricted']:
                return self.next_experiment()['context']
            self.navigate(self.config.get('task_url', DEFAULT_TASK))
        elif urlparse(self.browser.current_url).path.startswith('/classrooms/'):
            if self.selection()['restricted']:
                return self.next_experiment()['context']
            self.navigate(self.config.get('task_url', DEFAULT_TASK))
        snap = self.select_file(path) if path else self.wait_snapshot(code=True)
        return self.context_from_snapshot(snap, since)

    def context_from_snapshot(self, snap, since=None):
        if not snap.get('description') and not (snap.get('task_type') == 'choice' and snap.get('choices')):
            raise ToolError('未读取到题目，不能导出空上下文', 'missing_description')
        if not snap.get('editor_available'):
            snap['content'] = None
        result = self.store.context(snap, since)
        self.publish_progress(snap)
        if snap.get('content') is None and snap.get('task_type') != 'choice':
            result['notice'] = '未读取完整代码；请在网页中打开代码文件，然后再次 context'
        return result

    def save(self):
        self.collect()
        baseline = self.seq
        result = self.js('save', selector=self.config.get('selectors', {}).get('save', ''))
        if isinstance(result, dict) and result.get('callback'):
            if result.get('rejected'):
                raise ToolError('网站拒绝保存；不会继续评测', 'save_rejected')
            # Some versions return {content,resubmit,sec_key,content_modified},
            # others return true when already saved. The platform evaluation is authoritative.
            return
        if hasattr(result, 'click'):
            result.click()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.collect()
            for event in reversed(self.events):
                if event['seq'] > baseline and event['path'].endswith('/update_file.json'):
                    data = event['data']
                    if isinstance(data, dict):
                        if 'status' in data and data['status'] != 0:
                            raise ToolError('网站没有确认保存成功', 'save_rejected')
                        if data.get('status') == 0 or isinstance(data.get('content'), str):
                            return
            self.sleep(0.25)
        raise ToolError('没有收到保存确认；不会继续评测。请检查网页提示或 save 选择器', 'save_unconfirmed',
                        notice=self.js('notice'))

    def input_choices(self, answers, task, evaluate=True, advance=True):
        if not isinstance(task, str) or not task or not isinstance(evaluate, bool) or not isinstance(advance, bool):
            raise ToolError('task 必须为字符串，evaluate 和 advance 必须为布尔值', 'invalid_input')
        snap = self.wait_snapshot(code=True)
        self.check_selection(snap['url'])
        if task_id(snap['url']) != task or snap.get('task_type') != 'choice':
            raise ToolError('当前网页不是指定的选择题关卡', 'task_mismatch')
        from .codex_solver import validate_choices
        validate_choices(answers, snap.get('choices', []))
        result = self.js('set_choices', task_id=task, answers=answers)
        if not result or not result.get('selected'):
            raise ToolError('网页没有完整应用所选答案', 'choice_selection_failed')
        response = {'task_id': task, 'selected': True}
        if not evaluate:
            return response | {'action': 'selected'}
        return self.after_evaluation(response, self.evaluate(), advance)

    def input_code(self, code, task, path, expected=None, evaluate=True, advance=True):
        if not isinstance(code, str) or len(code.encode('utf-8')) > 1000000:
            raise ToolError('code 必须为不超过 1 MB 的字符串', 'invalid_code')
        if not isinstance(task, str) or not task or not isinstance(path, str) or not path:
            raise ToolError('必须传入 task_id 和网页文件 path', 'invalid_input')
        if not isinstance(evaluate, bool) or not isinstance(advance, bool):
            raise ToolError('evaluate 和 advance 必须为布尔值', 'invalid_input')
        result = self.write_code(code, path=path, expected=expected, task=task)
        if not evaluate:
            return result | {'action': 'saved'}
        return self.after_evaluation(result, self.evaluate(), advance)

    def after_evaluation(self, result, evaluation, advance):
        result['evaluation'] = evaluation
        if not evaluation['result']['passed']:
            return result | {'action': 'revise', 'message': '请根据网页评测结果修改答案，再调用 input'}
        if not advance:
            return result | {'action': 'passed'}
        try:
            following = self.next_level()
            return result | {'action': 'next_level', 'context': following}
        except ToolError as error:
            if error.code != 'next_level_missing':
                return result | {'action': 'passed_navigation_pending', 'navigation_error': str(error)}
        try:
            return result | {'action': 'next_experiment', **self.next_experiment()}
        except ToolError as error:
            if error.code == 'selection_complete':
                return result | {'action': 'selection_complete', 'message': str(error),
                                 'selection': self.selection()}
            return result | {'action': 'passed_navigation_pending', 'navigation_error': str(error),
                             'navigation_code': error.code}

    def write_code(self, code, path=None, expected=None, task=None):
        snap = self.select_file(path) if path else self.wait_snapshot(code=True)
        self.check_selection(snap['url'])
        tid = task_id(snap['url'])
        if task and task != tid:
            raise ToolError('网页已切换关卡，停止写入', 'task_mismatch', expected=task, actual=tid)
        checkpoint = self.store.checkpoint(tid)
        remote_path = snap['current_path']
        original = checkpoint.get('files', {}).get(remote_path)
        if not original:
            raise ToolError('请先 context 读取当前网页文件，再通过 input 传入代码', 'missing_checkpoint')
        expected = expected or original['sha256']
        if digest(snap['content']) != expected:
            raise ToolError('网页代码已变化，请重新 context 并合并修改', 'code_conflict', actual=digest(snap['content']))
        changed = code != snap['content']
        if changed:
            self.js('set_code', code=code)
            self.sleep(0.35)  # Allow React's onChange to commit before invoking its save callback.
        # Even an unchanged editor can contain unsaved work from a previous attempt.
        self.save()
        # Keep the editor/environment alive; judge the answer using the platform evaluation.
        fresh = self.select_file(remote_path)
        if task_id(fresh['url']) != tid or fresh['current_path'] != remote_path:
            raise ToolError('保存时网页切换了关卡或文件，已停止评测', 'task_mismatch')
        actual_hash = digest(fresh['content'])
        original.pop('local_file', None)
        original.update(sha256=actual_hash)
        write_json(self.store.directory(tid) / 'checkpoint.json', checkpoint)
        return {'task_id': tid, 'path': remote_path, 'changed': changed, 'saved': True,
                'sha256': actual_hash}

    def start_evaluation(self, snap):
        """Wait for the action bar, then dispatch exactly one JS click."""
        tid = task_id(snap['url'])
        deadline = time.monotonic() + self.config.get('evaluation_ready_timeout', self.config.get('page_timeout', 45))
        last = {}
        while time.monotonic() < deadline:
            self.check_cancel()
            self.collect()
            baseline = self.seq
            try:
                last = self.js('evaluate_click', selector=self.config.get('selectors', {}).get('evaluate', ''),
                               task_id=tid, path=snap.get('current_path'))
            except ToolError:
                raise
            except Exception:
                # A transport/handler error may occur after dispatch. Do not click again.
                raise ToolError('评测点击回执未确认；可能已经开始，请检查网页，不会自动重复点击',
                                'evaluation_click_uncertain') from None
            if not isinstance(last, dict) or not isinstance(last.get('clicked'), bool):
                raise ToolError('评测点击回执无法识别；不会自动重复点击', 'evaluation_click_uncertain')
            if last['clicked']:
                return baseline
            if last.get('reason') == 'task_mismatch':
                raise ToolError('等待评测按钮时网页已切换关卡，已停止点击', 'task_mismatch')
            if last.get('reason') == 'evaluating':
                raise ToolError('网页已经在评测，不会重复点击', 'evaluation_running')
            self.sleep(0.3)
        raise ToolError('等待评测按钮可用超时；未点击评测，请检查环境加载或弹窗',
                        'evaluation_button_timeout', control=last)

    def evaluate(self, timeout=None, reset_attempts=False):
        snap = self.wait_snapshot(code=True)
        self.check_selection(snap['url'])
        tid = task_id(snap['url'])
        checkpoint = self.store.checkpoint(tid)
        attempts = 0 if reset_attempts else checkpoint.get('attempts', 0)
        if attempts >= self.config.get('max_attempts', 5):
            raise ToolError('已到连续评测上限；查看日志或修正方案后使用 --reset-attempts', 'attempt_limit')
        if snap.get('evaluating'):
            raise ToolError('网页正在评测；请等待现有评测结束', 'evaluation_running')
        before_url = snap['url']
        baseline = self.start_evaluation(snap)
        checkpoint['attempts'] = attempts + 1
        write_json(self.store.directory(tid) / 'checkpoint.json', checkpoint)
        deadline = time.monotonic() + (timeout or self.config.get('evaluation_timeout', 180))
        started = False
        loading_observed = False
        while time.monotonic() < deadline:
            self.sleep(self.config.get('poll_interval', 1))
            self.collect()
            current = self.snapshot()
            if task_id(current['url']) != tid:
                raise ToolError('评测期间网页切换关卡，不能使用结果', 'task_mismatch')
            started = started or current.get('evaluating') is True
            loading_observed = loading_observed or current.get('evaluating') is True
            for event in list(self.events):
                if event['seq'] <= baseline or not event['path'].startswith('/api/tasks/' + tid + '/'):
                    continue
                data = event['data']
                if not isinstance(data, dict):
                    continue
                if event['path'].endswith('/game_build.json'):
                    started = True
                    if isinstance(data.get('status'), (int, float)) and data['status'] < 0:
                        raise ToolError('网站拒绝本次评测', 'evaluation_rejected',
                                        response={k: data[k] for k in ('status', 'message') if k in data})
                if event['path'].endswith('/game_status.json') and isinstance(data.get('status'), (int, float)) and data['status'] >= 0:
                    return self.finish_result(tid, data | {'visible_result': current.get('result', '')})
                if snap.get('task_type') == 'choice' and event['path'].endswith('/choose_build.json'):
                    started = True
                    if isinstance(data.get('status'), (int, float)) and data['status'] < 0:
                        raise ToolError('网站拒绝本次选择题评测', 'evaluation_rejected')
                    result = choice_result(data, current.get('result', ''))
                    if result is not None:
                        return self.finish_result(tid, result)
            if snap.get('task_type') != 'choice' and loading_observed and current.get('evaluating') is False:
                return self.finish_result(tid, {'status': current.get('game_status'),
                    'test_sets': current.get('test_sets', []), 'visible_result': current.get('result', ''),
                    'message': current.get('message', '')})
        raise ToolError('等待评测超时；未判定通过，不会自动再次点击评测', 'evaluation_timeout',
                        started=started, notice=self.js('notice'), url=before_url)

    def finish_result(self, tid, data):
        result = compact_result(data, self.config.get('result_chars', 4500))
        checkpoint = self.store.checkpoint(tid)
        progress = self.publish_progress({'task_id': tid, 'url': checkpoint.get('url', ''),
            'experiment': checkpoint.get('experiment', {}), 'subject': checkpoint.get('subject', ''),
            'level_status': '已提交（未公开判分）' if data.get('feedback_visible') is False else None},
            passed=result['passed'])
        checkpoint['last_passed'] = result['passed']
        if result['passed']:
            checkpoint['attempts'] = 0
        else:
            from .diagnostics import record_error
            result.update(record_error('test', ToolError(
                '网页评测未通过，status=' + str(data.get('status')) + '，请根据返回的评测结果修改代码',
                'evaluation_failed')))
        write_json(self.store.directory(tid) / 'checkpoint.json', checkpoint)
        return {'task_id': tid, 'result': result, 'progress': progress}

    def progress_snapshot(self):
        with self.progress_lock:
            return {key: dict(value) for key, value in self.live_progress.items()}

    def passed_task_ids(self):
        return [p.parent.name for p in self.store.root.glob('tasks/*/checkpoint.json')
                if load_json(p, {}).get('last_passed')]

    def publish_progress(self, snap, passed=None):
        experiment = snap.get('experiment') or {}
        homework = experiment.get('homework_id')
        if not homework:
            return {}
        catalog = load_json(self.root / 'course.json', {})
        if catalog.get('course_url') != self.config.get('course_url', DEFAULT_COURSE):
            return {}
        item = next((i for i in catalog.get('experiments', []) if i['id'] == homework), None)
        if not item:
            return {}
        tid = snap.get('task_id') or task_id(snap['url'])
        if passed:
            counted = set(catalog['counted_tasks'] if 'counted_tasks' in catalog else self.passed_task_ids())
            if tid not in counted:
                item['finished'] = (item.get('finished') or 0) + 1
                counted.add(tid)
            total = item.get('total') or experiment.get('total')
            if total:
                item['total'] = total
                item['finished'] = min(item.get('finished') or 0, total)
                item['complete'] = item['finished'] >= total
            catalog['counted_tasks'] = sorted(counted)
            write_json(self.root / 'course.json', catalog)
        value = {key: item.get(key) for key in ('finished', 'total', 'complete')}
        level_status = snap.get('level_status')
        if (snap.get('task_type') == 'choice' and snap.get('game_status') == 2 and
                snap.get('feedback_visible') is False):
            level_status = '已提交（未公开判分）'
        value.update(homework_id=homework, task_id=tid, subject=snap.get('subject', ''),
                     position=experiment.get('position'),
                     level_status=level_status or ('已通过' if passed is True or snap.get('game_status') == 2 else
                     '未通过' if passed is False else '待解答'))
        with self.progress_lock:
            self.live_progress[homework] = value
        return value

    def course(self, all_pages=True):
        self.navigate(self.config.get('course_url', DEFAULT_COURSE))
        seen, items = set(), []
        for _ in range(100):
            self.check_cancel()
            deadline = time.monotonic() + self.config.get('page_timeout', 45)
            cards = []
            while time.monotonic() < deadline:
                cards = self.js('course')
                ids = tuple(c['id'] for c in cards)
                if cards and ids not in seen:
                    break
                self.sleep(0.4)
            if not cards or ids in seen:
                if not items:
                    raise ToolError('没有读取到实验列表；请先登录并检查课程权限', 'course_not_ready', notice=self.js('notice'))
                raise ToolError('实验列表翻页未完成，不能把部分列表当作完整列表', 'pagination_incomplete', partial=items)
            seen.add(ids)
            for card in cards:
                entry = card.pop('entry', None)
                card['can_enter'] = entry is not None
                if not any(i['id'] == card['id'] for i in items):
                    items.append(card)
            if not all_pages:
                break
            next_button = self.js('next_page')
            if not next_button:
                break
            next_button.click()
            self.sleep(0.4)
        else:
            raise ToolError('实验列表超过分页上限', 'pagination_limit')
        if all_pages:
            write_json(self.root / 'course.json', {'course_url': self.config.get('course_url', DEFAULT_COURSE),
                                                  'experiments': items, 'counted_tasks': self.passed_task_ids()})
        return {'experiments': items, 'selection': self.selection()}

    def selection(self, ids=None, unrestricted=False, start=False):
        course_url = self.config.get('course_url', DEFAULT_COURSE)
        stored = load_json(self.root / 'selection.json', {})
        if stored.get('course_url') != course_url:
            stored = {}
        if ids is not None or unrestricted:
            if ids is not None and (not isinstance(ids, list) or
                    any(not isinstance(i, str) or not i.isdigit() for i in ids)):
                raise ToolError('ids 必须为实验 ID 字符串数组', 'invalid_selection')
            if unrestricted and ids is not None:
                raise ToolError('不能同时设置 ids 和 unrestricted', 'invalid_selection')
            if ids is not None:
                catalog = load_json(self.root / 'course.json', {})
                if not isinstance(catalog, dict) or catalog.get('course_url') != course_url:
                    raise ToolError('请先获取当前课程的完整实验列表，再勾选', 'selection_needs_list')
                known = {i['id'] for i in catalog.get('experiments', [])}
                unknown = sorted(set(ids) - known)
                if unknown:
                    raise ToolError('勾选中包含列表以外的实验 ID', 'invalid_selection', ids=unknown)
            stored = {'course_url': course_url, 'ids': None if unrestricted else list(dict.fromkeys(ids))}
            write_json(self.root / 'selection.json', stored)
        selected = stored.get('ids')
        result = {'restricted': selected is not None, 'ids': selected or []}
        if start:
            if not result['restricted'] or not result['ids']:
                raise ToolError('请先勾选至少一个实验', 'empty_selection')
            try:
                return {'selection': result, **self.next_experiment()}
            except ToolError as error:
                error.details['selection'] = result
                raise
        return result

    def check_selection(self, url):
        selected = self.selection()
        if not selected['restricted']:
            return
        parts = urlparse(url).path.strip('/').split('/')
        if len(parts) != 4 or parts[0] != 'tasks':
            raise ToolError('无法确认当前关卡所属实验，请通过勾选列表进入', 'selection_task_unknown')
        course = urlparse(self.config.get('course_url', DEFAULT_COURSE)).path.split('/')[2]
        if parts[1] != course or parts[2] not in selected['ids']:
            raise ToolError('当前实验未勾选，已停止写入和评测', 'experiment_not_selected', homework_id=parts[2])

    def experiment_candidates(self, items, homework_id=None):
        selection = self.selection()
        if selection['restricted']:
            if homework_id is not None and str(homework_id) not in selection['ids']:
                raise ToolError('该实验未勾选', 'experiment_not_selected', homework_id=str(homework_id))
            items = [i for i in items if i['id'] in selection['ids']]
            if not selection['ids']:
                raise ToolError('尚未勾选实验，请在列表中勾选并应用', 'empty_selection')
            if len(items) == len(selection['ids']) and all(i['complete'] is True for i in items):
                raise ToolError('勾选的实验已全部完成', 'selection_complete', ids=selection['ids'])
        candidates = [i for i in items if i['id'] == str(homework_id)] if homework_id is not None else [
            i for i in items if i['complete'] is False and i['can_enter']]
        if not candidates:
            raise ToolError('没有能明确判定为未完成且可进入的实验，请检查列表状态和勾选范围', 'no_next_experiment',
                            unknown=[i for i in items if i['complete'] is None])
        return candidates

    def next_experiment(self, homework_id=None):
        items = self.course()['experiments']
        candidates = self.experiment_candidates(items, homework_id)
        selected = candidates[0]
        # Locate the card again without retaining stale WebElements across pages/navigation.
        self.navigate(self.config.get('course_url', DEFAULT_COURSE))
        for _ in range(100):
            self.check_cancel()
            deadline = time.monotonic() + self.config.get('page_timeout', 45)
            cards = []
            while not cards and time.monotonic() < deadline:
                cards = self.js('course')
                if not cards:
                    self.sleep(0.4)
            card = next((c for c in cards if c['id'] == selected['id']), None)
            if card:
                if not card['entry']:
                    raise ToolError('实验卡片没有可用的进入按钮，请在网页中打开实验', 'entry_missing', experiment=selected)
                old_handles = set(self.browser.window_handles)
                card['entry'].click()
                deadline = time.monotonic() + self.config.get('page_timeout', 45)
                while time.monotonic() < deadline:
                    handles = set(self.browser.window_handles) - old_handles
                    if handles:
                        self.browser.switch_to.window(next(iter(handles)))
                        break
                    if '/tasks/' in urlparse(self.browser.current_url).path:
                        break
                    self.sleep(0.3)
                if '/tasks/' not in urlparse(self.browser.current_url).path:
                    raise ToolError('进入实验需要处理网页提示', 'entry_blocked', notice=self.js('notice'))
                # One active tab: no accumulation of classroom/task tabs.
                active = self.browser.current_window_handle
                for handle in list(self.browser.window_handles):
                    if handle != active:
                        self.browser.switch_to.window(handle)
                        self.browser.close()
                self.browser.switch_to.window(active)
                return {'experiment': selected, 'context': self.context()}
            next_button = self.js('next_page')
            if not next_button:
                break
            old_ids = tuple(c['id'] for c in cards)
            next_button.click()
            deadline = time.monotonic() + self.config.get('page_timeout', 45)
            while time.monotonic() < deadline:
                new_cards = self.js('course')
                if new_cards and tuple(c['id'] for c in new_cards) != old_ids:
                    break
                self.sleep(0.3)
        raise ToolError('未定位到所选实验', 'experiment_missing')

    def auto_context(self):
        selected = self.selection()
        if not selected['restricted'] or not selected['ids']:
            raise ToolError('请先勾选并应用要完成的实验', 'empty_selection')
        context = None
        if self.browser:
            try:
                self.check_selection(self.browser.current_url)
            except ToolError as error:
                if error.code not in ('experiment_not_selected', 'selection_task_unknown'):
                    raise
            else:
                snap = self.wait_snapshot()
                if snap.get('game_status') != 2:
                    if snap.get('editor_available') and snap.get('description'):
                        return self.context_from_snapshot(snap)
                    return self.context()
                context = {'task_id': task_id(self.browser.current_url), 'game_status': 2}
        if context is None:
            context = self.next_experiment()['context']
        visited = set()
        # After restarting the browser, "开始学习" can reopen an already passed level.
        # Skip these before calling the model or submitting the same answer again.
        while context.get('game_status') == 2:
            tid = context['task_id']
            if tid in visited:
                raise ToolError('网页重复返回已通过关卡，请刷新实验进度后继续', 'passed_navigation_pending')
            visited.add(tid)
            try:
                context = self.next_level()
            except ToolError as error:
                if error.code != 'next_level_missing':
                    raise
                context = self.next_experiment()['context']
        return context

    def next_level(self):
        snap = self.wait_snapshot()
        # A completed response can arrive just before React updates its game status.
        tid = task_id(snap['url'])
        if snap.get('game_status') != 2 and self.store.checkpoint(tid).get('last_passed'):
            deadline = time.monotonic() + self.config.get('navigation_ready_timeout', 5)
            while snap.get('game_status') != 2 and time.monotonic() < deadline:
                self.sleep(0.15)
                snap = self.snapshot()
                if task_id(snap['url']) != tid:
                    raise ToolError('等待通过状态时网页已切换关卡', 'task_mismatch')
        if snap.get('game_status') != 2:
            raise ToolError('当前关卡尚未确认通过', 'current_not_passed')
        self.check_selection(snap['url'])
        if snap.get('is_last_game') is True:
            raise ToolError('当前实验已到最后一关', 'next_level_missing')
        current = urlparse(snap['url'])
        next_id = snap.get('next_task')
        if not isinstance(next_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', next_id):
            destination = self.js('task_next_url')
            if destination:
                destination = validate_url(urljoin(snap['url'], destination))
                target = urlparse(destination)
                next_id = task_id(destination)
                # Educoder's links may omit course/homework. Keep the current selection scope.
                if len(current.path.strip('/').split('/')) == 4 and len(target.path.strip('/').split('/')) == 4:
                    if current.path.rsplit('/', 1)[0] != target.path.rsplit('/', 1)[0]:
                        raise ToolError('下一关链接指向其他实验，已停止跳转', 'next_level_unavailable')
            else:
                next_id = None
        if not next_id:
            if snap.get('is_last_game') is True:
                raise ToolError('当前实验已到最后一关', 'next_level_missing')
            raise ToolError('未定位下一关且无法确认本实验已全部结束，请检查网页关卡列表', 'next_level_unavailable')
        if next_id == task_id(snap['url']):
            raise ToolError('下一关仍指向当前关卡，已停止重复进入', 'next_level_unavailable')
        path = current.path.rsplit('/', 1)[0] + '/' + next_id
        destination = current._replace(path=path, query='', fragment='').geturl()
        try:
            # Normal site navigation avoids overlay interception and never resubmits the answer.
            self.navigate(destination)
            following = self.context()
            if following.get('task_id') != next_id:
                raise ToolError('下一关跳转后关卡不一致，请检查网页提示', 'next_level_unavailable')
            self.check_selection(following['url'])
            return following
        except ToolError:
            raise
        except Exception as error:
            # Preserve the passed evaluation in input_code even when browser navigation fails.
            raise ToolError('当前关卡已通过，但打开下一关失败；请继续导航，无需重新提交',
                            'next_level_unavailable', browser_error=type(error).__name__) from None

    def submit(self, confirm=False):
        # Separate explicit course submission from evaluation; do not infer success from a click.
        self.js('submit', selector=self.config.get('selectors', {}).get('submit', '')).click()
        self.sleep(0.7)
        notice = self.js('notice')
        if confirm and notice['dialogs']:
            self.js('confirm_submit').click()
            self.sleep(0.7)
        selector = self.config.get('selectors', {}).get('submit_success', '')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            notice = self.js('notice', selector=selector)
            messages = notice['messages'] + notice['submit_success']
            if any(re.search(r'(?:提交|交卷)成功', message) for message in messages):
                return {'submitted': True, 'notice': notice}
            self.sleep(0.3)
        return {'submitted': None, 'clicked': True, 'notice': notice,
                'message': '未取得明确提交成功提示，请核对网页状态；未自动重复提交'}

    def dispatch(self, command, args):
        self.cancel.clear()
        if command == 'login':
            self.navigate(args.get('url') or self.config.get('course_url', DEFAULT_COURSE), headless=False)
            return {'url': self.browser.current_url, 'message': '请在浏览器中登录；完成后调用 context、list 或 open。登录状态自动保留。'}
        if command == 'open':
            self.navigate(args.get('url') or self.config.get('task_url', DEFAULT_TASK))
            return {'url': self.browser.current_url}
        if command == 'context':
            return self.context(**args)
        if command == 'auto-context':
            return self.auto_context()
        if command == 'input':
            if 'answers' in args:
                return self.input_choices(**args)
            return self.input_code(**args)
        if command == 'test':
            return self.evaluate(**args)
        if command == 'list':
            return self.course(**args)
        if command == 'select':
            value = self.selection(**args)
            return value if 'selection' in value else {'selection': value}
        if command == 'next':
            return self.next_experiment(**args)
        if command == 'next-level':
            return self.next_level()
        if command == 'submit':
            return self.submit(**args)
        if command == 'status':
            return {'browser_open': self.browser is not None,
                    'headless': self.browser_headless,
                    'url': self.browser.current_url if self.browser else None,
                    'state_dir': str(self.root), 'selection': self.selection()}
        if command == 'close':
            self.close()
            return {'browser_open': False}
        raise ToolError('未知命令: ' + command, 'invalid_command')
