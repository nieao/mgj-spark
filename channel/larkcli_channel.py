# -*- coding: utf-8 -*-
"""
lark-cli 飞书通道（node 长连接接收 · 给已装 @larksuite/cli 的机器）
==================================================================
发送与 HttpChannel 完全一致（飞书发送本就走 HTTP REST，无需 node）；
仅「接收」改用 node lark-cli 的 `event consume` 长连接——给 Windows 开发机或
已 `lark login` 好 bot 的环境用。跨平台探 node + run.js 路径；断线自动重启子进程。

切换：settings.feishu_channel = "larkcli"。
"""
import os
import json
import time
import subprocess
import threading

from channel.http_channel import HttpChannel
from channel.base import IncomingMessage


def _find_node():
    for c in ("node", os.path.join(os.environ.get("APPDATA", ""), "npm", "node.exe")):
        if c == "node" or os.path.exists(c):
            return c
    return "node"


def _find_runjs():
    """跨平台探 @larksuite/cli 的 run.js。"""
    cands = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(os.path.join(appdata, "npm", "node_modules", "@larksuite", "cli", "scripts", "run.js"))
    # Linux/mac 全局 npm 常见位置
    for base in ("/usr/lib/node_modules", "/usr/local/lib/node_modules",
                 os.path.expanduser("~/.npm-global/lib/node_modules"),
                 os.path.expanduser("~/node_modules")):
        cands.append(os.path.join(base, "@larksuite", "cli", "scripts", "run.js"))
    env = os.environ.get("LARK_RUNJS")
    if env:
        cands.insert(0, env)
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


class LarkCliChannel(HttpChannel):
    def __init__(self, creds):
        super().__init__(creds)
        self.profile = creds.get("lark_profile", "default")
        self.node = _find_node()
        self.runjs = _find_runjs()

    def selftest(self):
        ok, detail = super().selftest()   # 复用 HTTP 的 token 自检
        if not ok:
            return ok, detail
        if not self.runjs:
            return False, f"{detail}；但未找到 lark-cli run.js（装 @larksuite/cli 或设 LARK_RUNJS）"
        return True, f"{detail}；lark-cli run.js: {self.runjs}"

    def start_consume(self, on_message):
        if not self.runjs:
            raise RuntimeError("未找到 lark-cli run.js —— `npm i -g @larksuite/cli` 并 `lark login`，"
                               "或设环境变量 LARK_RUNJS 指向 run.js；或切回 http 通道。")
        event_key = "im.message.receive_v1"
        backoff = 3
        while True:
            print(f"[larkcli] 启动 event consume 子进程 key={event_key}", flush=True)
            try:
                proc = subprocess.Popen(
                    [self.node, self.runjs, "event", "consume", event_key,
                     "--as", "bot", "--profile", self.profile],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                print(f"[larkcli] 启动失败 {e}，{backoff}s 后重试", flush=True)
                time.sleep(backoff); backoff = min(backoff * 2, 60); continue

            def _drain(p):
                try:
                    for ln in p.stderr:
                        ln = ln.strip()
                        if ln:
                            print(f"[larkcli:stderr] {ln}", flush=True)
                except Exception:
                    pass
            threading.Thread(target=_drain, args=(proc,), daemon=True).start()

            backoff = 3
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(obj, on_message)
            except Exception as e:
                print(f"[larkcli] 读取 consume 输出异常: {e}", flush=True)
            finally:
                rc = proc.poll()
                print(f"[larkcli] consume 退出 rc={rc}，{backoff}s 后重启", flush=True)
                time.sleep(backoff); backoff = min(backoff * 2, 60)

    def _dispatch(self, obj, on_message):
        """把 lark-cli event JSON 归一化成 IncomingMessage。"""
        try:
            ev = obj.get("event") or obj
            msg = ev.get("message") or {}
            sender = ev.get("sender") or {}
            open_id = ((sender.get("sender_id") or {}).get("open_id")
                       or sender.get("open_id") or "")
            message_id = msg.get("message_id") or ""
            mtype = msg.get("message_type") or msg.get("msg_type") or "text"
            text = ""
            if mtype == "text":
                try:
                    text = (json.loads(msg.get("content") or "{}") or {}).get("text", "")
                except Exception:
                    text = msg.get("content") or ""
            if message_id:
                on_message(IncomingMessage(open_id, text, message_id, raw=obj, msg_type=mtype))
        except Exception as e:
            print(f"[larkcli] 归一化事件失败: {e}", flush=True)
