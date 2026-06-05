#!/usr/bin/env python3
"""列出适合整理成文章的视频候选。

默认读取项目根目录的 video_index.json，优先展示 A/B 级、未转文章的视频。
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="列出视频文章候选")
    parser.add_argument("--index", default=str(Path(__file__).resolve().parents[1] / "video_index.json"))
    parser.add_argument("--score", default="A,B,B-需核查", help="逗号分隔的选题等级")
    parser.add_argument("--topic", help="只看指定分类，例如：流放2攻略")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    index_path = Path(args.index)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    wanted_scores = {s.strip() for s in args.score.split(",") if s.strip()}

    videos = []
    for item in data.get("videos", []):
        if item.get("article_score") not in wanted_scores:
            continue
        if args.topic and item.get("topic") != args.topic:
            continue
        if item.get("status") == "已写文稿":
            continue
        videos.append(item)

    score_rank = {"A": 0, "B-需核查": 1, "B": 2, "C": 3}
    videos.sort(key=lambda x: (
        score_rank.get(x.get("article_score"), 9),
        x.get("sheet", ""),
        x.get("row", 0),
    ))

    for item in videos[:args.limit]:
        risk = item.get("fact_risk") or "无明显风险"
        print(f"[{item.get('article_score')}] {item.get('topic')} | {item.get('sheet')}!R{item.get('row')}")
        print(f"标题：{item.get('title')}")
        print(f"作者：{item.get('author')} | 时间：{item.get('create_time')}")
        print(f"风险：{risk}")
        print(f"摘要：{item.get('description')}")
        print("")


if __name__ == "__main__":
    main()
