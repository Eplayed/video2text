"""已核攻略的公众号版本与跑本助手宣传素材。"""
import copy
import json
import shutil
from pathlib import Path


MINI_COPY = "想查副本入口、任务和掉落，可以打开「跑本助手」的副本手册。"
MINI_CARD = "在公众号编辑器的「小程序」中选择「跑本助手」，关联副本手册页并插入原生卡片。"


def _write_manifest(folder, manifest):
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def add_mini_promo(folder, manifest, asset):
    """宣传素材属于公众号文末，独立于攻略画面与已核游戏结论。"""
    if manifest.get("publish_channel") != "wechat":
        return None
    target = "mini-dungeons.png"
    promo = {"copy": MINI_COPY, "card_hint": MINI_CARD,
             "page_path": "pages/dungeons/index", "image": ""}
    if Path(asset).is_file():
        shutil.copyfile(asset, folder / target)
        promo["image"] = target
    manifest["mini_promo"] = promo
    _write_manifest(folder, manifest)
    return promo


def render_wechat(stage, summary, ai_config, plan, publish, render, asset, **options):
    """复用出图的同一稿，不经过公众号拆稿或改写模型。"""
    folder = stage / "wechat"
    manifest = render(
        summary, ai_config, plan=copy.deepcopy(plan), publish=copy.deepcopy(publish),
        ratio="3:4", publish_channel="wechat", form="article", output_dir=folder,
        copies=1, do_review=False, **options)
    prefix = "/media/poster/%d/wechat/" % int(summary["id"])
    manifest["url"] = prefix + manifest["image"]
    for rows in (manifest["pages"], manifest["candidates"]):
        for row in rows:
            row["url"] = prefix + row["file"]

    promo = add_mini_promo(folder, manifest, asset)
    lines = ["# " + manifest["title"], ""]
    pages = plan.get("pages") or [plan]
    for index, page in enumerate(pages):
        if index or page.get("title") != manifest["title"]:
            lines.extend(["## " + page["title"], ""])
        if page.get("subtitle"):
            lines.extend([page["subtitle"], ""])
        bignum = page.get("bignum") or {}
        number = " ".join(bignum.get(key) or "" for key in ("v", "label", "note")).strip()
        if number:
            lines.extend([number, ""])
        notes = list(page.get("cards") or []) + list(page.get("contrast") or [])
        if page.get("warn"):
            notes.append(page["warn"])
        for card in notes:
            lines.extend(["**%s**　%s" % (card.get("t", ""), card.get("d", "")), ""])
        lines.extend(["![攻略配图 %d](%s)" % (index + 1, manifest["pages"][index]["file"]), ""])
    if manifest.get("source_note"):
        lines.extend([manifest["source_note"], ""])
    lines.extend([promo["copy"], ""])
    if promo["image"]:
        lines.extend(["![跑本助手副本手册界面](%s)" % promo["image"], ""])
    (folder / "article.md").write_text("\n".join(lines), encoding="utf-8")
    _write_manifest(folder, manifest)
    return {
        "article_url": prefix + "article.md",
        "manifest_url": prefix + "manifest.json",
        "pages": [{"url": row["url"]} for row in manifest["pages"]],
        "promo": {**promo, "image_url": prefix + promo["image"] if promo["image"] else ""},
        "publishable": bool(manifest.get("publishable")),
        "ratio": manifest["ratio"],
    }
