[English](README.md) | **简体中文**

# Hunk Ledger（改动块台账）

**一次不允许“悄悄跳过”的推送前代码审查。**

Hunk Ledger 是一个智能体技能（skill），用来在代码离开你的电脑之前，审查你自己（或你的编程智能体）写的改动。
它把改动切成编号的 hunk（改动块），让一个全新的审查者给 **每一个** hunk 写下结论；每条问题都必须 **逐字引用** 被指控的改动行；
每条问题再经过第二轮专门 **尝试推翻** 它的核验；最后给出一张简短回执：`PASS`（通过）、`HOLD`（暂缓）或 `INCOMPLETE`（未完成）。

适用于 Claude Code、Codex、Cursor、Cline、Gemini CLI，以及任何能读 `SKILL.md`、能运行 Python 的智能体。

## 为什么需要它

智能体自审常常不声不响地失败：

- 只看看起来有意思的文件，剩下的提都不提。
- 引用不存在的行号，或把代码“意译”成另一回事。
- 带着写出 bug 的那套假设去审。
- 审的是一个之后又变过的 diff。
- “顺手修”了十个没人要求的风格问题，把唯一一个真问题淹没了。

Hunk Ledger 把这些都变成辅助脚本会明确报出的硬性失败，而不是靠你自己去发现。

## 功能

- **覆盖台账**：每个 hunk 都必须是 `clean`（写明检查了什么）、`finding`，或 `skipped`（写明原因）。漏一个，审查就是 `INCOMPLETE`。
- **引用可核对的问题**：每条问题注明文件、行号和所在一侧（`new` 新代码 / `old` 被删代码），并引用原文；脚本会核对引用确实在那里，多行引用也支持。
- **先找，再推翻**：核验轮给每条问题标 `confirmed`（成立）、`refuted`（推翻）或 `unsure`（不确定）并写理由。被推翻的不拦截，不确定的会拦截。
- **拦截线**：你来决定会阻止推送的最低严重程度（`critical`、`major`、`minor`、`nit`）。低于拦截线的问题只列出、不修。
- **防过期审查**：台账保存 diff 的哈希，`receipt --recheck` 会拒绝为已经变化的 diff 出结论。
- **影响范围**：`callers` 列出 diff 涉及的函数在 diff 之外的调用点。
- **默认审整个改动**：未推送的提交、未提交的修改、未跟踪的新文件；也可以直接传入任意补丁。
- **适合钩子的退出码**：0 通过、1 暂缓、2 未完成，附带一个示例 `pre-push` 钩子。
- **零依赖**：单个 Python 3.8+ 文件，只用标准库，68 个测试。

## 安装

把整个文件夹复制到智能体的技能目录：

| 智能体 | 位置 |
|---|---|
| Claude Code | `~/.claude/skills/hunk-ledger/`（或项目里的 `.claude/skills/`） |
| Codex | `~/.codex/skills/hunk-ledger/`（或项目里的 `.agents/skills/`） |
| Cursor | 项目里的 `.cursor/skills/hunk-ledger/` |
| 其他智能体 | 放在它读取技能的任意位置，或直接让它读 `SKILL.md` |

```bash
git clone https://github.com/fangdididi/hunk-ledger.git ~/.claude/skills/hunk-ledger
```

中文说明见 `SKILL.zh-CN.md`，中英文任选一个作为技能入口。

## 使用方法

直接对智能体说：

> 推送前用 hunk-ledger 审一下我的改动。

或者自己调用辅助脚本：

```bash
S=~/.claude/skills/hunk-ledger/scripts/hunkledger.py

python3 $S split --out .hunk-ledger              # 冻结改动 -> ledger.json + brief.md
python3 $S callers .hunk-ledger/ledger.json      # diff 之外的调用点
# 审查者写出 .hunk-ledger/review.json（先查找轮，再核验轮）
python3 $S check .hunk-ledger/ledger.json .hunk-ledger/review.json
python3 $S receipt .hunk-ledger/ledger.json .hunk-ledger/review.json --block major --recheck
```

常用参数：`--base <ref>` 与某个分支对比，`--diff file.patch` 或 `--diff -` 审查补丁，`--json` 输出机器可读的结论。

## 示例

```
HUNK LEDGER RECEIPT  verdict: HOLD
diff ad72d9db248c7885  hunks 4  clean 2  finding 1  skipped 1  stop line: major and above

Blocking:
  F1 [major, confirmed] src/pay.py:11  zero-value orders (full coupons) now raise instead of completing

Below the stop line (do not fix unless asked):
  F2 [minor] src/pay.py:13  retries is reset inside charge(); the old module-level counter is now dead

Refuted by the verifier: F3

Not reviewed:
  H4 binary image, checked dimensions only
```

F1（零元订单会抛异常）已确认且达到拦截线，所以结论是暂缓；F2 低于拦截线，只列出；F3 被核验者推翻；H4 是二进制图片，明确标为未审查。
智能体会把回执原样给你看，问你要修哪些；你回答之前它不改代码，结论是 `HOLD` 时没有你的同意也不推送。完整示例（diff、台账、简报、审查记录）见 `examples/`。

## 文件结构

```
hunk-ledger/
├── SKILL.md               # 技能说明（英文）
├── SKILL.zh-CN.md         # 技能说明（中文）
├── README.md              # 英文 README
├── README.zh-CN.md        # 本文件
├── LICENSE                # MIT
├── scripts/
│   └── hunkledger.py      # split / check / receipt / callers
├── examples/
│   ├── change.diff        # 示例改动
│   ├── ledger.json        # 对应台账
│   ├── brief.md           # 对应的冷启动审查简报
│   ├── review.json        # 一份完成的审查记录
│   ├── receipt.txt        # 打印出的回执
│   └── pre-push.sh        # 可选的 git 钩子
└── tests/
    ├── sample.diff
    └── test_hunkledger.py # python3 -m unittest discover -s tests
```

## 许可证

MIT © fangdididi
