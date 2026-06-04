"""AI文案优化模块

调用大模型API优化口播文案，输出结构化JSON。
"""

import json
from typing import Optional, Dict, Any


AI_OPTIMIZE_PROMPT = """你是短视频内容运营专家。请基于以下抖音视频信息，整理并优化文案。

要求：
1. 保留原视频的事实信息，不要编造。
2. 去掉口播中的语气词、重复词、无意义停顿。
3. 输出适合短视频发布的自然文案。
4. 生成5个标题备选。
5. 提炼3-8个关键词。
6. 如果存在明显夸大、违规、敏感表达，请指出。
7. 输出JSON，不要输出多余解释。

输入信息：
标题：{title}
描述：{desc}
作者：{author}
口播识别文本：{transcript}

输出JSON格式：
{{
  "clean_transcript": "清洗后的口播文案",
  "optimized_copy": "优化后的完整文案",
  "short_copy": "精简版文案（100字以内）",
  "title_options": ["标题1", "标题2", "标题3", "标题4", "标题5"],
  "summary": "内容摘要（50字以内）",
  "selling_points": ["卖点1", "卖点2"],
  "keywords": ["关键词1", "关键词2"],
  "risk_words": ["可能的敏感词"],
  "recommended_tags": ["标签1", "标签2"]
}}"""


class AIOptimizer:
    """AI文案优化器"""

    def __init__(self, method: str = "skip", api_key: str = "",
                 api_base: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini"):
        self.method = method
        self.api_key = api_key
        self.api_base = api_base
        self.model = model

    def optimize(self, title: str, desc: str, author: str,
                 transcript: str) -> Dict[str, Any]:
        """优化文案。

        Args:
            title: 视频标题
            desc: 视频描述
            author: 作者
            transcript: ASR转写文本

        Returns:
            优化结果字典
        """
        if self.method == "skip":
            return self._skip_optimize(title, desc, transcript)

        prompt = AI_OPTIMIZE_PROMPT.format(
            title=title, desc=desc, author=author, transcript=transcript
        )

        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            result_text = response.choices[0].message.content
            return json.loads(result_text)
        except Exception as e:
            return {"error": str(e), "clean_transcript": transcript}

    def _skip_optimize(self, title: str, desc: str,
                       transcript: str) -> Dict[str, Any]:
        """跳过AI优化，返回基础清洗结果"""
        # 简单清洗：去除多余空格和换行
        clean = transcript.strip()
        return {
            "clean_transcript": clean,
            "optimized_copy": "",
            "short_copy": "",
            "title_options": [],
            "summary": "",
            "selling_points": [],
            "keywords": [],
            "risk_words": [],
            "recommended_tags": [],
        }
