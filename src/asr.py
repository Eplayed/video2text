"""语音识别模块

支持本地 Whisper 和云端 ASR 服务。
从视频中提取音频并转录为文字。
"""

import os
import subprocess
import tempfile
from typing import Optional
from pathlib import Path


def extract_audio(video_path: str, output_path: Optional[str] = None,
                  sample_rate: int = 16000) -> str:
    """从视频中提取音频为 WAV 格式。

    Args:
        video_path: 视频文件路径
        output_path: 输出音频路径（默认为临时文件）
        sample_rate: 采样率

    Returns:
        音频文件路径
    """
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".wav", dir="/tmp")

    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-ac", "1",
        "-y", output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def download_video(video_url: str, output_path: str,
                   referer: str = "https://www.douyin.com/") -> str:
    """下载视频文件。

    Args:
        video_url: 视频下载URL
        output_path: 输出路径
        referer: Referer头

    Returns:
        下载后的文件路径
    """
    cmd = [
        "curl", "-L", "-o", output_path,
        "-H", f"Referer: {referer}",
        video_url
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


class WhisperASR:
    """本地 Whisper 语音识别"""

    def __init__(self, model: str = "small", language: str = "zh"):
        self.model = model
        self.language = language

    def transcribe(self, audio_path: str) -> str:
        """转录音频文件。

        Args:
            audio_path: WAV音频文件路径

        Returns:
            转录文本
        """
        import whisper

        model = whisper.load_model(self.model)
        result = model.transcribe(audio_path, language=self.language)
        return result["text"].strip()


class WhisperAPIASR:
    """OpenAI Whisper API 语音识别"""

    def __init__(self, api_key: str, api_base: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.api_base = api_base

    def transcribe(self, audio_path: str) -> str:
        """通过 API 转录音频。

        Args:
            audio_path: 音频文件路径

        Returns:
            转录文本
        """
        import openai

        client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
        with open(audio_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="zh",
            )
        return result.text.strip()


def get_asr_engine(method: str = "local_whisper", **kwargs):
    """获取 ASR 引擎实例。

    Args:
        method: ASR方式 (local_whisper / whisper_api)
        **kwargs: 各引擎参数

    Returns:
        ASR 实例
    """
    if method == "local_whisper":
        return WhisperASR(
            model=kwargs.get("whisper_model", "small"),
            language=kwargs.get("language", "zh"),
        )
    elif method == "whisper_api":
        return WhisperAPIASR(
            api_key=kwargs.get("api_key", ""),
            api_base=kwargs.get("api_base", "https://api.openai.com/v1"),
        )
    else:
        raise ValueError(f"不支持的 ASR 方式: {method}")


def transcribe_video(video_url: str, asr_method: str = "local_whisper",
                     temp_dir: str = "/tmp", **kwargs) -> str:
    """完整流程：下载视频 → 提取音频 → ASR转录。

    Args:
        video_url: 无水印视频下载URL
        asr_method: ASR方式
        temp_dir: 临时文件目录
        **kwargs: ASR引擎参数

    Returns:
        转录文本
    """
    import time

    # 下载视频
    video_path = os.path.join(temp_dir, f"video_{int(time.time())}.mp4")
    download_video(video_url, video_path)

    # 提取音频
    audio_path = extract_audio(video_path)

    # ASR 转录
    engine = get_asr_engine(asr_method, **kwargs)
    transcript = engine.transcribe(audio_path)

    # 清理临时文件
    for f in [video_path, audio_path]:
        if os.path.exists(f):
            os.remove(f)

    return transcript
