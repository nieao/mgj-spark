# -*- coding: utf-8 -*-
"""模型价格表与费用计算 — $/MTok（内化自 Nieao-Token backend/pricing.py）
================================================================
来源：Anthropic 官方定价页（Nieao-Token 于 2026-07-03 调研核对，含 5 分钟/1 小时缓存写分价）。
猫管家用途：dispatcher 从 stream-json 拿到 usage 后，当 claude CLI 的 total_cost_usd
缺失/为 0 时用本表兜底估价（双轨核算），并显式标 estimated——不许把未知记 0 假装省钱。

费用公式（每聚合行）：
  cost = input·p_in + output·p_out + (cache_w − cache_w1h)·p_w5 + cache_w1h·p_w1h + cache_r·p_read  （均 /1e6）

匹配规则：model_id 按前缀表最长优先匹配；匹配不到用 _FALLBACK 档并标 estimated。
`<synthetic>` 是错误占位模型，不计价。
同步纪律：价目为静态手工表，需定期与 Nieao-Token 侧核对（若官方调价，两处一起改）。
"""
from datetime import date

# 前缀 → (input, output, cache_write_5m, cache_write_1h, cache_read)
# 注意顺序无关：匹配时按前缀长度降序，避免 opus-4-1 被 opus-4 误吞
_PRICES = {
    "claude-fable-5":            (10.0, 50.0, 12.50, 20.0, 1.00),
    "claude-mythos-5":           (10.0, 50.0, 12.50, 20.0, 1.00),
    "claude-opus-4-8":           (5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-7":           (5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-6":           (5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-5":           (5.0, 25.0, 6.25, 10.0, 0.50),
    "claude-opus-4-1":           (15.0, 75.0, 18.75, 30.0, 1.50),
    "claude-opus-4-2":           (15.0, 75.0, 18.75, 30.0, 1.50),   # claude-opus-4-20250514
    "claude-sonnet-5":           (2.0, 10.0, 2.50, 4.0, 0.20),      # 介绍价，2026-09-01 起切标准价
    "claude-sonnet-4-6":         (3.0, 15.0, 3.75, 6.0, 0.30),
    "claude-sonnet-4-5":         (3.0, 15.0, 3.75, 6.0, 0.30),
    "claude-sonnet-4-2":         (3.0, 15.0, 3.75, 6.0, 0.30),      # claude-sonnet-4-20250514
    "claude-3-7-sonnet":         (3.0, 15.0, 3.75, 6.0, 0.30),      # 已退役，fallback 同档
    "claude-3-5-sonnet":         (3.0, 15.0, 3.75, 6.0, 0.30),      # 已退役，fallback 同档
    "claude-haiku-4-5":          (1.0, 5.0, 1.25, 2.0, 0.10),
    "claude-3-5-haiku":          (0.80, 4.0, 1.00, 1.60, 0.08),
}

# sonnet-5 标准价（2026-09-01 起）
_SONNET5_STD = (3.0, 15.0, 3.75, 6.0, 0.30)
_SONNET5_SWITCH = date(2026, 9, 1)

# 未知模型 fallback（sonnet 档），结果标 estimated
_FALLBACK = (3.0, 15.0, 3.75, 6.0, 0.30)

# 按前缀长度降序缓存
_ORDERED = sorted(_PRICES, key=len, reverse=True)


def price_of(model, row_date=""):
    """model_id → ((in, out, w5, w1h, read), 是否精确匹配)"""
    if not model or model == "<synthetic>":
        return (0.0, 0.0, 0.0, 0.0, 0.0), True
    for prefix in _ORDERED:
        if model.startswith(prefix):
            p = _PRICES[prefix]
            if prefix == "claude-sonnet-5":
                # 缺省 date 取今日：切换日后即使调用方没传 date 也用标准价（否则低估 33%）；
                # 非法日期串保持旧行为（按介绍价，不猜）
                try:
                    d = date.fromisoformat(row_date) if row_date else date.today()
                    if d >= _SONNET5_SWITCH:
                        p = _SONNET5_STD
                except ValueError:
                    pass
            return p, True
    return _FALLBACK, False


def row_cost(row):
    """一条聚合行的费用（美元）。返回 (cost, exact)。
    row 需含 model/date/input/output/cache_w/cache_w1h/cache_r（缺省容忍为 0）。"""
    (p_in, p_out, p_w5, p_w1h, p_read), exact = price_of(row.get("model", ""), row.get("date", ""))
    w1h = row.get("cache_w1h", 0)
    w5 = max(0, row.get("cache_w", 0) - w1h)
    cost = (
        row.get("input", 0) * p_in
        + row.get("output", 0) * p_out
        + w5 * p_w5
        + w1h * p_w1h
        + row.get("cache_r", 0) * p_read
    ) / 1e6
    return cost, exact


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # 冒烟：几条已知输入
    for m, row in [
        ("claude-opus-4-8", {"model": "claude-opus-4-8", "input": 100000, "output": 5000}),
        ("claude-haiku-4-5", {"model": "claude-haiku-4-5", "input": 100000, "output": 5000}),
        ("unknown-x", {"model": "unknown-x", "input": 1000, "output": 1000}),
    ]:
        c, exact = row_cost(row)
        print(f"  {m:<24} ${c:.4f}  exact={exact}")
