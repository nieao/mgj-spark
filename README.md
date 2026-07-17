# mgj-spark · 猫管家 Spark 精简版

跑在 **NVIDIA DGX Spark**（ARM64/GB10，Ubuntu 24.04）上的精简飞书总控，专管这台机器上
**已部署一半的 [AIGC-spark](../AIGC-spark) 生图工作台 + aeon-spark ComfyUI**。

从 [猫管家](../猫管家) 提炼而来，**保留安全内核**（审批门 T0–T3 + Token 发证 + 分层记忆 + 台账），
**砍掉 Windows 强绑定**（.bat/.vbs/PowerShell、写死的 lark-cli 路径），把意图分类升级为**插件**，
飞书通道做成**可切换**（纯 HTTP / lark-cli）。

## 它能干什么

在飞书里对 bot 说：

| 你说 | 谁接 | 结果 |
|---|---|---|
| 「spark 状态 / 生图好了吗 / ComfyUI 在线吗 / 部署到哪一步了」 | `spark_ops` 插件（只读 T0） | 一张运维态卡：ComfyUI/生图工作台/spark-keeper 进度/GPU |
| 「把配置部署到服务器」等系统级动作 | 审批门 | 先发 T2 审批卡，回「批准」才跑 |
| 其它任意问题 | `general_qa` 插件 | 大模型单发作答（默认 **StepFun step-3.7-flash**） |
| 批准 / 取消 / 状态 / 帮助 | 控制命令 | 审批放行 / 台账 / 帮助 |

## 快速开始（本地或 Spark 通用）

```bash
pip install -r requirements.txt
python setup.py                 # 自检 + 从模板生成 config/credentials.json
# 填 config/credentials.json：飞书 app_id/app_secret/user_open_id + stepfun_api_key
python bridge.py --selftest     # 全绿即就绪
python bridge.py --once "spark 状态"   # 本地直跑一次（不经飞书）
./start.sh                      # 正式启动，消费飞书消息
```

Spark 生产守护（systemd）见 **[DEPLOY-SPARK.md](DEPLOY-SPARK.md)**。

## 架构

```
bridge.py                    编排：去重→本人校验→控制命令→硬阻断→插件路由→定档→审批门→发证→执行→台账
core/                        安全内核（原样复用自猫管家，已验证可移植）
  registry.py                注册表加载（真相源 config/registry.json，热重载）
  broker.py                  Token Broker（发证 + 工具白名单 + CIAA 审计，落盘 _state/tokens.json）
  approval.py                审批门（T2/T3 不点不跑，30 分钟过期）
  intent.py                  定档工具（意图→T0-T3 + 灾难级硬阻断）——供插件复用
  memory_store.py            L1/L2/L3 分层记忆（scope 强制隔离 + 派发注入）
  providers.py               多 provider（+ StepFun 默认；OpenAI 兼容 HTTP，不依赖 claude CLI）
  ledger.py / pricing.py     台账 + 成本估算
channel/                     飞书通道抽象（可切）
  http_channel.py            纯 Python：tenant_token 发送 + lark-oapi ws 接收（Spark 默认）
  larkcli_channel.py         node lark-cli 接收（给已装 @larksuite/cli 的机器）
plugins/                     能力插件（未来多接一个项目 = 多写一个文件）
  base.py                    插件基座 + 自动发现 + 路由（can_handle 最高分者处理）
  spark_ops.py               ★ 管 AIGC-spark/ComfyUI/spark-keeper/GPU 运维态（只读）
  general_qa.py              兜底：intent 定档 + StepFun 单发 + 记忆注入
config/                      registry.json（真相源）+ credentials.json（gitignore）
systemd/mgj-spark.service    生产守护单元
```

## 设计要点

- **飞书发送本就走 HTTP**（tenant_access_token + REST），Node 只曾用于「接收」——所以 http 通道
  用 lark-oapi 的 ws 长连接收消息，彻底去 Node，最适合无桌面 ARM 服务器。
- **默认大模型 = StepFun**（阶跃星辰，OpenAI 兼容），不依赖 claude CLI，云端直连。
  配了别的 key 可在飞书里说「用 deepseek / 用 glm…」临时切。
- **插件化路由**：`spark_ops` 只接「观测查询」、绝不抢「部署/生成」这类动作请求（动作走 general_qa
  由 intent 定档，该 T2 就走审批门），避免只读插件绕过安全门。
