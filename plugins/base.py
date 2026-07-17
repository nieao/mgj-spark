# -*- coding: utf-8 -*-
"""
插件基座（mgj-spark · 把「意图分类」从写死的分支升级为可插拔能力）
================================================================
猫管家原版把意图分类写死在 core/intent.py 的四个正则分支里；mgj-spark 精简总控后，
「能处理哪类消息」交给插件自己声明——未来多接一个项目 = 多写一个插件文件，零改主程序。

一个插件实现三件事：
  · name           唯一名（英文，落台账/审计用）
  · can_handle(t,ctx)-> float  该插件对这条消息的置信度 0~1（0=不接，1=强命中）
  · handle(t,ctx,rec)-> Reply  真正处理，返回给用户的回复
可选：
  · tier(t,ctx)-> int          该消息的权限档 T0~T3（默认 1）。审批门/发证按它走。
  · node_id                    落账时算哪个节点（默认 = name）

主程序（bridge）拿所有插件里 can_handle 最高分的那个来处理；并列时按 priority（大者先）。
Reply 只承载「给用户看什么」，权限/审批/发证/台账全在 bridge 统一做（插件不碰安全面）。
"""
import os
import importlib
import pkgutil


def env_int(name, default):
    """读取整数型环境变量，容忍脏值（行内 # 注释 / 首尾空白 / 引号）。
    根因：.env 写成 `MGJ_LLM_TIMEOUT=90  # 单发补全超时(秒)`，若被非 bash 的加载
    方式整行读入，值就带注释尾巴，int() 直接 ValueError 崩掉整条派发。这里剥掉 #
    之后、首尾空白与引号再转；仍无法解析则退回 default，绝不因一句注释崩。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.split("#", 1)[0].strip().strip('"').strip("'")
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


class Reply:
    """插件回复。text=纯文本；或给 title+body 走飞书交互卡片。
    image_paths 非空 → 卡片内嵌生成图（生图插件用）；有图时即使无 title/body 也走卡片。"""
    def __init__(self, text=None, title=None, body=None, template="blue",
                 urls=None, actions=None, meta=None, image_paths=None):
        self.text = text
        self.title = title
        self.body = body
        self.template = template
        self.urls = urls or []
        self.actions = actions
        self.meta = meta or {}
        self.image_paths = image_paths or []

    @property
    def is_card(self):
        return bool(self.title or self.body or self.image_paths)


class Plugin:
    """插件基类。子类至少覆盖 name / can_handle / handle。"""
    name = "base"
    priority = 0          # 同分时的裁决权重（大者先）
    node_id = None        # 落台账的节点名，None=用 name

    def can_handle(self, text, ctx):
        return 0.0

    def tier(self, text, ctx):
        return 1          # 默认 T1（项目内写/单发问答）；只读插件应覆盖为 0

    def handle(self, text, ctx, rec):
        raise NotImplementedError

    def node(self):
        return self.node_id or self.name


# ---------------- 插件发现 ----------------
_cache = {"plugins": None}


def load_plugins(force=False):
    """自动发现 plugins/ 下所有模块里的 Plugin 子类并实例化。
    约定：每个插件模块定义一个或多个 Plugin 子类即被收录（base/__init__ 除外）。
    结果缓存；force=True 重扫（热插拔用）。绝不抛——单个插件坏了不拖垮其余。"""
    if _cache["plugins"] is not None and not force:
        return _cache["plugins"]
    found = []
    here = os.path.dirname(os.path.abspath(__file__))
    for mod in pkgutil.iter_modules([here]):
        if mod.name in ("base", "__init__"):
            continue
        try:
            m = importlib.import_module(f"plugins.{mod.name}")
        except Exception as e:
            print(f"[plugins] 加载 {mod.name} 失败：{type(e).__name__}: {e}", flush=True)
            continue
        for attr in vars(m).values():
            if (isinstance(attr, type) and issubclass(attr, Plugin)
                    and attr is not Plugin and attr.__module__ == m.__name__):
                try:
                    found.append(attr())
                except Exception as e:
                    print(f"[plugins] 实例化 {attr.__name__} 失败：{e}", flush=True)
    found.sort(key=lambda p: p.priority, reverse=True)
    _cache["plugins"] = found
    return found


def route(text, ctx):
    """返回 (plugin, score)。取 can_handle 最高分；全 0 时返回兜底（priority 最低的那个，
    约定 general_qa 声明极低 priority 兼最低正分做兜底）。"""
    best, best_score = None, -1.0
    for p in load_plugins():
        try:
            s = float(p.can_handle(text, ctx) or 0.0)
        except Exception:
            s = 0.0
        if s > best_score:
            best, best_score = p, s
    return best, max(best_score, 0.0)
