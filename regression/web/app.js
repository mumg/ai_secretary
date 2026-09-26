const $ = id => document.getElementById(id);
let selectedCase = null;
let result = null;
let allCases = [];
let allRuns = [];

async function json(url, options) {
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error || `HTTP ${response.status}`);
  return value;
}

function button(className, title, subtitle, click) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = className;
  const strong = document.createElement('strong');
  strong.textContent = title;
  const span = document.createElement('span');
  span.textContent = subtitle;
  item.append(strong, span);
  if (click) item.addEventListener('click', click);
  return item;
}

function setStatus(message, error = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
}

function matchesFilter(meta) {
  const kind = $('kind-filter').value;
  const tasks = meta?.task_count || 0;
  const delegations = meta?.delegation_count || 0;
  if ($('hide-matched').checked && meta?.comparison === 'matched') return false;
  if (kind === 'unreviewed_tasks') return !!meta && tasks > 0 && !meta.review;
  return kind === 'all' || (meta && (
    (kind === 'tasks' && tasks > 0) ||
    (kind === 'delegations' && delegations > 0) ||
    (kind === 'any' && (tasks > 0 || delegations > 0)) ||
    (kind === 'none' && tasks === 0 && delegations === 0)));
}

function comparisonLabel(meta) {
  if (meta?.comparison === 'diverged') return 'Расхождение с проверкой';
  if (meta?.comparison === 'matched') return 'Совпадает с проверкой';
  if (meta?.legacy_review) return 'Нужна новая оценка';
  return 'Без проверки';
}

function renderLists() {
  $('cases').replaceChildren();
  const kind = $('kind-filter').value;
  const visibleCases = allCases.filter(entry => kind === 'unanalyzed'
    ? entry.valid && !entry.current_result
    : matchesFilter(entry.latest_result));
  for (const entry of visibleCases) {
    const name = entry.file.replace(/\.json$/, '');
    const meta = entry.latest_result;
    const counts = entry.current_result ? `Задач ${meta.task_count}, поручений ${meta.delegation_count}`
      : meta ? 'Текущей моделью ещё не анализировалось' : 'Ещё не анализировалось';
    const item = button('item' + (entry.valid ? '' : ' invalid') + (meta?.comparison === 'diverged' ? ' diverged' : ''),
      entry.valid ? (entry.subject || '(без темы)') : entry.file,
      entry.valid ? `${entry.direction === 'OUTGOING' ? 'Исходящее' : 'Входящее'} · ${counts} · ${comparisonLabel(meta)}` : entry.error,
      entry.valid ? () => openCase(name) : null);
    if (selectedCase === name) item.classList.add('active');
    $('cases').append(item);
  }
  if (!$('cases').children.length) $('cases').textContent = 'Нет писем для выбранного фильтра.';
  $('runs').replaceChildren();
  const runs = kind === 'unanalyzed' ? [] : allRuns.filter(matchesFilter);
  if (!runs.length) {
    const text = document.createElement('p');
    text.className = 'muted';
    text.textContent = 'Нет запусков для выбранного фильтра';
    $('runs').append(text);
  }
  for (const run of runs) {
    const verdict = {agree: 'Согласовано', disagree: 'Есть замечания'}[run.review] || 'Без оценки';
    $('runs').append(button('item' + (run.comparison === 'diverged' ? ' diverged' : ''),
      run.subject || '(без темы)',
      `${run.ran_at.slice(0, 16)} · ${run.model} · задач ${run.task_count}, поручений ${run.delegation_count} · ${verdict} · ${comparisonLabel(run)}`,
      () => openResult(run.case, run.run_id)));
  }
}

async function refresh() {
  [allCases, allRuns] = await Promise.all([json('/api/cases'), json('/api/results')]);
  renderLists();
}

function showLetter(item) {
  $('empty').hidden = true;
  $('content').hidden = false;
  $('subject').textContent = item.subject;
  $('direction').textContent = item.direction === 'OUTGOING' ? 'Исходящее' : 'Входящее';
  const people = (item.participants || []).map(p => `${p.role || 'participant'}: ${p.name || ''} ${p.address || p.email || ''}`).join('\n');
  $('letter').textContent = `От: ${item.author}\nТема: ${item.subject}\n${people}\n\n${item.body}`;
}

async function openCase(name) {
  selectedCase = name;
  result = null;
  const item = await json('/api/cases/' + encodeURIComponent(name));
  showLetter(item);
  $('run-meta').textContent = '';
  $('tasks').replaceChildren();
  $('delegations').replaceChildren();
  $('task-count').textContent = '';
  $('delegation-count').textContent = '';
  $('raw').textContent = '';
  $('comparison').hidden = true;
  $('review-panel').hidden = true;
  setStatus('Готово к запуску анализа.');
  await refresh();
}

function line(label, text) {
  const p = document.createElement('p');
  const strong = document.createElement('strong');
  strong.textContent = label + ': ';
  p.append(strong, document.createTextNode(text || 'не указано'));
  return p;
}

function renderCards(container, items, kind) {
  container.replaceChildren();
  if (!items.length) {
    const none = document.createElement('div');
    none.className = 'none';
    none.textContent = 'Модель не предложила записей.';
    container.append(none);
    return;
  }
  for (const item of items) {
    const card = document.createElement('article');
    card.className = 'card';
    const heading = document.createElement('h4');
    heading.textContent = item.title || 'Без названия';
    card.append(heading);
    const assignee = kind === 'task' ? (item.assignee === 'user' ? 'Вы' : item.assignee || 'не установлен') :
      [item.assignee_name, item.assignee_email].filter(Boolean).join(' · ') || 'не установлен';
    card.append(line('Исполнитель', assignee));
    if (kind === 'task' && item.assignment_evidence) card.append(line('Основание назначения', item.assignment_evidence));
    if (item.confidence !== undefined && item.confidence !== null) card.append(line('Уверенность модели', `${Math.round(Number(item.confidence) * 100)}%`));
    if (item.evidence) {
      const quote = document.createElement('blockquote');
      quote.textContent = item.evidence;
      card.append(quote);
    }
    container.append(card);
  }
}

function renderRun(data) {
  result = data;
  selectedCase = data.case_file.replace(/\.json$/, '');
  showLetter(data.input);
  $('run-meta').textContent = `${data.ran_at.slice(0, 16)} · ${data.model.provider} / ${data.model.model}`;
  const comparison = data.comparison || {};
  $('comparison').hidden = comparison.status === 'unchecked' || !comparison.status;
  $('comparison').classList.toggle('diverged', comparison.status === 'diverged');
  $('comparison').textContent = comparison.status === 'diverged'
    ? 'Предложения модели отличаются от вашей проверки.'
    : comparison.status === 'matched' ? 'Предложения модели совпадают с вашей проверкой.' : '';
  const suggestions = data.suggestions;
  renderCards($('tasks'), suggestions.tasks, 'task');
  renderCards($('delegations'), suggestions.delegations, 'delegation');
  $('task-count').textContent = `${suggestions.tasks.length} предложено`;
  $('delegation-count').textContent = `${suggestions.delegations.length} предложено`;
  $('raw').textContent = JSON.stringify(data.raw, null, 2);
  $('review-panel').hidden = false;
  const review = data.review?.scope === 'suggestions_v1' ? data.review : {};
  document.querySelectorAll('input[name=verdict]').forEach(input => { input.checked = input.value === review.verdict; });
  $('reason').value = review.reason || '';
  $('expected-tasks').value = review.expected_tasks || '';
  $('expected-delegations').value = review.expected_delegations || '';
  $('review-state').textContent = review.reviewed_at ? `Сохранено ${review.reviewed_at.slice(0, 16)}`
    : data.review?.verdict ? 'Старая оценка относится к прежним правилам; оцените предложения заново' : 'Ожидает вашей оценки';
  setStatus('Результат сохранён в regression/results.');
}

async function openResult(name, runId) {
  try {
    renderRun(await json(`/api/results/${encodeURIComponent(name)}/${encodeURIComponent(runId)}`));
    await refresh();
  } catch (error) { setStatus(error.message, true); }
}

$('run').addEventListener('click', async () => {
  if (!selectedCase) return;
  $('run').disabled = true;
  setStatus('Модель анализирует письмо. Это может занять несколько минут…');
  try {
    const data = await json('/api/run', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({case: selectedCase})});
    await openResult(selectedCase, data.run_id);
  } catch (error) { setStatus(error.message, true); }
  finally { $('run').disabled = false; }
});

$('save-review').addEventListener('click', async () => {
  if (!result) return;
  const verdict = document.querySelector('input[name=verdict]:checked')?.value;
  if (!verdict) { setStatus('Выберите «Согласен» или «Не согласен».', true); return; }
  const payload = {case: selectedCase, run_id: result.run_id, verdict, reason: $('reason').value,
    expected_tasks: $('expected-tasks').value, expected_delegations: $('expected-delegations').value};
  try {
    await json('/api/review', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)});
    await openResult(selectedCase, result.run_id);
    setStatus('Ваша оценка сохранена.');
  } catch (error) { setStatus(error.message, true); }
});

$('refresh').addEventListener('click', () => refresh().catch(error => setStatus(error.message, true)));
$('kind-filter').addEventListener('change', renderLists);
$('hide-matched').addEventListener('change', renderLists);
refresh().catch(error => setStatus(error.message, true));
