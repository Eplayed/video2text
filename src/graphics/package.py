# -*- coding: utf-8 -*-
"""manifest 包管理（Phase 1 抽取自 toutiao_graphics.py）。

输出目录（output_dir）由调用方注入，渠道无关。Python 3.9 兼容。
"""
import json


def list_packages(output_dir):
    """扫描 output/toutiao/*/manifest.json，倒序返回列表。"""
    items = []
    if not output_dir.exists():
        return items
    for mf in output_dir.glob("*/manifest.json"):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            items.append(data)
        except (ValueError, OSError):
            continue
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def get_package(summary_id, output_dir):
    mf = output_dir / str(summary_id) / "manifest.json"
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except ValueError:
        return None


def delete_package(summary_id, output_dir):
    """删 manifest + 图片；目录内残留 html 一并清理，目录本身保留（send_from_directory 安全）。"""
    import shutil

    d = output_dir / str(summary_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False
