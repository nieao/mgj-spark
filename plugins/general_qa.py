# -*- coding: utf-8 -*-
"""
通用问答/派发插件（兜底）
==========================
不属于任何专用插件（如 spark_ops）的消息落到这里：用大模型单发作答。
· 权限档由 core.intent.classify 判（bridge 据此走审批门/发证）——保留猫管家的 T0~T3 安全面。
· 模型走 core.providers（默认 StepFun step-3.7-flash，OpenAI 兼容 HTTP；不依赖 claude CLI）。
· 注入分层记忆（L1/L2/L3）里与本条相关的片段——「记忆多发」在此落地。
纯 HTTP 单发补全，不做多步工具循环（要 agentic 改文件得另配 claude CLI，spark 上一般不需要）。
"""
import os

from plugins.base import Plugin, Reply, env_int
from core import intent as intent_mod
from core import providers

try:
    from core import memory_store
except Exception:
    memory_store = None


def _default_spec():
    """默认模型规格：registry.settings.model_policy.default → 环境 → stepfun。"""
    try:
        from core.registry import settings
        pol = settings().get("model_policy") or {}
        d = pol.get("default")
        if d:
            return d
    except Exception:
        pass
    return os.environ.get("MGJ_DEFAULT_MODEL", "stepfun")


def _fallback_spec():
    try:
        from core.registry import settings
        fb = settings().get("llm_fallback")
        if fb:
            return fb
    except Exception:
        pass
    return "stepfun"


def _memory_context(text, node_id):
    """取与本条相关的分层记忆拼进 system（scope 强制隔离）。失败静默返回空串。"""
    if not memory_store:
        return ""
    try:
        block = memory_store.inject_context(text, node_id, k=3, max_chars=220)
    except Exception:
        return ""
    return ("\n\n" + block) if block else ""


SYSTEM_BASE = (
    "你是「猫管家·Spark 版」，跑在一台 NVIDIA DGX Spark 服务器上，帮主人打理这台机器上的 "
    "AIGC-spark 生图工作台与 aeon-spark ComfyUI。回答简洁务实、用中文。"
    "涉及运维状态类的问题若你手头没有实时数据，就提示主人问「spark 状态 / 生图好了吗」由专用插件查。"
)


class GeneralQAPlugin(Plugin):
    name = "general_qa"
    priority = -100        # 最低：只在没有专用插件命中时兜底
    node_id = "general"

    def can_handle(self, text, ctx):
        # 恒定极小正分：任何非空消息都能兜底，但任何专用插件（正分）都会盖过它
        return 0.05 if (text or "").strip() else 0.0

    def tier(self, text, ctx):
        _intent, tier, _why = intent_mod.classify(text)
        return tier

    def handle(self, text, ctx, rec):
        node_id = self.node()
        sys_prompt = SYSTEM_BASE + _memory_context(text, node_id)
        spec = _default_spec()
        provider, model, gated = providers.resolve(spec, fallback=_fallback_spec())
        out, meta = providers.complete(provider, model, sys_prompt, text,
                                       timeout=env_int("MGJ_LLM_TIMEOUT", 90))
        if out is None:
            err = (meta or {}).get("error", "未知错误")
            return Reply(text=f"⚠ 模型没答上来（{provider}）：{err}",
                         meta={"provider": provider, "model": model, "error": err})
        note = f"— via {meta.get('provider')}:{meta.get('model')}"
        if gated:
            note += "（默认模型不可用，已降级）"
        return Reply(text=out.strip() + f"\n\n{note}",
                     meta={"provider": meta.get("provider"), "model": meta.get("model"),
                           "usage": meta.get("usage"), "duration_s": meta.get("duration_s")})
