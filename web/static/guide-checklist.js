let pkChecklistSheet = null;
let pkChecklistEpoch = 0;

function pkChecklistReset() {
  pkChecklistEpoch++;
  pkChecklistSheet = null;
  pkFrameReset();
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
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，或撤销本页改动。');
    return;
  }
  const token = pkChecklistEpoch;
  const actions = document.getElementById('pk-checklist-actions');
  const msg = document.getElementById('pk-checklist-msg');
  const buttons = actions.querySelectorAll('button');
  buttons.forEach(button => { button.disabled = true; });
  try {
    const value = await pkChecklistRequest('', pkChecklistForm());
    if (token !== pkChecklistEpoch) return;
    pkChecklistSheet = value.sheet;
    if (pkFrameLayout && pkFrameLayout.revision !== value.sheet.revision) pkFrameReset();
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

let pkFrameLayout = null;
let pkFramePageKey = '';
let pkFrameDirty = false;

function pkFrameReset() {
  pkFrameLayout = null;
  pkFramePageKey = '';
  pkFrameDirty = false;
  document.getElementById('pk-page-frames').hidden = true;
  document.getElementById('pk-frame-assignments').replaceChildren();
  document.getElementById('pk-frame-previews').replaceChildren();
  document.getElementById('pk-frame-status').textContent = '';
}

function pkFrameMessage(message) {
  document.getElementById('pk-frame-status').textContent = message;
}

async function pkChecklistFramesOpen() {
  if (!pkChecklistSheet || document.getElementById('pk-checklist').hidden) await pkChecklistOpen();
  if (!pkChecklistSheet || document.getElementById('pk-checklist-actions').hidden) return;
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，或撤销本页改动。');
    return;
  }
  const token = pkChecklistEpoch;
  const panel = document.getElementById('pk-page-frames');
  panel.hidden = false;
  pkFrameMessage('正在读取当前清单的页序…');
  try {
    const saved = await pkChecklistRequest('', pkChecklistForm());
    if (token !== pkChecklistEpoch) return;
    pkChecklistSheet = saved.sheet;
    const data = await pkChecklistRequest('/frames');
    if (token !== pkChecklistEpoch) return;
    pkFrameShow(data);
    pkFrameMessage('选择页面和关键帧，确认对应的条目后保存。');
    panel.scrollIntoView({block: 'nearest'});
  } catch (error) {
    if (token === pkChecklistEpoch) pkFrameMessage(error.message);
  }
}

function pkFrameShow(data) {
  pkFrameLayout = data;
  pkFrameDirty = false;
  pkChecklistSheet.revision = data.revision;
  const host = document.getElementById('pk-frame-assignments');
  document.getElementById('pk-frame-previews').replaceChildren();
  const page = data.pages.find(p => p.key === pkFramePageKey) || data.pages[0];
  if (!page) return;
  pkFramePageKey = page.key;

  const chooser = document.createElement('select');
  chooser.setAttribute('aria-label', '配图页面');
  for (const p of data.pages) {
    const state = p.stale ? '需重新确认' : p.selected ?
      (p.selected.basis ? '已确认 ' + (p.candidates.find(f => f.id === p.selected.id) || {}).time : '待补使用依据')
      : '文字页';
    chooser.append(new Option('第 ' + p.page + ' 页 · ' + p.title + ' · ' + state, p.key));
  }
  chooser.value = page.key;
  chooser.addEventListener('change', () => {
    if (pkFrameDirty) {
      chooser.value = page.key;
      pkFrameMessage('请先确认本页配图，或撤销本页改动。');
      return;
    }
    pkFramePageKey = chooser.value;
    pkFrameShow(data);
  });

  const candidates = document.createElement('select');
  candidates.setAttribute('aria-label', '本页关键帧');
  candidates.append(new Option('文字页', ''));
  for (const item of page.candidates) {
    const label = [item.suggested ? '推荐' : '', item.time, item.subject || item.source].filter(Boolean).join(' · ');
    candidates.append(new Option(label, item.id));
  }
  candidates.value = !page.stale && page.selected ? page.selected.id : '';

  const anchor = document.createElement('select');
  anchor.setAttribute('aria-label', '画面对应的本页条目');
  anchor.append(new Option('选择画面对应的本页条目', ''));
  page.labels.forEach(name => anchor.append(new Option(name, name)));

  const basis = pkChecklistField('画面使用依据（发布包需要，预览可留空）',
    page.selected && !page.stale ? page.selected.basis : '', 200);
  const basisInput = basis.querySelector('input');
  basisInput.placeholder = '如本人录制，或已获得作者许可';
  const info = document.createElement('div');
  info.className = 'pk-frame-selected';
  const details = document.createElement('p');
  details.textContent = page.stale ? '原配图已变化，请重新选图或设为文字页。' : '';
  const confirm = pkChecklistButton('确认本页配图', () => {
    const frame = page.candidates.find(item => item.id === candidates.value);
    pkFrameSave({page: page.key, id: candidates.value, anchor: anchor.value,
      basis: basisInput.value, digest: frame ? frame.digest : ''});
  });
  const undo = pkChecklistButton('撤销本页改动', () => {
    pkFrameShow(data);
    pkFrameMessage('已恢复服务器保存的配图。');
  });

  function selectFrame(reset) {
    const frame = page.candidates.find(item => item.id === candidates.value);
    anchor.hidden = basis.hidden = info.hidden = !frame;
    info.replaceChildren();
    if (frame) {
      const figure = document.createElement('img');
      figure.src = frame.url;
      figure.alt = (frame.time + ' ' + frame.subject).trim();
      const link = document.createElement('a');
      link.href = frame.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '打开大图';
      const text = document.createElement('p');
      text.textContent = [frame.source, frame.time, frame.subject].filter(Boolean).join(' · ');
      info.append(figure, text, link);
      if (/^https?:/i.test(frame.source_url)) {
        const source = link.cloneNode();
        source.href = frame.source_url;
        source.textContent = '打开原视频';
        info.append(' · ', source);
      }
      if (reset) {
        anchor.value = frame.anchor || (page.labels.length === 1 ? page.labels[0] : '');
        basisInput.value = '';
      }
    }
    confirm.textContent = frame ? '确认用在第 ' + page.page + ' 页' : '确认第 ' + page.page + ' 页为文字页';
  }
  function markDirty() {
    pkFrameDirty = true;
    document.getElementById('pk-frame-previews').replaceChildren();
    pkFrameMessage('选择已更改，确认本页后保存到清单。');
  }
  candidates.addEventListener('change', () => { selectFrame(true); markDirty(); });
  anchor.addEventListener('change', markDirty);
  basisInput.addEventListener('input', markDirty);
  if (page.selected && candidates.value) anchor.value = page.selected.anchor;
  selectFrame(false);

  host.replaceChildren(chooser, candidates, details, info, anchor, basis, confirm, undo);
  if (data.outdated) {
    host.append(pkChecklistButton('清除已失效的配图', () => pkFrameSave({discard_stale: true})));
    pkFrameMessage('有 ' + data.outdated + ' 个页面改动过，请清除失效配图或重新选择。');
  }
}

async function pkFrameSave(body) {
  const token = pkChecklistEpoch;
  const host = document.getElementById('pk-frame-assignments');
  const inputs = Array.from(host.querySelectorAll('button, select, input'));
  inputs.forEach(el => { el.disabled = true; });
  pkFrameMessage('正在保存逐页配图…');
  try {
    const data = await pkChecklistRequest('/frames', Object.assign({revision: pkFrameLayout.revision}, body));
    if (token !== pkChecklistEpoch) return;
    pkFrameShow(data);
    pkFrameMessage(body.id ? (body.basis.trim() ? '本页配图已确认。' : '本页配图已保存，可先预览。出图前需填写使用依据。')
      : body.discard_stale ? '已清除失效配图。' : '本页已设为文字页。');
  } catch (error) {
    if (token === pkChecklistEpoch) pkFrameMessage(error.message);
  } finally {
    inputs.forEach(el => { el.disabled = false; });
  }
}

async function pkFramePreview(channel) {
  if (!pkFrameLayout) return;
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，再预览。');
    return;
  }
  const token = pkChecklistEpoch;
  pkFrameMessage('正在排版预览…');
  try {
    const result = await pkChecklistRequest('/preview', {revision: pkFrameLayout.revision, channel});
    if (token !== pkChecklistEpoch) return;
    const target = document.getElementById('pk-frame-previews');
    target.replaceChildren();
    for (const page of result.pages) {
      const figure = document.createElement('figure');
      const link = document.createElement('a');
      link.href = page.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const img = document.createElement('img');
      img.src = page.url;
      img.alt = '第 ' + page.page + ' 页 ' + page.title;
      const caption = document.createElement('figcaption');
      caption.textContent = [img.alt, page.check.photos ? '含配图' : '文字页',
        ...(page.check.problems || [])].join(' · ');
      link.append(img);
      figure.append(link, caption);
      target.append(figure);
    }
    pkFrameMessage(result.ratio + ' 预览已生成，可点图放大确认。');
    target.scrollIntoView({block: 'nearest'});
  } catch (error) {
    if (token === pkChecklistEpoch) pkFrameMessage(error.message);
  }
}
