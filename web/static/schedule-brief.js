/* schedule-brief.js — 给排产表的每张卡与每条素材上颜色（判据不在这里，全在服务端）。
 *
 * 为什么单独一个文件（2026-10-21 号主要求"卡片按颜色区分哪篇先做/哪篇暂缓"）：
 * `schedule.html` 是另一条会话未提交的活，我把四十行判据塞进那份文件里，
 * 将来谁覆盖谁都不知道。颜色只是显示层，**判据必须只有一份**：
 * 这里只读 `/api/plan/brief`（同一份 `src/plan_brief.py` 算出来的），
 * 页面自己不算日期、不算窗口、不猜发没发——算不出就标灰，不装知道。
 *
 * 六档的名字、每档为什么是这个颜色、新料窗口怎么称呼，**全部由接口下发**
 * （`verdicts` / `slots[].label` / `slots[].why` / `fresh_label`）：这里一份都不抄。
 * 2026-10-21 复核 R1 就是冲着"页面上还写着一句今天就发"来的——文案抄两处，改一处就互相说反话。
 * 颜色要说清边界：它只表示**排期与素材优先级**，不等于这篇能发；
 * 文字、事实、关键帧、使用依据那四道审核在下游各闸里，图例那句不是装饰。
 *
 * R3 修的是选择器：卡片是 `.card`（`schedule.html` 里 `class="card …"`），
 * 原来这里写 `.item.vb-*`，一条都没命中——色条从来没画出来过，
 * 而走查只断言"卡上有 vb- 类"和"标签有背景色"，所以一直全绿。
 * 现在选择器按真实 DOM 写，走查改成读 `getComputedStyle` 的 border-left。
 */
(function () {
  var CLS = { red: 'vb-red', deferred: 'vb-deferred', orange: 'vb-orange',
              yellow: 'vb-yellow', green: 'vb-green', grey: 'vb-grey' };
  var CHIP = { red: ['#f6d7d3', '#8f1d15'], deferred: ['#e6ebfa', '#33477f'],
               orange: ['#fbe6cf', '#8a4b0c'], yellow: ['#f7f3d9', '#7a6612'],
               green: ['#e3f4ea', '#1a7a45'], grey: ['#eef0f3', '#5b6470'] };
  var slots = null, labels = null, why = null, freshLabel = '窗口内', briefErr = '';

  function css() {
    var s = document.createElement('style');
    // 选择器必须打在真实 DOM 上：卡片是 `.card`（原来写 `.item` 一条都没命中，色条从没出现过）。
    // `.card.late` 自己也设 border-color，权重与这里同为两个类——所以这份 style 一定在 head 末尾，
    // 同权重后写的算，色条不会被"过期红框"那套吃掉。
    // 宽度按"该不该今天动它"分三级：红 8px（一眼要找到的）、橙/顺延 6px（要处理但今天不做）、
    // 其余 5px（只是状态）。号主 10-21 反馈"必须发布那条色不明显"——5px 贴在 1100px 宽的卡上确实找不着，
    // 所以除了加粗，红档再配一道左侧淡红渐层 + 标签描边，三样一起才叫"这一格今天动它"。
    s.textContent = [
      '.card.vb-red{border-left:8px solid #c0271d;'
      + 'background:linear-gradient(90deg,rgba(192,39,29,.11),rgba(192,39,29,0) 46%)}',
      '.card.vb-orange{border-left:6px solid #d97b16}',
      '.card.vb-deferred{border-left:6px solid #4a63b0}',
      '.card.vb-yellow{border-left:5px solid #c9b23a}',
      '.card.vb-green{border-left:5px solid #2f9e63;opacity:.82}',
      '.card.vb-grey{border-left:5px solid #b9bfc7}',
      '.vb-chip{font-size:11px;font-weight:700;padding:2px 7px;border-radius:999px;white-space:nowrap}',
      // 红档的标签跟着加重：字大一号、内描边一圈，扫一眼就知道是哪一格
      '.card.vb-red .vb-chip{font-size:12.5px;padding:3px 10px;'
      + 'box-shadow:inset 0 0 0 1.5px #c0271d}',
      '.ref.vf-fresh{background:rgba(47,158,99,.10);box-shadow:inset 3px 0 0 #2f9e63}',
      '.ref.vf-window{box-shadow:inset 3px 0 0 #c9cfd8}',
      '.vb-note{font-size:11.5px;color:#5b6470;margin:0 0 10px}',
      '.vb-demote{font-size:11.5px;color:#3b5288;margin:4px 0 0}',
      '@media (prefers-color-scheme:dark){.vb-note{color:#aab2bd}'
      + '.card.vb-grey{border-left-color:#4b5563}'
      + '.card.vb-red{background:linear-gradient(90deg,rgba(192,39,29,.22),'
      + 'rgba(192,39,29,0) 46%)}}'
    ].join('\n');
    document.head.appendChild(s);
  }

  function slotOf(el) {
    return (slots || {})[el.getAttribute('data-item')];
  }

  function paint() {
    var cards = document.querySelectorAll('[data-item]');
    for (var i = 0; i < cards.length; i++) {
      var el = cards[i], s = slotOf(el);
      el.className = el.className.replace(/\bvb-\w+\b/g, '').replace(/\s+/g, ' ').trim();
      var chip = el.querySelector('.vb-chip');
      if (chip && chip.parentNode) chip.parentNode.removeChild(chip);
      var old = el.querySelector('.vb-demote');
      if (old && old.parentNode) old.parentNode.removeChild(old);
      if (!s) {
        if (slots) { el.classList.add('vb-grey'); }
        continue;
      }
      el.classList.add(CLS[s.verdict] || 'vb-grey');
      var row1 = el.querySelector('.row1');
      if (row1) {
        var b = document.createElement('span');
        var c = CHIP[s.verdict] || CHIP.grey;
        b.className = 'vb-chip';
        b.style.background = c[0];
        b.style.color = c[1];
        b.textContent = s.label;                    // 档位名字来自接口，这里不抄第二份
        b.title = s.why || '';                       // 这一档为什么是这个颜色，同样来自服务端
        var hint = row1.querySelector('.hint');
        row1.insertBefore(b, hint || null);
      }
      if (s.demoted) {
        var d = document.createElement('div');
        d.className = 'vb-demote';
        d.textContent = '⚠ ' + s.demoted;
        var t = el.querySelector('.title');
        if (t && t.parentNode) t.parentNode.insertBefore(d, t.nextSibling);
      }
      paintRefs(el, s);
    }
  }

  /* 素材行只认接口点名的那几条：页面不再自己算日子，避免两处判据打架 */
  function paintRefs(el, s) {
    var refs = el.querySelectorAll('.ref');
    var list = (s.fresh && s.fresh.list) || [];
    for (var i = 0; i < refs.length; i++) {
      refs[i].classList.remove('vf-fresh', 'vf-window');
      var a = refs[i].querySelector('.tt');
      var txt = a ? (a.getAttribute('title') || a.textContent || '') : '';
      for (var j = 0; j < list.length; j++) {
        var key = (list[j].title || '').slice(0, 18);
        if (key && txt.indexOf(key) >= 0) {
          refs[i].classList.add(list[j].status === 'fresh' ? 'vf-fresh' : 'vf-window');
          refs[i].title = list[j].status === 'fresh'
            ? freshLabel + '的新素材' : '窗口外的旧素材（最新一条 ' + s.fresh.newest + '）';
          break;
        }
      }
    }
  }

  function jesc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; });
  }

  function legend() {
    var host = document.getElementById('list') || document.body;
    var box = document.getElementById('vb-legend');
    if (!box) {
      box = document.createElement('div');
      box.id = 'vb-legend';
      box.className = 'vb-note';
      host.parentNode.insertBefore(box, host);
    }
    if (!labels) {
      box.textContent = '颜色判据没拿到（' + (briefErr || '接口无响应')
        + '）——工作台没起就算不出「发过没有 / 素材新不新」，这里不猜。';
      return;
    }
    // 档位名字与解释全从接口来（for..in 走的就是服务端 VERDICTS 的键序），这里一个字都不抄：
    // 复核 R1 点名的就是"服务端改了名、页面还挂着一句今天就发"。
    var h = '<b>颜色 = 今天先动哪一题</b>（判据与每天 8:30 那份日报同一份，服务端算；'
          + '新料窗口 = ' + jesc(freshLabel) + '）：';
    for (var k in labels) {
      var c = CHIP[k] || CHIP.grey;
      h += '<span class="vb-chip" style="background:' + c[0] + ';color:' + c[1]
        + '" title="' + jesc((why || {})[k] || '') + '">' + jesc(labels[k]) + '</span> ';
    }
    h += '素材行左边<span style="color:#1a7a45">绿条</span>=' + jesc(freshLabel)
      + '的新料、灰条=窗口外的旧料。'
      + '<br><b>这些颜色只说排期与素材优先级，不等于「这篇能发」</b>——正式发布还要过文字、'
      + '事实、关键帧与使用依据四道审核（那是下游各闸的事）。'
      + '今天已发 <b>' + (window.__vbPubToday || 0) + '</b> 篇；发过之后其余原本会红的格子'
      + '改判「顺延」，那是今天名额用完了，不是缺料。';
    box.innerHTML = h;
  }

  function boot() {
    css();
    fetch('/api/plan/brief', {cache: 'no-store'})
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (d && d.error) throw new Error(d.error);
        // 握手：老进程算出来的还是旧五档（"今天就发"那句就在这一类混态里回到页面上）。
        // 缺任何一份下发文案就**整个不画**，比"新图例配旧档位名"诚实。
        if (!d.verdicts || !d.verdict_why || !d.fresh_label) {
          throw new Error('接口没下发 verdict_why/fresh_label，工作台进程还是旧判据，重启后才有新颜色');
        }
        slots = (d && d.slots) || null;
        labels = (d && d.verdicts) || null;
        why = (d && d.verdict_why) || null;
        freshLabel = (d && d.fresh_label) || '窗口内';
        window.__vbPubToday = ((d && d.published_today) || []).length;
        legend();
        paint();
      })
      .catch(function (e) {
        briefErr = String((e && e.message) || e).slice(0, 60);
        slots = null;
        labels = null;
        why = null;
        legend();
        paint();
      });
    // 页面每次重画素材清单（loadLive 是异步的）都要重新上色，否则颜色会停在旧 DOM 上
    var host = document.getElementById('list');
    if (host && window.MutationObserver) {
      var lock = 0;
      new MutationObserver(function () {
        if (!slots) return;
        if (lock) return;
        lock = setTimeout(function () { lock = 0; paint(); }, 400);
      }).observe(host, {childList: true, subtree: true});
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(boot, 1200); });
  } else {
    setTimeout(boot, 1200);
  }
})();
