# -*- coding: utf-8 -*-
"""出图门禁：判定「这次能不能出图」，头条与公众号两条渠道共用。

存在理由（2026-09-30 事故复盘）：DeepSeek 欠费返回 402 后，整合稿与卡片化两层
都静默掉到本地兜底，把一份带「[AI生成失败…Error code: 402]」的 590 字规则版骨架
渲染成 5 张读者可见的卡片，用户侧只看到 lint 里两条 warn。

项目纪律「AI 未配置时静默跳过、不阻断主流程」针对的是**采集主流程**；
出图是用户主动点击的动作，二者要分开：
  - 未配置 → 允许本地兜底（保持旧行为，e2e 也依赖它）；
  - 已配置但调用失败 → 必须抛错，绝不能拿兜底图冒充成品。
"""


class GraphicsGateError(RuntimeError):
    """出图前置条件不满足。消息可直接展示给用户，不需要 traceback。"""


def ai_usable(ai_config):
    """AI 是否「配了且该用」。返回 False 时调用方走本地兜底。"""
    return bool(ai_config) and bool(ai_config.get("api_key")) and ai_config.get("method") != "skip"


# 规则版整合稿指纹：由 content_store 的 AI 失败兜底分支写进正文（见其 wechat_material
# 规则版模板与「[AI生成失败…」追加逻辑）。这类稿只有骨架没有血肉，拿去出图必然产出
# 空洞卡片。在消费侧识别而不是只在上游修，是因为历史库里已经存了一批污染稿。
FALLBACK_DRAFT_MARKERS = (
    "[AI生成失败",
    "规则版素材档案仅提供骨架",
    "规则版草稿未做事实核对",
    "规则版草稿基于 ASR 自动抽题",
    "配置 AI 后重新生成可获得完整素材档案",
)


def draft_blockers(summary):
    """整合稿出图前的质量判据，返回问题列表（空 = 可用）。"""
    content = str((summary or {}).get("content") or "")
    for marker in FALLBACK_DRAFT_MARKERS:
        if marker in content:
            return ["整合稿是 AI 失败时留下的规则版骨架（命中「%s」）" % marker]
    return []


def assert_draft_generatable(summary, channel=""):
    """不合格整合稿直接拒绝出图，把「先修上游」显式告诉用户。"""
    bad = draft_blockers(summary)
    if bad:
        raise GraphicsGateError("%s无法出图：%s。请先重新生成整合稿（确认 AI 可用）后再出图。"
                                % (channel, "；".join(bad)))


def brief_error(exc, limit=180):
    """把 openai/网络异常压成一行可读原因，供 UI 直接展示。"""
    text = " ".join(str(exc or "").split())
    return text[:limit]
