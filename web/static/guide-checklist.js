let pkChecklistSheet = null;
let pkChecklistEpoch = 0;
let pkWorkflow = 'auto';

function pkChecklistModeLine() {
  const count = pkFrameLayout
    ? pkFrameLayout.pages.filter(page => page.selected && !page.stale).length
    : Object.keys((pkChecklistSheet || {}).frames || {}).length;
  return count ? '攻略清单 · 已保存 ' + count + ' 页配图' : '攻略清单';
}

function pkSetWorkflow(mode) {
  pkWorkflow = mode;
  const overlay = document.getElementById('pk-overlay');
  overlay.classList.toggle('pk-workflow-checklist', mode === 'checklist');
  overlay.classList.toggle('pk-workflow-simple', mode !== 'free');
  for (const id of ['pk-preview-top', 'pk-preview-bot']) {
    const button = document.getElementById(id);
    button.textContent = mode === 'checklist' ? '② 预览清单' : '② 看版面 · 免费';
    if (mode === 'checklist') button.style.display = '';
    button.title = mode === 'checklist' ? '按已保存的清单和配图预览' : '预览当前自由稿版面';
  }
  for (const id of ['pk-go-top', 'pk-go-bot']) {
    const button = document.getElementById(id);
    button.textContent = pkGoLabel();
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
  const copy = document.querySelector('.pk-checklist-copy');
  const stale = value.stale;
  copy.hidden = !pkChecklistSheet || stale;
  document.getElementById('pk-checklist-msg').textContent = stale
    ? '原文或母稿已有更新，请重新准备清单。' : '';
  if (pkChecklistSheet) {
    kind.value = pkChecklistSheet.kind;
    pkSetWorkflow('checklist');
  }
  editor.replaceChildren();
  prepare.hidden = Boolean(pkChecklistSheet && !stale);
  actions.hidden = !pkChecklistSheet || stale;
  kind.disabled = false;
  if (!pkChecklistSheet || stale) return;
  const pending = pkChecklistSheet.rows.filter(row => !row.included).length;
  copy.open = pending > 0;
  document.getElementById('pk-checklist-copy-title').textContent = '核对文案 · '
    + pkChecklistSheet.rows.length + ' 条' + (pending ? ' · ' + pending + ' 条未纳入' : '');
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
    document.getElementById('pk-frame-status').scrollIntoView({block: 'nearest'});
    return;
  }
  const token = pkChecklistEpoch;
  const actions = document.getElementById('pk-checklist-actions');
  const msg = document.getElementById('pk-checklist-msg');
  const buttons = [...actions.querySelectorAll('button'), document.getElementById('pk-go-top')];
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

  const candidates = document.createElement('div');
  candidates.className = 'pk-frame-gallery';
  candidates.setAttribute('role', 'radiogroup');
  candidates.setAttribute('aria-label', '本页关键帧');
  const selectedId = !page.stale && page.selected ? page.selected.id : '';
  let crop = selectedId ? page.selected.crop || null : null;
  for (const item of [{id: ''}, ...page.candidates]) {
    const tile = document.createElement('label');
    tile.className = 'pk-frame-tile';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'pk-page-frame';
    radio.value = item.id;
    radio.checked = item.id === selectedId;
    const text = document.createElement('span');
    text.className = 'pk-frame-tile-label';
    text.textContent = item.id ?
      [item.time, item.suggested ? '推荐' : '', item.subject || item.source].filter(Boolean).join(' · ')
      : '文字页';
    tile.append(radio);
    if (item.id) {
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

  const basis = pkChecklistField('画面使用依据（选填，发布前核对）',
    page.selected && !page.stale ? page.selected.basis : '', 200);
  const basisInput = basis.querySelector('input');
  basisInput.placeholder = '如本人录制，或已获得作者许可';
  const info = document.createElement('section');
  info.className = 'pk-frame-selected';
  info.setAttribute('aria-label', '本页配图预览');
  const details = document.createElement('p');
  details.textContent = page.stale ? '原配图已变化，请重新选图或设为文字页。' : '';
  const confirm = pkChecklistButton('确认本页配图', () => {
    const frame = page.candidates.find(item => item.id === frameChoice());
    pkFrameSave({page: page.key, id: frameChoice(), anchor: anchor.value,
      basis: basisInput.value, digest: frame ? frame.digest : '', crop});
  });
  const undo = pkChecklistButton('撤销本页改动', () => {
    pkFrameShow(data);
    pkFrameMessage('已恢复服务器保存的配图。');
  });

  function selectFrame(reset) {
    const frame = page.candidates.find(item => item.id === frameChoice());
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
  candidates.addEventListener('change', () => {
    crop = page.selected && page.selected.id === frameChoice() ? page.selected.crop || null : null;
    selectFrame(true);
    if (page.selected && page.selected.id === frameChoice()) {
      anchor.value = page.selected.anchor;
      basisInput.value = page.selected.basis;
    }
    markDirty();
  });
  anchor.addEventListener('change', markDirty);
  basisInput.addEventListener('input', markDirty);
  if (page.selected && frameChoice()) anchor.value = page.selected.anchor;
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
    pkFrameMessage(body.id ? '本页配图已保存，预览和出图会使用这张图。'
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
