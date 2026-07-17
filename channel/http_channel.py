# -*- coding: utf-8 -*-
"""
HTTP 飞书通道（纯 Python，零 Node 依赖 · Spark 默认）
=====================================================
· 发送：tenant_access_token（内存缓存，过期自动刷）+ REST /open-apis/im/v1/messages。
        文本长自动分段；卡片走 interactive，格式对齐猫管家原版。
· 接收：lark-oapi 的 ws 长连接（`pip install lark-oapi`），订阅 im.message.receive_v1。
        长连接无需公网回调，最适合无桌面服务器。ws 依赖缺失时给出清晰安装提示，
        不影响发送与 --once（发送零 SDK 依赖）。
"""
import json
import time
import threading

import requests

from channel.base import FeishuChannel, IncomingMessage

FEISHU_BASE = "https://open.feishu.cn"
REPLY_CHUNK = 3500          # 单条文本上限（飞书约 4KB，留余量）


class HttpChannel(FeishuChannel):
    def __init__(self, creds):
        super().__init__(creds)
        self._tok = {"val": "", "exp": 0}
        self._tok_lock = threading.Lock()

    # ---------- token ----------
    def _tenant_token(self):
        with self._tok_lock:
            if self._tok["val"] and time.time() < self._tok["exp"] - 60:
                return self._tok["val"]
            r = requests.post(
                f"{FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=15)
            d = r.json()
            if d.get("code") != 0:
                raise RuntimeError(f"取 tenant_access_token 失败: {d.get('code')} {d.get('msg')}")
            self._tok["val"] = d["tenant_access_token"]
            self._tok["exp"] = time.time() + int(d.get("expire", 7200))
            return self._tok["val"]

    # ---------- 图片上传（发图前置：拿 image_key）----------
    def upload_image(self, local_path):
        """把本地图上传到飞书，返回 image_key（供 image 消息或卡片 img 元素用）。
        走 POST /open-apis/im/v1/images（multipart, image_type=message）。
        需 bot 有 im:resource:upload 权限。失败返回 ('', 错误串)，绝不抛。"""
        import os
        if not (local_path and os.path.isfile(local_path)):
            return "", f"文件不存在:{local_path}"
        try:
            tok = self._tenant_token()
            with open(local_path, "rb") as f:
                r = requests.post(
                    f"{FEISHU_BASE}/open-apis/im/v1/images",
                    headers={"Authorization": f"Bearer {tok}"},
                    data={"image_type": "message"},
                    files={"image": (os.path.basename(local_path), f, "image/png")},
                    timeout=60)
            d = r.json()
            if d.get("code") != 0:
                return "", f"上传失败 code={d.get('code')} msg={str(d.get('msg'))[:120]}"
            return (d.get("data") or {}).get("image_key", ""), ""
        except Exception as e:
            return "", f"{type(e).__name__}:{e}"

    def send_image(self, open_id, image_path):
        """直接发一条图片消息（备用；主链路走 send_card 内嵌图）。"""
        key, err = self.upload_image(image_path)
        if not key:
            return self.send_text(open_id, f"⚠ 出图成功但发图失败：{err}\n本地文件：{image_path}")
        j = self._post_message(open_id, "image", {"image_key": key})
        if j.get("code") != 0:
            print(f"[http] send_image 失败: code={j.get('code')} msg={str(j.get('msg'))[:200]}", flush=True)
            return False
        return True

    # ---------- 发送 ----------
    def _post_message(self, open_id, msg_type, content_obj):
        tok = self._tenant_token()
        r = requests.post(
            f"{FEISHU_BASE}/open-apis/im/v1/messages?receive_id_type=open_id",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=utf-8"},
            json={"receive_id": open_id, "msg_type": msg_type,
                  "content": json.dumps(content_obj, ensure_ascii=False)},
            timeout=20)
        try:
            return r.json()
        except Exception:
            return {"code": -1, "msg": f"http {r.status_code}"}

    def send_text(self, open_id, text):
        if not text:
            text = "(空结果)"
        chunks = [text[i:i + REPLY_CHUNK] for i in range(0, len(text), REPLY_CHUNK)] or ["(空)"]
        ok = True
        for idx, ch in enumerate(chunks):
            prefix = f"[{idx+1}/{len(chunks)}] " if len(chunks) > 1 else ""
            j = self._post_message(open_id, "text", {"text": prefix + ch})
            if j.get("code") != 0:
                ok = False
                print(f"[http] send_text 失败: code={j.get('code')} msg={str(j.get('msg'))[:200]}", flush=True)
        return ok

    def _build_card(self, title, body_md, template, urls, actions, img_keys=None):
        elements = []
        if body_md:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body_md[:1800]}})
        for k in (img_keys or []):
            if k:
                elements.append({"tag": "img", "img_key": k, "mode": "fit_horizontal",
                                 "alt": {"tag": "plain_text", "content": "生成图"}})
        buttons = []
        urls = urls or []
        for i, u in enumerate(urls[:6]):
            if isinstance(u, dict):
                label, link = u.get("text", "🔗 打开"), u.get("url", "")
            else:
                label, link = ("🔗 打开链接" if len(urls) == 1 else f"🔗 链接 {i+1}"), u
            buttons.append({"tag": "button", "text": {"tag": "plain_text", "content": label},
                            "url": link, "type": "primary" if i == 0 else "default"})
        if actions:
            buttons.extend(actions)
        if buttons:
            elements.append({"tag": "hr"})
            elements.append({"tag": "action", "actions": buttons})
        return {"config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
                "elements": elements}

    def send_card(self, open_id, title, body_md, template="blue", urls=None, actions=None,
                  image_paths=None):
        """发交互卡片。image_paths 非空时先上传每张图拿 img_key 内嵌进卡片；
        上传失败的图降级为 body 里的一行提示（不影响卡片主体）。"""
        img_keys, fails = [], []
        for p in (image_paths or []):
            k, err = self.upload_image(p)
            if k:
                img_keys.append(k)
            else:
                fails.append(f"（图上传失败：{err}）")
        if fails:
            body_md = (body_md or "") + "\n" + "\n".join(fails)
        j = self._post_message(open_id, "interactive",
                               self._build_card(title, body_md, template, urls, actions, img_keys))
        if j.get("code") != 0:
            print(f"[http] send_card 失败: code={j.get('code')} msg={str(j.get('msg'))[:200]}", flush=True)
            return False
        return True

    # ---------- 接收（lark-oapi ws 长连接）----------
    def start_consume(self, on_message):
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        except Exception:
            raise RuntimeError(
                "HTTP 通道的接收依赖 lark-oapi（长连接），请先 `pip install lark-oapi`；"
                "或把 settings.feishu_channel 切成 'larkcli' 用 node 通道。")

        def _on_msg(data):
            try:
                ev = data.event
                msg = ev.message
                open_id = ev.sender.sender_id.open_id
                message_id = msg.message_id
                mtype = msg.message_type
                text = ""
                if mtype == "text":
                    try:
                        text = (json.loads(msg.content) or {}).get("text", "")
                    except Exception:
                        text = msg.content or ""
                on_message(IncomingMessage(open_id, text, message_id,
                                           raw={"event": "im.message.receive_v1"}, msg_type=mtype))
            except Exception as e:
                print(f"[http] 消息处理异常: {type(e).__name__}: {e}", flush=True)

        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(_on_msg)
                   .build())
        cli = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler,
                             log_level=lark.LogLevel.WARNING)
        print("[http] lark-oapi ws 长连接启动，订阅 im.message.receive_v1", flush=True)
        cli.start()   # 阻塞，内部自带断线重连

    def selftest(self):
        ok, detail = super().selftest()
        if not ok:
            return ok, detail
        # 试取一次 token 验证 app_id/secret 真实可用
        try:
            self._tenant_token()
        except Exception as e:
            return False, f"取 token 失败（app_id/secret 有误或网络不通）: {e}"
        try:
            import lark_oapi  # noqa: F401
            ws = "lark-oapi 已装（接收就绪）"
        except Exception:
            ws = "⚠ 未装 lark-oapi（发送可用，但收不到消息，需 pip install lark-oapi）"
        return True, f"token OK；{ws}"
