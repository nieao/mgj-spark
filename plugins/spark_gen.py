# -*- coding: utf-8 -*-
"""
Spark 生图插件（mgj-spark 的「动作能力」核心）
================================================
把飞书里的「画一张 / 生成一张图 / 出图 xxx」变成真正的 ComfyUI 出图：
  识别画图意图 → core.comfyui_client.generate（复用 AIGC-spark 已在 Spark 跑通的 Klein 工作流）
  → 落盘到 out/ → 回一张飞书卡片 + 内嵌生成图。

与 spark_ops 的分工（互不抢）：
  · spark_ops 只接「问状态」（生图好了吗 / ComfyUI 在线吗）——本插件遇状态信号词直接让路（返回 0）。
  · 本插件只接「要画图」这类动作请求，命中即高分接管（盖过 spark_ops 与 general_qa 兜底）。

档位（tier）：默认 T1（自动放行，说画就画）；registry.settings.gen_tier 可改 2 走审批门。
端点/档位全走 env（COMFY_URL / COMFY_PROFILE / FLUX_VARIANT）——本机跑用 aki 秋叶包，
Spark 上跑用 aeon-spark Klein-9B，同一份代码零改动。
"""
import os
import re

from plugins.base import Plugin, Reply, env_int

# ---------- 意图识别词表 ----------
# 问状态 → 让给 spark_ops（"生图好了吗 / 出图服务在线吗 / 画得怎么样了"）
_STATUS_SIG = ("好了吗", "在线", "状态", "进度", "健康", "怎么样了", "怎样了", "起来了",
               "挂了", "死了", "就绪", "ok吗", "正常吗", "跑起来了", "在不在")
# 明确画图短语（强命中）
_STRONG_DRAW = ("画一张", "画一幅", "画个", "画张", "画幅", "帮我画", "给我画", "帮忙画",
                "生成一张", "生成一幅", "生成图", "生成图片", "生成一个图", "生成个图",
                "出一张", "出张图", "出一幅", "来一张", "来张图", "做一张图", "做张图",
                "整一张", "渲染一张", "生一张", "画一个")
# 动词 + 图像名词组合（"画个海报 / 生成壁纸 / 渲染一张插画"）
_DRAW_VERB = ("画", "生成", "出图", "生图", "渲染", "draw", "generate")
_IMG_NOUN = ("图", "图片", "照片", "画面", "海报", "插画", "壁纸", "封面", "立绘", "头像", "logo", "banner")


def _out_dir():
    d = os.environ.get("MGJ_OUT_DIR")
    if d:
        return os.path.expanduser(d)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "out")


def _gen_tier(default=1):
    try:
        from core.registry import settings
        v = settings().get("gen_tier")
        if v is not None:
            return int(v)
    except Exception:
        pass
    return default


# ---------- prompt 清洗 + 参数解析 ----------
_PREFIX_RE = re.compile(
    r"^\s*(帮我|给我|请|麻烦|帮忙)?\s*(用\s*(comfyui|spark|ai|flux)\s*)?"
    r"(画一张|画一幅|画一个|画个|画张|画幅|画|生成一张图片|生成一张图|生成一张|生成一幅|"
    r"生成图片|生成个图|生成图|生成|出一张图|出一张|出张图|出一幅|出图|来一张|来张图|"
    r"做一张图|做张图|整一张|渲染一张|渲染|生一张|draw|generate)\s*[:：,，]?\s*",
    re.I)


def _parse_opts(text):
    """从原文解析 (clean_prompt, width, height, seed)。尺寸/seed 词从 prompt 里剔掉，描述保留。"""
    t = text.strip()
    seed = 0
    m = re.search(r"(seed|种子)\s*[=＝:：]?\s*(\d{1,10})", t, re.I)
    if m:
        seed = int(m.group(2))
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    # 显式像素（1024x1024 / 768*1344）
    w = h = 0
    m = re.search(r"(\d{3,4})\s*[x×*]\s*(\d{3,4})", t)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    # 中文版式关键词
    if not (w and h):
        if any(k in t for k in ("竖版", "竖图", "竖屏", "手机壁纸", "海报", "立绘")):
            w, h = 768, 1344
        elif any(k in t for k in ("横版", "横图", "横屏", "宽屏", "壁纸")):
            w, h = 1344, 768
        else:
            w, h = 1024, 1024
    # 去掉纯版式词，避免污染 prompt（保留"海报/壁纸/立绘"这类有语义的名词）
    for k in ("竖版", "竖图", "竖屏", "横版", "横图", "横屏", "宽屏", "方图", "正方形", "方形"):
        t = t.replace(k, "")
    clean = _PREFIX_RE.sub("", t).strip(" ，,。.:：、") or text.strip()
    return clean, w, h, seed


class SparkGenPlugin(Plugin):
    name = "spark_gen"
    priority = 20          # 高于 spark_ops(10)，确保画图指令不被观测插件截走
    node_id = "aigc-spark"

    def can_handle(self, text, ctx):
        t = (text or "").strip()
        if not t:
            return 0.0
        low = t.lower()
        # 问状态的一律让给 spark_ops
        if any(w in t for w in _STATUS_SIG):
            return 0.0
        if any(p in t for p in _STRONG_DRAW):
            return 0.92
        has_verb = any(v in low for v in _DRAW_VERB)
        has_noun = any(n in t for n in _IMG_NOUN)
        if has_verb and has_noun:
            return 0.9
        if t[:1] == "画" or low.startswith("draw"):
            return 0.88
        return 0.0

    def tier(self, text, ctx):
        return _gen_tier(1)

    def eta_seconds(self, text, ctx):
        # 生图同步阻塞出图，约 1~2 分钟 → 触发 bridge 通用回执 + 心跳（每 30s）
        return env_int("MGJ_GEN_ETA", 90)

    def handle(self, text, ctx, rec):
        try:
            from core import comfyui_client as cc
        except Exception as e:
            return Reply(text=f"⚠ 生图模块加载失败：{type(e).__name__}: {e}",
                         meta={"kind": "gen_error"})

        if not cc.is_available():
            cfg = cc.active_config()
            return Reply(title="🎨 生图未就绪", template="orange",
                         body=(f"ComfyUI 没在线（{cfg['comfy_url']}）。\n"
                               f"档位 `{cfg['profile']}`／{cfg['flux_variant']}。\n\n"
                               "先把 ComfyUI 拉起来（Spark 上是 8188），或问我「spark 状态」看运维态。"),
                         meta={"kind": "gen_error", "reason": "comfy_offline"})

        prompt, w, h, seed = _parse_opts(text)
        if not prompt:
            return Reply(text="想画什么？给我一句描述，比如「画一张 橘猫戴宇航头盔坐在月球上」。",
                         meta={"kind": "gen_empty"})

        timeout = env_int("MGJ_GEN_TIMEOUT", 600)
        res = cc.generate(prompt, _out_dir(), mode="flux", width=w, height=h,
                          seed=seed, timeout=timeout)
        if not res.get("ok"):
            return Reply(title="🎨 出图失败", template="red",
                         body=f"提示词：{prompt}\n\n原因：{res.get('msg', '未知')}",
                         meta={"kind": "gen_fail", "provider": res.get("provider", ""),
                               "prompt_id": res.get("prompt_id", "")})

        paths = res.get("image_paths", [])
        secs = res.get("secs", "?")
        cfg = cc.active_config()
        body = (f"**提示词**：{prompt}\n"
                f"**尺寸**：{w}×{h}　**seed**：{seed or '随机'}\n"
                f"**引擎**：{res.get('provider', 'comfyui')}　**耗时**：{secs}s\n"
                f"**prompt_id**：`{str(res.get('prompt_id', ''))[:12]}`")
        return Reply(title="🎨 出图完成", template="turquoise", body=body,
                     image_paths=paths,
                     meta={"kind": "gen_ok", "provider": res.get("provider", ""),
                           "prompt_id": res.get("prompt_id", ""), "secs": secs,
                           "model": cfg["flux"].get("unet", ""), "tier": self.tier(text, ctx),
                           "image_paths": paths})
