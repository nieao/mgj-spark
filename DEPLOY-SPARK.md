# mgj-spark 部署到 DGX Spark

> 目标机：NVIDIA DGX Spark（aarch64 / GB10 / Ubuntu 24.04）
> SSH：`nvdspk02@73.237.141.115:2227`（据 AIGC-spark/spark-keeper/STATUS.md，sudo 密码=登录密码）
> 稳定通道：Python paramiko / scp（Windows Git-Bash 的 ssh 偶发抖动，重要传输走 paramiko）

## 一、把代码送上 Spark

本机（Windows）打包上传（排除运行时/密钥/venv）：

```bash
cd "E:/claude code"
# 用 scp（端口 2227）。credentials.json 含密钥，单独手动传，别进 rsync 公共流。
scp -P 2227 -r mgj-spark nvdspk02@73.237.141.115:~/mgj-spark
```

> 若 scp 抖，用 AIGC-spark/spark-keeper 里的 paramiko 通道同法传。
> `_state/`、`.venv/`、`config/credentials.json` 不要传（前两个是运行时，凭证在 Spark 上单独填）。

## 二、Spark 上安装与配置

```bash
ssh -p 2227 nvdspk02@73.237.141.115
cd ~/mgj-spark
sed -i 's/\r$//' *.sh                      # 防 Windows CRLF
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.spark.example .env                 # 端点默认 localhost:8188/8265，通常无需改
python setup.py                            # 生成 config/credentials.json
```

编辑 `config/credentials.json`：
- 飞书三件套 `app_id / app_secret / user_open_id`（与你现有猫管家/共享 bot 同一套，
  见猫管家 `config/credentials.json`；bot 事件订阅需选**长连接 WebSocket** + 订阅 `im.message.receive_v1`）。
- `stepfun_api_key`：阶跃星辰 platform.stepfun.com 的 apikey（默认大模型 `step-3.7-flash`）。

## 三、验证

```bash
./.venv/bin/python bridge.py --selftest
```
应看到：飞书通道 token OK、插件 2 个、可用 provider 含 stepfun、默认解析 `stepfun:step-3.7-flash`、
Spark 运维态里 **ComfyUI/生图工作台**在你已拉起后转 🟢，GPU 显示 **GB10**。

```bash
./.venv/bin/python bridge.py --once "spark 状态"     # 本地直跑，看运维态卡
```

## 四、生产守护（systemd）

```bash
# 改单元里的 User/路径为你的真实值后：
sudo cp systemd/mgj-spark.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mgj-spark
journalctl -u mgj-spark -f                  # 看日志
```
ws 长连接偶断由通道内部自愈；进程崩由 systemd 兜底重启。

## 五、与 AIGC-spark 的关系

mgj-spark **只观测不接管** AIGC-spark：读 `COMFY_URL`(8188)/`AIGC_URL`(8265) 健康端点 +
`~/spark-deploy/status.json`(spark-keeper 部署进度) + `nvidia-smi`。真正部署/出图仍走 AIGC-spark
自己的 `start.sh` 与 aeon-spark ComfyUI。二者可同机共存，端口不冲突（mgj-spark 不占 web 端口，
只作飞书长连接消费者）。

## 六、飞书通道二选一

`config/registry.json` 的 `settings.feishu_channel`：
- `http`（默认）：纯 Python，`pip install lark-oapi` 即可长连接收消息，**无需 Node**。推荐。
- `larkcli`：若 Spark 上已装 `@larksuite/cli` 并 `lark login` 好 bot，可切此模式用 node 接收。

## 七、注意

- 与共享同一飞书 app 的其它桥（猫管家/猫猫中台）**绝不能同时消费**同一事件流——同一时间只跑一个消费者。
  mgj-spark 有单实例文件锁（`_state/bridge.lock`），但跨机/跨 app 需你自己保证只有一个在收。
- 只响应 `user_open_id` 本人消息；T2/T3 动作必须飞书点「批准」才跑；灾难级请求（rm -rf /、外发凭证等）直接拒绝受理。
