# -*- coding: utf-8 -*-
"""
飞书通道抽象（mgj-spark · 把收发飞书从「写死 lark-cli」升级为可切换后端）
========================================================================
猫管家原版收发全绑 Windows 上的 node lark-cli（%APPDATA%/npm/.../run.js + 隐窗）。
mgj-spark 抽象出统一接口，两种后端按配置切：
  · http    —— 纯 Python：发送走 tenant_access_token + REST；接收走 lark-oapi 的 ws 长连接。
              零 Node 依赖，最适合无桌面 ARM 服务器（Spark）。
  · larkcli —— 包装 node lark-cli（跨平台探路径），给已装 lark-cli 的机器（如 Windows 开发机）。

统一接口（子类实现）：
  send_text(open_id, text)
  send_card(open_id, title, body_md, template, urls, actions)
  start_consume(on_message)   —— 阻塞/起线程消费 im.message.receive_v1，回调 IncomingMessage
配置来源：config/credentials.json（app_id/app_secret/user_open_id）+ settings.feishu_channel。
"""


class IncomingMessage:
    """收到的一条飞书消息（后端已归一化）。"""
    def __init__(self, open_id, text, message_id, raw=None, msg_type="text"):
        self.open_id = open_id          # 发送者 open_id
        self.text = text                # 纯文本内容
        self.message_id = message_id    # 去重用
        self.raw = raw or {}
        self.msg_type = msg_type


class FeishuChannel:
    """通道基类。子类实现 send_text / send_card / start_consume。"""
    def __init__(self, creds):
        self.app_id = creds.get("app_id", "")
        self.app_secret = creds.get("app_secret", "")
        self.user_open_id = creds.get("user_open_id", "")
        self.creds = creds

    def send_text(self, open_id, text):
        raise NotImplementedError

    def send_card(self, open_id, title, body_md, template="blue", urls=None, actions=None,
                  image_paths=None):
        raise NotImplementedError

    def send_image(self, open_id, image_path):
        raise NotImplementedError

    def start_consume(self, on_message):
        """阻塞消费消息事件；每条消息回调 on_message(IncomingMessage)。"""
        raise NotImplementedError

    def selftest(self):
        """返回 (ok, detail) —— 不真连飞书，只查凭证/后端就绪。子类可覆盖。"""
        if not (self.app_id and self.app_secret and self.user_open_id):
            return False, "credentials.json 缺 app_id/app_secret/user_open_id"
        return True, "凭证齐备"


def make_channel(mode, creds):
    """工厂：按 mode 造通道。mode ∈ {'http','larkcli'}，默认 http。"""
    mode = (mode or "http").strip().lower()
    if mode == "larkcli":
        from channel.larkcli_channel import LarkCliChannel
        return LarkCliChannel(creds)
    from channel.http_channel import HttpChannel
    return HttpChannel(creds)
