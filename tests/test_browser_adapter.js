// Deterministic adapter checks: simulated React/editor metadata, no lab code execution.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const run = new Function(fs.readFileSync(require('node:path').join(__dirname, '../browser.js'), 'utf8'));
let value = 'synthetic model contents\n'.repeat(1200);
const model = {getValue: () => value, getLanguageId: () => 'plaintext'};
const node = title => ({title, parentElement: null, getClientRects: () => [1],
  getAttribute(key) { return key === 'title' ? this.title : null; },
  contains(other) { return other === this; }, closest() { return this; }});
const host = node(), monaco = node(), panel = node(), file = node('file.txt');
monaco.parentElement = host;
const editor = {getModel: () => model, getDomNode: () => monaco,
  setValue: v => { value = v; }, getRawOptions: () => ({readOnly: false})};
let saved, saveMode = 'normal';
host['__reactFiber$fixture'] = {memoizedProps: {
  currentPath: 'dir/file.txt', content: value,
  monacoEditor: {onSave: (evaluate, content) => {
    assert.equal(evaluate, 0, 'saving must not evaluate');
    saved = content;
    if (saveMode === 'skip') return true;
    if (saveMode === 'reject') return {status: -1, content, sec_key: 'private-fixture'};
    if (saveMode === 'mismatch') return {content: 'stale fixture', sec_key: 'private-fixture'};
    return {content, resubmit: false, sec_key: 'private-fixture', content_modified: true};
  }}},
  memoizedState: {memoizedState: {current: editor}, next: null}, return: {
    memoizedProps: {codeRepData: {paths: ['dir/file.txt']}, taskData: {
      challenge: {subject: 'fixture', task_pass: 'fixture description'}, game: {identifier: 'fixture', status: 0}}},
    memoizedState: null, return: null}};
global.getComputedStyle = () => ({visibility: 'visible'});
global.location = {href: 'https://www.educoder.net/tasks/C/H/fixture', pathname: '/tasks/C/H/fixture'};
global.window = {};
let files = [file];
let evaluationButtons = [], clicks = 0;
let choiceInputs = [];
const evaluationButton = {...node(), innerText: '评 测', disabled: false,
  getAttribute(key) { return key === 'title' ? '运行评测' : null; },
  click() { clicks++; }};
const nextLink = {...node(), innerText: '下一关', getAttribute(key) {
  return key === 'href' ? '/tasks/following' : null;
}, click() { throw Error('evaluation panel intercepts this click'); }};
global.document = {title: 'fixture', querySelectorAll(selector) {
  if (selector.includes('.monaco-editor')) return [monaco, panel];
  if (selector === '#task-right-panel [title], #task-right-panel [role="treeitem"]') return files;
  if (selector === 'a[href*="/tasks/"]') return [nextLink];
  if (selector === 'button, a, [role="button"]') return evaluationButtons;
  if (selector === '#task-right-panel input[type="radio"], #task-right-panel input[type="checkbox"]') return choiceInputs;
  return [];
}};
(async () => {
  const snapshot = await run('snapshot', {});
  assert.equal(snapshot.editor_available, true);
  assert.equal(snapshot.content, value);
  assert.equal(snapshot.current_path, 'dir/file.txt');
  assert.deepEqual(snapshot.files, ['dir/file.txt']);
  assert.equal(snapshot.description, 'fixture description');
  assert.equal(await run('select_file', {path: 'dir/file.txt'}), file);
  files = [file, node('file.txt')];
  assert.equal(await run('select_file', {path: 'dir/file.txt'}), null);
  const changed = await run('set_code', {code: 'synthetic replacement'});
  assert.equal(changed.changed, true);
  const receipt = await run('save', {});
  assert.equal(saved, 'synthetic replacement');
  assert.deepEqual(receipt, {callback: true, path: 'dir/file.txt', rejected: false, content_matches: true});
  assert.equal(JSON.stringify(receipt).includes('private-fixture'), false);
  assert.equal(JSON.stringify(receipt).includes(saved), false);
  saveMode = 'skip';
  assert.equal((await run('save', {})).content_matches, null);
  saveMode = 'reject';
  assert.equal((await run('save', {})).rejected, true);
  saveMode = 'mismatch';
  assert.equal((await run('save', {})).content_matches, false);
  assert.equal(await run('task_next_url', {}), '/tasks/following');
  const evaluateArgs = {task_id: 'fixture', path: 'dir/file.txt'};
  assert.equal((await run('evaluate_click', evaluateArgs)).reason, 'missing');
  evaluationButtons = [evaluationButton];
  evaluationButton.disabled = true;
  assert.equal((await run('evaluate_click', evaluateArgs)).reason, 'disabled');
  assert.equal(clicks, 0);
  evaluationButton.disabled = false;
  evaluationButtons = [evaluationButton, {...evaluationButton}];
  assert.equal((await run('evaluate_click', evaluateArgs)).reason, 'ambiguous');
  assert.equal(clicks, 0);
  evaluationButtons = [evaluationButton];
  assert.equal((await run('evaluate_click', {...evaluateArgs, task_id: 'other'})).reason, 'task_mismatch');
  assert.equal((await run('evaluate_click', {...evaluateArgs, path: 'other/file'})).reason, 'file_loading');
  assert.equal(clicks, 0);
  assert.equal((await run('evaluate_click', evaluateArgs)).clicked, true);
  assert.equal(clicks, 1);
  const task = host['__reactFiber$fixture'].return.memoizedProps.taskData;
  task.challenge.st = 1;
  task.chooses = [
    {challenge_choose_id: 10, subject: 'single fixture', category: 1,
      challenge_question: [{position: 0, option_name: 'a'}, {position: 1, option_name: 'b'}]},
    {challenge_choose_id: 20, subject: 'multi fixture', category: 2,
      challenge_question: [{position: 2, option_name: 'c'}, {position: 3, option_name: 'd'}]}];
  host['__reactFiber$fixture'].memoizedState = null; // choice page has no Monaco
  let checked = [true, false, true, false], choiceClicks = 0;
  function renderInputs() {
    choiceInputs = checked.map((value, index) => ({...node(), checked: value, disabled: false,
      click() {
        choiceClicks++;
        if (index < 2) { checked[0] = checked[1] = false; checked[index] = true; }
        else checked[index] = !checked[index];
        renderInputs(); // React replaces nodes after a selection
      }}));
  }
  renderInputs();
  const choiceSnapshot = await run('snapshot', {});
  assert.equal(choiceSnapshot.editor_available, false);
  assert.equal(choiceSnapshot.task_type, 'choice');
  assert.equal(choiceSnapshot.choice_ready, true);
  const choices = {task_id: 'fixture', answers: [
    {question_id: '10', positions: [1]}, {question_id: '20', positions: [3]}]};
  assert.deepEqual(await run('set_choices', choices), {selected: true});
  assert.deepEqual(checked, [false, true, false, true]);
  assert.equal(choiceClicks, 3);
  await assert.rejects(run('set_choices', {...choices, task_id: 'other'}));
  await assert.rejects(run('set_choices', {...choices, answers: [choices.answers[0], choices.answers[0]]}));
  assert.equal(choiceClicks, 3);
  assert.equal((await run('evaluate_click', {task_id: 'fixture'})).clicked, true);
  assert.equal(clicks, 2);
  console.log('PASS: editor/save, guarded JS evaluation, choice extraction and selection across React renders');
})().catch(error => { console.error(error); process.exitCode = 1; });
