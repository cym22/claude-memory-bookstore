#!/usr/bin/env python3
"""记忆索引体积闸 —— 由 memory_backup.sh (PostToolUse) 调用。

越线时向 stdout 打印一行告警(bash 侧负责发 TG);一切正常则不输出任何东西。
本脚本永不抛异常退出 non-zero(闸坏了不能拖垮备份 hook)。

🔴 口径铁律:单位一律 **字符**,不是字节。
   harness 提醒里的「KB」= 千字符(用几份历史会话标定:
   报 19.6-20.1KB 时字符数正好 19.3K-20.0K,而字节数是 30.5-32.6KB)。
   中文 1.63 字节/字符,按字节量会在真实占用仅 79% 时误报超限——曾因此
   误诊「MEMORY.md 某行起被截断」,实际从未截断。别再用 wc -c / len(encode())。

   ✅ 阈值已用 git 历史反推标定(靠一次真实截断事故实录反推,无需专门造哨兵):
   某次 _triggers.md 共 328 行,Read **被截断在第 140 行**。翻出当时的 git blob 实算:
     前 140 行 = **24,510 字符** / 43,192 字节
     24,400 **字符** → 正好落在第 **140** 行 ✅ 与实录吻合(差 0.4%)
     24,400 **字节** → 落在第 77 行 ❌ 与实录差 63 行
   ⟹ 单位=字符、阈值=24,400、且**到线硬切**,三件事一次性坐实。
   ⚠️ 严格说这条实测走的是 **Read 工具**路径;MEMORY.md 的**自动加载**路径数值同为 24,400
   (来自 harness 提示原文,且它用的就是 "read limit" 这个词)⟹极可能同一机制,但未直接观测。

判据(三条,各自每天最多发一次):
  ① MEMORY.md >= 19,500 字符           = harness advisory 线(硬上限 24,400)
  ② _triggers.md 当日净增 > 3,000 字符  = 有人在往索引里抄正文
  ③ _triggers.md >800 字符的条目数比昨天多 = 新长出肥条目
判据②③的基线按天存,首次运行当天只建基线不告警。
以上三条 = **存量闸**,发 TG 给用户。

────────────────────────────────────────────────────────────
Gate v2(新增)= **流量闸**,回执给写入方的那个 Claude 会话。
Gate v3(后加)= 链接总数闸 + logs 保质期,在净增事件指针时点名该退役的最老几条。

起因(一次增量成分分解):复胖引擎是**每天新增 8-10 条 × 均 260 字符
的新行**,不是旧行变胖(比例 4~7:1)。⟹ 只砍存量(瘦身/降层)永远是一次性的,
必须管流量。而流量唯一能机器执法的是「写入当刻」。

回执通道:PostToolUse hook 的 `hookSpecificOutput.additionalContext`
  (官方 hooks 文档确认;**不是** exit 2——PostToolUse 的 exit 2 只给用户看,不进模型上下文)。
  本脚本把 JSON 写到 FEEDBACK_FILE,由 memory_backup.sh 搬到 stdout。
  ⚠️ 沿用既有纪律:一切中文关在 python 里,bash 侧只搬运,不碰多字节(旧版 bash 会吞变量名)。

取增量的姿势:本闸由 memory_backup.sh 在 _autogit **之前**调用 ⟹ 工作区已是新内容、
  HEAD 还是上次提交 ⟹ `git diff HEAD -- MEMORY.md` 正好等于本次写入的增量。

三个检查(仅对 MEMORY.md,_triggers.md 不走):
  ⓐ 新增条目行 > 400 字符 → 提示压回钩子(含 🔴🔴🔴 的宪法级红线行豁免)
     阈值由数百条历史新增行的分布定:中位数 205、P90 473。取 400 ≈ 只打最肥的 14%,
     **绝不按纪律线 120 设闸**——那会命中过半的写入,变噪音后必被无视。
  ⓑ 新增行指向的真源已被另一行索引 → 重复/矛盾(机器化抓"同一真源被多条索引各自漂移"这种坑)
  ⓒ 新增 logs/ 或 decisions/ 指针却没删旧的 → 事件行净增(提示以换代增,不是报错)
"""
import os
import re
import sys
import glob
import json
import datetime
import traceback
import subprocess

# ⚠️ 示例路径:请替换成你自己 Claude Code 项目目录下的记忆库路径
MEM_DIR = os.path.expanduser("~/.claude/projects/<your-project-id>/memory")
STATE = os.path.expanduser("~/.claude/hooks/.memory_gate")

ADVISORY_CHARS = 19500   # harness 开始提醒的线
LIMIT_CHARS = 24400      # harness 读取上限,已用历史 git 实测反推确认(见上)
TRIGGERS_DAILY_GROWTH = 3000
FAT_ENTRY_CHARS = 800

# ---- Gate v2(流量闸)参数 ----
LONG_LINE_CHARS = 400        # 新增条目行超此长度才回执(见文件头:绝不设成纪律线 120)
HOOK_CHARS = 120             # 「一句钩子」纪律线,只用于回执文案里报数
EXEMPT_MARK = "\U0001F534\U0001F534\U0001F534"   # 🔴🔴🔴 宪法级红线行豁免(必须每会话在场)
FEEDBACK_FILE = os.path.join(STATE, "feedback.json")
LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")

# ---- Gate v3:链接总数闸 + logs 保质期 ----
# 病灶(实测):索引里约 38% 的链接是 `logs/` 事件记账,只进不出。
#   先立的「写入前自数链接,超线就同步降层」是**软约束**,五天内净增 25 条、零次触发,
#   已实测击穿。⟹ 改成由闸现算并**点名该退役的哪条**,把「要自己想」降成「照着删」。
#   仍不阻断写入(拒绝写记忆 = 信息永久丢失,代价不对等)。
LINK_WARN = 230              # 链接总数警戒线(约 98 字符/链接 ⟹ 24,400 硬顶 ≈ 249 条)
LOGS_PER_TRACK = 3           # 同一战线在索引里最多留几条 logs/ 指针,超出的最老者进退役候选
LOG_PATH_RE = re.compile(r"^logs/(\d{4}-\d{2}-\d{2})_(.+)\.md$")


def _stale_log_candidates(rows):
    """按「战线」给 logs/ 指针分组,返回超保质期的最老几条。

    战线 key = 文件名日期后的第一个 token(handoff_/plan_ 前缀则取第二个)。
    实测:92 条 logs 分 42 组,其中 36 组只有 1-2 条(健康,不该动),
    超标全集中在头部少数几组 ⟹ 该启发式够用。
    🔑 产物是**候选不是结论**——最肥那组里可能有当天刚上线的活线,必须交人拍板。
    """
    groups = {}
    for row in rows:
        for path in LINK_RE.findall(row):
            m = LOG_PATH_RE.match(path)
            if not m:
                continue
            slug = m.group(2)
            tok = slug.split("_")[0]
            if tok in ("handoff", "plan") and "_" in slug:
                tok = slug.split("_")[1]
            groups.setdefault(tok, []).append((m.group(1), path))
    out = []
    for _tok, items in groups.items():
        if len(items) <= LOGS_PER_TRACK:
            continue
        items.sort()                       # 按日期,最老在前
        out.extend(p for _d, p in items[:len(items) - LOGS_PER_TRACK])
    out.sort()
    return out

# ---- 流量闸的两条熔断 ----
# 🔴 病灶:`memory_backup.sh` 的 _autogit **只在 rsync 成功分支里执行** ⟹ 备份盘一掉
#    就永不 commit、HEAD 冻结,此后每次写 MEMORY.md,`git diff HEAD` 取到的都是
#    「掉盘以来的全部累计增量」而不是本次写入 ⟹ 按较高的写入频率重复轰炸,
#    而且恰好砸在系统已经降级的时刻。两条熔断都必须在跑检查之前判。
BACKUP_FAIL_FLAG = "/tmp/memory_backup_fail.flag"   # 与 memory_backup.sh 的 $FLAG 同路径
MAX_ADDED_LINES = 15    # 单次写入新增条目行超此数 ⟹ 不是正常写入(HEAD冻结/批量重写),不回执


def chars(path):
    """按字符数量体积。绝不用 len(f.read().encode()) —— 见文件头口径铁律。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return len(fh.read())
    except OSError:
        return None


def fat_entries(path):
    """_triggers.md 里超长的条目行(以 '- ' 开头的才算条目,标题/注释不算)。"""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [(len(ln.rstrip("\n")), ln[2:30].strip())
                    for ln in fh if ln.startswith("- ") and len(ln) > FAT_ENTRY_CHARS]
    except OSError:
        return []
    rows.sort(reverse=True)
    return rows


# ═══════════ Gate v2:流量闸(回执给写入方的 Claude) ═══════════

def _uncommitted_diff():
    """本次写入的增量。见文件头:本闸跑在 _autogit 之前,所以工作区 vs HEAD = 本次写入。

    git 不可用 / 不是仓库 / 无提交 → 返回空串 ⟹ 全部检查静默跳过(fail-open)。
    """
    try:
        r = subprocess.run(["git", "diff", "HEAD", "--", "MEMORY.md"],
                           cwd=MEM_DIR, capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except Exception:
        return ""


def _entry_lines(diff_text, sign):
    """从 unified diff 里取新增('+')或删除('-')的条目行。

    条目行 = markdown 列表项。注意 diff 前缀和 '- ' 前缀会叠加:新增条目行长这样 '+- 🟢...'。
    """
    out = []
    skip = sign * 3          # '+++' / '---' 是文件头,不是内容
    for ln in diff_text.splitlines():
        if ln.startswith(sign) and not ln.startswith(skip):
            body = ln[1:]
            if body.startswith("- "):
                out.append(body)
    return out


def writer_feedback():
    """跑三个检查,返回给写入方看的一段话;没问题返回 None。"""
    # 熔断①:备份已失败 ⟹ HEAD 大概率冻结,diff 不再等于「本次写入」
    if os.path.exists(BACKUP_FAIL_FLAG):
        return None

    diff = _uncommitted_diff()
    if not diff:
        return None
    added = _entry_lines(diff, "+")
    if not added:
        return None

    # 熔断②:新增行过多 ⟹ 不是一次正常写入(HEAD 冻结累积 / 批量重写 / 瘦身),回执没有意义
    if len(added) > MAX_ADDED_LINES:
        sys.stderr.write("[gate v2] 跳过:本次 diff 含 %d 条新增行(>%d),"
                         "疑似 HEAD 冻结或批量重写\n" % (len(added), MAX_ADDED_LINES))
        return None

    removed = _entry_lines(diff, "-")

    try:
        with open(os.path.join(MEM_DIR, "MEMORY.md"), encoding="utf-8") as fh:
            full_rows = [r for r in fh.read().splitlines() if r.startswith("- ")]
    except OSError:
        return None

    notes = []

    # ⓐ 超长新行
    fat = [ln for ln in added
           if len(ln) > LONG_LINE_CHARS and EXEMPT_MARK not in ln]
    for ln in fat:
        notes.append("• 新增行 %d 字符(纪律线 %d「文件存在+一句钩子」):%s…\n"
                     "  → 判据/数字/清单请留在 topic 真源,索引只留钩子。"
                     % (len(ln), HOOK_CHARS, ln[2:36]))

    # ⓑ 同一真源被多行索引(重复/矛盾)
    seen = set()
    for ln in added:
        for path in LINK_RE.findall(ln):
            if path in seen:
                continue
            n = sum(1 for r in full_rows if ("](%s)" % path) in r)
            if n > 1:
                seen.add(path)
                notes.append("• `%s` 已被 %d 行同时索引 → 请**合并**而非新增。"
                             "\n  (同一真源多条索引会各自漂移成互相矛盾的判词——"
                             "这是索引膨胀最隐蔽的一种)"
                             % (path, n))

    # ⓒ 事件指针净增
    def _is_event(ln):
        return "](logs/" in ln or "](decisions/" in ln
    net = sum(1 for ln in added if _is_event(ln)) - \
          sum(1 for ln in removed if _is_event(ln))
    if net > 0:
        notes.append("• 本次净增 %d 条 logs/decisions 事件指针。"
                     "\n  → 同一战线的旧交接/拍板行应**就地替换或降层**,不要净增"
                     "(举例:两条长期跟踪的战线各自已占 20+ 行,合计吃掉索引近一半)。" % net)

    # ⓓ 链接总数闸 + logs 保质期(Gate v3)
    #    只在「净增事件指针」时判——瘦身/纯改写不该被这条骚扰。
    total_links = len({p for r in full_rows for p in LINK_RE.findall(r)})
    if net > 0 and total_links > LINK_WARN:
        tip = ("• 🔴 **索引已有 %d 条链接(警戒线 %d),本次还净增 %d 条事件指针**"
               "\n  → 必须**同步降层至少 %d 条**,否则下次瘦身只能砍在跑的线"
               "(实测:索引约 38%% 是 logs 事件记账,只进不出)。"
               "\n  → 降层≠删除:迁进 `_archive_index.md`,验收判「消失的 ⊆ 落档的」。"
               % (total_links, LINK_WARN, net, net))
        cand = _stale_log_candidates(full_rows)
        if cand:
            tip += ("\n  → 现成候选(同战线 logs 已超 %d 条,列最老 5 条,**是候选不是结论,"
                    "开真源核过再降**):\n     %s"
                    % (LOGS_PER_TRACK, "\n     ".join(cand[:5])))
        notes.append(tip)

    if not notes:
        return None

    cur = chars(os.path.join(MEM_DIR, "MEMORY.md"))
    head = ("📏 记忆索引流量闸:本次写入 MEMORY.md 有 %d 处可以收一收"
            "(当前 %s/%d 字符,advisory %d)\n"
            % (len(notes), cur if cur else "?", LIMIT_CHARS, ADVISORY_CHARS))
    return head + "\n".join(notes)


def emit_writer_feedback():
    """把回执写成 PostToolUse 的 additionalContext JSON,交给 bash 搬到 stdout。"""
    try:
        os.remove(FEEDBACK_FILE)
    except OSError:
        pass
    msg = writer_feedback()
    if not msg:
        return
    payload = {"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }}
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def main():
    os.makedirs(STATE, exist_ok=True)

    # Gate v2 流量闸:只对 MEMORY.md 的写入跑(_triggers.md 有自己的判据②③)。
    # 单独 try 包住:流量闸出任何问题都不许影响下面的存量闸和整条备份链。
    if (sys.argv[1] if len(sys.argv) > 1 else "").endswith("MEMORY.md"):
        try:
            emit_writer_feedback()
        except Exception:  # noqa: BLE001 —— 流量闸坏了不影响存量闸,但必须留痕
            # 🔴 绝不静默:bash 已把本进程 stderr 引到 memory_gate.log(`2>>`),
            #    吞掉 traceback = 闸因 bug 永久死亡而无人知晓(违反「沉默即失效」)。
            sys.stderr.write("[gate v2] 流量闸异常(存量闸继续):\n")
            traceback.print_exc(file=sys.stderr)

    day = datetime.datetime.now().strftime("%Y%m%d")

    mem = chars(os.path.join(MEM_DIR, "MEMORY.md"))
    trg_path = os.path.join(MEM_DIR, "_triggers.md")
    trg = chars(trg_path)
    if mem is None or trg is None:
        return  # 文件读不到就别猜,静默退出

    fat = fat_entries(trg_path)

    # ---- 每日基线(顺便清掉旧日的基线与已发标记) ----
    base_file = os.path.join(STATE, "baseline_%s.txt" % day)
    first_run_today = not os.path.exists(base_file)
    if first_run_today:
        with open(base_file, "w") as fh:
            fh.write("%d %d %d" % (mem, trg, len(fat)))
        for stale in glob.glob(os.path.join(STATE, "baseline_*.txt")) + \
                     glob.glob(os.path.join(STATE, "fired_*")):
            if os.path.basename(stale) != os.path.basename(base_file) and day not in stale:
                try:
                    os.remove(stale)
                except OSError:
                    pass
    try:
        with open(base_file) as fh:
            _b_mem, b_trg, b_fat = [int(x) for x in fh.read().split()]
    except (OSError, ValueError):
        return

    def fire_once(key):
        """同一判据当天只发一次,防止每次写记忆都轰炸。"""
        marker = os.path.join(STATE, "fired_%s_%s" % (day, key))
        if os.path.exists(marker):
            return False
        open(marker, "w").close()
        return True

    msgs = []
    if mem >= ADVISORY_CHARS and fire_once("mem"):
        msgs.append("MEMORY.md %d 字符,已过 advisory 线 %d(上限 %d)"
                    % (mem, ADVISORY_CHARS, LIMIT_CHARS))

    if not first_run_today:
        grew = trg - b_trg
        if grew > TRIGGERS_DAILY_GROWTH and fire_once("trg_growth"):
            msgs.append("_triggers.md 今日净增 %d 字符(%d→%d)" % (grew, b_trg, trg))
        if len(fat) > b_fat and fire_once("trg_fat"):
            msgs.append("_triggers.md 新增 %d 条 >%d 字符的条目(现共 %d 条,最肥 %d 字符:%s)"
                        % (len(fat) - b_fat, FAT_ENTRY_CHARS, len(fat),
                           fat[0][0], fat[0][1]))

    if msgs:
        sys.stdout.write(
            "\U0001F4CF [记忆索引体积闸] " + " | ".join(msgs) +
            "\n口径=字符(不是字节)。把 topic 正文抄进索引=复胖病发作,"
            "细节请回 topic 真源,索引只留「文件存在+一句钩子」。"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 —— 闸坏了绝不能拖垮备份 hook,但也绝不静默
        sys.stderr.write("[gate] 体积闸整体异常:\n")
        traceback.print_exc(file=sys.stderr)   # → memory_gate.log,见上方同款说明
