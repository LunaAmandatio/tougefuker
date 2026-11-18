// Runs inside the existing page. Only returns the task fields needed by the agent.
return (async function (op, arg) {
  const visible = e => !!e && !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e && (e.innerText || e.textContent) || '').trim();
  const query = selector => selector ? [...document.querySelectorAll(selector)].filter(visible) : [];
  const unique = xs => [...new Set(xs)];
  const state = {task: null, rep: null, fileProps: null, loading: null, editor: null, editorProps: null};
  const seen = new Set();
  function inspect(x, depth = 0) {
    if (!x || typeof x !== 'object') return;
    if (x.taskData && x.taskData.challenge && x.taskData.game) state.task = x.taskData;
    if (x.codeRepData) state.rep = x.codeRepData;
    if (typeof x.currentPath === 'string' && typeof x.content === 'string') state.fileProps = x;
    if (typeof x.evaluateLoading === 'boolean') state.loading = x.evaluateLoading;
    if (x.monacoEditor && typeof x.monacoEditor.onSave === 'function') state.editorProps = x.monacoEditor;
    if (typeof x.getModel === 'function' && typeof x.setValue === 'function') {
      try {
        const node = x.getDomNode && x.getDomNode();
        if (x.getModel() && (!node || (visible(node) && node.closest('#task-right-panel')))) state.editor = x;
      } catch (_) { /* Disposed editor refs are not usable. */ }
    }
    if (depth < 2) {
      for (const key of ['current', 'editor', 'editorRef', 'monacoEditor', 'codeRepData']) {
        if (x[key] && x[key] !== x) inspect(x[key], depth + 1);
      }
    }
  }
  // The site's editor is module-scoped; React exposes the mounted editor's normal callbacks.
  // Inspect a bounded set of ancestors/hooks rather than serializing the React tree.
  const needsEditor = ['snapshot', 'set_code', 'save', 'evaluate_click', 'select_file', 'set_choices'].includes(op);
  for (const el of needsEditor ? query('#task-right-panel .monaco-editor, #task-right-panel, #task-left-panel, .task-header') : []) {
    // Monaco creates its DOM itself, so the React key is usually on its parent host.
    let host = el, fiber = null;
    for (let i = 0; host && i < 12; i++, host = host.parentElement) {
      const key = Object.keys(host).find(k => k.startsWith('__reactFiber$'));
      if (key) { fiber = host[key]; break; }
    }
    for (let depth = 0; fiber && depth < 65; depth++, fiber = fiber.return) {
      if (seen.has(fiber)) continue;
      seen.add(fiber);
      inspect(fiber.memoizedProps);
      let hook = fiber.memoizedState;
      const hooks = new Set();
      for (let i = 0; hook && i < 100 && !hooks.has(hook); i++, hook = hook.next) {
        hooks.add(hook); inspect(hook); inspect(hook.memoizedState);
      }
    }
  }
  if (!state.editor && window.monaco && window.monaco.editor && window.monaco.editor.getEditors) {
    const editors = window.monaco.editor.getEditors().filter(e => {
      const node = e.getDomNode();
      return visible(node) && !!node.closest('#task-right-panel');
    });
    if (editors.length === 1) state.editor = editors[0];
  }
  const model = state.editor && state.editor.getModel();
  let rep = {...(state.rep || {}), ...(state.fileProps || {})};
  const task = state.task || {};
  const challenge = task.challenge || {};
  const game = task.game || {};
  const props = state.editorProps || {};
  const choiceTask = challenge.st === 1 || (Array.isArray(task.chooses) && task.chooses.length > 0);
  const choiceQuestions = (task.chooses || []).map(c => ({
    id: String(c.challenge_choose_id), subject: c.subject, multiple: c.category === 2,
    options: (c.challenge_question || []).map(o => ({position: o.position, text: o.option_name}))
  }));
  const choiceInputs = () => query('#task-right-panel input[type="radio"], #task-right-panel input[type="checkbox"]');
  let currentPath = rep.currentPath || '';
  const readOnly = state.editor && state.editor.getRawOptions
    ? !!state.editor.getRawOptions().readOnly : null;
  const dialogs = () => query('[role="dialog"], .ant-modal-content').map(text);
  function button(labels, selector, scope) {
    const candidates = selector ? query(selector) : query((scope || '') + ' button, ' +
      (scope || '') + ' a, ' + (scope || '') + ' [role="button"]')
      .filter(e => labels.includes(text(e)) || labels.includes(e.getAttribute('title')));
    const enabled = unique(candidates).filter(e => !e.disabled && e.getAttribute('aria-disabled') !== 'true');
    if (enabled.length !== 1) throw Error('按钮不存在、不可用或不唯一: ' + labels.join('/') + ' (' + enabled.length + ')');
    return enabled[0];
  }
  if (op === 'snapshot') {
    const descriptions = challenge.task_pass ? [] : query(arg.description);
    const renderedDescription = descriptions.sort((a, b) => text(b).length - text(a).length)[0];
    const tree = rep.paths && rep.paths.length ? [] :
      query('#task-right-panel [role="treeitem"], #task-right-panel .ant-tree-treenode');
    return {
      url: location.href, title: document.title,
      task_id: game.identifier || location.pathname.split('/').filter(Boolean).pop(),
      subject: challenge.subject || '', description: challenge.task_pass || text(renderedDescription),
      task_available: !!state.task,
      task_type: choiceTask ? 'choice' : 'code', choices: choiceQuestions,
      ...(choiceTask ? {feedback_visible: task.shixun_choice_public_result !== false} : {}),
      choice_ready: choiceTask && choiceInputs().length === choiceQuestions.reduce((n, q) => n + q.options.length, 0),
      experiment: {homework_id: String(task.homework_common_id || location.pathname.split('/')[3] || ''),
        title: task.homework_common_name || document.title, position: challenge.position,
        total: task.shixun && task.shixun.challenges_count},
      game_status: game.status === undefined ? null : game.status,
      next_task: task.next_game || null,
      is_last_game: typeof task.is_last_game === 'boolean' ? task.is_last_game :
        (Object.prototype.hasOwnProperty.call(task, 'next_game') ? task.next_game === null : null),
      current_path: currentPath,
      content: model ? model.getValue() : (typeof rep.content === 'string' ? rep.content : null),
      language: model && model.getLanguageId ? model.getLanguageId() : rep.language || '',
      editor_available: !!model, read_only: readOnly,
      files: rep.paths || [],
      tree: unique(tree.map(e => e.getAttribute('title') || text(e)).filter(Boolean)),
      evaluating: state.loading,
      result: unique(query(arg.result).map(text)).join('\n'),
      test_sets: (task.test_sets || []).map(t => ({
        result: t.result, input: t.input, output: t.output, actual_output: t.actual_output,
        compile_success: t.compile_success
      })),
      message: task.message || '', dialogs: dialogs()
    };
  }
  if (op === 'set_code') {
    if (!model || !state.editor) throw Error('没有可用的 Monaco 编辑器，请在浏览器中打开代码文件');
    if (readOnly) throw Error('当前文件只读');
    state.editor.setValue(arg.code);
    return {changed: model.getValue() === arg.code, path: currentPath};
  }
  if (op === 'set_choices') {
    if (!choiceTask || location.pathname.split('/').pop() !== arg.task_id || game.identifier !== arg.task_id) {
      throw Error('选择题关卡已经改变');
    }
    const inputs = choiceInputs;
    const total = choiceQuestions.reduce((n, q) => n + q.options.length, 0);
    if (inputs().length !== total || arg.answers.length !== choiceQuestions.length) throw Error('选择题选项尚未完整加载');
    const selections = new Map(arg.answers.map(a => [String(a.question_id), a.positions]));
    if (selections.size !== choiceQuestions.length || choiceQuestions.some(q => !selections.has(q.id))) throw Error('选择题编号不匹配');
    const plan = [];
    let offset = 0;
    for (const q of choiceQuestions) {
      const selected = selections.get(q.id);
      if (!Array.isArray(selected) || !selected.length || (!q.multiple && selected.length !== 1) ||
          new Set(selected).size !== selected.length ||
          selected.some(p => !Number.isInteger(p) || !q.options.some(o => o.position === p))) throw Error('选择题答案格式错误');
      q.options.forEach((o, i) => plan.push({index: offset + i, selected: selected.includes(o.position), multiple: q.multiple}));
      offset += q.options.length;
    }
    if (inputs().some(input => input.disabled)) throw Error('选择题选项不可用');
    for (const item of plan) {
      const input = inputs()[item.index];
      if (!input || input.disabled) throw Error('选择题选项不可用');
      if (input.checked !== item.selected && (item.multiple || item.selected)) {
        input.click();
        await new Promise(resolve => setTimeout(resolve, 0));
      }
    }
    const applied = inputs();
    return {selected: plan.every(p => applied[p.index] && applied[p.index].checked === p.selected)};
  }
  if (op === 'save') {
    if (readOnly) throw Error('当前文件只读');
    if (arg.selector) return button([], arg.selector);
    if (typeof props.onSave === 'function') {
      const content = model ? model.getValue() : rep.content;
      // Educoder's signature is (evaluate = 0, content, force = false).
      // Keep the response (including content and sec_key) inside the page.
      const response = await props.onSave(0, content);
      const object = response && typeof response === 'object' ? response : {};
      return {callback: true, path: currentPath,
        rejected: object.status !== undefined && object.status !== 0,
        content_matches: typeof object.content === 'string' ? object.content === content : null};
    }
    return button(['保存', '保存代码']);
  }
  if (op === 'evaluate') return button(['评测', '重新评测'], arg.selector);
  if (op === 'evaluate_click') {
    const pageTask = location.pathname.split('/').filter(Boolean).pop();
    if (arg.task_id !== pageTask || (game.identifier && arg.task_id !== game.identifier)) {
      return {clicked: false, reason: 'task_mismatch'};
    }
    if (arg.path && currentPath !== arg.path) return {clicked: false, reason: 'file_loading'};
    if (!model && !choiceTask) return {clicked: false, reason: 'editor_loading'};
    if (state.loading === true) return {clicked: false, reason: 'evaluating'};
    const labels = ['评测', '重新评测', '运行评测'];
    const normalized = value => (value || '').replace(/\s+/g, '');
    const candidates = unique(arg.selector ? query(arg.selector) : query('button, a, [role="button"]')
      .filter(e => [text(e), e.getAttribute('title'), e.getAttribute('aria-label')]
        .some(value => labels.includes(normalized(value)))));
    const enabled = candidates.filter(e => !e.disabled && e.getAttribute('disabled') === null &&
      e.getAttribute('aria-disabled') !== 'true' && e.getAttribute('aria-busy') !== 'true' &&
      !/(?:^|\s)(?:disabled(?:___\S+)?|ant-btn-disabled|ant-btn-loading)(?:\s|$)/.test(e.className || ''));
    const receipt = {clicked: false, candidates: candidates.length, enabled: enabled.length};
    if (enabled.length !== 1) return {...receipt,
      reason: enabled.length > 1 ? 'ambiguous' : (candidates.length ? 'disabled' : 'missing')};
    // Check and click in one page operation. Never retry after a click may have been dispatched.
    enabled[0].click();
    return {clicked: true, task_id: arg.task_id};
  }
  if (op === 'submit') return button(['提交作业', '提交实验'], arg.selector);
  if (op === 'confirm_submit') return button(['确定', '确认', '确认提交'], '', '[role="dialog"]');
  if (op === 'select_file') {
    const nodes = query('#task-right-panel [title], #task-right-panel [role="treeitem"]');
    let exact = nodes.filter(e => e.getAttribute('title') === arg.path);
    if (!exact.length) exact = nodes.filter(e => text(e) === arg.path);
    if (!exact.length) {
      // Some trees render basenames while retaining the full path in React data.
      exact = nodes.filter(e => {
        const key = Object.keys(e).find(k => k.startsWith('__reactFiber$'));
        let f = key && e[key];
        for (let i = 0; f && i < 6; i++, f = f.return) {
          const p = f.memoizedProps || {};
          if ([p.eventKey, p.path, p.data && p.data.key, p.data && p.data.path].includes(arg.path)) return true;
        }
        return false;
      });
    }
    if (!exact.length) {
      const basename = arg.path.split('/').pop();
      const paths = (rep.paths || []).filter(p => typeof p === 'string');
      if (paths.includes(arg.path) && paths.filter(p => p.split('/').pop() === basename).length === 1) {
        exact = nodes.filter(e => e.getAttribute('title') === basename || text(e) === basename);
      }
    }
    // Prefer the innermost exact node; never guess between files sharing a basename.
    exact = exact.filter(e => !exact.some(other => other !== e && e.contains(other)));
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) return null;
    // Expand a collapsed parent directory, then let the next poll locate the file.
    const parts = arg.path.split('/');
    for (let i = 0; i < parts.length - 1; i++) {
      const prefix = parts.slice(0, i + 1).join('/');
      const folder = nodes.filter(e => [prefix, parts[i]].includes(e.getAttribute('title')))
        .map(e => e.closest('.ant-tree-treenode, [role="treeitem"]'))
        .filter(Boolean).map(e => e.querySelector('.ant-tree-switcher_close, [aria-expanded="false"]'))
        .filter(visible);
      if (unique(folder).length === 1) return folder[0];
    }
    return null;
  }
  if (op === 'course') {
    const cards = [];
    // Actual student cards use spans for titles and an <aside> for "开始学习".
    // /shixun_homework/<id> links in the sidebar are directory IDs, not homework IDs.
    const labels = ['开始学习','继续学习','开启挑战','继续挑战','开始实验','继续实验','进入实验','开始实训','继续实训'];
    for (const node of query('[class*="info___"], [data-homework-id]')) {
      let record = null;
      const k = Object.keys(node).find(k => k.startsWith('__reactFiber$'));
      let f = k && node[k];
      for (let j = 0; f && j < 15 && !record; j++, f = f.return) {
          const p = f.memoizedProps || {};
          for (const v of Object.values(p)) {
            if (v && typeof v === 'object' && v.homework_id && v.name &&
                (v.is_shixun || v.shixun_identifier || v.checked_challenge_count !== undefined)) {
              record = v; break;
            }
          }
      }
      if (!record) continue;
      const t = text(node);
      const buttons = [...node.querySelectorAll('aside,a,button,[role="button"]')].filter(visible);
      const entry = buttons.find(e => labels.includes(text(e)) && !e.disabled &&
        e.getAttribute('aria-disabled') !== 'true' && record.is_enter_shixun !== false);
      let complete = null;
      if (Number(record.checked_challenge_count) > 0 && record.finished_challenge_count !== undefined) {
        complete = Number(record.finished_challenge_count) >= Number(record.checked_challenge_count);
      }
      const id = String(record.homework_id);
      const course = location.pathname.match(/\/classrooms\/([^/]+)/);
      if (!cards.some(c => c.id === id)) cards.push({
        id, title: record.name,
        url: location.origin + '/classrooms/' + (course ? course[1] : '') + '/shixun_homework/' + id + '/detail',
        complete, entry: entry || null,
        finished: record.finished_challenge_count, total: record.checked_challenge_count,
        status: record.status, summary: t.slice(0, 400)
      });
    }
    return cards;
  }
  if (op === 'next_page') {
    const next = query('.ant-pagination-next:not(.ant-pagination-disabled) button');
    return next.length === 1 ? next[0] : null;
  }
  if (op === 'task_next' || op === 'task_next_url') {
    const next = query('a[href*="/tasks/"]').find(e => ['下一关','下一任务'].includes(text(e)) &&
      e.getAttribute('aria-disabled') !== 'true');
    const links = query('.task-item-container:not(.finish) a[href*="/tasks/"]');
    const target = next || links.find(e => !e.closest('.task-item-container').classList.contains('active')) || null;
    // The evaluation panel can cover the link. Read its destination instead of clicking through it.
    return op === 'task_next_url' ? (target && target.getAttribute('href')) : target;
  }
  if (op === 'notice') return {
    url: location.href, dialogs: dialogs(), messages: unique(query('.ant-message-notice, .ant-notification-notice').map(text)),
    submit_success: arg.selector ? query(arg.selector).map(text) : []
  };
  throw Error('未知网页操作: ' + op);
})(arguments[0], arguments[1] || {});
