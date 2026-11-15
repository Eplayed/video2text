"""攻略开始前用本机 OCR 核对转写。抽到的画面在这里只充当内部证据。"""
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from . import frame_pool, source_corrections
from .term_glossary import is_wow


# 只改动有稳定锚点的同音项，且须标题与早期原帧独立支持。
_TITLE_TERMS = {"无限壶": "无限服", "一键红": "一键宏"}
# 已人工确认会损坏游戏动作或条件的组合。宽泛单字不触发。
_UNCLEAR = ("骑兽冲锋", "一键输入虎", "一键拉外虎", "输出虎", "和隐习不断拼开")
_OCR_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "read_frame_text.swift"


class EvidenceError(ValueError):
    pass


def _paths(video, frames):
    raw = str(video.get("transcript") or "")
    anchors = [term for term in _UNCLEAR if term in raw]
    regular = [frame for frame in frames if not frame.get("evidence_only")]
    selected = frame_pool._even_sample(regular, 8)
    selected.extend(frame for frame in frames if frame.get("evidence_only"))
    # 即使两段离得很近，优先保留与可疑句同时间窗的帧。
    for term in anchors:
        matches = [frame for frame in frames if term in (frame.get("near") or "")]
        selected.extend(frame_pool._even_sample(matches, 2))
    seen = set()
    out = []
    for frame in sorted(selected, key=lambda item: item["i"]):
        if frame["i"] in seen:
            continue
        seen.add(frame["i"])
        for key in ("raw", "file"):
            if frame.get(key) and Path(frame[key]).is_file():
                file = Path(frame[key])
                stat = file.stat()
                out.append((str(file), stat.st_mtime_ns, stat.st_size, frame.get("ts") or "", frame.get("near") or ""))
                break
    return tuple(out)


@lru_cache(maxsize=96)
def _recognize(paths):
    if sys.platform != "darwin" or not paths:
        return ()
    result = subprocess.run(
        ["/usr/bin/swift", str(_OCR_SCRIPT), *(entry[0] for entry in paths)],
        capture_output=True, text=True, timeout=55, check=True,
    )
    rows = json.loads(result.stdout)
    if len(rows) != len(paths):
        return ()
    return tuple({"path": entry[0], "ts": entry[3], "near": entry[4], "lines": lines}
                 for entry, lines in zip(paths, rows))


def read(video):
    aid = video.get("aweme_id")
    if not aid:
        return ()
    try:
        pool = frame_pool.load(aid)
        frames = (pool.get("frames") or []) + (pool.get("evidence_frames") or [])
        return _recognize(_paths(video, frames))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # OCR 可用性不改变常规素材行为。需要复核的已知错听仍会在下面拦住。
        print("[素材核对] 本机画面识别不可用:", type(exc).__name__, flush=True)
        return ()


def _compact(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or ""))


def _confirmed_title_patch(raw, title, observations):
    core = re.split(r"[，,！!？?｜|：:]", title)[0].strip()
    if len(core) < 6 or len(core) > 35 or not re.fullmatch(r"[\u4e00-\u9fff]+", core):
        return None
    opening = raw[:len(core)]
    repaired = opening
    for wrong, right in _TITLE_TERMS.items():
        if right in core:
            repaired = repaired.replace(wrong, right)
    if repaired != core or repaired == opening or raw.count(opening) != 1:
        return None
    for row in observations[:3]:
        if row["ts"] and any(core in _compact(line) for line in row["lines"]):
            return {"old": opening, "new": core,
                    "evidence": "原片 %s 的字幕与素材标题逐字一致（仅纠正片头术语）" % row["ts"]}
    return None


def prepare(db_path, video):
    title = str(video.get("title") or "")
    raw = str(video.get("transcript") or "")
    if not video.get("aweme_id") or not is_wow(title + raw):
        return ()
    evidence = read(video)
    record = source_corrections.inspect(db_path, video["id"])
    if record["stale"]:
        raise EvidenceError("素材 %s 的转写已变化，请先重新核对定点修订" % video["id"])
    patch = _confirmed_title_patch(raw, title, evidence)
    if patch and not record["patches"]:
        source_corrections.save(db_path, video["id"], record["base_hash"], [patch], only_if_missing=True)
        record = source_corrections.inspect(db_path, video["id"])
    effective = record["effective_text"]
    suspicious = [wrong for wrong, right in _TITLE_TERMS.items()
                  if right in title and wrong in effective[:70]]
    suspicious += [text for text in _UNCLEAR if text in effective]
    if suspicious:
        first = suspicious[0]
        during = next((row["ts"] for row in evidence if first in row["near"] and row["ts"]), "")
        location = "（可先看原片 %s 附近）" % during if during else ""
        raise EvidenceError("素材 %d 转写待核：%s%s。请在转写修订中对照原片确认，再生成整理稿或拆页。"
                            % (video["id"], "、".join(suspicious[:4]), location))
    return evidence
