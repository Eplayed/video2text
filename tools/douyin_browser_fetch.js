#!/usr/bin/env node
/**
 * douyin_browser_fetch.js — 用真实 Chromium 抓抖音数据（绕过 Argus API 签名拦截）
 *
 * 背景（2026-09-21）：抖音 Argus 风控升级，纯 API 的 a_bogus 签名被拦
 * （Blocked by ArgusSecurityPlugin Signature Not Found）。本脚本让页面
 * 自己生成签名（页面 XHR 全部 200），零逆向、不怕算法再升级。
 *
 * 用法：
 *   node douyin_browser_fetch.js user   <user_url>  [max_videos]  → {"ok":true,"ids":[...],"notes":[...]}
 *   node douyin_browser_fetch.js detail <video_url>               → {"ok":true,"info":{...}}
 *   node douyin_browser_fetch.js users  <json_file> [max_videos]  → 每作者一行 {"ok":..,"id":..,"ids":[...],"notes":[...]}
 *   node douyin_browser_fetch.js author <work_url>                → {"ok":true,"sec_uid":...,"nickname":...}
 * 输出：最后一行为 JSON（users 模式为逐作者流式多行）。
 *
 * notes 是这一轮新带出来的图文条目（aweme_type=68，锚点是 /note/<id>，老代码只刮
 * /video/ 所以图文全漏）；调用方不读它也不影响视频那条路径。
 *
 * users 批量模式：一次 Chromium 启动跑完所有作者主页（订阅同步的主路径），
 * 单作者失败只输出错误行、不中断后续作者。
 */
const path = require("path");
const fs = require("fs");

const CHROMIUM_CANDIDATES = [
  path.join(process.env.HOME || "", "Library/Caches/ms-playwright/chromium-1217/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
];
const COOKIE_FILE = path.join(__dirname, "..", "vendor", "douyin_parse", "douyin_cookie.txt");
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36";

function emit(obj) { console.log(JSON.stringify(obj)); }

async function launch(ctx) {
  let executablePath = null;
  for (const c of CHROMIUM_CANDIDATES) {
    if (fs.existsSync(c)) { executablePath = c; break; }
  }
  if (!executablePath) throw new Error("no chromium found");
  const { chromium } = require("playwright-core");
  return chromium.launch({ executablePath, headless: true, args: ["--no-sandbox", "--disable-gpu"] });
}

async function newContext(browser) {
  const ctx = await browser.newContext({
    userAgent: UA, viewport: { width: 1440, height: 900 }, locale: "zh-CN",
  });
  // 注入本地 cookie（有完整串就全注入，增强登录态）
  try {
    const cookieStr = fs.readFileSync(COOKIE_FILE, "utf8").trim();
    const cookies = [];
    for (const pair of cookieStr.split(";")) {
      if (!pair.includes("=")) continue;
      const idx = pair.indexOf("=");
      const name = pair.slice(0, idx).trim();
      const value = pair.slice(idx + 1).trim();
      if (name) cookies.push({ name, value, domain: ".douyin.com", path: "/" });
    }
    if (cookies.length) await ctx.addCookies(cookies);
  } catch (e) { /* 无 cookie 文件也能以游客身份拉 */ }
  return ctx;
}

/** 作者主页列表（视频 + 图文）：挂响应监听把页面自取的 aweme/post 收下来。
 *
 * 为什么要读接口而不是继续刮 DOM：图文作品（aweme_type=68）在主页上的锚点是
 * /note/<id>，而老的 user/users 两个模式只刮 `a[href*="/video/"]`——所以图文一条都
 * 进不来（2026-10-16 实测：笨小孩首屏 27 条里 20 条是图文）。页面自己发的 aweme/post
 * 返回里直接带 images[].url_list / download_url_list、desc、statistics、create_time，
 * 一次加载就够，不必逐条打开作品页、也不用再签一次名。
 * 视频那条 ids 的提取逻辑一字未改，只是多带一份 notes 出去。
 */
function attachPostCapture(page) {
  const store = { byId: {}, items: [] };
  page.on("response", (resp) => {
    const u = resp.url() || "";
    if (u.indexOf("aweme/v1/web/aweme/post") < 0) return;
    resp.json().then((j) => {
      const list = (j && (j.aweme_list || j.data || j.items)) || [];
      for (const it of list) {
        if (!it || !it.aweme_id || store.byId[it.aweme_id]) continue;
        store.byId[it.aweme_id] = true;
        const rec = {
          aweme_id: it.aweme_id,
          aweme_type: (it.aweme_type === undefined ? -1 : it.aweme_type),
          desc: it.desc || "",
          create_time: it.create_time || 0,
          digg: ((it.statistics || {}).digg_count) || 0,
          comment: ((it.statistics || {}).comment_count) || 0,
          share: ((it.statistics || {}).share_count) || 0,
          images: [],
          cover: ((((it.video || {}).cover || {}).url_list) || [])[0] || "",
          video_play: ((((it.video || {}).play_addr || {}).url_list) || [])[0] || "",
        };
        for (const im of (it.images || [])) {
          rec.images.push({
            url: ((im.url_list || [])[0]) || "",
            download: ((im.download_url_list || [])[0]) || "",
            width: im.width || 0, height: im.height || 0,
          });
        }
        store.items.push(rec);
      }
    }).catch(() => { /* 非 JSON（被风控挡回 HTML）就什么也不带，调用方按缺省处理 */ });
  });
  return store;
}

/** 图文＝这一条带 images 数组（aweme_type=68）；视频条目即便有 cover 也不算 */
function noteItemsOf(store) {
  return (store && store.items || []).filter((it) => (it.images || []).length > 0);
}

/** 视频 id 以**接口那份**为准（作者自己的作品列表），抓不到才退回 DOM。
 *
 *  2026-10-16 实测：笨小孩主页 DOM 上刮到 15 条 `/video/`，可 aweme/post 里他自己只有
 *  7 条视频——多出来那 8 条是侧栏推荐位**别人**的视频（铝型材模具、好物分享、2024 年的
 *  老片子）。老代码只刮 DOM，于是这 8 条被当成"这个号的新视频"写进 Excel、逐条做了 ASR
 *  （花钱），素材行盖上这个号的 author_sec_uid，订阅昵称还被第一条的作者列覆盖成了
 *  「厦门阿波」。所以 DOM 那条路只能当兜底，不能当主路。
 */
function authorVideoIds(store, domIds) {
  const apiIds = [];
  const seen = {};
  for (const it of (store && store.items || [])) {
    if ((it.images || []).length) continue;      // 图文另走 notes
    if (!it.aweme_id || seen[it.aweme_id]) continue;
    seen[it.aweme_id] = true;
    apiIds.push(it.aweme_id);
  }
  if (store) store.fromApi = apiIds.length > 0;
  return apiIds.length ? apiIds : (domIds || []);
}

/** 模式一：作者主页 → 最新视频 ID 列表 */
async function fetchUserVideos(userUrl, maxVideos) {
  const browser = await launch();
  try {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();
    const store = attachPostCapture(page);
    await page.goto(userUrl, { waitUntil: "domcontentloaded", timeout: 40000 });
    try {
      await page.waitForSelector('a[href*="/video/"]', { timeout: 20000 });
    } catch (e) {
      // 纯图文的号一条 /video/ 锚点都没有，这里不能直接判失败——先看接口那一趟有没有带回图文
      const t = await page.title().catch(() => "");
      const body = await page.evaluate(() => document.body.innerText.slice(0, 120)).catch(() => "");
      const onlyNotes = noteItemsOf(store);
      if (onlyNotes.length) {
        emit({ ok: true, ids: [], notes: onlyNotes.slice(0, maxVideos), note_only: true });
        return;
      }
      emit({ ok: false, error: "no video cards, title=" + t.slice(0, 30) + " body=" + body.replace(/\n/g, " ").slice(0, 80) });
      return;
    }
    await page.waitForTimeout(2500);
    const domIds = await page.evaluate(() => {
      const out = [];
      for (const a of document.querySelectorAll('a[href*="/video/"]')) {
        const m = (a.getAttribute("href") || "").match(/video\/(\d+)/);
        if (m) out.push(m[1]);
      }
      return [...new Set(out)];
    });
    const ids = authorVideoIds(store, domIds);
    if (!ids.length && !noteItemsOf(store).length) { emit({ ok: false, error: "empty list (maybe login wall / captcha)" }); return; }
    emit({ ok: true, ids: ids.slice(0, maxVideos), notes: noteItemsOf(store).slice(0, maxVideos),
           ids_from: store.fromApi ? "api" : "dom" });
  } finally {
    await browser.close().catch(() => {});
  }
}

/** 模式二：单视频页 → aweme_detail（结构与 parser.parse_video 对齐） */
async function fetchVideoDetail(videoUrl) {
  const browser = await launch();
  try {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();

    let detail = null;
    page.on("response", (resp) => {
      if (detail) return;
      if (resp.url().includes("aweme/v1/web/aweme/detail")) {
        resp.json().then((j) => {
          if (j && j.aweme_detail) detail = j.aweme_detail;
        }).catch(() => {});
      }
    });

    await page.goto(videoUrl, { waitUntil: "domcontentloaded", timeout: 40000 });
    // 等 detail XHR（最长 15s）
    for (let i = 0; i < 30 && !detail; i++) await page.waitForTimeout(500);
    if (!detail) { emit({ ok: false, error: "no aweme/detail XHR captured" }); return; }

    const videoObj = detail.video || {};
    const playList = (videoObj.play_addr && videoObj.play_addr.url_list) || [];
    const coverList = (videoObj.cover && videoObj.cover.url_list) || [];
    const author = detail.author || {};
    const stats = detail.statistics || {};

    emit({
      ok: true,
      info: {
        aweme_id: detail.aweme_id || (videoUrl.match(/video\/(\d+)/) || [])[1] || "",
        desc: detail.desc || "",
        create_time: detail.create_time || 0,
        author_nickname: author.nickname || "",
        author_sec_uid: author.sec_uid || "",
        cover_url: coverList[0] || "",
        nwm_url: playList[0] || "",
        video: { duration: videoObj.duration || null },
        statistics: {
          digg_count: stats.digg_count || 0,
          comment_count: stats.comment_count || 0,
          share_count: stats.share_count || 0,
          collect_count: stats.collect_count || 0,
        },
      },
    });
  } finally {
    await browser.close().catch(() => {});
  }
}

/** 模式三：批量作者主页 → 每作者一行 NDJSON（一次 Chromium，订阅同步主路径） */
async function fetchUserVideosBatch(file, maxVideos) {
  const items = JSON.parse(fs.readFileSync(file, "utf8"));
  const browser = await launch();
  try {
    const ctx = await newContext(browser);
    for (const it of items) {
      const line = { ok: false, id: it.id || "", ids: [], notes: [] };
      const page = await ctx.newPage();
      const store = attachPostCapture(page);
      try {
        await page.goto(it.url, { waitUntil: "domcontentloaded", timeout: 40000 });
        try {
          await page.waitForSelector('a[href*="/video/"]', { timeout: 20000 });
        } catch (e) {
          // 同上：纯图文号没有 /video/ 锚点，只要接口带回图文就不算这个作者失败
          const onlyNotes = noteItemsOf(store);
          if (!onlyNotes.length) {
            const t = await page.title().catch(() => "");
            throw new Error("no video cards, title=" + t.slice(0, 30));
          }
        }
        await page.waitForTimeout(2500);
        const domIds = await page.evaluate(() => {
          const out = [];
          for (const a of document.querySelectorAll('a[href*="/video/"]')) {
            const m = (a.getAttribute("href") || "").match(/video\/(\d+)/);
            if (m) out.push(m[1]);
          }
          return [...new Set(out)];
        });
        const ids = authorVideoIds(store, domIds);
        const notes = noteItemsOf(store);
        if (ids.length || notes.length) {
          line.ok = true;
          line.ids = ids.slice(0, maxVideos);
          line.notes = notes.slice(0, maxVideos);
          line.ids_from = store.fromApi ? "api" : "dom";
        } else line.error = "empty list (maybe login wall / captcha)";
      } catch (e) {
        line.error = (e && e.message) || String(e);
      } finally {
        await page.close().catch(() => {});
      }
      emit(line);
    }
  } finally {
    await browser.close().catch(() => {});
  }
}

/** 模式四：作品链接（视频 / 图文）→ 作者主页 sec_uid + 昵称。
 *
 *  为什么要这一条：抖音 App 分享「图文作品」出来的短链展开后是
 *  www.douyin.com/note/<id>，链接里根本没有 sec_uid，老的订阅路径只能报错
 *  「无法从链接提取 sec_uid」——不是订阅坏了，是这条输入形状没有解析路径。
 *  认作者不许靠"页面里第一个 /user/ 链接"：侧栏全是推荐位，第一个很可能是别人，
 *  而且导航栏那个 /user/self 是登录者自己。所以取到候选之后必须开一次主页，
 *  确认这一条作品出现在 ta 的作品列表里才算认对。
 */
async function fetchAuthorFromWork(workUrl) {
  const workMatch = (workUrl || "").match(/\/(video|note|slides)\/(\d+)/);
  const workId = workMatch ? workMatch[2] : "";
  const browser = await launch();
  try {
    const ctx = await newContext(browser);
    const candidates = [];
    const push = (s) => { if (s && /^MS4wLjAB/.test(s) && candidates.indexOf(s) < 0) candidates.push(s); };

    const page = await ctx.newPage();
    // 作品页自己会预取作者主页的作品列表，请求参数里的 sec_user_id 就是作者
    page.on("request", (r) => {
      const m = r.url().match(/sec_user_id=([^&]+)/);
      if (m) { try { push(decodeURIComponent(m[1])); } catch (e) { /* 非法编码就跳过 */ } }
    });
    await page.goto(workUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForTimeout(6000);
    const fromDom = await page.evaluate(() => {
      const out = [];
      for (const a of document.querySelectorAll("a[href*='/user/']")) {
        const href = a.getAttribute("href") || "";
        if (/\/user\/self(\?|$)/.test(href)) continue;   // 导航栏那个是登录者本人
        const m = href.match(/\/user\/(MS4wLjAB[A-Za-z0-9_\-]+)/);
        if (m) out.push(m[1]);
      }
      return out;
    });
    for (const s of (fromDom || [])) push(s);
    await page.close().catch(() => {});
    if (!candidates.length) { emit({ ok: false, error: "作品页里没出现任何作者主页链接" }); return; }

    for (let i = 0; i < Math.min(candidates.length, 4); i++) {
      const sec = candidates[i];
      const p2 = await ctx.newPage();
      let check = null;
      try {
        await p2.goto("https://www.douyin.com/user/" + sec, { waitUntil: "domcontentloaded", timeout: 45000 });
        await p2.waitForTimeout(7000);
        check = await p2.evaluate((wid) => {
          const ids = {};
          for (const a of document.querySelectorAll("a[href]")) {
            const m = (a.getAttribute("href") || "").match(/\/(video|note|slides)\/(\d+)/);
            if (m) ids[m[1]] = (ids[m[1]] || 0) + 1;
          }
          const all = [];
          for (const a of document.querySelectorAll("a[href]")) {
            const m = (a.getAttribute("href") || "").match(/\/(video|note|slides)\/(\d+)/);
            if (m) all.push(m[2]);
          }
          const h1 = document.querySelector("h1");
          const info = document.querySelector("[data-e2e='user-info']");
          return {
            hit: !wid || all.indexOf(wid) >= 0,
            nickname: ((h1 && h1.textContent) || "").trim(),
            subline: ((info && info.textContent) || "").replace(/\s+/g, "").slice(0, 70),
            counts: { video: ids.video || 0, note: (ids.note || 0) + (ids.slides || 0) },
          };
        }, workId);
      } catch (e) { /* 这一候选打不开就换下一个 */ }
      await p2.close().catch(() => {});
      if (check && check.hit) {
        emit({
          ok: true, sec_uid: sec, nickname: check.nickname || "", subline: check.subline || "",
          work_id: workId, confirmed: !!workId, counts: check.counts, tried: i + 1,
        });
        return;
      }
    }
    emit({ ok: false, error: "候选主页里没找到这条作品，认不出作者", candidates: candidates.length });
  } finally {
    await browser.close().catch(() => {});
  }
}

(async () => {
  const mode = process.argv[2];
  if (mode === "user") {
    await fetchUserVideos(process.argv[3], parseInt(process.argv[4] || "20", 10) || 20);
  } else if (mode === "detail") {
    await fetchVideoDetail(process.argv[3]);
  } else if (mode === "users") {
    await fetchUserVideosBatch(process.argv[3], parseInt(process.argv[4] || "20", 10) || 20);
  } else if (mode === "author") {
    await fetchAuthorFromWork(process.argv[3]);
  } else {
    emit({ ok: false, error: "usage: douyin_browser_fetch.js user <url> [max] | detail <url> | users <json_file> [max] | author <work_url>" });
  }
})().catch((e) => { emit({ ok: false, error: (e && e.message) || String(e) }); });
