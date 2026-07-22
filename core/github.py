# -*- coding: utf-8 -*-
"""
GitHub 极简助手（给 cli-spark 建 Issue 用）
==========================================
只做一件事：把「迭代方案」发成 GitHub Issue。纯 urllib，无第三方依赖。
Token 来源优先级：环境 GITHUB_TOKEN → config/credentials.json 的 github_token。
仓库：环境 GITHUB_REPO → credentials.json 的 github_repo → 默认 nieao/spark。
无 token 时不抛错，返回 {ok:False, reason:"no_token"}，由上层降级（只出规划、不建 issue）。
"""
import os
import json
import urllib.request

API = "https://api.github.com"
DEFAULT_REPO = "nieao/spark"
_CONF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def _creds():
    try:
        return json.load(open(os.path.join(_CONF_DIR, "credentials.json"), encoding="utf-8"))
    except Exception:
        return {}


def token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t.split("#", 1)[0].strip()
    return (_creds().get("github_token") or "").strip()


def repo():
    return (os.environ.get("GITHUB_REPO") or _creds().get("github_repo") or DEFAULT_REPO).strip()


def has_token():
    return bool(token())


def create_issue(title, body, labels=None):
    """建 Issue。返回 {ok, url, number} 或 {ok:False, reason/msg}。"""
    tok = token()
    if not tok:
        return {"ok": False, "reason": "no_token", "msg": "未配置 github_token（credentials.json 或 GITHUB_TOKEN）"}
    data = {"title": title[:250], "body": body}
    if labels:
        data["labels"] = labels
    req = urllib.request.Request(
        f"{API}/repos/{repo()}/issues",
        data=json.dumps(data).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "User-Agent": "cli-spark", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {"ok": True, "url": d.get("html_url"), "number": d.get("number")}
    except Exception as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:200]  # HTTPError body
        except Exception:
            pass
        return {"ok": False, "reason": "api_error", "msg": f"{type(e).__name__}: {e} {detail}"}
