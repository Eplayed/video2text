let pkChecklistSheet = null;
let pkChecklistEpoch = 0;

function pkChecklistReset() {
  pkChecklistEpoch++;
  pkChecklistSheet = null;
  for (const id of ['pk-checklist', 'pk-current-package', 'pk-existing-revision']) {
    document.getElementById(id).hidden = true;
  }
  document.getElementById('pk-checklist-editor').replaceChildren();
  document.getElementById('pk-checklist-msg').textContent = '';
}

async function pkChecklistRequest(action = '', body) {
  const url = '/api/poster/checklist/' + encodeURIComponent(_pkId) + action;
  const options = body === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  };
  const reply = await fetch(url, options);
  const value = await reply.json();
  if (!reply.ok) throw new Error(value.error || '清单请求失败（' + reply.status + '）');
  return value;
}

function pkChecklistField(label, value, limit, multiline = false) {
  const box = document.createElement('label');
  box.textContent = label;
  const input = document.createElement(multiline ? 'textarea' : 'input');
  input.value = value || '';
  input.maxLength = limit;
  if (multiline) input.rows = 2;
  box.append(input);
  return box;
}

function pkChecklistButton(label, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn ghost sm';
  button.textContent = label;
  button.addEventListener('click', action);
  return button;
}

function pkChecklistCard(card, index) {
  const box = document.createElement('div');
  box.className = 'guide-sheet-card';
  const title = pkChecklistField('卡片标题', card.t, 8);
  title.hidden = index === 0;
  box.append(title, pkChecklistField('卡片正文', card.d, 1800, true));
  if (index) box.append(pkChecklistButton('移除附卡', () => box.remove()));
  return box;
}

function pkChecklistRow(item) {
  const row = document.createElement('fieldset');
  row.dataset.id = item.id;
  const heading = document.createElement('legend');
  heading.textContent = item.group + ' · ' + item.name;
  const fields = document.createElement('div');
  fields.className = 'guide-sheet-fields';
  fields.append(pkChecklistField('分组', item.group, 16),
    pkChecklistField('条目名称', item.name, 8));
  const control = document.createElement('label');
  const included = document.createElement('input');
  included.type = 'checkbox';
  included.checked = item.included;
  included.disabled = item.evidence.some(point => point.review_reason);
  control.append(included, ' 纳入成品');
  fields.append(control);
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = item.evidence.some(point => point.review_reason) ? '待核原句' : '对照引用原句';
  details.append(summary);
  for (const point of item.evidence) {
    const proof = document.createElement('p');
    const condition = point.conditions.length ? '适用条件 · ' + point.conditions.join('、') : '';
    proof.textContent = [point.title, point.quote, condition, point.review_reason].filter(Boolean).join(' · ');
    if (/^https?:/i.test(point.url)) {
      const source = document.createElement('a');
      source.href = point.url;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      source.textContent = '打开来源';
      proof.append(' ', source);
    }
    details.append(proof);
  }
  const cards = document.createElement('div');
  item.cards.forEach((card, i) => cards.append(pkChecklistCard(card, i)));
  row.append(heading, fields, cards, details,
    pkChecklistField('核对备注（选填）', item.note, 200),
    pkChecklistButton('添加附卡', () => {
      if (cards.children.length < 5) cards.append(pkChecklistCard({}, cards.children.length));
    }));
  return row;
}

function pkChecklistShow(value) {
  pkChecklistSheet = value.sheet;
  const editor = document.getElementById('pk-checklist-editor');
  const kind = document.getElementById('pk-checklist-kind');
  const prepare = document.getElementById('pk-checklist-prepare');
  const actions = document.getElementById('pk-checklist-actions');
  const stale = value.stale;
  document.getElementById('pk-checklist-msg').textContent = stale
    ? '原文或母稿已有更新，请重新准备清单。' : '';
  if (pkChecklistSheet) kind.value = pkChecklistSheet.kind;
  editor.replaceChildren();
  prepare.hidden = Boolean(pkChecklistSheet && !stale);
  actions.hidden = !pkChecklistSheet || stale;
  kind.disabled = false;
  if (!pkChecklistSheet || stale) return;
  const title = pkChecklistField('攻略标题', pkChecklistSheet.title, 30);
  title.querySelector('input').id = 'pk-checklist-title';
  editor.append(title, ...pkChecklistSheet.rows.map(pkChecklistRow));
}

async function pkChecklistOpen() {
  const panel = document.getElementById('pk-checklist');
  const token = ++pkChecklistEpoch;
  const msg = document.getElementById('pk-checklist-msg');
  panel.hidden = false;
  msg.textContent = '正在读取清单…';
  try {
    const value = await pkChecklistRequest();
    if (token === pkChecklistEpoch) pkChecklistShow(value);
  } catch (error) {
    if (token === pkChecklistEpoch) msg.textContent = error.message;
  }
}

function pkChecklistForm() {
  const editor = document.getElementById('pk-checklist-editor');
  const rows = Array.from(editor.querySelectorAll('fieldset'), row => {
    const [group, name, included, ...rest] = row.querySelectorAll('input');
    const cards = Array.from(row.querySelectorAll('.guide-sheet-card'), (card, i) => ({
      t: i === 0 ? name.value : card.querySelector('input').value,
      d: card.querySelector('textarea').value,
    }));
    return {id: row.dataset.id, group: group.value, name: name.value, included: included.checked,
      cards, note: rest[rest.length - 1].value};
  });
  return {revision: pkChecklistSheet.revision, title: document.getElementById('pk-checklist-title').value,
    kind: document.getElementById('pk-checklist-kind').value, rows};
}

async function pkChecklistSave(render) {
  if (!pkChecklistSheet) return;
  const token = pkChecklistEpoch;
  const actions = document.getElementById('pk-checklist-actions');
  const msg = document.getElementById('pk-checklist-msg');
  const buttons = actions.querySelectorAll('button');
  buttons.forEach(button => { button.disabled = true; });
  try {
    const value = await pkChecklistRequest('', pkChecklistForm());
    if (token !== pkChecklistEpoch) return;
    pkChecklistSheet = value.sheet;
    msg.textContent = render ? '已保存，正在提交排版…' : '清单已保存。';
    if (render) {
      const result = await pkChecklistRequest('/render', {revision: value.sheet.revision,
        channel: _pkChannel, also_wechat: document.getElementById('pk-also-wechat').checked});
      if (token === pkChecklistEpoch) {
        if (result.status === 'reused') {
          msg.textContent = '清单版本一致，已打开现有成品。';
          document.getElementById('pk-current-package').hidden = false;
          await renderPosterResult(_pkId);
          return;
        }
        msg.textContent = '正在核对并排版，请查看任务进度。';
        pollPoster('poster', result.task_id);
      }
    }
  } catch (error) {
    if (token === pkChecklistEpoch) msg.textContent = error.message;
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function pkChecklistPrepare() {
  const token = pkChecklistEpoch;
  const button = document.getElementById('pk-checklist-prepare');
  const msg = document.getElementById('pk-checklist-msg');
  button.disabled = true;
  msg.textContent = '正在提交母稿提取…';
  try {
    const result = await pkChecklistRequest('/prepare', {kind: document.getElementById('pk-checklist-kind').value});
    const deadline = Date.now() + 15 * 60 * 1000;
    while (token === pkChecklistEpoch && Date.now() < deadline) {
      const response = await fetch('/api/poster/status', {cache: 'no-store'});
      const status = await response.json();
      if (token !== pkChecklistEpoch) return;
      if (!response.ok || status.task_id !== result.task_id) {
        await pkChecklistOpen();
        return;
      }
      if (status.error) throw new Error(status.error);
      if (!status.running) {
        await pkChecklistOpen();
        return;
      }
      msg.textContent = status.progress || '正在准备核对清单…';
      await new Promise(resolve => setTimeout(resolve, 1600));
    }
    if (token === pkChecklistEpoch) msg.textContent = '读取任务超时，可稍后打开清单查看。';
  } catch (error) {
    if (token === pkChecklistEpoch) msg.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
