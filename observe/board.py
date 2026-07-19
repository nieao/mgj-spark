# -*- coding: utf-8 -*-
"""
mgj-spark 观测看板（board_port，默认 127.0.0.1:8250）
=====================================================
纯 Python 标准库（零新依赖，Spark 上免 pip）。对外经 Caddy Basic Auth + Cloudflare
隧道暴露为 https://spark.nieao.eu.cc。黑金多宝阁风，15s 自刷，全接真实数据：

  · 运维态   plugins.spark_ops.collect_all()  —— ComfyUI/AIGC工作台/spark-keeper/GPU
  · 台账     core.ledger.recent()/summary()   —— 任务流水 + token/成本汇总
  · 节点     core.registry.all_nodes()         —— 注册表
  · 发证/审批 _state/tokens.json / pending_approval.json —— 活跃权限证 + 待审队列

只读观测，绝不改任何东西。路由：GET / (HTML) · GET /api/state (JSON)。
启动：python -m observe.board   或   python observe/board.py
"""
import os
import sys
import json
import time
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STATE_DIR = os.path.join(ROOT, "_state")


# ---------------- 数据聚合（每块独立 try，一处坏不拖垮整页）----------------
def _ops():
    try:
        from plugins.spark_ops import collect_all
        return collect_all()
    except Exception as e:
        return [{"name": "运维态采集失败", "online": None, "detail": f"{type(e).__name__}: {e}"}]


def _ledger():
    try:
        from core import ledger
        return {"recent": ledger.recent(30), "summary": ledger.summary()}
    except Exception as e:
        return {"recent": [], "summary": {"total": {}, "by_node": {}, "by_model": {}},
                "error": f"{type(e).__name__}: {e}"}


def _nodes():
    try:
        from core import registry
        return {"nodes": registry.all_nodes(), "settings": registry.settings()}
    except Exception as e:
        return {"nodes": [], "settings": {}, "error": f"{type(e).__name__}: {e}"}


def _tokens():
    """活跃权限证（未过期）。直接读落盘的 tokens.json。"""
    try:
        with open(os.path.join(STATE_DIR, "tokens.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    now = time.time()
    out = []
    for rec in d.values():
        exp = rec.get("expire_at", 0)
        if exp > now:
            out.append({"node": rec.get("node"), "tier": rec.get("tier"),
                        "task": (rec.get("task") or "")[:60],
                        "left_s": int(exp - now)})
    return sorted(out, key=lambda x: x["left_s"])


def _pending():
    """待审队列（30 分钟内未过期）。"""
    try:
        with open(os.path.join(STATE_DIR, "pending_approval.json"), "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return []
    now = time.time()
    out = []
    for it in items:
        ts = it.get("ts", 0)
        if now - ts <= 1800:
            out.append({"pid": it.get("pid"), "text": (it.get("text") or "")[:80],
                        "tier": it.get("tier"), "intent": it.get("intent"),
                        "nodes": it.get("nodes"), "left_s": int(1800 - (now - ts))})
    return out


def build_state():
    led = _ledger()
    nod = _nodes()
    return {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "ops": _ops(),
        "ledger": led,
        "nodes": nod.get("nodes", []),
        "settings": nod.get("settings", {}),
        "tokens_active": _tokens(),
        "pending": _pending(),
    }


# ---------------- 页面（黑金多宝阁 · charset 第一标签）----------------
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mgj-spark 观测看板</title>
<style>
:root{
  --bg-void:#0a0a0d;--bg-panel:#14131a;--bg-panel-2:#1c1a24;--bg-elevated:#242029;
  --gold-primary:#d4af37;--gold-bright:#f4d98b;--gold-deep:#a88422;
  --gold-line:rgba(212,175,55,.35);--border-gold:rgba(212,175,55,.22);
  --text-primary:#ede8dc;--text-muted:#9a927f;--text-dim:#6b6555;
  --danger:#c0563a;--ok:#8fae6b;
  --radius:4px;--glow:0 0 20px rgba(212,175,55,.12);
  --font-serif:"Noto Serif SC",serif;--font-mono:"Space Mono","SFMono-Regular",Consolas,monospace;
  --font-sans:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg-void);color:var(--text-primary);font-family:var(--font-sans);
  line-height:1.8;padding:28px 20px 80px;min-height:100vh;
  background-image:radial-gradient(ellipse at 20% -10%,rgba(212,175,55,.06),transparent 55%);}
.wrap{max-width:1180px;margin:0 auto}
header{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:12px;
  border-bottom:1px solid var(--border-gold);padding-bottom:18px;margin-bottom:26px}
.brand{font-family:var(--font-serif);font-size:30px;font-weight:600;
  background:linear-gradient(135deg,#f4d98b,#d4af37,#a88422);-webkit-background-clip:text;
  background-clip:text;color:transparent}
.brand small{display:block;font-family:var(--font-mono);font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--text-muted);-webkit-text-fill-color:var(--text-muted);margin-top:4px}
.meta{font-family:var(--font-mono);font-size:12px;color:var(--text-muted);text-align:right}
.meta b{color:var(--gold-primary)}
.grid{display:grid;gap:16px}
.cols-4{grid-template-columns:repeat(4,1fr)}
.cols-2{grid-template-columns:1fr 1fr}
.panel{background:var(--bg-panel);border:1px solid var(--border-gold);border-radius:var(--radius);
  padding:18px 20px;box-shadow:var(--glow)}
.panel h2{font-family:var(--font-mono);font-size:11px;letter-spacing:.15em;text-transform:uppercase;
  color:var(--gold-primary);margin-bottom:14px;display:flex;justify-content:space-between}
.panel h2 span{color:var(--text-dim)}
.section-title{font-family:var(--font-serif);font-size:17px;color:var(--gold-bright);
  margin:30px 0 14px;display:flex;align-items:center;gap:10px}
.section-title::before{content:"";width:14px;height:1px;background:var(--gold-line)}
.stat{font-family:var(--font-mono)}
.stat .num{font-size:34px;color:var(--gold-primary);line-height:1.2}
.stat .lbl{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.1em}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px;vertical-align:middle}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.off{background:var(--danger);box-shadow:0 0 8px var(--danger)}
.dot.na{background:var(--text-dim)}
.ops-name{font-weight:600;font-size:14px}
.ops-detail{font-family:var(--font-mono);font-size:12px;color:var(--text-muted);margin-top:4px;word-break:break-all}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{font-family:var(--font-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--gold-deep);text-align:left;padding:8px 10px;border-bottom:1px solid var(--border-gold)}
td{padding:8px 10px;border-bottom:1px solid rgba(212,175,55,.08);color:var(--text-primary);vertical-align:top}
td.mono,.mono{font-family:var(--font-mono)}
.tier{display:inline-block;padding:1px 7px;border-radius:3px;font-family:var(--font-mono);font-size:11px;
  border:1px solid var(--border-gold);color:var(--gold-primary)}
.tier.t2,.tier.t3{border-color:var(--danger);color:var(--danger)}
.ok-y{color:var(--ok)}.ok-n{color:var(--danger)}
.pill{display:inline-block;padding:1px 8px;border:1px solid var(--border-gold);border-radius:99px;
  font-family:var(--font-mono);font-size:11px;color:var(--text-muted);margin:2px 4px 2px 0}
.empty{color:var(--text-dim);font-style:italic;font-size:12.5px;padding:6px 0}
.chip{font-family:var(--font-mono);font-size:11.5px;color:var(--text-muted)}
.chip b{color:var(--gold-primary)}
footer{margin-top:40px;text-align:center;font-family:var(--font-mono);font-size:11px;color:var(--text-dim)}
.err{color:var(--danger);font-family:var(--font-mono);font-size:11.5px}
@media(max-width:820px){.cols-4{grid-template-columns:1fr 1fr}.cols-2{grid-template-columns:1fr}
  .brand{font-size:24px}table{font-size:11.5px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">mgj-spark 观测看板<small>DGX Spark · Feishu 总控 · 只读遥测</small></div>
    <div class="meta">刷新于 <b id="ts">—</b><br>下次自刷 <span id="cd">15</span>s · <span id="err" class="err"></span></div>
  </header>

  <div class="section-title">运维态</div>
  <div class="grid cols-4" id="ops"></div>

  <div class="section-title">台账汇总</div>
  <div class="grid cols-4" id="sum"></div>

  <div class="grid cols-2" style="margin-top:16px">
    <div class="panel"><h2>按节点 <span id="bn-c"></span></h2><div id="bynode"></div></div>
    <div class="panel"><h2>按模型 <span id="bm-c"></span></h2><div id="bymodel"></div></div>
  </div>

  <div class="section-title">最近任务</div>
  <div class="panel"><div id="recent"></div></div>

  <div class="grid cols-2" style="margin-top:16px">
    <div class="panel"><h2>活跃权限证 <span id="tk-c"></span></h2><div id="tokens"></div></div>
    <div class="panel"><h2>待审队列 <span id="pd-c"></span></h2><div id="pending"></div></div>
  </div>

  <div class="section-title">注册节点</div>
  <div class="panel"><div id="nodes"></div></div>

  <footer>mgj-spark · observe/board.py · 数据每 15 秒自动刷新</footer>
</div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dotCls=o=>o===true?'on':(o===false?'off':'na');
const fmt=n=>(n==null?'—':Number(n).toLocaleString());
const money=n=>'$'+(Number(n||0)).toFixed(4);
function left(s){if(s==null)return'';const m=Math.floor(s/60),ss=s%60;return m+'m'+String(ss).padStart(2,'0')+'s';}

function render(d){
  $('ts').textContent=d.ts||'—';
  $('err').textContent=(d.ledger&&d.ledger.error)?('台账:'+d.ledger.error):'';
  // 运维态
  $('ops').innerHTML=(d.ops||[]).map(r=>`<div class="panel">
    <div class="ops-name"><span class="dot ${dotCls(r.online)}"></span>${esc(r.name)}</div>
    <div class="ops-detail">${esc(r.detail||'')}</div></div>`).join('');
  // 台账汇总
  const t=(d.ledger&&d.ledger.summary&&d.ledger.summary.total)||{};
  const cards=[['任务总数',fmt(t.tasks)],['Tokens 入',fmt(t.tokens_in)],
    ['Tokens 出',fmt(t.tokens_out)],['累计成本',money(t.cost_usd)]];
  $('sum').innerHTML=cards.map(c=>`<div class="panel stat"><div class="num">${c[1]}</div>
    <div class="lbl">${c[0]}</div></div>`).join('')
    +(t.fail?`<div class="panel stat" style="grid-column:1/-1"><span class="ok-n mono">失败 ${t.fail}</span> · <span class="chip">成本未知条目 ${fmt(t.cost_unknown)}</span></div>`:'');
  // 按节点/模型
  const bn=(d.ledger.summary&&d.ledger.summary.by_node)||{};
  const bm=(d.ledger.summary&&d.ledger.summary.by_model)||{};
  $('bn-c').textContent=Object.keys(bn).length;
  $('bm-c').textContent=Object.keys(bm).length;
  $('bynode').innerHTML=Object.keys(bn).length?Object.entries(bn).sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<span class="pill">${esc(k)} · ${v}</span>`).join(''):'<div class="empty">暂无</div>';
  $('bymodel').innerHTML=Object.keys(bm).length?`<table><tr><th>模型</th><th>任务</th><th>成本</th></tr>`
    +Object.entries(bm).sort((a,b)=>b[1].tasks-a[1].tasks).map(([k,v])=>
    `<tr><td class="mono">${esc(k)}</td><td class="mono">${v.tasks}</td><td class="mono">${money(v.cost_usd)}</td></tr>`).join('')+'</table>':'<div class="empty">暂无</div>';
  // 最近任务
  const rc=(d.ledger&&d.ledger.recent)||[];
  $('recent').innerHTML=rc.length?`<table><tr><th>时间</th><th>节点</th><th>任务/意图</th><th>档</th><th>结果</th><th>时长</th><th>成本</th></tr>`
    +rc.map(r=>{const tier=r.tier==null?'':`<span class="tier t${r.tier}">T${r.tier}</span>`;
      const ok=r.ok===false?'<span class="ok-n">✗</span>':(r.ok===true?'<span class="ok-y">✓</span>':'—');
      return `<tr><td class="mono">${esc((r.ts||'').replace('T',' ').slice(5,19))}</td>
        <td>${esc(r.node||'?')}</td><td>${esc(r.task||r.intent||'')}</td>
        <td>${tier}</td><td>${ok}</td><td class="mono">${r.duration_s==null?'—':r.duration_s+'s'}</td>
        <td class="mono">${r.cost_usd==null?'—':money(r.cost_usd)}</td></tr>`;}).join('')+'</table>'
    :'<div class="empty">台账为空——还没有任务流水。</div>';
  // 权限证
  const tk=d.tokens_active||[];
  $('tk-c').textContent=tk.length;
  $('tokens').innerHTML=tk.length?`<table><tr><th>节点</th><th>档</th><th>任务</th><th>剩余</th></tr>`
    +tk.map(x=>`<tr><td>${esc(x.node)}</td><td><span class="tier t${x.tier}">T${x.tier}</span></td>
      <td>${esc(x.task)}</td><td class="mono">${left(x.left_s)}</td></tr>`).join('')+'</table>'
    :'<div class="empty">无活跃权限证。</div>';
  // 待审
  const pd=d.pending||[];
  $('pd-c').textContent=pd.length;
  $('pending').innerHTML=pd.length?`<table><tr><th>PID</th><th>档</th><th>请求</th><th>剩余</th></tr>`
    +pd.map(x=>`<tr><td class="mono">${esc(x.pid)}</td><td><span class="tier t${x.tier}">T${x.tier}</span></td>
      <td>${esc(x.text)}</td><td class="mono">${left(x.left_s)}</td></tr>`).join('')+'</table>'
    :'<div class="empty">无待审任务。</div>';
  // 节点 + 设置
  const nodes=d.nodes||[],s=d.settings||{};
  const mp=(s.model_policy&&s.model_policy.default)||s.llm_fallback||'?';
  const setStrip=`<div style="margin-bottom:12px" class="chip">通道 <b>${esc(s.feishu_channel||'?')}</b> ·
    默认模型 <b>${esc(mp)}</b> · 生图档 <b>T${esc(s.gen_tier)}</b> · 看板端口 <b>${esc(s.board_port)}</b></div>`;
  $('nodes').innerHTML=setStrip+(nodes.length?`<table><tr><th>ID</th><th>名称</th><th>类别</th><th>档上限</th><th>说明</th></tr>`
    +nodes.map(n=>`<tr><td class="mono">${esc(n.id)}</td><td>${esc(n.name)}</td>
      <td>${esc(n.category||'')}</td><td class="mono">T${esc(n.tier_max)}</td>
      <td style="color:var(--text-muted)">${esc(n.desc||'')}</td></tr>`).join('')+'</table>':'<div class="empty">无节点</div>');
}

let cd=15;
async function tick(){
  // 用 location.origin 拼绝对地址(剥掉 URL 里可能内嵌的 user:pass)，
  // 否则从带凭证书签打开时 fetch 会因「URL includes credentials」报错。
  try{const r=await fetch(location.origin+'/api/state',{cache:'no-store'});render(await r.json());$('err').textContent='';}
  catch(e){$('err').textContent='拉取失败:'+e.message;}
  cd=15;
}
setInterval(()=>{cd--;$('cd').textContent=cd;if(cd<=0)tick();},1000);
tick();
</script>
</body>
</html>"""


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            try:
                body = json.dumps(build_state(), ensure_ascii=False)
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as e:
                self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}),
                           "application/json; charset=utf-8")
        elif path == "/healthz":
            self._send(200, "ok", "text/plain; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def log_message(self, *a):
        pass   # 静默：日志走 systemd，不刷屏


def main():
    try:
        from core.registry import settings
        reg_port = int(settings().get("board_port", 8250) or 8250)
    except Exception:
        reg_port = 8250
    # BOARD_PORT 环境变量可覆盖注册表端口（本机测试避让占用时用）
    port = int(os.environ.get("BOARD_PORT") or reg_port)
    host = os.environ.get("BOARD_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[board] mgj-spark 观测看板启动 http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("[board] 收到中断，退出。", flush=True)
        srv.shutdown()


if __name__ == "__main__":
    main()
