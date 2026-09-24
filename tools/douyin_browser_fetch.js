#!/usr/bin/env node
/**
 * douyin_browser_fetch.js — 用真实 Chromium 抓抖音数据（绕过 Argus API 签名拦截）
 *
 * 背景（2026-09-21）：抖音 Argus 风控升级，纯 API 的 a_bogus 签名被拦
 * （Blocked by ArgusSecurityPlugin Signature Not Found）。本脚本让页面
 * 自己生成签名（页面 XHR 全部 200），零逆向、不怕算法再升级。
 *
 * 用法：
 *   node douyin_browser_fetch.js user   <user_url>  [max_videos]  → {"ok":true,"ids":[...]}
 *   node douyin_browser_fetch.js detail <video_url>               → {"ok":true,"info":{...}}
 * 输出：最后一行为 JSON。
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

/** 模式一：作者主页 → 最新视频 ID 列表 */
async function fetchUserVideos(userUrl, maxVideos) {
  const browser = await launch();
  try {
    const ctx = await newContext(browser);
    const page = await ctx.newPage();
    await page.goto(userUrl, { waitUntil: "domcontentloaded", timeout: 40000 });
    try {
      await page.waitForSelector('a[href*="/video/"]', { timeout: 20000 });
    } catch (e) {
      const t = await page.title().catch(() => "");
      const body = await page.evaluate(() => document.body.innerText.slice(0, 120)).catch(() => "");
      emit({ ok: false, error: "no video cards, title=" + t.slice(0, 30) + " body=" + body.replace(/\n/g, " ").slice(0, 80) });
      return;
    }
    await page.waitForTimeout(2500);
    const ids = await page.evaluate(() => {
      const out = [];
      for (const a of document.querySelectorAll('a[href*="/video/"]')) {
        const m = (a.getAttribute("href") || "").match(/video\/(\d+)/);
        if (m) out.push(m[1]);
      }
      return [...new Set(out)];
    });
    if (!ids.length) { emit({ ok: false, error: "empty list (maybe login wall / captcha)" }); return; }
    emit({ ok: true, ids: ids.slice(0, maxVideos) });
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

(async () => {
  const mode = process.argv[2];
  if (mode === "user") {
    await fetchUserVideos(process.argv[3], parseInt(process.argv[4] || "20", 10) || 20);
  } else if (mode === "detail") {
    await fetchVideoDetail(process.argv[3]);
  } else {
    emit({ ok: false, error: "usage: douyin_browser_fetch.js user <url> [max] | detail <url>" });
  }
})().catch((e) => { emit({ ok: false, error: (e && e.message) || String(e) }); });
