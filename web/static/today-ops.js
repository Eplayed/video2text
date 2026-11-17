(() => {
  const panel = document.getElementById('today-ops');
  const picker = document.getElementById('today-channel');
  const list = document.getElementById('today-topics');
  const sources = document.getElementById('today-sources');
  const existing = document.getElementById('today-existing');
  const existingList = document.getElementById('today-existing-list');
  const customForm = document.getElementById('today-custom-form');
  const customTheme = document.getElementById('today-custom-theme');
  const customResults = document.getElementById('today-custom-results');
  const customStatus = document.getElementById('today-custom-status');
  let searchId = 0;

  function clearCustom() {
    searchId++;
    customResults.replaceChildren();
    customStatus.textContent = '';
  }

  function busyChannel() {
    return snapshot && snapshot.channel === picker.value
      && snapshot.existing.some(topic => topic.job && topic.job.running);
  }
  const context = document.getElementById('today-context');
  const refresh = panel.querySelector('[data-today-refresh]');
  const loadProgress = panel.querySelector('[data-today-load-progress]');
  let requestId = 0;
  let refreshTimer;
  let snapshot = null;

  function errorMessage(error) {
    return error && error.message ? error.message : String(error);
  }

  async function json(url, options) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_MS);
    try {
      const response = await fetch(url, Object.assign({cache: 'no-store', signal: controller.signal}, options));
      const data = response.ok ? await response.json() : await response.json().catch(() => ({}));
      if (!response.ok || data.error) {
        const error = new Error(data.error || '请求失败（' + response.status + '）');
        error.editRequired = Boolean(data.edit_required);
        throw error;
      }
      return data;
    } catch (error) {
      if (controller.signal.aborted) throw new Error('服务响应超时，请刷新查看');
      throw error;
    } finally {
      clearTimeout(timeout);
    }
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

  function openTopic(topic, channel) {
    const id = Number(topic.summary_id || (topic.job || {}).summary_id);
    if (topic.has_package && id) {
      if (channel === 'wechat' && !topic.poster_package) {
        switchPage('wechat');
        openWechatDetail(id);
      } else {
        gotoPosterPage(id, channel);
      }
    } else if (id) {
      if (topic.custom) openSummary(id);
      else if (topic.form === 'guide') {
        openPosterDialog(id, {channel, checklist: true});
      } else if (channel === 'wechat') {
        openWechatGenDialog(id);
        if (topic.edit_notice || (topic.job || {}).error) wxShowBlockedDraft(id);
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
    const overview = sources.parentElement.querySelector('summary');
    const hasError = items.some(source => source.status === 'error' || source.status === 'fail');
    overview.textContent = hasError ? '素材源最近回执 · 有拉取异常' : '素材源最近回执';
    overview.classList.toggle('today-error', hasError);
    if (!items.length) sources.append(text('p', '暂无素材源回执'));
  }

  function supplementPicker(topic, channel) {
    const details = document.createElement('details');
    details.className = 'today-checks';
    const title = text('summary', '补充资料（可选）');
    const body = document.createElement('div');
    details.append(title, body);
    let state = null;
    let loading = false;
    details.addEventListener('toggle', async () => {
      if (!details.open || state || loading) return;
      loading = true;
      body.textContent = '正在查找与主视频相关的资料…';
      try {
        const query = new URLSearchParams({channel, slot_key: topic.slot_key,
          candidate_id: topic.candidate_id || ''});
        const data = await json('/api/ops/today/supplements?' + query);
        const selected = new Set(data.adopted);
        state = {selected, before: data.adopted};
        body.replaceChildren();
        if (!data.candidates.length) {
          body.append(text('p', '暂无强相关补充资料，可以用主视频直接生成。'));
          return;
        }
        const hint = text('p', '最多勾选 ' + data.max_items + ' 条。官方资料可用于核对口径，其他资料供编辑比对。');
        const rows = document.createElement('ul');
        for (const item of data.candidates) {
          const row = document.createElement('li');
          const label = document.createElement('label');
          label.className = 'today-supplement-option';
          const check = document.createElement('input');
          check.type = 'checkbox';
          check.checked = selected.has(item.id);
          check.addEventListener('change', () => {
            if (check.checked && selected.size >= data.max_items) {
              check.checked = false;
              hint.textContent = '已选满 ' + data.max_items + ' 条，取消一条后可再选。';
              return;
            }
            if (check.checked) selected.add(item.id);
            else selected.delete(item.id);
            title.textContent = '补充资料 · 已选 ' + selected.size + ' 条';
          });
          label.append(check, text('span', item.title + ' · ' + item.tier
            + (item.date ? ' · ' + item.date.slice(0, 10) : '')));
          row.append(label);
          if (item.why) row.append(text('div', item.why, 'today-reason'));
          rows.append(row);
        }
        if (selected.size) title.textContent = '补充资料 · 已选 ' + selected.size + ' 条';
        body.append(hint, rows);
      } catch (error) {
        body.textContent = '补充资料加载失败。' + errorMessage(error) + ' 可合上后重试。';
      } finally {
        loading = false;
      }
    });
    return {details, selection() {
      if (!state) return {};
      const ids = Array.from(state.selected);
      if (ids.length === state.before.length && state.before.every(id => state.selected.has(id))) return {};
      return {supplement_ids: ids, base_supplement_ids: state.before};
    }};
  }

  function render(data) {
    if (!data.topics.length) {
      list.replaceChildren(text('p', '近期没有未做过的新题，可在下方输入话题搜索素材。'));
    } else renderTopics(list, data.topics, data);
    existing.hidden = !data.existing.length;
    existing.querySelector('summary').textContent = '待审与在制稿件 · ' + data.existing.length + ' 篇';
    if (busyChannel()) existing.open = true;
    renderTopics(existingList, data.existing, data);
    const generate = customResults.querySelector('[data-custom-generate]');
    if (generate) generate.disabled = Boolean(busyChannel() || !data.generation_enabled);
  }

  function renderTopics(container, topics, data) {
    container.replaceChildren();
    for (const topic of topics) {
      const job = topic.job || {};
      const notice = job.running ? null : topic.edit_notice;
      const failure = job.error || topic.task_error;
      const card = document.createElement('article');
      card.className = 'today-card';
      card.dataset.slot = topic.slot_key;
      const head = document.createElement('header');
      const stage = job.running ? '生成中' : notice ? '草稿待修改' : topic.stage;
      head.append(text('h3', topic.title), text('span', stage, notice ? 'today-tag today-error' : 'today-tag'));
      card.append(head, text('p', topic.reason, 'today-reason'));
      if (topic.angle) card.append(text('p', topic.angle));
      if (topic.materials.length) {
        const materials = document.createElement('ul');
        materials.className = 'today-materials';
        for (const material of topic.materials) {
          const item = document.createElement('li');
          const heading = material.title === topic.title ? '主视频' : (material.title || '素材 ' + material.video_id);
          const name = [heading, material.author, material.date]
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
      const supplements = !topic.custom && !topic.summary_id && !job.summary_id && !job.running && topic.materials.length
        ? supplementPicker(topic, data.channel) : null;
      if (supplements) card.append(supplements.details);
      const actions = document.createElement('div');
      actions.className = 'today-actions';
      if (topic.has_package) {
        actions.append(button('查看待审稿', () => openTopic(topic, data.channel), true));
        if (topic.form === 'guide' && topic.summary_id) {
          actions.append(button('编辑攻略清单',
            () => openPosterDialog(topic.summary_id, {channel: data.channel, checklist: true})));
        }
      } else if (!job.running && topic.form === 'guide' && (topic.summary_id || job.summary_id)) {
        actions.append(button(topic.custom ? '查看整合稿' : '核对攻略清单', () => openTopic(topic, data.channel), true));
      } else if (notice) {
        actions.append(button('修改草稿', () => openTopic(topic, data.channel), true));
      } else {
        const hasSummary = Boolean(topic.summary_id || job.summary_id);
        const generate = button(job.running ? '生成中…' : hasSummary ? '继续生成图文'
          : failure ? '重试生成' : '生成待审稿',
          () => start(topic, data.channel, generate, supplements ? supplements.selection() : {}), true);
        generate.disabled = Boolean(busyChannel()) || !data.generation_enabled;
        actions.append(generate);
        if (topic.summary_id || job.summary_id) {
          actions.append(button('查看并编辑草稿', () => openTopic(topic, data.channel)));
        }
      }
      card.append(actions);
      if (notice) {
        card.append(text('p', notice.message, 'today-reason'));
        if (notice.issues.length) {
          const details = document.createElement('details');
          details.className = 'today-checks';
          details.append(text('summary', '查看 ' + notice.issues.length + ' 条检查结果'));
          const checks = document.createElement('ul');
          for (const issue of notice.issues) checks.append(text('li', issue));
          details.append(checks);
          card.append(details);
        }
      } else if (job.running || failure) {
        const progress = text('p', job.running ? job.progress : failure,
          job.running ? 'today-reason' : 'today-error');
        if (job.running) {
          progress.dataset.todayProgress = '';
          progress.setAttribute('role', 'status');
          const bar = document.createElement('progress');
          bar.className = 'today-job-progress';
          bar.setAttribute('aria-label', '图文生成进度');
          card.append(bar);
        }
        card.append(progress);
      }
      container.append(card);
    }
  }

  async function poll(id, channel, slot) {
    if (id !== requestId || currentView !== 'dashboard') return;
    const progress = existingList.querySelector('[data-slot="' + slot + '"] [data-today-progress]');
    try {
      const data = await json('/api/ops/today/status?channel=' + encodeURIComponent(channel)
        + '&slot_key=' + encodeURIComponent(slot));
      if (id !== requestId || currentView !== 'dashboard') return;
      if (!data.job || !data.job.running) {
        await load();
        return;
      }
      if (progress && progress.textContent !== data.job.progress) progress.textContent = data.job.progress;
      refreshTimer = setTimeout(() => poll(id, channel, slot), 3000);
    } catch (error) {
      if (id !== requestId || currentView !== 'dashboard') return;
      if (progress) progress.textContent = '暂时读不到生成进度，正在重连。';
      refreshTimer = setTimeout(() => poll(id, channel, slot), 5000);
    }
  }

  async function load(manual = false) {
    const id = ++requestId;
    clearTimeout(refreshTimer);
    if (currentView !== 'dashboard') return;
    refresh.disabled = true;
    refresh.textContent = '刷新中…';
    context.textContent = '正在读取今日选题…';
    loadProgress.hidden = false;
    try {
      const data = await json('/api/ops/today?channel=' + encodeURIComponent(picker.value));
      if (id !== requestId || currentView !== 'dashboard') return;
      const sameTopics = snapshot && snapshot.channel === data.channel
        && JSON.stringify([snapshot.topics, snapshot.existing]) === JSON.stringify([data.topics, data.existing]);
      const generationChanged = snapshot && snapshot.generation_enabled !== data.generation_enabled;
      snapshot = data;
      const refreshed = manual ? (sameTopics ? '已刷新 · 当前选题无变化 · ' : '已刷新 · ') : '';
      context.textContent = refreshed + data.today + (data.last_match ? ' · 最近成功匹配 ' + String(data.last_match.finished_at || '').slice(5, 16) : ' · 暂无成功匹配')
        + (data.match_error ? ' · 最新匹配失败：' + data.match_error : '')
        + (data.text_model ? ' · ' + data.text_model : ' · 待配置 DeepSeek V4 Pro')
        + (data.text_model && !data.generation_enabled ? ' · 图文生成已暂停' : '');
      if (!manual || !sameTopics || generationChanged) render(data);
      renderSources(data.sources);
      const active = data.existing.find(topic => topic.job && topic.job.running);
      if (active) refreshTimer = setTimeout(() => poll(id, data.channel, active.slot_key), 3000);
    } catch (error) {
      if (id !== requestId || currentView !== 'dashboard') return;
      context.textContent = '今日选题刷新失败。' + errorMessage(error);
      if (snapshot && snapshot.channel === picker.value) {
        if (snapshot.existing.some(topic => topic.job && topic.job.running)) {
          refreshTimer = setTimeout(load, 5000);
        }
      } else {
        list.replaceChildren(button('重试', () => load(true)));
      }
    } finally {
      if (id === requestId) {
        refresh.disabled = false;
        refresh.textContent = '刷新';
        loadProgress.hidden = true;
      }
    }
  }

  async function start(topic, channel, target, supplements) {
    target.disabled = true;
    const original = target.textContent;
    target.textContent = '提交中…';
    try {
      await json('/api/ops/today/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(Object.assign({channel, slot_key: topic.slot_key, candidate_id: topic.candidate_id}, supplements)),
      });
      await load();
    } catch (error) {
      if (channel !== picker.value || currentView !== 'dashboard') return;
      if (error.editRequired) {
        await load();
        return;
      }
      target.disabled = false;
      target.textContent = original;
      context.textContent = errorMessage(error);
    }
  }

  customTheme.addEventListener('input', clearCustom);
  customForm.addEventListener('submit', async event => {
    event.preventDefault();
    clearCustom();
    const id = searchId;
    const channel = picker.value;
    const theme = customTheme.value.trim();
    customStatus.textContent = '正在搜索素材库…';
    try {
      const data = await json('/api/ops/today/custom?' + new URLSearchParams({channel, theme}));
      if (id !== searchId) return;
      if (!data.materials.length) {
        customStatus.textContent = '没有找到有转写的相关视频，可以换一两个具体关键词。';
        return;
      }
      const rows = document.createElement('ul');
      for (const material of data.materials) {
        const row = document.createElement('li');
        const label = document.createElement('label');
        label.className = 'today-supplement-option';
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.value = material.video_id;
        label.append(check, text('span', [material.title, material.author, material.date,
          material.version_note].filter(Boolean).join(' · ')));
        row.append(label);
        if (material.sheet && material.row) {
          const view = button('查看素材', () => gotoMaterial(material.sheet, Number(material.row)));
          view.className = 'today-link';
          row.append(view);
        }
        rows.append(row);
      }
      const generate = button('生成整合稿', async () => {
        const video_ids = Array.from(rows.querySelectorAll('input:checked'), item => Number(item.value));
        if (!video_ids.length || video_ids.length > 4) {
          customStatus.textContent = '请勾选 1 至 4 条素材。';
          return;
        }
        generate.disabled = true;
        customStatus.textContent = '正在提交整合稿…';
        try {
          await json('/api/ops/today/custom', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({channel, theme, video_ids}),
          });
          if (id !== searchId) return;
          customStatus.textContent = '已提交，进度和稿件见下方「待审与在制稿件」。';
          existing.open = true;
          await load();
        } catch (error) {
          if (id !== searchId) return;
          customStatus.textContent = errorMessage(error);
          generate.disabled = Boolean(busyChannel());
        }
      }, true);
      generate.dataset.customGenerate = '';
      generate.disabled = Boolean(busyChannel() || (snapshot && !snapshot.generation_enabled));
      customResults.append(rows, generate);
      customStatus.textContent = '勾选 1 至 4 条素材。选多条会合并成一篇整合稿。';
    } catch (error) {
      if (id === searchId) customStatus.textContent = errorMessage(error);
    }
  });

  picker.addEventListener('change', () => {
    clearCustom();
    existing.open = false;
    existing.hidden = true;
    existingList.replaceChildren();
    list.replaceChildren(text('p', '正在加载选题…'));
    load();
  });
  refresh.addEventListener('click', () => load(true));
  window.loadTodayOps = load;
})();
