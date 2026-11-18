let pkChecklistSheet = null;
let pkChecklistEpoch = 0;
let pkChecklistRecommendation = null;
let pkWorkflow = 'auto';

function pkChecklistModeLine() {
  const count = pkFrameLayout
    ? pkFrameLayout.pages.filter(page => page.selected && !page.stale).length
    : Object.keys((pkChecklistSheet || {}).frames || {}).length;
  return count ? '攻略清单 · 已保存 ' + count + ' 页配图' : '攻略清单';
}

function pkUpdateChecklistFooter() {
  if (pkWorkflow !== 'checklist') return;
  const status = document.getElementById('pk-checklist-footer-state');
  const checked = document.querySelectorAll('#pk-checklist-editor [data-include]:checked:not(:disabled)').length;
  const frames = pkChecklistModeLine().replace(/^攻略清单 · /, '');
  const selection = checked ? '已选 ' + checked + ' 条' : '请先选择出图条目';
  status.textContent = pkFrameDirty ? '本页配图待确认'
    : !pkChecklistSheet ? '请先准备清单'
    : [selection, frames === '攻略清单' ? '' : frames].filter(Boolean).join(' · ');
}

function pkSetWorkflow(mode) {
  pkWorkflow = mode;
  const overlay = document.getElementById('pk-overlay');
  overlay.classList.toggle('pk-workflow-checklist', mode === 'checklist');
  overlay.classList.toggle('pk-workflow-simple', mode !== 'free');
  const footer = document.getElementById('pk-checklist-footer');
  footer.hidden = mode !== 'checklist';
  const destination = document.getElementById(mode === 'checklist' ? 'pk-checklist-footer-actions' : 'pk-main-actions');
  for (const id of ['pk-current-package', 'pk-preview-top', 'pk-go-top']) {
    const action = document.getElementById(id);
    if (action.parentElement !== destination) destination.append(action);
  }
  pkUpdateChecklistFooter();
  for (const id of ['pk-preview-top', 'pk-preview-bot']) {
    const button = document.getElementById(id);
    button.textContent = mode === 'checklist' ? '② 预览清单' : '② 看版面 · 免费';
    if (mode === 'checklist') button.style.display = '';
    if (id === 'pk-preview-top') button.disabled = mode === 'checklist' && !pkChecklistSheet;
    button.title = mode === 'checklist' ? '按已保存的清单和配图预览' : '预览当前自由稿版面';
  }
  for (const id of ['pk-go-top', 'pk-go-bot']) {
    const button = document.getElementById(id);
    button.textContent = pkGoLabel();
    if (id === 'pk-go-top') button.disabled = mode === 'checklist' && !pkChecklistSheet;
    button.title = mode === 'checklist' ? '使用已保存的清单、关键帧和裁剪区域' : '生成当前图文';
  }
  if (mode === 'checklist') {
    document.getElementById('pk-mode').textContent = pkChecklistModeLine();
    if (_pkFrames) pkRenderFramesHint();
  }
}

async function pkPosterAction(generate) {
  if (!_pkId) return;
  if (pkWorkflow === 'auto') {
    const token = pkChecklistEpoch;
    try {
      const value = await pkChecklistRequest();
      if (token !== pkChecklistEpoch) return;
      if (value.sheet) {
        document.getElementById('pk-checklist').hidden = false;
        pkChecklistShow(value);
      }
    } catch (error) {
      if (token === pkChecklistEpoch) {
        const result = document.getElementById('pk-result');
        result.textContent = '读取攻略清单失败。' + error.message;
        result.scrollIntoView({block: 'nearest'});
      }
      return;
    }
  }
  if (pkWorkflow !== 'checklist') return generate ? posterGenerate() : posterPreview();
  if (!pkChecklistSheet || document.getElementById('pk-checklist-actions').hidden) {
    document.getElementById('pk-checklist').scrollIntoView({block: 'nearest'});
    return;
  }
  if (!pkChecklistRequireSelection()) return;
  if (generate) return pkChecklistSave(true);
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，再预览。');
    document.getElementById('pk-frame-status').scrollIntoView({block: 'nearest'});
    return;
  }
  if (await pkChecklistFramesOpen()) await pkFramePreview(_pkChannel);
}

function pkChecklistReset() {
  pkChecklistEpoch++;
  pkChecklistSheet = null;
  pkChecklistRecommendation = null;
  document.getElementById('pk-checklist-format').value = 'classic';
  pkSetWorkflow('auto');
  document.querySelector('.pk-more-tools').open = false;
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
  control.append(included, included.disabled ? ' 待核，暂不可选' : ' 纳入图文');
  included.dataset.include = '';
  included.addEventListener('change', () => {
    if (pkFrameLayout && !pkFrameDirty) pkFrameReset();
    pkChecklistUpdateSelection();
  });
  control.className = 'guide-sheet-choice';
  const preview = document.createElement('p');
  preview.className = 'guide-sheet-excerpt';
  const excerpt = item.cards.map(card => card.d).join(' ').trim();
  preview.textContent = excerpt.length > 100 ? excerpt.slice(0, 100) + '…' : excerpt;
  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = included.disabled ? '查看待核原因与原句' : '核对原句与编辑文案';
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
  details.append(fields, cards, pkChecklistField('核对备注（选填）', item.note, 200),
    pkChecklistButton('添加附卡', () => {
      if (cards.children.length < 5) cards.append(pkChecklistCard({}, cards.children.length));
    }));
  row.append(heading, control, preview, details);
  return row;
}

function pkChecklistUpdateSelection() {
  const choices = document.querySelectorAll('#pk-checklist-editor [data-include]');
  const count = Array.from(choices).filter(input => input.checked && !input.disabled).length;
  const held = Array.from(choices).filter(input => input.disabled).length;
  document.getElementById('pk-checklist-copy-title').textContent =
    '出图条目 · 已选 ' + count + ' 条' + (held ? ' · ' + held + ' 条待核' : '');
  if (count && document.getElementById('pk-checklist-msg').dataset.selectionNeeded) {
    const status = document.getElementById('pk-checklist-msg');
    status.textContent = '已勾选条目，可预览清单或逐页配图。';
    delete status.dataset.selectionNeeded;
  }
  pkUpdateChecklistFooter();
}

function pkChecklistFocusSelection() {
  const copy = document.querySelector('.pk-checklist-copy');
  copy.open = true;
  const fields = copy.querySelector('[data-include]:checked:not(:disabled)')
    || copy.querySelector('[data-include]:not(:disabled)');
  const target = fields || document.getElementById('pk-checklist-copy-title');
  target.scrollIntoView({block: 'center'});
  target.focus({preventScroll: true});
}

function pkChecklistRequireSelection() {
  const copy = document.querySelector('.pk-checklist-copy');
  if (copy.querySelector('[data-include]:checked:not(:disabled)')) return true;
  const status = document.getElementById('pk-checklist-msg');
  status.dataset.selectionNeeded = 'true';
  status.textContent = copy.querySelector('[data-include]:not(:disabled)')
    ? '请勾选至少一条「纳入图文」，再预览、配图或出图。'
    : '这份清单的条目都待核，请先核对原句。';
  pkChecklistFocusSelection();
  return false;
}

function pkChecklistRecommend() {
  if (!pkChecklistRecommendation || pkFrameDirty) return;
  const ids = new Set(pkChecklistRecommendation.row_ids);
  for (const row of document.querySelectorAll('#pk-checklist-editor fieldset')) {
    const checkbox = row.querySelector('[data-include]');
    if (!checkbox.disabled) checkbox.checked = ids.has(row.dataset.id);
  }
  pkFrameReset();
  pkChecklistUpdateSelection();
  document.getElementById('pk-checklist-msg').textContent =
    '已勾选推荐条目，预计 ' + pkChecklistRecommendation.pages + ' 页（含封面）。';
  pkChecklistFocusSelection();
}

function pkChecklistShow(value) {
  pkChecklistSheet = value.sheet;
  const editor = document.getElementById('pk-checklist-editor');
  const kind = document.getElementById('pk-checklist-kind');
  const prepare = document.getElementById('pk-checklist-prepare');
  const recommend = document.getElementById('pk-checklist-recommend');
  pkChecklistRecommendation = value.recommendation;
  recommend.hidden = !(pkChecklistRecommendation && pkChecklistRecommendation.pages);
  if (!recommend.hidden) recommend.textContent = '推荐勾选 · 预计 ' + pkChecklistRecommendation.pages + ' 页';
  const actions = document.getElementById('pk-checklist-actions');
  const copy = document.querySelector('.pk-checklist-copy');
  const stale = value.stale;
  copy.hidden = !pkChecklistSheet || stale;
  const status = document.getElementById('pk-checklist-msg');
  delete status.dataset.selectionNeeded;
  delete status.dataset.lengthWarning;
  status.textContent = stale ? '原文或母稿已有更新，请重新准备清单。' : '';
  if (pkChecklistSheet) {
    kind.value = value.can_merge && value.suggested_kind ? value.suggested_kind : pkChecklistSheet.kind;
    pkSetWorkflow('checklist');
  } else if (value.suggested_kind) kind.value = value.suggested_kind;
  editor.replaceChildren();
  prepare.hidden = Boolean(pkChecklistSheet && !stale && !value.can_merge);
  prepare.textContent = pkChecklistSheet && !stale ? '按选中模板智能合并' : '准备清单';
  kind.onchange = () => {
    prepare.hidden = Boolean(pkChecklistSheet && !stale && !value.can_merge &&
      kind.value === pkChecklistSheet.kind);
  };
  actions.hidden = !pkChecklistSheet || stale;
  kind.disabled = false;
  if (!pkChecklistSheet || stale) return;
  copy.open = !pkChecklistSheet.rows.some(row => row.included &&
    !row.evidence.some(point => point.review_reason));
  const title = pkChecklistField('攻略标题', pkChecklistSheet.title, 30);
  title.querySelector('input').id = 'pk-checklist-title';
  editor.append(title, ...pkChecklistSheet.rows.map(pkChecklistRow));
  pkChecklistUpdateSelection();
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
    const [included, group, name, ...rest] = row.querySelectorAll('input');
    const cards = Array.from(row.querySelectorAll('.guide-sheet-card'), (card, i) => ({
      t: i === 0 ? name.value : card.querySelector('input').value,
      d: card.querySelector('textarea').value,
    }));
    return {id: row.dataset.id, group: group.value, name: name.value, included: included.checked,
      cards, note: rest[rest.length - 1].value};
  });
  return {revision: pkChecklistSheet.revision, title: document.getElementById('pk-checklist-title').value,
    kind: pkChecklistSheet.kind, rows};
}

function pkChecklistCheckLength(form) {
  const limit = PK_BY_MODE.typeset.cardD;
  const blocked = form.rows.find(row => row.included &&
    row.cards.some(card => Array.from(card.d.trim()).length > limit));
  const status = document.getElementById('pk-checklist-msg');
  if (!blocked) {
    if (status.dataset.lengthWarning) {
      delete status.dataset.lengthWarning;
      status.textContent = '';
    }
    return true;
  }
  const copy = document.querySelector('.pk-checklist-copy');
  copy.open = true;
  const entry = Array.from(copy.querySelectorAll('fieldset')).find(row => row.dataset.id === blocked.id);
  const details = entry.querySelector('details');
  details.open = true;
  const target = Array.from(details.querySelectorAll('textarea'))
    .find(input => Array.from(input.value.trim()).length > limit);
  status.dataset.lengthWarning = 'true';
  status.textContent = '「' + blocked.name + '」的卡片正文超过 ' + limit + ' 字，请精简或拆成附卡后继续。';
  target.scrollIntoView({block: 'center'});
  target.focus({preventScroll: true});
  return false;
}

async function pkChecklistSave(render) {
  if (!pkChecklistSheet) return;
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，或撤销本页改动。');
    document.getElementById('pk-frame-status').scrollIntoView({block: 'nearest'});
    return;
  }
  const form = pkChecklistForm();
  if (!pkChecklistCheckLength(form)) return;
  const token = pkChecklistEpoch;
  const reading = pkChecklistReading();
  const actions = document.getElementById('pk-checklist-actions');
  const msg = document.getElementById('pk-checklist-msg');
  const buttons = [...actions.querySelectorAll('button'), document.getElementById('pk-go-top')];
  buttons.forEach(button => { button.disabled = true; });
  try {
    const value = await pkChecklistRequest('', form);
    if (token !== pkChecklistEpoch) return;
    pkChecklistSheet = value.sheet;
    if (pkFrameLayout && pkFrameLayout.revision !== value.sheet.revision) pkFrameReset();
    msg.textContent = render ? '已保存，正在提交排版…' : '清单已保存。';
    if (render) {
      const result = await pkChecklistRequest('/render', {revision: value.sheet.revision,
        channel: _pkChannel, reading, also_wechat: document.getElementById('pk-also-wechat').checked});
      if (token === pkChecklistEpoch) {
        if (result.status === 'reused') {
          msg.textContent = '清单版本一致，已打开现有成品。';
          document.getElementById('pk-current-package').hidden = false;
          await renderPosterResult(_pkId, true);
          return;
        }
        msg.textContent = '正在核对并排版，请查看任务进度。';
        pollPoster('poster', result.task_id);
      }
    }
  } catch (error) {
    if (token === pkChecklistEpoch) {
      msg.textContent = error.message;
      msg.scrollIntoView({block: 'nearest'});
    }
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
  pkUpdateChecklistFooter();
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
  if (!pkChecklistRequireSelection()) return;
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，或撤销本页改动。');
    return;
  }
  const form = pkChecklistForm();
  if (!pkChecklistCheckLength(form)) return;
  const token = pkChecklistEpoch;
  const panel = document.getElementById('pk-page-frames');
  panel.hidden = false;
  pkFrameMessage('正在读取当前清单的页序…');
  try {
    const saved = await pkChecklistRequest('', form);
    if (token !== pkChecklistEpoch) return;
    pkChecklistSheet = saved.sheet;
    const data = await pkChecklistRequest('/frames');
    if (token !== pkChecklistEpoch) return;
    pkFrameShow(data);
    pkFrameMessage('配图可选。选择画面后确认对应条目，文字页也能直接预览。');
    panel.scrollIntoView({block: 'nearest'});
    return true;
  } catch (error) {
    if (token === pkChecklistEpoch) pkFrameMessage(error.message);
  }
}

function pkFrameShow(data) {
  pkFrameLayout = data;
  pkFrameDirty = false;
  pkChecklistSheet.revision = data.revision;
  document.getElementById('pk-mode').textContent = pkChecklistModeLine();
  pkUpdateChecklistFooter();
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
      : (p.visual === 'asset' ? '本地图标／原画' : '纯文字信息页');
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

  const candidates = document.createElement('div');
  candidates.className = 'pk-frame-gallery';
  candidates.setAttribute('role', 'radiogroup');
  candidates.setAttribute('aria-label', '本页配图方案');
  const selectedId = !page.stale && page.selected ? page.selected.id : '__' + (page.manual_visual || 'auto');
  let crop = page.selected && !page.stale ? page.selected.crop || null : null;
  const alternatives = [
    {id: '__auto', label: '自动配图'},
    {id: '__text', label: '纯文字信息页'},
  ];
  if (page.has_asset) alternatives.splice(1, 0, {id: '__asset', label: '本地图标 / 原画'});
  for (const item of [...alternatives, ...page.candidates]) {
    const tile = document.createElement('label');
    tile.className = 'pk-frame-tile';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'pk-page-frame';
    radio.value = item.id;
    radio.checked = item.id === selectedId;
    const text = document.createElement('span');
    text.className = 'pk-frame-tile-label';
    text.textContent = item.label ||
      [item.time, item.suggested ? '推荐实图' : '', item.subject || item.source].filter(Boolean).join(' · ');
    tile.append(radio);
    if (item.url) {
      const thumb = document.createElement('img');
      thumb.src = thumbOf(item.url, 360);
      thumb.alt = '';
      tile.append(thumb);
    }
    tile.append(text);
    candidates.append(tile);
  }
  function frameChoice() {
    return candidates.querySelector('input:checked').value;
  }

  const anchor = document.createElement('select');
  anchor.setAttribute('aria-label', '画面对应的本页条目');
  anchor.append(new Option('选择画面对应的本页条目', ''));
  page.labels.forEach(name => anchor.append(new Option(name, name)));

  const usage = document.createElement('select');
  usage.setAttribute('aria-label', '画面用途');
  usage.append(new Option('条目实图（可辨认对应内容）', 'evidence'),
    new Option('场景参考', 'scene'));
  usage.value = page.selected && !page.stale ? page.selected.usage : 'scene';

  const basis = pkChecklistField('画面使用依据（选填，发布前核对）',
    page.selected && !page.stale ? page.selected.basis : '', 200);
  const basisInput = basis.querySelector('input');
  basisInput.placeholder = '如本人录制，或已获得作者许可';
  const info = document.createElement('section');
  info.className = 'pk-frame-selected';
  info.setAttribute('aria-label', '本页配图预览');
  const details = document.createElement('p');
  details.textContent = page.stale ? '原配图已变化，预览采用当前可用的备选方案。' :
    '本页推荐 · ' + ({evidence: '核对后使用视频实图', asset: '本地图标或原画',
      scene: '可选视频场景', text: '纯文字信息页'})[page.recommended];
  const confirm = pkChecklistButton('确认本页配图', () => {
    const frame = page.candidates.find(item => item.id === frameChoice());
    pkFrameSave({page: page.key, id: frame ? frame.id : '', visual: frame ? undefined : frameChoice().slice(2),
      anchor: anchor.value, usage: usage.value, basis: basisInput.value, digest: frame ? frame.digest : '', crop});
  });
  const undo = pkChecklistButton('撤销本页改动', () => {
    pkFrameShow(data);
    pkFrameMessage('已恢复服务器保存的配图。');
  });

  function selectFrame(reset) {
    const frame = page.candidates.find(item => item.id === frameChoice());
    anchor.hidden = usage.hidden = basis.hidden = info.hidden = !frame;
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
      const caption = document.createElement('strong');
      caption.textContent = '第 ' + page.page + ' 页配图预览';
      const imageBox = document.createElement('div');
      imageBox.className = 'pk-frame-image';
      imageBox.append(figure);
      info.append(caption, imageBox, text, link);
      if (crop) {
        const canvas = document.createElement('canvas');
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', '已裁剪的画面');
        figure.hidden = true;
        imageBox.append(canvas);
        figure.addEventListener('load', () => pkPaintCrop(canvas, figure, crop), {once: true});
        if (figure.complete && figure.naturalWidth) pkPaintCrop(canvas, figure, crop);
        text.append(' · 已裁剪');
      }
      const cropButton = pkChecklistButton('裁剪图片', () => pkOpenFrameCrop(frame, crop, next => {
        crop = next;
        selectFrame(false);
        markDirty();
      }));
      cropButton.dataset.frameCrop = '';
      info.append(' ', cropButton);
      if (/^https?:/i.test(frame.source_url)) {
        const source = link.cloneNode();
        source.href = frame.source_url;
        source.textContent = '打开原视频';
        info.append(' · ', source);
      }
      if (reset) {
        anchor.value = frame.anchor || (page.labels.length === 1 ? page.labels[0] : '');
        usage.value = frame.anchor ? 'evidence' : 'scene';
        basisInput.value = '';
      }
    }
    confirm.textContent = frame ? '确认用在第 ' + page.page + ' 页' : '保存本页画面方案';
  }
  function markDirty() {
    pkFrameDirty = true;
    pkUpdateChecklistFooter();
    document.getElementById('pk-frame-previews').replaceChildren();
    pkFrameMessage('选择已更改，确认本页后保存到清单。');
  }
  candidates.addEventListener('change', () => {
    crop = page.selected && page.selected.id === frameChoice() ? page.selected.crop || null : null;
    selectFrame(true);
    if (page.selected && page.selected.id === frameChoice()) {
      anchor.value = page.selected.anchor;
      usage.value = page.selected.usage;
      basisInput.value = page.selected.basis;
    }
    markDirty();
  });
  anchor.addEventListener('change', markDirty);
  usage.addEventListener('change', markDirty);
  basisInput.addEventListener('input', markDirty);
  if (page.selected && frameChoice()) anchor.value = page.selected.anchor;
  selectFrame(false);

  host.replaceChildren(chooser, candidates, details, info, anchor, usage, basis, confirm, undo);
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
    pkFrameMessage(body.id ? '本页配图已保存，预览和出图会使用这张图。'
      : body.discard_stale ? '已清除失效配图。' : '本页画面方案已保存。');
  } catch (error) {
    if (token === pkChecklistEpoch) pkFrameMessage(error.message);
  } finally {
    inputs.forEach(el => { el.disabled = false; });
  }
}

function pkChecklistReading() {
  return document.getElementById('pk-checklist-format').value === 'reading';
}

document.getElementById('pk-checklist-format').addEventListener('change', () => {
  document.getElementById('pk-frame-previews').replaceChildren();
  if (pkFrameLayout) pkFrameMessage('已切换版式，请重新预览。');
});

async function pkFramePreview(channel) {
  if (!pkFrameLayout) return;
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，再预览。');
    return;
  }
  const token = pkChecklistEpoch;
  const reading = pkChecklistReading();
  pkFrameMessage('正在排版预览…');
  try {
    const result = await pkChecklistRequest('/preview', {revision: pkFrameLayout.revision, channel, reading});
    if (token !== pkChecklistEpoch || reading !== pkChecklistReading()) return;
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
      img.alt = (page.role === '封面' ? '封面 · ' : '第 ' + page.page + ' 页 · ') + page.title;
      const caption = document.createElement('figcaption');
      caption.textContent = [img.alt, page.check.photos ? '视频画面' : page.check.icons || page.check.art ? '本地图标 / 原画' : '纯文字信息页',
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

async function pkFrameCropFromReview(id) {
  if (pkFrameDirty) {
    pkFrameMessage('请先确认本页配图，或撤销本页改动。');
    return;
  }
  if (!await pkChecklistFramesOpen()) return;
  const matches = pkFrameLayout.pages.filter(p => p.candidates.some(frame => frame.id === id));
  const page = matches.find(p => p.candidates.some(frame => frame.id === id && frame.suggested))
    || matches.find(p => p.key === pkFramePageKey) || matches[0];
  if (!page) {
    pkFrameMessage('这张图没有符合条件的页面，请在逐页配图中查看候选。');
    return;
  }
  pkFramePageKey = page.key;
  pkFrameShow(pkFrameLayout);
  const radio = [...document.querySelectorAll('.pk-frame-gallery input')].find(input => input.value === id);
  if (radio) {
    radio.click();
    document.querySelector('[data-frame-crop]').click();
  }
}

const PK_CROP_FULL = [0, 0, 10000, 10000];

function pkPaintCrop(canvas, img, crop) {
  const [left, top, right, bottom] = (crop || PK_CROP_FULL)
    .map((value, i) => Math.round(value * (i % 2 ? img.naturalHeight : img.naturalWidth) / 10000));
  const width = right - left;
  const height = bottom - top;
  if (width < 1 || height < 1) return;
  const scale = Math.min(1, 520 / width, 300 / height);
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext('2d').drawImage(img, left, top, width, height, 0, 0, canvas.width, canvas.height);
}

function pkOpenFrameCrop(frame, previous, applyCrop) {
  const dialog = document.createElement('dialog');
  dialog.className = 'pk-crop-dialog';
  dialog.setAttribute('aria-label', '自由裁剪关键帧');
  const title = document.createElement('h3');
  title.textContent = ['裁剪关键帧', frame.time, frame.subject].filter(Boolean).join(' · ');
  const tip = document.createElement('p');
  tip.textContent = '在原图上拖出要保留的区域，重新拖动可调整。';
  const error = document.createElement('p');
  error.setAttribute('role', 'status');
  const source = document.createElement('canvas');
  source.setAttribute('aria-label', '裁剪框选画布');
  const preview = document.createElement('canvas');
  preview.setAttribute('role', 'img');
  preview.setAttribute('aria-label', '裁剪效果');
  const result = document.createElement('div');
  result.className = 'pk-crop-preview';
  result.append(preview);
  const img = new Image();
  let rect = previous ? [...previous] : [...PK_CROP_FULL];
  let start = null;

  const use = pkChecklistButton('应用裁剪', () => {
    if (rect[2] - rect[0] < 10000 / img.naturalWidth || rect[3] - rect[1] < 10000 / img.naturalHeight) {
      error.textContent = '裁剪范围太小，请重新框选。';
      return;
    }
    applyCrop(rect.every((value, i) => value === PK_CROP_FULL[i]) ? null : rect);
    dialog.close();
  });
  use.disabled = true;
  const reset = pkChecklistButton('恢复原图', () => {
    rect = [...PK_CROP_FULL];
    paint();
  });
  const cancel = pkChecklistButton('取消', () => dialog.close());
  const buttons = document.createElement('div');
  buttons.className = 'guide-sheet-actions';
  buttons.append(use, reset, cancel);
  dialog.append(title, tip, error, source, result, buttons);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  document.body.append(dialog);
  dialog.showModal();

  function paint() {
    if (!img.naturalWidth) return;
    const ctx = source.getContext('2d');
    const w = source.width;
    const h = source.height;
    const [left, top, right, bottom] = rect.map((value, i) => value / 10000 * (i % 2 ? h : w));
    ctx.drawImage(img, 0, 0, w, h);
    ctx.fillStyle = 'rgba(0,0,0,.6)';
    ctx.fillRect(0, 0, w, top);
    ctx.fillRect(0, bottom, w, h - bottom);
    ctx.fillRect(0, top, left, bottom - top);
    ctx.fillRect(right, top, w - right, bottom - top);
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.strokeRect(left, top, right - left, bottom - top);
    pkPaintCrop(preview, img, rect);
  }

  function point(event) {
    const box = source.getBoundingClientRect();
    return [event.clientX - box.left, event.clientY - box.top].map((value, i) =>
      Math.min(10000, Math.max(0, Math.round(value / (i ? box.height : box.width) * 10000))));
  }

  source.addEventListener('pointerdown', event => {
    if (!img.naturalWidth || (event.pointerType === 'mouse' && event.button !== 0)) return;
    event.preventDefault();
    start = point(event);
    source.setPointerCapture(event.pointerId);
  });
  function drag(event) {
    if (!start) return;
    const [x, y] = point(event);
    if (Math.abs(x - start[0]) < 10000 / img.naturalWidth
        || Math.abs(y - start[1]) < 10000 / img.naturalHeight) return;
    rect = [Math.min(start[0], x), Math.min(start[1], y), Math.max(start[0], x), Math.max(start[1], y)];
    error.textContent = '';
    paint();
  }
  source.addEventListener('pointermove', drag);
  source.addEventListener('pointerup', event => { drag(event); start = null; });
  source.addEventListener('pointercancel', () => { start = null; });
  img.onload = () => {
    const ratio = Math.min(1, 820 / img.naturalWidth, 450 / img.naturalHeight);
    source.width = Math.max(1, Math.round(img.naturalWidth * ratio));
    source.height = Math.max(1, Math.round(img.naturalHeight * ratio));
    paint();
    use.disabled = false;
  };
  img.onerror = () => { error.textContent = '画面读取失败，请关闭后重新选择。'; };
  img.src = frame.url;
}
