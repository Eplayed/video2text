"""链接识别与解析模块

识别抖音链接类型（视频/主页/短链），解析并返回标准化信息。
"""

import re
import requests
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class LinkType(Enum):
    VIDEO = "video"        # 单条视频链接
    PROFILE = "profile"    # 主页链接
    UNKNOWN = "unknown"    # 无法识别


@dataclass
class ResolvedLink:
    link_type: LinkType
    original_url: str
    resolved_url: str
    aweme_id: Optional[str] = None       # 视频 ID
    sec_user_id: Optional[str] = None    # 用户主页 ID
    user_home_url: Optional[str] = None  # 主页链接


# 抖音链接模式
PATTERNS = {
    # 视频详情页: https://www.douyin.com/video/7xxx
    "video_web": re.compile(r"douyin\.com/video/(\d+)"),
    # 主页: https://www.douyin.com/user/MS4wLjABxxx
    "profile_web": re.compile(r"douyin\.com/user/(MS4w[\w=]+)"),
    # 分享短链: https://v.douyin.com/xxx
    "share_short": re.compile(r"v\.douyin\.com/[\w-]+"),
    # 移动端视频: https://www.iesdouyin.com/share/video/7xxx
    "video_mobile": re.compile(r"iesdouyin\.com/share/video/(\d+)"),
}


def resolve_link(url: str, timeout: int = 10) -> ResolvedLink:
    """解析抖音链接，识别类型并提取关键 ID。

    Args:
        url: 原始链接（短链、视频链接、主页链接均可）
        timeout: 短链重定向超时秒数

    Returns:
        ResolvedLink 对象
    """
    original_url = url.strip()

    # 先尝试直接匹配
    for pattern_name, pattern in PATTERNS.items():
        m = pattern.search(original_url)
        if m:
            if pattern_name in ("video_web", "video_mobile"):
                return ResolvedLink(
                    link_type=LinkType.VIDEO,
                    original_url=original_url,
                    resolved_url=original_url,
                    aweme_id=m.group(1),
                )
            elif pattern_name == "profile_web":
                sec_uid = m.group(1)
                home_url = f"https://www.douyin.com/user/{sec_uid}"
                return ResolvedLink(
                    link_type=LinkType.PROFILE,
                    original_url=original_url,
                    resolved_url=home_url,
                    sec_user_id=sec_uid,
                    user_home_url=home_url,
                )

    # 如果是短链，做重定向解析
    if "v.douyin.com" in original_url:
        try:
            resp = requests.head(original_url, timeout=timeout, allow_redirects=True)
            resolved = resp.url
            return resolve_link(resolved)
        except requests.RequestException:
            return ResolvedLink(
                link_type=LinkType.UNKNOWN,
                original_url=original_url,
                resolved_url=original_url,
            )

    return ResolvedLink(
        link_type=LinkType.UNKNOWN,
        original_url=original_url,
        resolved_url=original_url,
    )
