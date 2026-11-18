"""Entry point: python edu.py gui | login | context | input | test ..."""
import argparse
import json
import sys

from core import BASE, ToolError
from service import ensure_server, error_result, rpc, serve


def parser():
    p = argparse.ArgumentParser(description='头歌 Selenium 工具：Tkinter 窗口及精简 JSON 命令行')
    sub = p.add_subparsers(dest='command')
    for name in ('gui', 'serve', 'status', 'pause', 'close', 'stop', 'next-level'):
        sub.add_parser(name)
    for name in ('login', 'open'):
        sub.add_parser(name).add_argument('url', nargs='?')
    context = sub.add_parser('context', help='读取网页题目和当前代码；--since 获取变化部分')
    context.add_argument('--since')
    context.add_argument('--path', help='切换到文件树中可唯一定位的路径')
    sub.add_parser('input', help='从标准输入读取 JSON，直接写入网页、评测，通过后前进')
    auto = sub.add_parser('auto-start', help='用已登录的 Codex 自动解答勾选实验并根据网页结果纠错')
    auto.add_argument('--model', help='准确模型 ID；默认取 config.json 中 codex.model')
    sub.add_parser('auto-status', help='自动答题状态和内存中的模型输出').add_argument('--since', type=int, default=0)
    sub.add_parser('auto-stop', help='停止自动答题和模型子进程')
    sub.add_parser('logs', help='读取工具报错日志（不含实验源码）').add_argument('--tail', type=int, default=30)
    test = sub.add_parser('test', help='运行一次评测并等待；默认最多连续 5 次')
    test.add_argument('--timeout', type=int)
    test.add_argument('--reset-attempts', action='store_true')
    listing = sub.add_parser('list', help='读取课程实验列表，含分页')
    listing.add_argument('--current-page', action='store_true')
    selection = sub.add_parser('select', help='保存勾选范围；不带参数查看当前勾选')
    selection.add_argument('--ids', nargs='*', help='实验 ID；空列表表示全部不选')
    selection.add_argument('--unrestricted', action='store_true', help='取消勾选限制，恢复所有实验')
    selection.add_argument('--start', action='store_true', help='应用后进入勾选范围内的未完成实验')
    sub.add_parser('next', help='进入下一个明确未完成的实验').add_argument('--homework-id')
    sub.add_parser('submit', help='点击独立的提交作业按钮；评测使用 test').add_argument('--confirm', action='store_true')
    return p


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    args = vars(parser().parse_args())
    command = args.pop('command') or 'gui'
    if command == 'gui':
        from gui import launch
        launch()
        return 0
    if command == 'serve':
        serve()
        return 0
    try:
        if command == 'input':
            raw = sys.stdin.buffer.read(2000001) if hasattr(sys.stdin, 'buffer') else sys.stdin.read(2000001).encode('utf-8')
            if len(raw) > 2000000:
                raise ToolError('输入 JSON 超过 2 MB', 'invalid_input')
            args = parse_input(raw.decode('utf-8-sig'))
        if command not in ('stop', 'pause', 'auto-stop'):
            ensure_server()
        if command == 'list':
            args = {'all_pages': not args.pop('current_page')}
        if command == 'stop':
            command = 'shutdown'
        if command == 'test' and args.get('timeout') is not None and not 1 <= args['timeout'] <= 3600:
            raise ToolError('--timeout 必须为 1 到 3600 秒', 'invalid_timeout')
        args = {k: v for k, v in args.items() if v is not None}
        timeout = 900 if command == 'input' else max(300, args.get('timeout', 180) + 70) if command == 'test' else 300
        result = rpc(command, args, timeout=timeout)
    except Exception as e:
        result = error_result(e, command)
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return 0 if result.get('ok') else 1


def parse_input(raw):
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ToolError('标准输入必须为 UTF-8 JSON 对象', 'invalid_input') from e
    if not isinstance(data, dict) or set(data) - {'code', 'task_id', 'path', 'expected', 'evaluate', 'advance', 'answers'}:
        raise ToolError('仅接受代码或选择题答案、task_id 及评测选项字段', 'invalid_input')
    if not isinstance(data.get('task_id'), str) or not data['task_id']:
        raise ToolError('必须提供 task_id', 'invalid_input')
    if any(k in data and not isinstance(data[k], bool) for k in ('evaluate', 'advance')):
        raise ToolError('evaluate 和 advance 必须为布尔值', 'invalid_input')
    if 'answers' in data:
        if any(k in data for k in ('code', 'path', 'expected')) or not isinstance(data['answers'], list) or not data['answers']:
            raise ToolError('选择题 answers 必须为非空数组，不能同时提供代码', 'invalid_input')
    elif not isinstance(data.get('code'), str) or not isinstance(data.get('path'), str) or not data['path']:
        raise ToolError('必须提供字符串 code 和 path', 'invalid_input')
    data['task'] = data.pop('task_id')
    return data


if __name__ == '__main__':
    raise SystemExit(main())
