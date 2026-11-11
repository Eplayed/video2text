(() => {
  const panel = document.getElementById('today-ops');
  const picker = document.getElementById('today-channel');
  const list = document.getElementById('today-topics');
  const sources = document.getElementById('today-sources');
  const context = document.getElementById('today-context');
  let requestId = 0;
  let refreshTimer;
  let snapshot = null;

  function errorMessage(error) {
    return error && error.message ? error.message : String(error);
  }

  async function json(url, options) {
    const response = await fetch(url, Object.assign({cache: 'no-store'}, options));
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || '请求失败（' + response.status + '）');
    return data;
  }

  function button(label, callback, primary = false) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'btn sm' + (primary ? '' : ' ghost');
    el.textContent = label;
    el.addEventListener('click', callback);
    return el;
  }

  function text(tag, value, className) {
    const el = document.createElement(tag);
    el.textContent = value;
    if (className) el.className = className;
    return el;
  }

  function jobHasError(topic) { return Boolean(topic.job && topic.job.error); }

  function openTopic(topic, channel) {
    const id = Number(topic.summary_id || (topic.job || {}).summary_id);
    if (topic.has_package && id) {
      if (channel === 'wechat') {
        switchPage('wechat');
        openWechatDetail(id);
      } else {
        gotoPosterPage(id);
      }
    } else if (id) {
      if (channel === 'wechat') {
        openWechatGenDialog(id);
        if (jobHasError(topic)) wxShowBlockedDraft(id);
      }
      else openPosterDialog(id);
    } else {
      switchPage('schedule');
    }
  }

  function renderSources(items) {
    const expanded = new Set(Array.from(sources.querySelectorAll('details[open]'), el => el.dataset.source));
    sources.replaceChildren();
    for (const source of items) {
      const block = document.createElement('details');
      block.className = 'today-source';
      block.dataset.source = source.label;
      block.open = expanded.has(source.label);
      const failed = source.status === 'error' || source.status === 'fail';
      block.append(text('summary', source.label + (failed ? ' · 拉取有异常' : '') +
        (source.at ? ' · ' + source.at.slice(5, 16) : ' · 暂无记录'), failed ? 'today-error' : ''));
      block.append(text('p', source.note || '暂无回执'));
      if (source.members.length) {
        const members = document.createElement('ul');
        for (const member of source.members) {
          members.append(text('li', member.author + ' · ' + (member.synced_at || '未同步') +
            ' · ' + (member.sync_note || '无回执')));
        }
        block.append(members);
      }
      sources.append(block);
    }
    const overview = document.querySelector('.today-sources > summary');
    const hasError = items.some(source => source.status === 'error' || source.status === 'fail');
    overview.textContent = hasError ? '素材源最近回执 · 有拉取异常' : '素材源最近回执';
    overview.classList.toggle('today-error', hasError);
    if (!items.length) sources.append(text('p', '暂无素材源回执'));
  }

  function render(data) {
    list.replaceChildren();
    if (!data.topics.length) {
      list.append(text('p', '当前没有具备近期素材的选题或在制稿件。'),
        button('查看排产表', () => switchPage('schedule')));
      return;
    }
    const busy = data.topics.some(topic => topic.job && topic.job.running);
    for (const topic of data.topics) {
      const job = topic.job || {};
      const card = document.createElement('article');
      card.className = 'today-card';
      const head = document.createElement('header');
      head.append(text('h3', topic.title), text('span', job.running ? '生成中' : topic.stage, 'today-tag'));
      card.append(head, text('p', topic.reason, 'today-reason'));
      if (topic.angle) card.append(text('p', topic.angle));
      if (topic.materials.length) {
        const materials = document.createElement('ul');
        materials.className = 'today-materials';
        for (const material of topic.materials) {
          const item = document.createElement('li');
          const name = [material.title || '素材 ' + material.video_id, material.author, material.date]
            .filter(Boolean).join(' · ');
          if (material.sheet && material.row) {
            const link = button(name, () => gotoMaterial(material.sheet, Number(material.row)));
            link.className = 'today-link';
            item.append(link);
          } else {
            item.textContent = name;
          }
          materials.append(item);
        }
        card.append(materials);
      }
      const actions = document.createElement('div');
      actions.className = 'today-actions';
      if (topic.has_package) {
        actions.append(button('查看待审稿', () => openTopic(topic, data.channel), true));
      } else {
        const generate = button(job.running ? '生成中…' : job.error ? '继续生成' : '生成待审稿',
          () => start(topic, data.channel, generate), true);
        generate.disabled = busy || !data.generation_enabled;
        actions.append(generate);
        if (topic.summary_id || job.summary_id) {
          actions.append(button('查看并编辑草稿', () => openTopic(topic, data.channel)));
        }
      }
      card.append(actions);
      if (job.running || job.error) {
        card.append(text('p', job.error || job.progress, job.error ? 'today-error' : 'today-reason'));
      }
      list.append(card);
    }
  }

  async function load() {
    const id = ++requestId;
    clearTimeout(refreshTimer);
    if (currentView !== 'dashboard') return;
    try {
      const data = await json('/api/ops/today?channel=' + encodeURIComponent(picker.value));
      if (id !== requestId) return;
      snapshot = data;
      context.textContent = data.today + (data.last_match ? ' · 最近匹配 ' + String(data.last_match.finished_at || '').slice(5, 16) : ' · 暂无成功匹配')
        + (data.generation_enabled ? '' : ' · 图文生成已暂停');
      render(data);
      renderSources(data.sources);
      if (data.topics.some(topic => topic.job && topic.job.running)) {
        refreshTimer = setTimeout(load, 3000);
      }
    } catch (error) {
      if (id !== requestId) return;
      context.textContent = '今日选题刷新失败。' + errorMessage(error);
      if (snapshot && snapshot.channel === picker.value) {
        refreshTimer = setTimeout(load, 5000);
      } else {
        list.replaceChildren(button('重试', load));
      }
    }
  }

  async function start(topic, channel, target) {
    target.disabled = true;
    const original = target.textContent;
    target.textContent = '提交中…';
    try {
      await json('/api/ops/today/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({channel, slot_key: topic.slot_key, candidate_id: topic.candidate_id}),
      });
      await load();
    } catch (error) {
      target.disabled = false;
      target.textContent = original;
      context.textContent = errorMessage(error);
    }
  }

  picker.addEventListener('change', () => {
    list.replaceChildren(text('p', '正在加载选题…'));
    load();
  });
  panel.querySelector('[data-today-refresh]').addEventListener('click', load);
  window.loadTodayOps = load;
})();
