# -*- coding: utf-8 -*-
"""
意图分类 + 权限档判定（猫管家新增）
====================================
把用户的四类控制需求映射为意图类，并给出该任务的权限档（T0-T3）：
  bootstrap 冷启动新项目      → T2（需审批卡）
  advance   现有项目推进      → T1
  code      当前代码修改      → T1（派发模板强制"自证输出"）
  analyze   大模型拆解/多模态 → T1
另有横切的档位升级词：
  SYSTEM 词（部署/浏览器/跨盘整理…）→ 升到 T2
  DANGER 词（删库/强推/发消息给别人…）→ 升到 T3
纯规则 v1；LLM 兜底路由留接口（classify_llm）本轮显式不接。
"""
import re

BOOTSTRAP_RE = re.compile(
    r"(新建|创建|新开|立项|初始化|冷启动|从零|搭一个|建一个|起一个)[^。\n]{0,12}(项目|目录|系统|应用|工程|服务)"
    r"|(新项目)")
CODE_RE = re.compile(
    r"修一下|修复|修个|fix|debug|排查|报错|崩溃|闪退|bug|重构|改代码|改一下代码|加个功能|加一个功能|写个函数|单测|测试挂", re.I)
ANALYZE_RE = re.compile(
    r"分析|拆解|解读|研究一下|总结|归纳|提取|看看这(张|条|个|份)|这是什么|读一下(图|表|报告)|对比一下|评估", re.I)

# 升 T2：要动系统面（浏览器/部署/跨项目文件/新建项目）
SYSTEM_RE = re.compile(
    r"部署|上云|发布到|上线|scp|服务器|nginx|浏览器|打开网页|打开.{0,8}后台|登录|配置.{0,8}(后台|平台)"
    r"|整理.{0,6}盘|清理.{0,6}目录|移动.{0,8}(目录|文件夹)|重命名.{0,8}目录|开机自启|计划任务|注册表", re.I)
# 升 T3：不可逆/对外
DANGER_RE = re.compile(
    r"删除|删掉|删库|清空|rm\s+-rf|format|强推|force\s*push|--force|发给|发消息给|群发|转账|付款|支付|改密码|改密钥|轮换密钥", re.I)


# ============ 任务级硬阻断（缺口8 · 务实范围）============
# 技术边界：猫管家进程级委派 claude，claude 内部自跑 Bash 对猫管家是事后可观测、不可拦截。
# 本层只做「明显灾难级请求在 issue 前直接不受理」——不是审批(审批是 T2/T3 的活)，是拒绝受理。
# 宁窄勿宽：只收会造成不可逆系统级/凭证外泄的死规则；日常「删除/清理」类交给 T3 审批，别在这里误杀。
HARD_BLOCK_RES = [
    # rm：容忍 -rf/-fr/-r -f/-f -r 任意序 + 大小写；目标仅限 根(/、/*)、家(~、~/、~/*、$HOME 及带尾斜杠/通配)、裸 *、裸 . 。
    # 相对子目录（rm -rf ./build 等）刻意放行——归 T3 审批，不在此硬拦。
    (re.compile(r"\brm\s+(-rf|-fr|-r\s+-f|-f\s+-r)\s+(/\*?|~/?\*?|\$HOME/?\*?|\*|\.)(\s|$)", re.I),
     "递归删除根/家目录"),
    (re.compile(r"\b(format|del\s+/[sq])\b.{0,12}[a-zA-Z]:\\?(\s|$)", re.I), "格式化/递归删整个盘符"),
    # rd/rmdir：/s /q 双序都拦（对齐 del 的写法），目标仍限整个盘符
    (re.compile(r"\b(rd|rmdir)(\s+/[sq]){2}\s+[a-zA-Z]:\\?(\s|$)", re.I), "强删整个盘符"),
    (re.compile(r"(把|将).{0,12}(密钥|密码|token|凭证|credential).{0,10}(发|贴|传|上传|同步|泄).{0,6}(给|到|出)", re.I),
     "外发密钥/凭证"),
    (re.compile(r"(credentials\.json|\.env|id_rsa|mem_mem\.pem).{0,12}(发|传|上传|贴|外发|泄)", re.I),
     "外传凭证文件"),
    (re.compile(r"(立即|马上|现在).{0,4}(关机|shutdown|重启系统|reboot)", re.I), "立即关机/重启系统"),
    # git 强推主分支：-f 或 --force，不论出现在 main/master 前后都拦；推其他分支（dev 等）不拦
    (re.compile(r"git\s+push\b(?=.*\s(--force|-f)(\s|$))(?=.*\b(main|master)\b)", re.I), "强推主分支"),
]


def hard_block(text):
    """明显灾难级请求 → 返回拒绝理由（字符串）；否则 None。"""
    t = (text or "").strip()
    if not t:
        return None
    for rx, reason in HARD_BLOCK_RES:
        if rx.search(t):
            return reason
    return None


def classify(text):
    """返回 (intent, tier, reason)。intent ∈ bootstrap|advance|code|analyze。"""
    t = (text or "").strip()
    if not t:
        return "advance", 1, "空文本默认推进"
    if BOOTSTRAP_RE.search(t):
        intent, base, why = "bootstrap", 2, "冷启动新项目"
    elif CODE_RE.search(t):
        intent, base, why = "code", 1, "代码修改"
    elif ANALYZE_RE.search(t):
        intent, base, why = "analyze", 1, "拆解/分析"
    else:
        intent, base, why = "advance", 1, "项目推进(默认)"
    tier = base
    if DANGER_RE.search(t):
        tier, why = 3, why + "+危险操作词"
    elif SYSTEM_RE.search(t):
        tier, why = max(tier, 2), why + "+系统权限词"
    return intent, tier, why


INTENT_NAME = {"bootstrap": "冷启动", "advance": "推进", "code": "改码", "analyze": "拆解分析"}

# 各意图的派发模板附加指令（拼进 system prompt）
INTENT_GUIDE = {
    "bootstrap": (
        "本任务是【冷启动新项目】：按全局 CLAUDE.md 端口分配规则选端口(先 netstat 扫占用)，"
        "建目录+CLAUDE.md+start.bat(守护壳)，完成后报告：项目路径/端口/入口地址。"),
    "code": (
        "本任务是【代码修改】：改完必须附可复现自证证据(实跑命令+输出/测试结果)，"
        "禁止只说'已修复'；测试失败要如实报告。"),
    "analyze": (
        "本任务是【拆解/分析】：有图片路径就真正打开图片读取内容（多模态），"
        "产出报告/HTML 时结尾附链接或文件路径。"),
    "advance": (
        "本任务是【项目推进】：先看项目内 progress/ 或 .plan/ 接续点再动手，完成后报告改了什么文件。"),
}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cases = [
        "新建一个宠物日记项目，做记录+提醒",
        "修一下中台收图崩溃的 bug",
        "分析这几张检查报告的趋势",
        "把策划者的门控功能推进到 v2",
        "部署到服务器并发布到 nieao.site",
        "删掉旧的测试目录",
        "打开飞书后台改回调地址",
    ]
    for c in cases:
        i, t, w = classify(c)
        print(f"  [{INTENT_NAME[i]}·T{t}] {w:24s} ← {c}")
