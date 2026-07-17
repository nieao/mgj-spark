# -*- coding: utf-8 -*-
"""
统一注册表（猫管家）
====================
融合两张表：猫猫中台 nodes.py 的调度字段（keywords/service_ports/needs_service/subprojects）
+ 九命中枢 projects-registry.json 的治理字段（category/tier_max/mem_namespace）。
真相源是 config/registry.json —— 新增项目改 JSON 即可，不用改源码。
对外 API 保持与中台 nodes.py 兼容：all_nodes() / get_node() / DEFAULT_NODE。
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTRY_PATH = os.path.join(ROOT, "config", "registry.json")

_cache = {"mtime": None, "data": None}


def _load():
    """带 mtime 缓存的加载：JSON 改了自动热重载，无需重启桥。"""
    try:
        mtime = os.path.getmtime(REGISTRY_PATH)
    except OSError:
        mtime = None
    if _cache["data"] is None or _cache["mtime"] != mtime:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            _cache["data"] = json.load(f)
        _cache["mtime"] = mtime
    return _cache["data"]


def settings():
    return _load().get("settings", {})


def all_nodes():
    return [n for n in _load().get("nodes", []) if n.get("enabled", True)]


def default_node():
    return _load().get("default_node")


def get_node(node_id):
    for n in all_nodes():
        if n["id"] == node_id:
            return n
    d = default_node()
    if d and node_id == d["id"]:
        return d
    return None


def get_node_by_name(name):
    for n in all_nodes():
        if n["name"] == name:
            return n
    return None


def category_of(node_id):
    n = get_node(node_id)
    return (n or {}).get("category", "")


# 与中台代码兼容的模块级常量（注意：DEFAULT_NODE 在 import 时固化一次即可，结构不常变）
DEFAULT_NODE = default_node()


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ns = all_nodes()
    print(f"注册表加载成功 · {len(ns)} 个启用节点 + 兜底「{DEFAULT_NODE['name']}」")
    for n in ns:
        print(f"  · {n['name']:8s} tier_max=T{n.get('tier_max',1)} cat={n.get('category','?'):9s} dir={n['dir']}")
