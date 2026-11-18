"""Real headless Edge test of the JS adapter against a local deterministic fixture."""
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from educoder_agent.core import Engine


def main():
    engine = Engine()
    engine.config = engine.config | {'headless': True}
    if '--in-process-gpu' in sys.argv:
        engine.config['browser_arguments'] = ['--in-process-gpu']
    # Separate test profile so the user's visible login browser can stay open.
    engine.root = engine.root / 'browser-smoke'
    engine.root.mkdir(parents=True, exist_ok=True)
    code = ''.join(f'// complete model line {i}\n' for i in range(1200))
    fixture = '''<!doctype html><meta charset="utf-8">
    <div id="task-left-panel"><div class="markdown">Full problem description.</div></div>
    <div id="task-right-panel"><div class="monaco-editor">Only one visible line</div>
    <div class="test-result">old result</div><button onclick="window.ran=true">评测</button></div>
    <script>
    window.code = INITIAL_CODE;
    window.locked = false;
    const model = {getValue:()=>window.code, getLanguageId:()=> 'cpp'};
    const editor = {getModel:()=>model, setValue:v=>window.code=v,
      getRawOptions:()=>({readOnly:window.locked})};
    const props = {monacoEditor:{onSave:(evaluate,v)=>{if(evaluate!==0) throw Error('invalid evaluate'); window.saved=v; return {content:v};}},taskData:{
      challenge:{task_pass:'Compute a+b.',subject:'Addition'},game:{identifier:'fixture',status:0}},
      codeRepData:{currentPath:'src/main.cpp', paths:['src/main.cpp']},evaluateLoading:false};
    document.querySelector('.monaco-editor')['__reactFiber$fixture']={
      memoizedProps:props,memoizedState:{memoizedState:editor,next:null},return:null};
    </script>'''.replace('INITIAL_CODE', json.dumps(code))
    try:
        engine.start()
        engine.browser.get('data:text/html;charset=utf-8,' + quote(fixture))
        snap = engine.js('snapshot', **engine.config['selectors'])
        assert snap['content'] == code, 'must read full model, not visible editor lines'
        assert snap['description'] == 'Compute a+b.'
        assert snap['current_path'] == 'src/main.cpp'
        engine.js('set_code', code='int main() {}\n')
        engine.js('save')
        assert engine.browser.execute_script('return window.saved') == 'int main() {}\n'
        engine.js('evaluate').click()
        assert engine.browser.execute_script('return window.ran') is True
        engine.browser.execute_script('window.locked=true')
        try:
            engine.js('set_code', code='must not write')
            raise AssertionError('read-only editor must reject writes')
        except AssertionError:
            raise
        except Exception:
            pass
        assert engine.browser.execute_script('return window.code') == 'int main() {}\n'
        print('PASS: real Edge, full 1200-line model, task extraction, edit/save callback, evaluation button, read-only guard')
    finally:
        engine.close()


if __name__ == '__main__':
    main()
