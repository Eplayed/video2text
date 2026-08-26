"""路径配置：统一管理 douyin_parse 解析器目录位置。

查找优先级：
1. 环境变量 DOUYIN_PARSE_DIR
2. 项目内 vendor/douyin_parse 目录
3. /tmp/douyin_parse（旧版默认路径，兼容）
"""
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENDOR_PARSER = _PROJECT_ROOT / "vendor" / "douyin_parse"
_TMP_PARSER = Path("/tmp/douyin_parse")


def find_parser_dir() -> str:
    """按优先级查找 douyin_parse 目录，返回第一个存在的路径。"""
    # 1. 环境变量
    env_dir = os.environ.get("DOUYIN_PARSE_DIR", "")
    if env_dir and Path(env_dir).is_dir():
        return str(Path(env_dir).resolve())

    # 2. 项目内 vendor 目录
    if _VENDOR_PARSER.is_dir():
        return str(_VENDOR_PARSER.resolve())

    # 3. /tmp 目录（兼容旧版本）
    if _TMP_PARSER.is_dir():
        return str(_TMP_PARSER.resolve())

    # 默认返回 vendor 路径（即使不存在，也用于错误提示）
    return str(_VENDOR_PARSER.resolve())


def ensure_parser_on_path() -> str:
    """确保 douyin_parse 目录在 sys.path 中，返回目录路径。"""
    parser_dir = find_parser_dir()
    if parser_dir not in sys.path:
        sys.path.insert(0, parser_dir)
    return parser_dir


def get_cookie_path() -> Path:
    """返回 cookie 文件路径（与 parser 同目录）。"""
    return Path(find_parser_dir()) / "douyin_cookie.txt"
