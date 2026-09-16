#!/bin/bash
# 记忆库备份 + 版本层 hook(PostToolUse Write|Edit 触发)
# stdin: 一行文件路径(settings.json 里 jq 提取后管道传入)
# 设计:成功rsync→清flag;失败→只在flag不存在时发一次TG(去抖防轰炸);盘恢复后下次写入自动补备份
#
# 三条独立备份线(起因=某次 hook 漏挂,导致一个自动化脚本的产出当天没写入版本层、
#   改错无从回滚——凡是「Claude 会改但不在任何 git 仓」的文件都该有版本层):
#   ①记忆库 → 镜像 memory-latest/ + 版本 claude-memory/memory.git
#   ②配置三件(CLAUDE.md/settings.json/hooks/*.sh) → 版本 claude-config/config.git
#   ③跨项目共用的某个"唯一真源"文件(示例:一份黑名单/白名单) → 版本 backups/git-mirrors(异盘:源和备份分开放)
#   ⚠️ 分支一律精确匹配, 不用前缀通配: ~/.claude 下每条 transcript 写入都会触发本 hook,
#      用 "$CFG"* 会每次空跑一遍 git。

# ⚠️ 以下路径均为示例占位符,请替换成你自己的实际路径
SRC="$HOME/.claude/projects/<your-project-id>/memory/"   # 见 README:替换成你自己的项目目录
DEST="/Volumes/YourBackupDrive/backups/claude-memory/memory-latest/"
CFG="$HOME/.claude/"
IMGP="/Volumes/YourBackupDrive/projects/some-shared-resource/"
FLAG="/tmp/memory_backup_fail.flag"
TG_ENV="$HOME/.claude/hooks/.env"          # 存 TG_TOKEN / TG_CHAT_ID,絕不进 git
GITLOG="$HOME/.claude/hooks/memory_git.log"
GATELOG="$HOME/.claude/hooks/memory_gate.log"

# 版本层通用件:自动 commit+push。失败只记日志不告警(镜像/主盘才是保命层,git 是版本层)。
# 无改动(diff-index 静默)则不产生空 commit。盘没挂→cd 失败→静默跳过,下次写入自动补。
# $2 可空格分隔多个远端(比如本地盘+云端仓库两个远端,互为副本);
#   某远端盘没挂时该 push 失败只记日志,不影响其他远端;push 是全分支,恢复挂载后下次自动补齐。
_autogit() {  # $1=工作区 $2=remote名(可多个,空格分隔) $3=commit消息后缀
  ( cd "$1" && git add -A >/dev/null 2>&1 && \
    { git diff-index --quiet HEAD 2>/dev/null || \
      { git commit -q -m "auto: $(date '+%Y-%m-%d %H:%M') ${3:-}" && \
        for _r in $2; do git push -q "$_r" main || true; done; }; } \
  ) >> "$GITLOG" 2>&1 || true
}

# 索引体积闸(详见 memory_size_gate.py):
#   起因=人工清理跑不过写入侧增长,病根是写入侧零约束(只有 MEMORY.md 自动加载
#   ⟹每个窗口都有动机往索引里抄正文)。
#   本闸不拦写入,只在越线时发一次 TG,让写的人当场知道自己在抄正文。
#   🔴 口径铁律:单位一律**字符**,不是字节(见 memory_size_gate.py 文件头的实测标定)。
#   ⚠️ 本 hook 若由系统自带的旧版 /bin/bash 3.2 执行:中文标点紧挨变量会被吞进变量名,
#      故一切中文一律关在带引号的 python heredoc / 独立 python 脚本里,bash 侧只搬运变量。
_tg() {  # $1=消息正文;先代理后直连,双失败静默(闸是提醒不是保命层)
  . "$TG_ENV" 2>/dev/null
  curl -sf ${TG_PROXY:+-x "$TG_PROXY"} --max-time 10 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -d chat_id="${TG_CHAT_ID}" -d text="$1" >/dev/null 2>&1 \
  || curl -sf --max-time 10 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -d chat_id="${TG_CHAT_ID}" -d text="$1" >/dev/null 2>&1 \
  || return 1
}

# Gate v2:除原有「存量闸→TG」外,增加「流量闸→回执给写入方的 Claude 会话」。
#   起因=增量成分分解发现:复胖引擎主要是每天新增的新行(不是旧行变胖),
#   ⟹ 只砍存量永远是一次性的,必须在写入当刻管流量。
#   通道=PostToolUse 的 hookSpecificOutput.additionalContext(官方文档确认;
#     **不能用 exit 2**——PostToolUse 的 exit 2 只给用户看,不进模型上下文)。
#   ⚠️ python 侧把整段 JSON(含中文)写进 FEEDBACK,bash 只负责 cat 出去:
#     沿用既有纪律,中文一律不进 bash 变量(旧版 bash 会把紧挨的中文标点吞进变量名)。
#   ⚠️ 本函数必须保持「无论如何 return 0」:闸坏了绝不能拖垮备份链。
_size_gate() {
  FEEDBACK="$HOME/.claude/hooks/.memory_gate/feedback.json"
  rm -f "$FEEDBACK" 2>/dev/null
  GATE_MSG=$(python3 "$HOME/.claude/hooks/memory_size_gate.py" "$1" 2>>"$GATELOG") || return 0

  # 流量闸回执:有就原样吐到 stdout 交给 harness(JSON 由 python 生成,bash 不拼装)
  if [ -s "$FEEDBACK" ]; then
    cat "$FEEDBACK"
    echo "$(date '+%F %T') WRITER_FEEDBACK $(wc -c <"$FEEDBACK" | tr -d ' ') bytes" >> "$GATELOG"
    rm -f "$FEEDBACK" 2>/dev/null
  fi

  # 存量闸告警:照旧发 TG 给用户
  [ -n "${GATE_MSG}" ] || return 0
  if _tg "${GATE_MSG}"; then
    echo "$(date '+%F %T') SENT ${GATE_MSG}" >> "$GATELOG"
  else
    echo "$(date '+%F %T') TG_FAIL ${GATE_MSG}" >> "$GATELOG"
  fi
}

read -r f
case "$f" in
  "$SRC"*)
    case "$f" in
      "$SRC"MEMORY.md | "$SRC"_triggers.md) _size_gate "$f" ;;   # 只这两个索引文件走闸,其余写入零开销
    esac
    if [ -d /Volumes/YourBackupDrive ] && rsync -a --delete "$SRC" "$DEST" 2>/dev/null; then
      rm -f "$FLAG"
      _autogit "$SRC" "backupdrive cloudmirror"
    elif [ ! -f "$FLAG" ]; then
      touch "$FLAG"
      # shellcheck source=/dev/null
      . "$TG_ENV" 2>/dev/null
      MSG="🚨 记忆库备份失败：备份盘未挂载或 rsync 报错。新记忆当前只有主盘单份！挂载恢复后下一次写记忆会自动补全量备份。(本告警去抖：恢复前只发这一条)"
      curl -sf ${TG_PROXY:+-x "$TG_PROXY"} --max-time 10 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="${TG_CHAT_ID}" -d text="$MSG" >/dev/null 2>&1 \
      || curl -sf --max-time 10 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="${TG_CHAT_ID}" -d text="$MSG" >/dev/null 2>&1 \
      || true
    fi
    ;;
  "$CFG"hooks/*.sh | "$CFG"settings.json | "$CFG"CLAUDE.md)
    _autogit "$CFG" "backupdrive cloudmirror" "$(basename "$f")"   # 仓库 .gitignore 是白名单式(防凭证入库)
    ;;
  "$IMGP"*.py | "$IMGP"*.md)
    _autogit "$IMGP" cloudmirror "$(basename "$f")"        # 示例:某个跨项目共用文件的唯一权威副本
    ;;
esac
exit 0
