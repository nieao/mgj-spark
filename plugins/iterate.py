# -*- coding: utf-8 -*-
"""
多步实施规划 + 对接故障建 Issue 插件（cli-spark）
================================================
用户选定的自主度：**只规划 + 建 Issue，绝不自动改代码/执行**。

两种触发：
  · 「规划」：多步任务 → 大模型拆成有序步骤计划，发飞书卡片（不执行）。
  · 「故障→迭代」：Spark 程序对接出问题 / 报错 / 要迭代升级 → 大模型草拟「迭代方案」
    （问题摘要 + 根因假设 + 分步修复计划 + 验收），并**自动建 GitHub Issue** 记录，
    卡片回 Issue 链接。无 token 时降级：只出方案卡片 + 提示去配 github_token。

安全面（审批/发证/台账）仍由 bridge 统一做；本插件不碰。建 Issue 属外部只写动作，设 T1（落账、无需审批）。
"""
import os
from plugins.base import Plugin, Reply, env_int
from core import providers
from core import github

try:
    from core import memory_store
except Exception:
    memory_store = None

# 故障/迭代类信号（要建 issue）
FAIL_KW = ("对接有问题", "对接不上", "接不上", "报错", "失败", "不通", "跑不起来", "起不来",
           "迭代", "升级", "issue", "开个单", "建单", "提单", "bug", "故障", "修一下", "改进方案")
# 纯规划类信号（只出计划）
PLAN_KW = ("多步", "分步", "规划", "计划", "步骤", "怎么实施", "实施方案", "roadmap", "路线",
           "怎么做", "如何落地", "拆解任务", "拆成几步")

SYS_PLAN = (
    "你是 cli-spark 的实施规划助手。把用户给的任务拆成**有依赖顺序、自底向上**的分步计划。"
    "每步：序号、要做什么、产出/验收、依赖哪一步。只做规划，不写整段代码、不假装已执行。"
    "务实简洁、中文，最后一行给「预计人力/风险点」。"
)
SYS_ITER = (
    "你是 cli-spark 的迭代方案助手。针对 Spark 上程序的对接故障/改进诉求，产出一份可直接当 "
    "GitHub Issue 的**迭代方案**，用 markdown，含：## 问题现象 / ## 根因假设（按可能性排序）/ "
    "## 分步修复计划（有序、可验收）/ ## 验收标准 / ## 风险与回滚。只规划不执行，不编造已知不了的实现细节，"
    "不确定处显式标『待核实』。中文。"
)


def _mem(text, node_id):
    if not memory_store:
        return ""
    try:
        b = memory_store.inject_context(text, node_id, k=3, max_chars=220)
        return ("\n\n" + b) if b else ""
    except Exception:
        return ""


def _spec():
    try:
        from core.registry import settings
        return (settings().get("model_policy") or {}).get("default") or "stepfun"
    except Exception:
        return os.environ.get("MGJ_DEFAULT_MODEL", "stepfun")


def _llm(sys_prompt, user):
    provider, model, _gated = providers.resolve(_spec(), fallback="stepfun")
    out, meta = providers.complete(provider, model, sys_prompt, user,
                                   timeout=env_int("MGJ_LLM_TIMEOUT", 120))
    return out, meta


class IteratePlugin(Plugin):
    name = "iterate"
    priority = 10          # 高于 general_qa 兜底；低于强命中的运维插件
    node_id = "general"

    def _mode(self, text):
        t = text or ""
        if any(k in t for k in FAIL_KW):
            return "issue"
        if any(k in t for k in PLAN_KW):
            return "plan"
        return None

    def can_handle(self, text, ctx):
        m = self._mode(text)
        if m == "issue":
            return 0.75
        if m == "plan":
            return 0.6
        return 0.0

    def tier(self, text, ctx):
        return 1           # 规划=只读级；建 Issue=外部只写，落账即可，无需审批

    def handle(self, text, ctx, rec):
        mode = self._mode(text) or "plan"
        node_id = self.node()
        if mode == "plan":
            out, meta = _llm(SYS_PLAN + _mem(text, node_id), text)
            if out is None:
                return Reply(text=f"⚠ 规划没出来：{(meta or {}).get('error','未知')}")
            return Reply(title="🧭 多步实施规划（仅规划·不执行）",
                         body=out.strip() + f"\n\n— via {meta.get('provider')}:{meta.get('model')}",
                         template="blue",
                         meta={"mode": "plan", "provider": meta.get("provider"), "model": meta.get("model")})

        # mode == issue：草拟迭代方案 + 建 Issue
        out, meta = _llm(SYS_ITER + _mem(text, node_id), text)
        if out is None:
            return Reply(text=f"⚠ 迭代方案没出来：{(meta or {}).get('error','未知')}")
        plan = out.strip()
        title = "cli-spark 迭代：" + (text.strip().replace("\n", " ")[:40] or "对接问题")
        body = plan + f"\n\n---\n> 由 cli-spark 自动草拟（原始诉求：{text.strip()[:200]}）。仅方案，未自动改动代码。"
        if not github.has_token():
            return Reply(title="🛠 迭代方案（未建 Issue：缺 token）",
                         body=plan + "\n\n⚠ 未配置 `github_token`（credentials.json 或 GITHUB_TOKEN），"
                              "Issue 未建。配好后再发我即可自动登记到 GitHub。",
                         template="orange", meta={"mode": "issue", "issue": False})
        r = github.create_issue(title, body, labels=["iteration", "cli-spark"])
        if r.get("ok"):
            return Reply(title="🛠 迭代方案已建 GitHub Issue",
                         body=plan + f"\n\n✅ 已登记：#{r['number']}（{github.repo()}）",
                         template="green",
                         urls=[{"text": f"🔗 查看 Issue #{r['number']}", "url": r["url"]}],
                         meta={"mode": "issue", "issue": True, "issue_url": r["url"]})
        return Reply(title="🛠 迭代方案（建 Issue 失败）",
                     body=plan + f"\n\n⚠ Issue 未建成：{r.get('msg') or r.get('reason')}",
                     template="orange", meta={"mode": "issue", "issue": False, "err": r.get("msg")})
