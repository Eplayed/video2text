/* schedule-series.js — 排产页「两个系列 + 今晚候选」这一块的全部前端逻辑。
 *
 * 为什么单独一个文件：判据与文案只在后端 `src/plan_series.py` 一份（八档状态、每档依据、
 * 检索词、边界、匹配开关与时刻都由 `/api/plan/series` 下发），这里只负责渲染与把人的勾选交回去。
 * 页面要是自己算一遍"发过没有 / 有没有候选"，过两周就会和日报、和台账互相说反话
 * （同一件事两份账，本项目反复栽过：usable=17 与界面 0 张、界面写 0 元后端照样扣钱）。
 *
 * 三条不越界：不自动采用、不自动生成、不出图不发布。所有写库都从按钮走。
 */
var SR = null;              // 看板整块（{slots, cfg, runs, stages, ...}）
var SRMAP = {};             // slot_key → 这一格
var SRPICK = {};            // 界面上勾了哪几条候选（只是待提交的选择，不是事实）
/* 视频素材那一行下面的「相关非视频」子集（2026-10-28 号主：照勾选素材那套形式，父行可展开、子集也多选）。
   判据、分级、上限全在后端 src/supplements.py 一份（真库实测命中率 5%，宁缺毋滥），
   这里只做三件事：点开才查（懒查，一次约 0.2s）、把勾选暂存在本地、点「采用这一格」时一起交回去。 */
var SRVideoSheets = ['抖音视频数据', 'B站视频'];   // 权威在 src/supplements.py 的 VIDEO_SHEETS，e2e 有对表断言
/* 候选行上的来源标记。非视频那条**给不出关键帧**，这件事必须在勾选之前就看见——
   2026-10-29 号主在「视频素材」这一栏勾了一条 NGA 帖，一路走到海报「② 看版面」才发现
   一张关键帧都没有，而那一栏的名字就叫视频素材。名单权威在后端那一份，这里只出人话标签。 */
var SR_SRC_LABEL = {'NGA无限板块': '论坛帖', '微信文章': '公众号文章',
                    '暴雪官方': '官方公告', '抖音图文': '图文'};
function srSrcTag(c) {
  var sh = c.sheet || '';
  if (SRVideoSheets.indexOf(sh) >= 0) return '';
  return '<span class="sr-novid" title="这条不是视频：没有画面可抽，海报攻略页要的「关键帧」它给不出。'
    + '要出图就换一条视频素材，或把它当补充资料勾在视频下面">'
    + esc(SR_SRC_LABEL[sh] || (sh || '未知来源')) + ' · 无关键帧</span>';
}
var SRSUB = {};             // 'slot:父素材id' → {list, scanned, err, max, not_video}
var SRSUB_OPEN = {};        // 同一个键 → 是否展开着（重画时保持）
var SRPICK_SUP = {};        // slot → {子素材id: 父素材id}，同样是"待提交的选择"
var SRNOTE = {};            // slot → 一句"这次替你做了什么"（中性那条，不占报错的红框）
var SRFORM = {};            // 展开着登记表单的槽号
var SR_OPEN = {};           // 哪几格被人展开过（重画时保持）

function srApi(path, body) {
  if (body === undefined) {
    return fetch(path, {cache: 'no-store'}).then(function (r) { return r.json(); });
  }
  return fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify(body)}).then(function (r) { return r.json(); });
}

function srErr(msg) {
  var box = document.getElementById('sr-err');
  if (!box) return;
  box.textContent = msg || '';
  box.style.display = msg ? 'block' : 'none';
}

function loadSeries(manual) {
  return srApi('/api/plan/series').then(function (d) {
    if (d.error) { srErr('看板读不到：' + d.error); return; }
    SR = d; SRMAP = {};
    (d.slots || []).forEach(function (s) { SRMAP[s.slot_key] = s; });
    srErr('');
    renderSeries();
    // 这里原来还有一句 render()（重画按日期排的那批卡）——那块 2026-10-29 删了，调用一并去掉。
    if (manual) { var m = document.getElementById('sr-meta');
      if (m) m.textContent = '看板已重读 · ' + d.generated_at; }
    srReportLegacy();
  }).catch(function (e) { srErr('看板请求失败：' + e.message); });
}

/* 旧浏览器里勾过"已发布"、服务端却没登记的那些 → 上报一次，界面标「待核对旧勾选」。
   只上报，不改服务端事实：静默清空或悄悄当成已登记，都是任务书明令禁止的。 */
var SR_REPORTED = false;
function srReportLegacy() {
  // 页面里那份全局 `state` 随「按日期排发布」那块一起删了（2026-10-29），但旧浏览器
  // 勾过「已发布」却没上报的记录还在 localStorage 那个键里。自己读，不许跟着一起失效——
  // 静默丢掉这些勾选是任务书明令禁止的（既不清空也不当成已登记，只能报上去标「待核对」）。
  var state = {};
  try { state = JSON.parse(localStorage.getItem('v2t:schedule:v1') || '{}') || {}; }
  catch (e) { state = {}; }
  if (SR_REPORTED || typeof state !== 'object' || !Object.keys(state).length) return;
  SR_REPORTED = true;
  var jobs = [];
  Object.keys(state || {}).forEach(function (id) {
    var s = state[id] || {};
    if (!s.published) return;
    var srv = SRMAP[id];
    if (srv && srv.published) return;            // 服务端已有登记，不用报
    jobs.push(srApi('/api/plan/publish', {slot_key: id, action: 'report_local',
                                          local_published: true,
                                          note: '这台浏览器勾过已发布（旧 localStorage）'}));
  });
  // 上报完必须**等上报落库**再重读看板：「待核对旧勾选」是服务端算出来的状态，
  // 前端不许自己标。第一版是发完就立刻重读，GET 抢在 POST 之前到，
  // 库里明明记了、界面还显示"有候选"（真页面走查抓到）。
  if (jobs.length) {
    Promise.all(jobs).then(function () { loadSeries(); }).catch(function () {});
  }
}

function srBadge(s) {
  if (s.pending_review) return '<span class="sr-badge b-pending_review">待核对旧勾选</span>';
  return '<span class="sr-badge b-' + s.stage + '">' + esc(s.stage_label) + '</span>';
}

function srRunBar() {
  var c = SR.cfg || {}, st = SR.status || {};
  var last = st.last_ok || st.last_failed;
  var bits = [];
  bits.push('<label class="hint"><input type="checkbox" id="sr-enabled"' +
            (c.enabled ? ' checked' : '') + ' onchange="srSaveCfg()"> 夜间匹配开</label>');
  bits.push('<span class="sep"></span>');
  bits.push('<span>计划时刻 <input type="time" id="sr-runat" value="' + esc(c.run_at || '22:00') +
            '" onchange="srSaveCfg()"> ' + esc(st.tz || '') + '</span>');
  bits.push('<span class="sep"></span>');
  bits.push('<span>下次 <b>' + esc(st.next || '-') + '</b></span>');
  if (last) {
    bits.push('<span>上次' + (last.status === 'ok' ? '成功' : '失败') + ' <b>' +
              esc(last.finished_at || last.started_at || '') + '</b>（新增 ' + (last.added || 0) +
              ' · 重复 ' + (last.seen || 0) + ' · 跳过 ' + (last.skipped || 0) + '）' +
              // 挡掉多少条不合第一级口径的，只有服务端那一份数（前端不许自己估）
              (last.note ? '<span class="hint"> ' + esc(last.note) + '</span>' : '') + '</span>');
  }
  bits.push('<button class="btn sm" onclick="srMatchNow(this)">现在匹配</button>');
  bits.push('<span class="sep"></span>');
  bits.push('<span class="hint">' + esc((st.due && st.due.why) || '') + '</span>');
  var run = document.getElementById('sr-run');
  if (run) run.innerHTML = bits.join(' ');
  var note = document.getElementById('sr-meta');
  if (note) note.textContent = '看板 ' + (SR.today || '') + ' · 位置 ' + (SR.slots || []).length +
                               ' · 今晚新增候选见各格徽章';
}

function renderSeries() {
  if (!SR) return;
  // 「已采用」那几行的复选框默认勾着 = 库里这一版的主素材清单。只在**没动过**的时候灌
  // （`SRPICK[key]` 不存在）；人取消勾选后那个键一定存在（`srPick` 只删条目、不删整格），
  // 所以清空过的格子不会被偷偷灌回去——界面说的必须是人点的，不是我以为的。
  (SR.slots || []).forEach(function (s) {
    if (SRPICK[s.slot_key] || !(s.adopted_ids || []).length) return;
    var m = {};
    (s.adopted_ids || []).forEach(function (i) { m[i] = true; });
    SRPICK[s.slot_key] = m;
  });
  srRunBar();
  srBody();
}

function srBody() {
  var host = document.getElementById('sr-body');
  if (!host) return;
  // 旧排产题（series='legacy'）不在这里列：2026-10-29 号主把「按日期节点排发布」那一整块
  // 从排产表去掉了，这 11 题也不再出现在页面上——但**槽位与台账仍在服务端** plan_slots 里
  // （origin='legacy'，夜间匹配与「已发布」照旧认它），只是没有渲染位。
  // 要看那 11 题的选题与依据：docs/11-05排产表-2026-10-16.md。
  var groups = [['s1', 'S1'], ['s2', 'S2'], ['free', '独立篇']];
  var html = groups.map(function (g) {
    var key = g[0];
    var mine = (SR.slots || []).filter(function (s) { return s.series === key; });
    if (!mine.length) return '';   // 独立篇一条都没有时整块不出现（空块比不写更骗人）
    var withCand = mine.filter(function (s) { return s.candidate_total > 0; }).length;
    var pub = mine.filter(function (s) { return s.published; }).length;
    var inner = '';
    if (key === 's1') {
      var chs = {};
      mine.forEach(function (s) { (chs[s.chapter] = chs[s.chapter] || []).push(s); });
      inner = Object.keys(chs).sort().map(function (ch) {
        var list = chs[ch];
        var lab = (list[0].angle || '');
        return '<details class="sr-ch"' + (ch === 'A' ? ' open' : '') + '><summary><b>' + esc(ch) +
               ' ' + esc((SR.chapter_labels || {})[ch] || '') + '</b> ' + list.length + ' 个位置 · ' +
               list.filter(function (s) { return s.candidate_total; }).length + ' 个有候选' +
               '<div style="font-weight:400;color:var(--text-secondary);font-size:12px;margin-top:2px">' +
               esc(list[0].boundary || lab) + '</div></summary>' +
               list.map(srCard).join('') + '</details>';
      }).join('');
    } else {
      inner = mine.map(srCard).join('');
    }
    return '<details class="sr-ser" open><summary><span class="car">▸</span>' +
           esc(SR.series_labels[key] || key) +
           '<span class="sr-cnt">' + mine.length + ' 格 · 有候选 ' + withCand + ' · 已发布 ' + pub +
           '</span></summary>' + inner + '</details>';
  }).join('');
  if (!html) html = '<div class="hint">还没有注册的内容位置</div>';
  var legacyN = (SR.slots || []).filter(function (s) { return s.series === 'legacy'; }).length;
  if (legacyN) {
    // 不许说"仍在下面原列表里"——那块 2026-10-29 已经删了，说了就是假话。
    html += '<div class="sr-note">旧排产题 ' + legacyN + ' 条不再按日期排期（槽位与「已发布」'
          + '台账还在服务端，夜间匹配照旧认它们）；选题与依据看 docs/11-05排产表-2026-10-16.md。</div>';
  }
  host.innerHTML = html;
}

function srCard(s) {
  var cls = 'sr-card' + (s.published ? ' pub' : (s.pending_review ? ' pend' :
            (s.candidate_total ? ' has' : '')));
  // 摘要行：位置号 + 标题 + 状态徽章 + 一句话计数。40 张卡全展开是 13000 多像素的一堵墙
  // （第一版就这样，走查截图里根本找不到"今晚新增候选"在哪），所以默认折成一行。
  var mini = [];
  if (s.candidate_total) mini.push('候选 ' + s.candidate_total);
  if ((s.adopted_ids || []).length) mini.push('已采用 ' + s.adopted_ids.length);
  if ((s.publish_rows || []).length) mini.push('台账 ' + s.publish_rows.length + ' 行');
  if (s.parent_slot) {
    // 关联是"这篇稿归在哪个位置下"，不是"多了一个位置"（第 4 条反例：原来会把 26 数成 27）
    mini.push('归入 ' + (((SRMAP[s.parent_slot] || {}).position) || s.parent_slot));
  }
  var head = '<summary class="sr-h"><span class="car">▸</span>' +
             '<span class="sr-pn">' + esc(s.position || s.slot_key) + '</span>' +
             '<span class="sr-tt">' + esc(s.title || s.slot_key) + '</span>' + srBadge(s) +
             '<span class="sr-mini">' + esc(mini.join(' · ')) + '</span></summary>';
  var why = '<div class="sr-why">' + esc(s.stage_why || '') + '</div>';
  var did = SRNOTE[s.slot_key] ? '<div class="sr-note">' + esc(SRNOTE[s.slot_key]) + '</div>' : '';
  var cand = '';
  if ((s.candidates || []).length) {
    var picked = SRPICK[s.slot_key] || {};
    cand = '<div class="sr-cand">' + s.candidates.map(function (c) {
      var one = '<label><input type="checkbox" data-slot="' + esc(s.slot_key) + '" data-vid="' +
             esc(c.video_id) + '"' + (picked[c.video_id] ? ' checked' : '') +
             ' onchange="srPick(this)">' +
             '<span class="fb f-' + esc(c.forever) + '">' +
             (c.forever === 'yes' ? 'Forever' : '待人工判定') + '</span>' +
             srSrcTag(c) +
             '<a href="' + esc(srHref(c)) + '" target="_blank" rel="noreferrer">' +
             esc(c.title || '（无标题）') + '</a>' +
             '<span class="src">' + esc(srReasonNoDate(c.reason)) + '</span>' + srDateTag(c)
             + srFramesTag(c) + '</label>';
      // 只有视频素材那一行下面挂子集；非视频那条本身也能被采用当主素材，不动它
      if (SRVideoSheets.indexOf(c.sheet || '') < 0) return '<div class="sr-ci">' + one + '</div>';
      return '<div class="sr-ci">' + one + srSubToggleHtml(s.slot_key, c) + srSubHtml(s.slot_key, c) + '</div>';
    }).join('') + '</div>';
  } else if ((s.candidate_hidden || 0) > 0) {
    // 候选变少/变空要当场说清是"按口径挡掉的"，不是"匹配坏了"——10-29 那次报障就是这么来的
    cand = '<div class="sr-note">这一格没有可列的第一级候选：' + esc(s.hidden_note || '不合判据的')
         + '不算在内。第一级只列视频素材，论坛帖／公众号文章要挂在视频素材下面的「相关非视频」里。</div>';
  } else if (s.stage === 'unmatched' && !s.task) {
    cand = '<div class="sr-note">这一格今晚没匹配到素材（零就是零，不拿旧料填位）。</div>';
  }
  var ado = '';
  if ((s.adopted_ids || []).length) {
    // 采用之后父行会从候选里挪走（候选是候选、已采用是已采用，这条规矩不动），
    // 但子集得跟着人走——所以已采用那几条也画成可展开的父行，采用后还能继续勾补充料
    var pk = SRPICK[s.slot_key] || {};
    ado = '<div class="sr-ado"><b>已采用 ' + s.adopted_ids.length + ' 条</b>'
          // 号主 2026-10-30 问"已采用是不是不能再勾了"：以前这几行没有复选框，
          // 点「采用」又是整组替换，于是"想再加一条"必然把旧的挤掉。现在它们默认勾着，
          // 勾着＝留在这一版，取消＝从这一版移出，再勾一条候选＝新增，语义终于对上那颗按钮。
          + '<span class="src">（勾着的算这一版的主素材：取消勾选＝移出，再勾候选＝新增）</span>'
          + '：素材 id ' +
          esc(s.adopted_ids.join('、')) + (s.task ? ' ｜ 任务 ' + esc(s.task.status) +
          (s.task.summary_id ? '（稿 id=' + s.task.summary_id + '）' : '') : '') + '</div>'
      // 注意：这一节**不叫 .sr-cand**——它是"已采用"，不是候选。
      // 借那个 class 会被"折叠时候选列表不占屏"那条判据算进去（走查真的抓到了）
      + '<div class="sr-ado-rows">' + (s.adopted_rows || []).map(function (a) {
          var one = '<label><input type="checkbox" data-slot="' + esc(s.slot_key) + '" data-vid="' +
                  esc(a.video_id) + '"' + (pk[a.video_id] ? ' checked' : '') +
                  ' onchange="srPick(this)"><span class="fb f-yes">已采用</span>' + srSrcTag(a)
                  + '<a href="' + esc(srHref(a)) + '" target="_blank" rel="noreferrer">'
                  + esc(a.title || ('素材 ' + a.video_id)) + '</a>'
                  + '<span class="src">' + esc(a.author || '') + '｜' + esc(a.sheet || '表未记录')
                  + '</span>' + srDateTag(a) + srFramesTag(a) + '</label>';
          if (SRVideoSheets.indexOf(a.sheet || '') < 0) return '<div class="sr-ci is-ado">' + one + '</div>';
          return '<div class="sr-ci is-ado" data-pvid="' + a.video_id + '">' + one
                 + srSubToggleHtml(s.slot_key, a, true) + srSubHtml(s.slot_key, a) + '</div>';
        }).join('') + '</div>';
  }
  var acts = [];
  if (s.adoptable) {
    acts.push('<button class="btn sm" onclick="srAdopt(\'' + esc(s.slot_key) + '\',this)">' +
              '采用勾选项到本篇</button>');
  } else {
    acts.push(srStartCtl(s));
  }
  if ((s.adopted_ids || []).length) {
    acts.push('<button class="btn sm pri" onclick="srOpenTask(\'' + esc(s.slot_key) + '\')">' +
              (s.task && s.task.summary_id ? '继续这一篇（整合稿/海报）' : '生成整合稿') + '</button>');
  }
  if (s.published) {
    acts.push('<button class="btn sm" onclick="srRevoke(\'' + esc(s.slot_key) + '\')">撤销这条发布登记</button>');
  } else if (SRFORM[s.slot_key]) {
    acts.push('');
  } else {
    acts.push('<button class="btn sm" onclick="srShowPub(\'' + esc(s.slot_key) + '\')">' +
              '登记已发布</button>');
  }
  var form = SRFORM[s.slot_key] ? srPubForm(s) : '';
  // 挂在这个位置下的关联稿（独立稿事后归进来只写 parent_slot，见 plan_series.link_to_series）
  var kids = (s.linked_slots || []).map(function (k) {
    return '<div class="sr-kid">关联稿：<b>' + esc(k.title) + '</b>（' + esc(k.slot_key) +
           ' · ' + esc(k.stage_label || '') + '）' +
           (k.has_task ? ' <button class="btn sm" onclick="srOpenTask(\'' + esc(k.slot_key) +
           '\')">继续这一篇</button>' : '') + '</div>';
  }).join('');
  var review = s.pending_review ? '<div class="sr-note" style="color:#8a5a24">' +
    esc(s.review_note || '这台浏览器勾过已发布，服务端查无登记') +
    ' ｜ <button class="btn sm" onclick="srResolve(\'' + esc(s.slot_key) +
    '\',\'not_published\')">其实没发过</button></div>' : '';
  // 展开状态由这里自己记：loadSeries 每次重画都重建 DOM，不记就会把人正看着的那格折回去
  var open = SR_OPEN[s.slot_key] || s.pending_review ||
             (s.stage === 'ready' || s.stage === 'layout');
  return '<details class="' + cls + '" id="src-' + esc(s.slot_key) + '"' +
         (open ? ' open' : '') + ' ontoggle="srToggleCard(this)">' + head +
         '<div class="sr-in">' + why + did + review + ado + cand + kids +
         '<div class="sr-act">' + acts.join('') + '</div>' + form + '</div></details>';
}

function srHref(c) {
  return '/?open=' + encodeURIComponent((c.sheet || '') + ':' + (c.row || ''));
}

function srToggleCard(el) {
  // 记展开状态必须挂在 details 的 **ontoggle**（状态变完之后才触发），不能挂在 summary 的 onclick 上：
  // onclick 跑在浏览器默认动作**之前**，那一刻 `box.open` 还是旧值，记下来的是反的——
  // 表现是"人把格子点开、再点登记已发布，整块又被折回去"，表单在 DOM 里但看不见
  // （走查 7d 撞到的正是这个，判据是 `#pt-t-<槽号>` 等 10 秒等不到）。
  var box = el && el.closest ? el.closest('.sr-card') : null;
  if (box) SR_OPEN[box.id.replace('src-', '')] = !!box.open;
}

function srPick(el) {
  var k = el.dataset.slot, id = el.dataset.vid;
  SRPICK[k] = SRPICK[k] || {};
  if (el.checked) SRPICK[k][id] = true; else delete SRPICK[k][id];
}

function srIds(key) {
  var picked = SRPICK[key] || {};
  return Object.keys(picked).map(Number).filter(function (n) { return n > 0; });
}

/* ── 视频素材那一行下面的「相关非视频」子集 ──
 * 判据、分级、上限全在后端 src/supplements.py 一份（真库实测命中率 5%，宁缺毋滥）。
 * 这里只做三件事：点开一条才算一次（懒查，一次约 0.2s；26 格全预查要几十秒）、
 * 勾选先落在本地镜像、有任务行就当场存进这一版（还没采用则等点「采用这一格」时一起交）。
 */
function srSubKey(slot, vid) { return slot + ':' + vid; }
function srSupMax(slot) {
  var g = SRMAP[slot] || {}, m = 3;
  (g.candidates || []).forEach(function (c) {
    var L = SRSUB[srSubKey(slot, c.video_id)];
    if (L && L.max) m = L.max;
  });
  return m;
}
function srSupCur(slot) {
  // 本地镜像只在"没暂存过"的时候从服务端那一版灌一次；
  // 存成功之后调用方会 delete 掉，让下一次重新读服务端——不然"刚存进去的"会显示成没勾
  if (SRPICK_SUP[slot] === undefined) {
    var t = (SRMAP[slot] || {}).task || {};
    SRPICK_SUP[slot] = {};
    (t.supplement_ids || []).forEach(function (i) { SRPICK_SUP[slot][i] = 0; });
  }
  return SRPICK_SUP[slot];
}
function srSupIds(slot) { return Object.keys(srSupCur(slot)).map(Number); }
function srSupStarted(slot) {
  // 只用来决定提示文案怎么说（"改了要重新整理才会带上"），不再拦着人不让勾——
  // 号主 2026-10-28 点名取消"已开工就冻结"：换一条参考料不该逼出一版新的、再花一次模型钱
  var t = (SRMAP[slot] || {}).task;
  return !!t && (t.status === 'running' || t.status === 'done'
                 || (t.status === 'failed' && t.summary_id));
}
function srSlotHasSup(slot) {
  // 这一格里有没有已经存着的补充料（人勾的、或任务行里存着的）
  return srSupIds(slot).length > 0;
}
// 素材日期按"距今几天"上色（号主 11-02 定的档）：<3 天绿、3–5 天浅绿、5–7 天橙。
// 整 3 天算浅绿、整 5 天算橙（他说的是"大于3小于5"，边界不自己发明第二套说法）；
// ≥7 天他没定，这里补一档更深的红棕——旧料要一眼看得出来，不能到了 7 天又变回普通灰字。
// 天数拿**看板那一天**算，不拿浏览器时钟：看板是夜里刷的，人可能隔天早上才打开页面，
// 用浏览器时间会让同一条素材在两个人屏幕上颜色不一样。
var SR_DATE_TIERS = [
  {lt: 3, cls: 'dt-fresh', name: '三天内'},
  {lt: 5, cls: 'dt-ok', name: '三到五天'},
  {lt: 7, cls: 'dt-warn', name: '五到七天'},
  {lt: 1e9, cls: 'dt-old', name: '七天以上'}
];
function srDateDaysAgo(d) {
  // 只认 YYYY-MM-DD；拿组件构造日期（`new Date('2026-11-02')` 按 UTC 解析，
  // 东八区会整体差一天，颜色就错一档）
  var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d || ''));
  var t = /^(\d{4})-(\d{2})-(\d{2})/.exec(String((SR && SR.today) || ''));
  if (!m || !t) return null;
  var a = new Date(+t[1], +t[2] - 1, +t[3]).getTime();
  var b = new Date(+m[1], +m[2] - 1, +m[3]).getTime();
  if (isNaN(a) || isNaN(b)) return null;
  return Math.round((a - b) / 86400000);
}
function srDateTag(x) {
  var d = String((x && x.date) || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return '<span class="dt dt-none">无日期</span>';
  var ago = srDateDaysAgo(d);
  if (ago === null || ago < 0)      // 看板那天读不出、或素材日期在将来：不猜颜色
    return '<span class="dt dt-none">' + esc(d) + '</span>';
  var tier = SR_DATE_TIERS[SR_DATE_TIERS.length - 1];
  for (var i = 0; i < SR_DATE_TIERS.length; i++) {
    if (ago < SR_DATE_TIERS[i].lt) { tier = SR_DATE_TIERS[i]; break; }
  }
  return '<span class="dt ' + tier.cls + '" title="素材发布 ' + esc(d) + '，距看板这天 '
    + ago + ' 天（' + tier.name + '）">' + esc(d) + '</span>';
}
function srReasonNoDate(r) {
  // 旧候选的理由里带着日期（「…｜2026-10-31｜正文 1200 字…」），而现在日期是行上一枚
  // 按"距今几天"上色的标签，同一天念两遍就成噪音。理由要等下一轮匹配才会重写，
  // 而且**没被选中的存量候选根本不会被重写**，所以这里渲染时摘掉那一段。
  // 摘的是我们自己拼的固定格式（前后都有全角竖线），不是去猜自然语言。
  return String(r || '').replace(/｜\d{4}-\d{2}-\d{2}(?=｜)/, '');
}
function srFramesTag(c) {
  // 画面账由后端一次批量数出来（`src/frame_pool.counts_for`，判据与海报弹窗同一份：
  // 文件在、判过、判为干净才算"能用"）。
  // 为什么摆到排产格上：号主挑素材第一问就是"这条有没有图"，而这句话以前只有海报弹窗
  // （要先有整合稿）才看得到——B 站那批 18 张帧明明在盘上，格子里却像"没有关键帧"（11-02）。
  // 三种状态分开说，别糊成一句"没有画面"：没抽过帧 / 抽了没看图 / 看了全不合格。
  if (SRVideoSheets.indexOf(c.sheet || '') < 0) return '';
  var f = c.frames;
  if (!f) return '<span class="src warn">没抽过帧</span>';
  if (f.usable) return '<span class="src">画面 ' + f.usable + '/' + f.total + ' 张能用</span>';
  return '<span class="src warn">' + (f.judged
    ? f.total + ' 张帧判过 ' + f.judged + ' 张，一张都不能用'
    : f.total + ' 张帧还没逐张看图（看图那一步才花钱）') + '</span>';
}
function srSubToggleHtml(slot, c, isAdo) {
  // sup_n 由后端一次批量数出来（src/supplements.counts_for）。数出 0 条就不画这颗按钮：
  // 点开来只会得到一句"没找到强相关的"，那是浪费人的手（号主 2026-10-30）。
  // 只有**明确数出 0** 才不画——null 是没数过（例如刚采用、还没刷看板），别拿"没数过"当"没有"。
  // 例外：已采用那一行，只要这一格里还存着补充料就留着——勾过的东西得看得见、能取消，
  // 判据以后漂移了它可能已经不在提名名单里（第一版把这条兜底加在**所有**候选行上，
  // 走查当场抓到：s1a1 那格因此多挂 6 颗点开是空的按钮）。
  if (c.sup_n === 0 && !(isAdo && srSlotHasSup(slot))) return '';
  var k = srSubKey(slot, c.video_id), L = SRSUB[k];
  var n = (L && !L.error) ? (' ' + (L.list || []).length) : (c.sup_n ? (' ' + c.sup_n) : '');
  return '<button class="sr-more" onclick="srSubToggle(\'' + esc(slot) + '\',' + c.video_id + ',this)">'
    + (SRSUB_OPEN[k] ? '▾' : '▸') + ' 相关非视频' + n + '</button>';
}
function srSubHtml(slot, c) {
  var k = srSubKey(slot, c.video_id);
  if (!SRSUB_OPEN[k]) return '<div class="sr-sub" id="ss-' + esc(slot) + '-' + c.video_id + '"></div>';
  var L = SRSUB[k];
  var box = '<div class="sr-sub" id="ss-' + esc(slot) + '-' + c.video_id + '">';
  if (!L) return box + '<div class="sr-subt">正在按标题与发布日找强相关的非视频料…</div></div>';
  if (L.error) return box + '<div class="sr-subt bad">' + esc(L.error) + '</div></div>';
  var sel = srSupIds(slot), started = srSupStarted(slot), max = srSupMax(slot);
  var rows = (L.list || []).map(function (x) {
    var on = sel.indexOf(x.id) >= 0;
    var dis = !on && sel.length >= max;      // 只按上限禁用，开工不再拦勾选
    return '<label><input type="checkbox" data-slot="' + esc(slot) + '" data-vid="' + x.id +
           '" data-parent="' + c.video_id + '"' + (on ? ' checked' : '') + (dis ? ' disabled' : '') +
           ' onchange="srPickSup(this)">' +
           '<span class="fb f-yes">' + esc(x.tier || '素材') + '</span>' +
           '<a href="' + esc(srHref({sheet: x.sheet, row: x.row})) + '" target="_blank" rel="noreferrer">' +
           esc(x.title || '（无标题）') + '</a>' +
           '<span class="src">' + esc(x.author || '') + '｜' + srDateTag(x) + '｜' +
           esc(x.why || '') + '</span></label>';
  }).join('');
  if (!rows) return box + '<div class="sr-subt">没找到强相关的（扫了 ' + (L.scanned || 0) +
    ' 条非视频料）。判据：标题有共同片段且发布日相差 ≤10 天——硬凑上来的资料比没有更坏。</div></div>';
  return box + rows + '<div class="sr-subt' + (started ? ' warn' : '') + '">' +
    '勾上的作为「补充资料」进这一版的提示词，最多 ' + max + ' 条；它们不当主素材、不冒充官方口径。'
    + ((L.count_all || 0) > (L.list || []).length
        ? '另有 ' + ((L.count_all || 0) - (L.list || []).length) + ' 条分低没列进来。' : '')
    + (started ? '<b>这一版已经出过稿——改了要点「重新整理一版」才会带上，已发出去的那篇不会变。</b>' : '')
    + '</div></div>';
}
function srSubToggle(slot, vid, btn) {
  var k = srSubKey(slot, vid);
  SRSUB_OPEN[k] = !SRSUB_OPEN[k];
  if (SRSUB_OPEN[k] && !SRSUB[k]) {
    SRSUB[k] = null;
    srApi('/api/material/supplements?video_id=' + encodeURIComponent(vid)).then(function (d) {
      SRSUB[k] = (d && d.error) ? {error: d.error}
        : {list: d.candidates || [], scanned: d.scanned || 0, count_all: d.count_all || 0,
           max: d.max_items || 3, not_video: !!d.not_video};
      renderSeries();
    }, function (e) {
      SRSUB[k] = {error: '查不到：' + e.message + '（没查成不等于没有相关料，可以再点开一次）'};
      renderSeries();
    });
  }
  renderSeries();
}
function srPickSup(el) {
  var slot = el.dataset.slot, vid = Number(el.dataset.vid), cur = srSupCur(slot);
  if (el.checked) cur[vid] = Number(el.dataset.parent || 0); else delete cur[vid];
  var t = (SRMAP[slot] || {}).task;
  if (!t) { renderSeries(); return; }              // 还没采用：先暂存，点「采用这一格」时一起交
  srApi('/api/plan/supplements', {task_id: t.id, video_ids: srSupIds(slot)}).then(function (j) {
    if (j && j.error) { srErr('补充料存不下来：' + j.error); loadSeries(); return; }
    // 存成功就丢掉本地镜像，让下一次从服务端重读（界面说的必须是库里存的）
    srErr('');
    delete SRPICK_SUP[slot];
    loadSeries();
  }, function (e) { srErr('补充料存不下来：' + e.message); loadSeries(); });
}

function srAdopt(key, btn) {
  var ids = srIds(key);
  if (!ids.length) { alert('先勾一条或多条候选素材（勾了才算你要的那几条）'); return; }
  var supAll = srSupIds(key);
  if (supAll.length > srSupMax(key)) {
    alert('补充料勾了 ' + supAll.length + ' 条，最多 ' + srSupMax(key) + ' 条——先去掉几条再采用');
    return;
  }
  // **补充料跟着它那条主素材走**（号主 2026-10-30）。两种情况都要丢，否则后端清了也会被这次
  // POST 塞回新版：① 这一版有素材被**移掉**了 → 挂在它下面的配角没有归属，整格重来；
  // ② 某条配角是勾在**这次没被采用的那条候选**下面 → 只丢那一条，其余保留。
  // ① 只认"移除了旧的"：只加不减（原来勾着的素材都还在）不清补充料——后端是同一份口径。
  var t0 = (SRMAP[key] || {}).task || {};
  var oldIds = (t0.video_ids || []).map(Number);
  var removed = oldIds.filter(function (i) { return ids.indexOf(i) < 0; });
  var sup = supAll;
  if (supAll.length && removed.length) {
    SRNOTE[key] = '移掉了 ' + removed.length + ' 条主素材：这一格原来勾的 ' + supAll.length
      + ' 条补充料已清空（提名是照被移掉那条素材算的），要按留下的素材重新勾。';
    sup = [];
    delete SRPICK_SUP[key];
  } else if (supAll.length) {
    var par = srSupCur(key);
    var kept = supAll.filter(function (i) {
      var p = Number(par[i] || 0);
      return !p || ids.indexOf(p) >= 0;      // p=0 是服务端存的、认不出父级的那条
    });
    if (kept.length !== supAll.length) {
      SRNOTE[key] = '有 ' + (supAll.length - kept.length)
        + ' 条补充料是勾在没被采用的那条素材下面的，这次不带过去。';
      sup = kept;
    }
  }
  btn.disabled = true;
  srApi('/api/plan/series/adopt', {slot_key: key, channel: 'toutiao',
                                   form: (SRMAP[key] || {}).form || 'guide', video_ids: ids})
    .then(function (d) {
      if (d.error) { btn.disabled = false; alert(d.error); return; }
      // 采用完把这份"待提交勾选"整格**删掉**（不是写成空对象）：空对象会被 renderSeries
      // 当成"人清空过"，种子就不灌了，下一版已采用那几行的复选框会全是空的
      var done = function () { delete SRPICK[key]; delete SRPICK_SUP[key]; loadSeries(); };
      // 子集里勾的那些跟着这次采用一起交（服务端会自己判"这一版能不能改"）
      if (!sup.length || !d.task || !d.task.id) {
        if (sup.length) srErr('主素材采用了，但这一格没拿到任务行 id——补充料没存，去工作台那一格勾一下');
        btn.disabled = false; done(); return;
      }
      srApi('/api/plan/supplements', {task_id: d.task.id, video_ids: sup}).then(function (j) {
        btn.disabled = false;
        if (j && j.error) srErr('主素材已采用；补充料没存：' + j.error);
        done();
      }, function (e) {
        btn.disabled = false;
        srErr('主素材已采用；补充料没存：' + e.message);
        done();
      });
    }).catch(function (e) { btn.disabled = false; alert('采用失败：' + e.message); });
}

function srOpenFrom(key, chapter) {
  var ids = srIds(key);
  if (!ids.length) { alert('先勾一条候选公告，再给它起一篇稿'); return; }
  var sel = document.getElementById('sr-link-' + key);
  var link = sel ? (sel.value || '') : '';
  if (srPriorChapters(chapter).length && !link) {
    alert('这一章要先选被关联的那一篇（服务端也会查这一格，空着建不出来）');
    return;
  }
  var title = prompt('这一篇的标题（台账与海报都要用）：', '');
  if (!title) return;
  srApi('/api/plan/series/open', {title: title, series: 's2', chapter: chapter,
                                  form: 'mix', video_ids: ids, channel: 'toutiao',
                                  link_slot: link})
    .then(function (d) {
      if (d.error) { alert(d.error); return; }
      SRPICK[key] = {};
      // 面板必须用**这次 POST 交回来的那份**（slot_obj + task）：新槽还没进看板时
      // SRMAP 里查不到它，旧写法先起 loadSeries()（异步）再同步 srOpenTask(d.slot)，
      // 于是交出去的是 form='guide'、空标题、空素材——库里明明是 toutiao/mix 且带一条采用素材，
      // 而 ptRefresh() 又按 form 精确筛任务，这篇 mix 稿在面板里就"不存在"
      // （Codex 三次复验第 1 条；真页面走查 7c 拍到面板写着"还没确认采用素材"）。
      srOpenTask(d.slot, d);
      loadSeries();            // 列表该刷也刷，但人不该等它才有面板
    });
}

/* S2 那三个章节模板各起各的稿。为什么这里不能只写一颗 O1 按钮（Codex 二次复验第 3 条）：
   三个模板都渲染"起一篇 O1 稿"、点的却是 O2/O3 那一格，提交出去的 chapter 永远是 O1，
   O2「条件预测」与 O3「上线后回看」在页面上**没有入口**。
   前序这一格只是"给人选"，真正的判据在 `plan_series.check_link`：
   绕过界面直接 POST 也建不出没有前序的预测稿。 */
function srStartCtl(s) {
  var ch = s.position || s.chapter || 'O1';
  var need = srPriorChapters(ch);
  var btn = '<button class="btn sm" onclick="srOpenFrom(\'' + esc(s.slot_key) + '\',\'' +
            esc(ch) + '\')">用勾选项起一篇 ' + esc(ch) + ' 稿</button>';
  if (!need.length) return btn;                    // O1 由真实公告建出来，没有前序
  var list = srPriors(need);
  if (!list.length) {
    return '<span class="sr-kid">这一章要先有前序那一篇（' + esc(need.join(' / ')) +
           '）：先到 ' + esc(need[0]) + ' 那一格用公告起稿，回来这里就能选了。</span>' + btn;
  }
  var opts = list.map(function (p) {
    return '<option value="' + esc(p.slot_key) + '">' +
           esc((p.position || p.slot_key) + ' ' + (p.title || '')) + '</option>';
  }).join('');
  return '<label class="sr-kid">前序那一篇<select id="sr-link-' + esc(s.slot_key) +
         '">' + opts + '</select></label>' + btn;
}

function srPriorChapters(ch) {
  if (ch === 'O2') return ['O1'];
  if (ch === 'O3') return ['O1', 'O2'];
  return [];
}

function srPriors(need) {
  var all = (SR && SR.slots) || [];
  return all.filter(function (s) {
    return s.series === 's2' && s.origin !== 'map' && need.indexOf(s.chapter) >= 0;
  });
}

/* 把工作台带到那一篇的排产面板：与旧 11 题走同一个 postMessage 契约，
   整合稿 → AI 海报①拆文案 → ②看版面 这条链不在这里重复实现。 */
/* `fresh` = 刚 POST 回来的那一份（{slot_obj, task, slot}）。新建的那一篇这时候还不在看板里，
   只认 SRMAP 就会交出一份空壳任务（形式退成 guide、标题与采用清单全空）。 */
function srOpenTask(key, fresh) {
  var s = (fresh && fresh.slot_obj) || SRMAP[key] || {};
  var t = (fresh && fresh.task) || s.task || {};
  var vids = t.video_ids;
  if (!vids || !vids.length) vids = s.adopted_ids || [];
  var payload = {v2t: 'openTask', slot_key: key, channel: t.channel || 'toutiao',
                 form: t.form || s.form || 'guide', title: t.title || s.title || '', hook: '',
                 note: s.angle || '', plan_date: '', video_ids: vids,
                 task_id: t.id || null, summary_id: t.summary_id || null,
                 status: t.status || 'none'};
  if (window.self !== window.top) {
    try { window.parent.postMessage(payload, window.location.origin); return; }
    catch (e) { /* 父窗口不是工作台，退化成新标签 */ }
  }
  window.open('/?task=' + encodeURIComponent(JSON.stringify(payload)), '_blank');
}

function srShowPub(key) { SRFORM[key] = true; srBody(); }

function srPubForm(s) {
  var today = (SR && SR.today) || '';
  return '<div class="sr-pub">' +
    '<input type="text" id="pt-t-' + esc(s.slot_key) + '" placeholder="平台标题" value="' +
      esc(s.title || '') + '">' +
    '<input type="text" id="pt-d-' + esc(s.slot_key) + '" placeholder="发布日期" value="' +
      esc(today) + '" style="flex:0 1 110px">' +
    '<input type="text" id="pt-u-' + esc(s.slot_key) + '" placeholder="平台链接（没有就写为什么没有）">' +
    '<button class="btn sm pri" onclick="srRegister(\'' + esc(s.slot_key) + '\')">确认登记</button>' +
    '<button class="btn sm" onclick="delete SRFORM[\'' + esc(s.slot_key) + '\'];srBody()">收起</button>' +
    '</div>';
}

function srRegister(key) {
  var g = function (p) { var e = document.getElementById(p + '-' + key); return e ? e.value.trim() : ''; };
  var title = g('pt-t'), date = g('pt-d'), url = g('pt-u');
  if (!title) { alert('平台标题不能空——台账里没标题的行没法复盘'); return; }
  srApi('/api/plan/publish', {slot_key: key, action: 'register', channel: 'toutiao',
                              title: title, pub_date: date, url: url,
                              url_missing_reason: url ? '' : '登记时没贴链接', by: '排产表'})
    .then(function (d) {
      delete SRFORM[key];
      if (d.error) { alert(d.error); return; }
      if ((d.state || {}).state !== 'confirmed') alert('登记写完了但服务端没认这条，请刷新再看');
      loadSeries();
    });
}

function srRevoke(key) {
  var s = SRMAP[key] || {};
  var rows = s.publish_rows || [];
  var reason = prompt('为什么要撤销这条发布登记（会写进操作记录，必须填）：', '');
  if (!reason) return;
  srApi('/api/plan/publish', {slot_key: key, action: 'revoke',
                              channel: (rows[0] || {}).channel || 'toutiao',
                              ref_id: (rows[0] || {}).ref_id || '', reason: reason, by: '排产表'})
    .then(function (d) { if (d.error) { alert(d.error); return; } loadSeries(); });
}

function srResolve(key, verdict) {
  srApi('/api/plan/publish', {slot_key: key, action: 'resolve_local', verdict: verdict,
                              by: '排产表'})
    .then(function (d) { if (d.error) { alert(d.error); return; } loadSeries(); });
}

function srMatchNow(btn) {
  btn.disabled = true; btn.textContent = '匹配中…';
  srApi('/api/plan/match/run', {by: '页面手动'}).then(function (d) {
    btn.disabled = false; btn.textContent = '现在匹配';
    if (d.error) { srErr('匹配失败：' + d.error); return; }
    if (d.skipped === true) { srErr(d.reason || '这一轮没跑'); return; }
    srErr('');
    loadSeries();
  }).catch(function (e) { btn.disabled = false; srErr('匹配请求失败：' + e.message); });
}

function srSaveCfg() {
  var en = document.getElementById('sr-enabled'), at = document.getElementById('sr-runat');
  srApi('/api/plan/match/config', {enabled: !!(en && en.checked),
                                   run_at: (at && at.value) || '22:00'})
    .then(function (d) { if (d.error) { srErr('配置没存：' + d.error); return; } loadSeries(); });
}

/* 旧 11 题那侧的"已发布"勾选：改成读写服务端。
   原来只写 localStorage，夜间任务拿它当不了发布真相（任务书 §1 点名的就是这个）。 */
function srLegacyToggle(cb) {
  var id = cb.dataset.id, s = SRMAP[id] || {};
  if (cb.checked) {
    SRFORM[id] = true;
    var card = cb.closest('.card');
    if (card && !card.querySelector('.sr-pub')) {
      var box = document.createElement('div');
      box.innerHTML = srPubForm({slot_key: id, title: (s.title || cb.dataset.title || '')});
      card.appendChild(box);
    }
    cb.checked = !!s.published;                 // 没登记之前不许先显示成已勾
    return false;
  }
  return true;
}

/* 给走查脚本与调试用的只读出口。
   刻意**不叫** window.SR：`var SR` 本身就是 window.SR，把它覆盖成函数会在
   loadSeries() 里 `SR = d` 之后又变回数据对象——同一个名字一会儿是数据一会儿是函数，
   第二次读就报 "window.SR is not a function"（真页面走查抓到过一次）。 */
window.srState = function () { return {slots: SRMAP, board: SR}; };
