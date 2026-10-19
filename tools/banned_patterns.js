// banned_patterns.js — 头条/公众号共用的「禁用句式 + 质检门」权威源
//
// 2026-10-19 从 media-workbench/server.js:951-1091 整段搬过来（逐字未改，含原有注释）。
// 为什么搬：5180 那个运营层已停（无监听、数据冻在 9 月下旬），号主决定删掉那个仓库，
//   而 `自媒体/_content_factory/verify_toutiao.js` 与 `verify_draft.js` 一直在按绝对路径
//   切 server.js 这一段来跑机检——仓库一删，"禁用句式"这一项会**静默失效**
//   （切片失败只打一行 INFO，`src/draft_audit.py` 只认 PASS/WARN/FAIL，会把那行丢掉）。
// 同步义务（DEV-SYNC 定的口径没变）：这份清单与
//   `自媒体/_content_factory/dify/custom/05-darkgold-integrated-publishing-v2.yml`
//   的 02C PATTERNS（残留检测）+ 02E SANITIZE（安全替换表）两处必须同步改；
//   DSL 改后要在 Dify 重新导入才生效。搬过来之后 server.js 里那份就是**待删的旧副本**，
//   别再往那边改。
// 消费方（三个，都读这一份）：
//   1) 自媒体/_content_factory/verify_toutiao.js —— require 后调 lintDraftContent（只查复制区）
//   2) 自媒体/_content_factory/verify_draft.js   —— require 后调 lintDraftContent + evaluateDraftGate
//   3) video2text/src/draft_audit.py             —— 经 1) 间接消费；报告里"禁用句式"那一项的口径就是这里
// 改完必须跑：node -e "const m=require('./tools/banned_patterns.js');console.log(m.BANNED_PATTERNS.length)"
//   以及一条真稿的 verify_toutiao.js，确认命中数没莫名其妙变 0。

const BANNED_PATTERNS = [
  [/不是[^。！？\n]{1,25}而是/g, '不是X而是Y（含标点变体）'],
  [/并不等于|并不代表|更像是|(?<![深简])不等于|不代表/g, '翻案腔'],
  [/真正的问题是|核心[^，。；\n]{0,4}在于|本质上|归根到底|说到底/g, '下定义腔'],
  [/先说结论|说白了|说穿了|值得注意的是|需要注意的是|需要指出的是|换句话说|总结一下|综上所述/g, 'AI过渡语'],
  [/首先|其次|综上/g, '首先其次'],
  [/(?:^|[。！？\n])\s*(?:先别|别把|别让)/g, '劝诫式句子'],
  [/别让[^，。！？\n]{2,12}(?:毁了|毁掉|偷走)/g, '别让X毁了Y'],
  [/切记[，,]/g, '说教腔'],
  [/这意味着什么？意味着/g, '自问自答'],
  // 「我按公告和公开页面整理」曾是资料整理型第一人称的标准写法，被误当成亲测禁掉，
  // 结果正文一个「我」都不剩、只剩转述腔（2026-10-04 改：只禁没有依据的亲测）
  [/我没亲测|我体验了|我进了游戏|我实测|我亲测|我打了\s*\d+\s*小时|我昨天也这样过/g, '无依据亲测（改写成资料整理型第一人称，不许整句删）'],
  [/建议收藏|懂的都懂|版本答案/g, '网感套话'],
  [/信息差|情绪价值|底层逻辑|商业闭环|用户心智|长期主义|认知升级|价值锚点/g, '商业黑话'],
  [/表面上.{0,20}实际上|看似.{0,20}实则|你以为.{0,20}其实/g, '假对立'],
  [/行动建议|评论区聊聊|评论区讨论/g, '模板收尾'],
  [/钱包没那么硬|人都麻了|花在刀刃上/g, '假口语'],
  [/终于把时间表拍在桌上|对于咱们这些/g, '头条模板腔'],
  [/(?:玩家|读者|用户)需(?:要)?(?:先)?(?:区分|注意|留意|了解|判断)/g, '读者须知腔'],
  [/本文基于.{0,24}(?:整理|编写|梳理)|搜索.{2,20}的玩家|据检索(?:结果)?|根据检索|网上信息(?:杂乱|良莠)/g, '元叙述腔（成文过程/检索行为泄漏）'],
  [/\d{2}-\d{2}岁.{0,10}(?:男性|玩家|读者|用户)|对于\d{2}-\d{2}岁|对于[^，。；\n]{0,10}(?:中年|中老年|男性|老年)|(?:\d{2}-\d{2}岁|\d{2}\+|\d{2}多岁|\d{2}岁以上|\d{2}岁|中年|中老年|老年|年轻人)[^，。；\n]{0,8}(?:男性|玩家|读者|用户|人(?!群)|我们|朋友)/g, '账号定位泄漏（正文暴露读者画像）'],
];
// 对比修辞密度兜底（2026-09-08 逗号盲区教训）：全文最多保留 1 处
const CONTRAST_DENSITY_RE = /不是[^，。；！？\n]{1,15}，是|不再[^。！？\n]{0,20}而是|与其说/g;
// 短语级安全替换表（确定性，改了不改意思；与 DSL 02E SANITIZE 同源）
const SANITIZE_PATTERNS = [
  [/对于\d{2}-\d{2}岁的(?:男性)?(?:玩家|读者|用户)/g, '对于时间有限的玩家'],
  [/\d{2}-\d{2}岁的(?:男性)?(?:玩家|读者|用户)/g, '时间有限的玩家'],
  [/对于[^，。；\n]{0,10}中年的?(?:男性)?(?:玩家|读者|用户)/g, '对于时间有限的玩家'],
  [/对于普通男性/g, '对于普通玩家'],
  [/(\d{2})岁以上(?:的)?(魔兽玩家|游戏玩家|玩家|读者)/g, '时间预算有限的$2'],
  [/\d{2}岁(?:的)?(?:男性)?(?:玩家|读者)/g, '老玩家'],
  [/中年的?男性(?:玩家|读者|用户)?/g, '时间有限的玩家'],
  [/\d{2}\+(?:的)?(?:玩家|读者|用户)/g, '老玩家'],
  [/\d{2}多岁的(我们|玩家|读者|用户|男性)/g, '时间有限的$1'],
  [/中年的?(?:男性)?(?:玩家|读者|用户)/g, '时间有限的玩家'],
  [/中年人的?游戏时间/g, '普通人的游戏时间'],
  [/中年人的?/g, '时间有限的人'],
  [/底层逻辑/g, '基本机制'],
  [/信息差/g, '信息不对称'],
  [/情绪价值/g, '情绪回报'],
  [/商业闭环/g, '盈利模式'],
  [/用户心智/g, '用户认知'],
  [/认知升级/g, '认知提升'],
  [/价值锚点/g, '价值参照'],
  [/长期主义/g, '长期投入'],
];

// 复制区提取（2026-09-15）：lint 只作用于「━ 渠道 · 复制区 ━」到下一个 ━ 分隔线
// 之间的正文。审计报告会引用违规短语原文（如终检行「命中片段：『玩家需留意』」），
// 全文扫描时这类引用自我触发检测，导致"正文已清理仍报残留"的假阳性（回归案例：
// custom_191252_魔兽资讯短文.md 正文手改后质检门仍拦截）。无复制区标记（旧稿/
// html）时回退全文，保持旧行为。
function extractLintZone(content) {
  const lines = String(content || '').split('\n');
  const start = lines.findIndex(l => /━+\s*\S+\s*·\s*复制区\s*━+/.test(l));
  if (start === -1) return { zone: lines.join('\n'), start: -1, end: lines.length };
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/━{3,}/.test(lines[i])) { end = i; break; }
  }
  return { zone: lines.slice(start + 1, end).join('\n'), start: start + 1, end };
}

function lintDraftContent(content) {
  const zoneInfo = extractLintZone(content);
  // 1) 先应用安全替换表（与 Dify 终检顺序一致：先 sanitize 再查残留），仅作用于复制区
  let fixedZone = zoneInfo.zone;
  const fixes = [];
  for (const [pat, rep] of SANITIZE_PATTERNS) {
    const re = new RegExp(pat.source, pat.flags);
    const found = fixedZone.match(re);
    if (found && found.length) {
      fixes.push({ from: [...new Set(found)].slice(0, 3), to: found[0].replace(new RegExp(pat.source), rep), count: found.length });
      fixedZone = fixedZone.replace(re, rep);
    }
  }
  // 2) 残留检测（替换后的复制区文本；审计报告的引用样例在区外，不再误触发）
  const hits = [];
  for (const [pat, name] of BANNED_PATTERNS) {
    const re = new RegExp(pat.source, pat.flags);
    const found = fixedZone.match(re);
    if (found && found.length) {
      hits.push({ name, count: found.length, samples: [...new Set(found)].slice(0, 3) });
    }
  }
  const soft = fixedZone.match(new RegExp(CONTRAST_DENSITY_RE.source, CONTRAST_DENSITY_RE.flags)) || [];
  if (soft.length >= 2) {
    hits.push({ name: '对比修辞密度超标', count: soft.length, samples: [...new Set(soft)].slice(0, 3), note: '正文最多保留 1 处，其余改直接陈述' });
  }
  // 3) 清理后的复制区拼回全文（区外内容——发布状态/配图池/审计报告——原样保留）
  let fixed = fixedZone;
  if (zoneInfo.start !== -1) {
    const lines = String(content || '').split('\n');
    fixed = [...lines.slice(0, zoneInfo.start), fixedZone, ...lines.slice(zoneInfo.end)].join('\n');
  }
  return { fixes, fixed, hits };
}

// ── 发布质检门（2026-09-10）：机器可判定的问题硬阻断，需人判断的问题二次确认 ──
// 硬门 = 正则可确定（禁用句式残留、空稿/正文缺失）；软门 = 发布状态行里的其他「需手改」项（数字溯源/口径/字数）
function evaluateDraftGate(content) {
  const text = String(content || '');
  const statusLine = (text.match(/^发布状态：(.*)$/m) || [])[1] || '';
  const needFix = statusLine.includes('需手改');
  const statusItems = needFix
    ? statusLine.replace(/^[^：:]*[：:]/, '').split(/[、；;，]/).map(s => s.trim()).filter(Boolean)
    : [];
  const lint = lintDraftContent(text);
  const hard = [];
  const soft = [];
  // 硬门 1：禁用句式残留（实时正则，不信任生成时的状态行；清理后自动放行）
  if (lint.hits.length) {
    hard.push(`禁用句式残留 ${lint.hits.length} 类：${lint.hits.map(h => `${h.name}×${h.count}`).join('；')}——可点「一键发布前体检」自动处理`);
  }
  // 硬门 2：正文缺失/过短（空稿防线）
  const prose = text
    .split('\n')
    .filter(l => !/^\s*(图\d+：|配图候选|配图任务|发布状态|━|===)/.test(l))
    .join('')
    .replace(/\s/g, '');
  if (!text.trim()) hard.push('稿件内容为空');
  else if (prose.length < 200) hard.push(`正文过短（约 ${prose.length} 字），疑似空稿`);
  // 软门：状态行里除禁用句式外的其余「需手改」项，需人工判断
  for (const item of statusItems) {
    if (item.includes('禁用句式')) continue;
    soft.push(item);
  }
  return {
    canPublish: hard.length === 0 && !needFix,
    hard,
    soft,
    statusLine,
    statusItems,
    lintHits: lint.hits,
    lintFixable: lint.fixes.length,
  };
}

// ── 数据洞察（2026-09-10）：用发布回填数据算「什么时间发、什么公式赢」 ──

module.exports = {
  BANNED_PATTERNS, CONTRAST_DENSITY_RE, SANITIZE_PATTERNS,
  extractLintZone, lintDraftContent, evaluateDraftGate
};
