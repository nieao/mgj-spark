# mgj-spark · Cloudflare 隧道外网访问

把 DGX Spark 上的三个本地服务，通过一条 Cloudflare 隧道 + 一层 Basic Auth 反代，
安全暴露到公网三个子域名。**只出站、免路由器端口映射、自动 TLS、带登录门。**

> 凭证（用户名/密码）不在本文件，见 gitignore 的 `_state/tunnel-access.txt`。

## 链路

```
外部浏览器  https://comfy.nieao.eu.cc
   │  ← 弹 Basic Auth 登录框（用户名 nieao + 密码）
   ▼
Cloudflare 边缘（自动 TLS）
   │  加密隧道（Spark 只出站，路由器零端口映射）
   ▼
Spark: cloudflared ──► Caddy 反代 :9080（Basic Auth）──┬─► 127.0.0.1:8188  ComfyUI
                                                        ├─► 127.0.0.1:8265  AIGC 工作台
                                                        └─► 127.0.0.1:8250  mgj 面板(待建)
```

三个子域名（CNAME 已在 Cloudflare 自动创建，指向隧道）：

| 公网地址 | 后端 | 状态 |
|---|---|---|
| `https://comfy.nieao.eu.cc` | ComfyUI 8188 | ✅ 通 |
| `https://aigc.nieao.eu.cc` | AIGC 工作台 8265 | ✅ 通 |
| `https://spark.nieao.eu.cc` | mgj 面板 8250 | 门已加，后端待开发（暂 502） |

## Spark 上的两个 systemd 服务（都开机自启 + 崩溃自愈）

| 服务 | 作用 | 配置文件 |
|---|---|---|
| `mgj-cloudflared` | Cloudflare 隧道 | `~/.cloudflared/config.yml`（ingress 全指向 `127.0.0.1:9080`） |
| `mgj-caddy` | Basic Auth 反代 | `~/mgj-spark/Caddyfile`（`:9080`，按 Host 路由到 8188/8265/8250） |

常用命令（在 Spark 上）：
```bash
sudo systemctl status  mgj-cloudflared mgj-caddy
sudo systemctl restart mgj-cloudflared      # 改了隧道 config 后
sudo systemctl restart mgj-caddy            # 改了 Caddyfile 后
journalctl -u mgj-cloudflared -f            # 看隧道日志
cloudflared tunnel info mgj-spark           # 看隧道边缘连接
```

## 改密码

Basic Auth 密码在 `~/mgj-spark/Caddyfile` 里是 bcrypt 哈希。换密码：
```bash
caddy hash-password --plaintext '新密码'     # 拷贝输出的 $2a$... 哈希
# 编辑 ~/mgj-spark/Caddyfile 的 basic_auth 段，把 nieao 后面的哈希替换掉
sudo systemctl restart mgj-caddy
```
改完同步更新本机 `_state/tunnel-access.txt` 记一笔。

## 加/改一个子域名

1. `~/.cloudflared/config.yml` 的 ingress 加一条 `hostname` → `http://127.0.0.1:9080`
2. `~/mgj-spark/Caddyfile` 加一段 `@x host x.nieao.eu.cc` + `reverse_proxy @x 127.0.0.1:端口`
3. `cloudflared tunnel route dns mgj-spark x.nieao.eu.cc`（建 CNAME）
4. `sudo systemctl restart mgj-cloudflared mgj-caddy`

## 关于 mgj-spark 面板（spark.nieao.eu.cc 8250）

`config/registry.json` 里预留了 `board_port: 8250`，但看板服务（`observe/board.py`）尚未开发。
建好后监听 `127.0.0.1:8250`，`spark.nieao.eu.cc` 自动从 502 转通，无需再动隧道/反代。

## 想升级成邮箱验证码登录（Cloudflare Access）

当前是 Basic Auth（账号密码）。若要换成 Cloudflare Access 邮箱 OTP：
需先开通 Cloudflare Zero Trust（起 team 域名 + 选免费套餐 + 绑卡，免费版不扣费），
再对 `*.nieao.eu.cc` 建 Self-hosted 应用 + Allow 邮箱策略，然后可撤掉 Caddy 这层。
