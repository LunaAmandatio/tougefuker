"""One browser owner, authenticated loopback RPC, shared by GUI and CLI."""
from __future__ import annotations

import concurrent.futures
import hmac
import hashlib
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .core import BASE, Engine, ToolError, load_json, write_json
from .paths import ProjectPaths

_background_services = []


def remove_runtime(path):
    # On Windows, the upgrader may briefly have this file open while polling it.
    deadline = time.monotonic() + 3
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.03)


def build_id(base=BASE):
    checksum = hashlib.sha256()
    for name in ProjectPaths(base).backend_files():
        path = Path(base) / name
        checksum.update(name.encode('utf-8'))
        checksum.update(path.read_bytes() if path.exists() else b'')
    return '2-' + checksum.hexdigest()[:12]


def error_result(e, command='client'):
    from .diagnostics import record_error
    try:
        diagnostic = record_error(command, e)
    except OSError:
        diagnostic = {'log_error': '报错日志无法写入，请检查 .state 目录权限'}
    if isinstance(e, ToolError):
        return {'ok': False, 'error': e.code, 'message': str(e), **e.details, **diagnostic}
    return {'ok': False, 'error': type(e).__name__, 'message': str(e)[:1500], **diagnostic}


class BrowserWorker:
    def __init__(self, base):
        self.engine = Engine(base)
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self.run, name='selenium-owner', daemon=True)
        self.thread.start()

    def run(self):
        while True:
            item = self.jobs.get()
            if item is None:
                self.engine.close()
                return
            command, args, future = item
            try:
                future.set_result({'ok': True, **self.engine.dispatch(command, args)})
            except Exception as e:
                future.set_result(error_result(e, command))

    def submit(self, command, args):
        future = concurrent.futures.Future()
        self.jobs.put((command, args, future))
        return future


def serve(base=BASE):
    base = Path(base)
    running_build = build_id(base)
    state = base / '.state'
    state.mkdir(parents=True, exist_ok=True)
    # OS lock is released on crashes; no stale-PID logic and no duplicate browsers.
    lock = (state / 'service.lock').open('a+b')
    try:
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            if lock.read(1) == b'':
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return
    token = secrets.token_urlsafe(32)
    worker = BrowserWorker(base)
    busy = threading.Lock()
    from .automation import AutoController
    automation = AutoController(worker)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            value.setdefault('backend_version', running_build)
            payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_POST(self):
            if self.path != '/rpc' or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.reply({'ok': False, 'error': 'unauthorized'}, 403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2000000:
                    raise ValueError('invalid request size')
                data = json.loads(self.rfile.read(length))
                command = data['command']
                args = data.get('args', {})
                if not isinstance(command, str) or not isinstance(args, dict):
                    raise ValueError('invalid request')
            except (ValueError, KeyError, TypeError) as e:
                self.reply({'ok': False, 'error': 'invalid_request', 'message': str(e)}, 400)
                return
            if command == 'ping':
                self.reply({'ok': True, 'service': 'educoder-agent', 'busy': busy.locked()})
                return
            if command == 'logs':
                from .diagnostics import read_logs
                try:
                    self.reply({'ok': True, **read_logs(args.get('tail', 30))})
                except (ValueError, TypeError) as e:
                    self.reply(error_result(ToolError('tail 必须为整数', 'invalid_input'), 'logs'))
                return
            if command == 'auto-status':
                try:
                    self.reply({'ok': True, **automation.status(**args)})
                except (ToolError, TypeError) as e:
                    self.reply(error_result(e, command))
                return
            if command == 'auto-stop':
                self.reply({'ok': True, **automation.stop()})
                return
            if command == 'pause':
                automation.stop()
                worker.engine.cancel.set()
                self.reply({'ok': True, 'message': '已请求暂停；浏览器操作在下个检查点停止'})
                return
            if command == 'shutdown':
                automation.stop()
                worker.engine.cancel.set()
                self.reply({'ok': True, 'message': '服务正在退出'})
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            if not busy.acquire(blocking=False):
                self.reply({'ok': False, 'error': 'busy', 'message': '已有操作正在执行；可运行 pause 暂停'})
                return
            if command == 'auto-start':
                try:
                    value = automation.start(**args, on_done=busy.release)
                    self.reply({'ok': True, **value})
                except Exception as e:
                    busy.release()
                    self.reply(error_result(e, command))
                return
            try:
                self.reply(worker.submit(command, args).result())
            finally:
                busy.release()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    runtime_path = state / 'service.json'
    write_json(runtime_path, {'port': server.server_port, 'token': token, 'pid': os.getpid(), 'backend_version': running_build})
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        automation.stop()
        if automation.thread:
            automation.thread.join(timeout=10)
        worker.engine.cancel.set()
        worker.jobs.put(None)
        worker.thread.join(timeout=55)
        server.server_close()
        current = load_json(runtime_path, {})
        if current.get('token') == token:
            remove_runtime(runtime_path)
        lock.close()


def rpc(command, args=None, base=BASE, timeout=240):
    runtime = load_json(Path(base) / '.state' / 'service.json', {})
    if not runtime:
        raise ToolError('本地服务未启动', 'service_not_running')
    request = Request('http://127.0.0.1:' + str(int(runtime['port'])) + '/rpc',
                      data=json.dumps({'command': command, 'args': args or {}}).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + runtime['token'], 'Content-Type': 'application/json'})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (URLError, OSError, HTTPError) as e:
        raise ToolError('无法连接本地服务或等待超时；操作可能仍在执行，请先 status/pause，避免重复提交',
                        'service_connection_failed') from e


def ensure_server(base=BASE):
    _background_services[:] = [process for process in _background_services if process.poll() is None]
    base = Path(base).resolve()
    wanted = build_id(base)
    try:
        info = rpc('ping', base=base, timeout=1)
    except (ToolError, ValueError, KeyError):
        info = None
    if info and info.get('ok') and info.get('backend_version') == wanted:
        return {'backend_version': wanted, 'updated': False}
    if info and info.get('ok'):
        # An old process can keep its imported Python and cached JS indefinitely.
        # Stop it gracefully so profile cookies flush; never kill arbitrary processes.
        previous = load_json(base / '.state' / 'service.json', {})
        rpc('shutdown', base=base, timeout=3)
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            try:
                current = load_json(base / '.state' / 'service.json', {})
            except PermissionError:
                # Windows can deny reads briefly while the old service removes this file.
                time.sleep(0.15)
                continue
            if not current or current.get('token') != previous.get('token'):
                break
            time.sleep(0.15)
        else:
            raise ToolError('旧服务仍在退出，未启动第二个浏览器。请稍后重试', 'service_upgrade_pending')
        time.sleep(0.2)
    state = base / '.state'
    state.mkdir(parents=True, exist_ok=True)
    kwargs = {'cwd': str(base), 'stdin': subprocess.DEVNULL}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True
    with (state / 'service.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, str(base / 'edu.py'), 'serve'], stdout=log, stderr=log, **kwargs)
        _background_services.append(process)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            ready = rpc('ping', base=base, timeout=0.5)
            if ready.get('ok') and ready.get('backend_version') == wanted:
                return {'backend_version': wanted, 'updated': bool(info)}
        except (ToolError, ValueError, KeyError):
            pass
        time.sleep(0.15)
    raise ToolError('本地服务启动失败，请检查 .state/service.log', 'service_start_failed')
