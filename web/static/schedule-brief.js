/* schedule-brief.js — 给排产表的每张卡与每条素材上颜色（判据不在这里，全在服务端）。
 *
 * 为什么单独一个文件（2026-10-21 号主要求"卡片按颜色区分一定要发/可以暂缓"）：
 * `schedule.html` 是另一条会话未提交的活，我把四十行判据塞进那份文件里，
 * 将来谁覆盖谁都不知道。颜色只是显示层，**判据必须只有一份**：
 * 这里只读 `/api/plan/brief`（同一份 `src/plan_brief.py` 算出来的），
 * 页面自己不算日期、不算窗口、不猜发没发——算不出就标灰，不装知道。
 *
 * 五档（跟日报同一套词，改档位改 `src/plan_brief.py:VERDICTS`）：
 *   红 今天就发      到期 + 未发 + 48 小时内有新素材
 *   橙 该发但缺料    到期 + 未发，但素材超出 48 小时窗口 → 先补素材，别当今天的消息发
 *   黄 疑似已发      台账只有标题对得上、没有排产槽号 → 要人确认，不当事实
 *   绿 已发过别再发  台账带槽号；72 小时内还标"别外发（含小红书）"
 *   灰 还没到        计划日在未来
 * 另有一条硬约束写进判据里而不是这里：今天已登记发过一篇，其余"今天就发"自动降级并写明原因
 * ——限流与首发补贴都吃这一条，不能靠自觉。
 */
(function () {
  var CLS = { red: 'vb-red', orange: 'vb-orange', yellow: 'vb-yellow',
              green: 'vb-green', grey: 'vb-grey' };
  var TIP = {
    red: '到期、没发过、48 小时内有新素材 —— 今天就写这篇',
    orange: '到期了但素材超出 48 小时窗口：先补素材或按旧口径写，别当今天的消息发',
    yellow: '台账里有标题对得上的行，但没填排产槽号 —— 你自己确认发过没有',
    green: '已登记发过。72 小时内别外发（含小红书），否则掉首发补贴、还容易被判批量搬运',
    grey: '还没到计划日，现在挂的素材只是选题依据'
  };
  var verdicts = null, briefErr = '';

  function css() {
    var s = document.createElement('style');
    s.textContent = [
      '.item.vb-red{border-left:5px solid #d0433a}',
      '.item.vb-orange{border-left:5px solid #e08a2e}',
      '.item.vb-yellow{border-left:5px solid #c9b23a}',
      '.item.vb-green{border-left:5px solid #2f9e63;opacity:.82}',
      '.item.vb-grey{border-left:5px solid #b9bfc7}',
      '.vb-chip{font-size:11px;font-weight:700;padding:2px 7px;border-radius:999px;white-space:nowrap}',
      '.vb-red .vb-chip{background:#fbe6e4;color:#a8402f}',
      '.vb-orange .vb-chip{background:#fdf0e0;color:#9a5c12}',
      '.vb-yellow .vb-chip{background:#f7f3d9;color:#7a6612}',
      '.vb-green .vb-chip{background:#e3f4ea;color:#1a7a45}',
      '.vb-grey .vb-chip{background:#eef0f3;color:#5b6470}',
      '.ref.vf-fresh{background:rgba(47,158,99,.10);box-shadow:inset 3px 0 0 #2f9e63}',
      '.ref.vf-window{box-shadow:inset 3px 0 0 #c9cfd8}',
      '.vb-note{font-size:11.5px;color:#5b6470;margin:0 0 10px}',
      '.vb-demote{font-size:11.5px;color:#9a5c12;margin:4px 0 0}',
      '@media (prefers-color-scheme:dark){.vb-note{color:#aab2bd}'
      + '.item.vb-grey{border-left-color:#4b5563}}'
    ].join('\n');
    document.head.appendChild(s);
  }

  function slotOf(el) {
    return (verdicts || {})[el.getAttribute('data-item')];
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
        if (verdicts) { el.classList.add('vb-grey'); }
        continue;
      }
      el.classList.add(CLS[s.verdict] || 'vb-grey');
      var row1 = el.querySelector('.row1');
      if (row1) {
        var b = document.createElement('span');
        b.className = 'vb-chip';
        b.textContent = s.label;
        b.title = TIP[s.verdict] || '';
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
            ? '48 小时内的新素材' : '窗口内的旧素材（最新一条 ' + s.fresh.newest + '）';
          break;
        }
      }
    }
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
    if (!verdicts) {
      box.textContent = '颜色判据没拿到（' + (briefErr || '接口无响应')
        + '）——工作台没起就算不出「发过没有 / 素材新不新」，这里不猜。';
      return;
    }
    box.innerHTML = '<b>颜色 = 该不该发</b>（判据与每天 8:30 那份日报同一份，服务端算）：'
      + '<span class="vb-chip" style="background:#fbe6e4;color:#a8402f">今天就发</span> 到期+未发+48h 内有新料　'
      + '<span class="vb-chip" style="background:#fdf0e0;color:#9a5c12">该发但缺料</span> 到期但素材超窗口　'
      + '<span class="vb-chip" style="background:#f7f3d9;color:#7a6612">疑似已发</span> 只标题对上　'
      + '<span class="vb-chip" style="background:#e3f4ea;color:#1a7a45">已发过</span> 72h 内别外发　'
      + '<span class="vb-chip" style="background:#eef0f3;color:#5b6470">还没到</span>　'
      + '素材行左边<span style="color:#1a7a45">绿条</span>=48h 内新料、灰条=窗口内旧料。'
      + '今天已发 <b>' + (window.__vbPubToday || 0) + '</b> 篇，超过一篇其余到期格自动降级。';
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
        verdicts = (d && d.slots) || null;
        window.__vbPubToday = ((d && d.published_today) || []).length;
        legend();
        paint();
      })
      .catch(function (e) {
        briefErr = String((e && e.message) || e).slice(0, 60);
        verdicts = null;
        legend();
        paint();
      });
    // 页面每次重画素材清单（loadLive 是异步的）都要重新上色，否则颜色会停在旧 DOM 上
    var host = document.getElementById('list');
    if (host && window.MutationObserver) {
      var lock = 0;
      new MutationObserver(function () {
        if (!verdicts) return;
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
