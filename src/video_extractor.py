"""视频详情提取模块

基于 douyin_parse 项目提取视频元数据。
"""

import sys
import os
import json
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List
from pathlib import Path

# 引入项目路径配置
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from src.path_config import ensure_parser_on_path, get_cookie_path

# 引入 douyin_parse
ensure_parser_on_path()
from douyin_video_parser import DouyinVideoParser


@dataclass
class VideoInfo:
    source_url: str                    # 原始输入链接
    real_url: str                      # 重定向后的真实链接
    aweme_id: str                      # 视频 ID
    author_name: str                   # 作者昵称
    author_sec_uid: str                # 作者 sec_user_id
    title: str                         # 标题（从desc去掉hashtag）
    desc: str                          # 完整描述/文案（含hashtag）
    create_time: str                   # 发布时间（格式化字符串）
    create_time_ts: int                # 发布时间戳
    duration: Optional[int] = None     # 视频时长（秒）
    like_count: Optional[int] = None   # 点赞数
    comment_count: Optional[int] = None # 评论数
    share_count: Optional[int] = None  # 分享数
    collect_count: Optional[int] = None # 收藏数
    hashtags: List[str] = None         # 话题标签列表
    music_title: Optional[str] = None  # 音乐标题
    cover_url: str = ""                # 封面图URL
    video_url: str = ""                # 视频下载URL（无水印）
    transcript_raw: Optional[str] = None   # ASR原始识别文本
    transcript_clean: Optional[str] = None # 清洗后文本
    ai_optimized_copy: Optional[str] = None # AI优化文案
    ai_title_options: Optional[str] = None  # AI标题备选（JSON字符串）
    summary: Optional[str] = None      # 内容摘要
    keywords: Optional[str] = None     # 关键词
    risk_words: Optional[str] = None   # 敏感词提醒
    recommended_tags: Optional[str] = None # AI推荐标签
    status: str = "success"            # 处理状态
    error_message: Optional[str] = None # 错误信息


class VideoExtractor:
    """视频详情提取器"""

    def __init__(self, cookie_file: str = None):
        self.parser = DouyinVideoParser()
        self.cookie_file = cookie_file or str(get_cookie_path())

    def extract_video(self, url_or_id: str) -> VideoInfo:
        """提取单条视频详情。

        Args:
            url_or_id: 视频链接或 aweme_id

        Returns:
            VideoInfo 对象
        """
        try:
            info = self.parser.parse_video(url_or_id)

            # 从 desc 中分离标题和 hashtag
            desc = info.get("desc", "")
            title = desc.split("#")[0].strip() if "#" in desc else desc

            # 提取 hashtags
            hashtags = []
            if "#" in desc:
                parts = desc.split("#")[1:]
                for part in parts:
                    tag = part.strip().split()[0] if part.strip() else ""
                    if tag:
                        hashtags.append(tag)

            # 格式化发布时间
            create_ts = info.get("create_time", 0)
            create_time_str = datetime.fromtimestamp(create_ts).strftime("%Y-%m-%d %H:%M:%S") if create_ts else ""

            # 统计数据
            statistics = info.get("statistics", {})
            video_obj = info.get("video", {})

            video_info = VideoInfo(
                source_url=url_or_id,
                real_url=f"https://www.douyin.com/video/{info.get('aweme_id', '')}",
                aweme_id=info.get("aweme_id", ""),
                author_name=info.get("author_nickname", ""),
                author_sec_uid=info.get("author_sec_uid", ""),
                title=title,
                desc=desc,
                create_time=create_time_str,
                create_time_ts=create_ts,
                duration=video_obj.get("duration", None),
                like_count=statistics.get("digg_count", None),
                comment_count=statistics.get("comment_count", None),
                share_count=statistics.get("share_count", None),
                collect_count=statistics.get("collect_count", None),
                hashtags=hashtags,
                music_title=info.get("music_title", ""),
                cover_url=info.get("cover_url", ""),
                video_url=info.get("nwm_url", ""),
            )
            return video_info

        except Exception as e:
            return VideoInfo(
                source_url=url_or_id,
                real_url=url_or_id,
                aweme_id="",
                author_name="",
                author_sec_uid="",
                title="",
                desc="",
                create_time="",
                create_time_ts=0,
                status="failed",
                error_message=str(e),
            )

    def extract_nwm_video_url(self, url_or_id: str) -> Optional[str]:
        """仅提取无水印视频下载链接（用于ASR音频提取）"""
        try:
            info = self.parser.parse_video(url_or_id)
            return info.get("nwm_url", None)
        except Exception:
            return None
