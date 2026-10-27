---
name: hunk-ledger
description: 推送前审查自己的代码改动，不允许任何部分被悄悄跳过。把改动切成编号的 hunk，由一个“没参与写代码”的审查者给每个 hunk 写下状态（clean / finding / skipped 并说明原因），每条问题都必须逐字引用被指控的改动行，再由第二轮尝试推翻每条问题，最后打印 PASS / HOLD / INCOMPLETE 回执。适用于 git push 之前、开 PR 之前、用户说“帮我审一下改动”，或智能体写完代码、需要在代码离开本机前做一次独立检查时。
---

# Hunk Ledger（改动块台账）

## 这是什么

Hunk Ledger 是一套推送前的代码审查流程，适用于你自己或智能体写的代码。核心只有一条：
**每个改动块（hunk）都必须有书面结论，每条指控都必须带一段脚本能核对的原文引用。**

自审通常以三种不声不响的方式失败：

1. **覆盖缺口看不见**：审查者只看了有意思的文件，剩下的没看，也没人知道哪些没看。
2. **证据是编的**：问题指向一个不存在的行号，或者把代码“意译”成了另一回事。
3. **作者的盲区**：写代码的同一个智能体来审，带着当初制造 bug 的同一套假设。

Hunk Ledger 把每一种失败都变成脚本会拒绝的东西：

| 失败方式 | 由什么拦住 |
|---|---|
| 有 hunk 没人看 | 每个 hunk 都标成 `clean`、`finding` 或带说明的 `skipped` 之前，`check` 不通过 |
| 引用编造或错位 | 引用必须出现在 diff 指定一侧的指定行，否则 `check` 不通过 |
| 只有单方面指控 | 每条问题都要有核验结论（`confirmed` 成立 / `refuted` 推翻 / `unsure` 不确定）和理由 |
| 审的是旧 diff | diff 哈希对不上时，`receipt --recheck` 拒绝出结论 |
| 顺手修了没人要求修的东西 | 低于“拦截线”的问题只列出，不修 |

辅助脚本 `scripts/hunkledger.py`（Python 3.8+，只用标准库）负责记账，判断仍然由你来做。

## 什么时候用

- `git push` 或开 PR 之前。
- 用户说“审一下我的改动”“推之前帮我看看”“这个能上线吗”。
- 智能体写完或重构完代码，需要一次和写代码的会话无关的独立检查。
- 修完审查问题之后，确认审的是新 diff 而不是旧的。

不适用于：处理别人在 PR 里留下的评论（那是对外来反馈的分拣）、全仓库审计、纯代码风格检查。

## 三种结论

| 结论 | 退出码 | 含义 |
|---|---|---|
| PASS（通过） | 0 | 每个 hunk 都有结论，每条问题都核验过，没有达到拦截线的 confirmed 或 unsure 问题 |
| HOLD（暂缓） | 1 | 审查完整，但至少有一条 confirmed 或 unsure 的问题达到或高于拦截线 |
| INCOMPLETE（未完成） | 2 | 审查本身没做完：有 hunk 缺结论、引用核对不上、问题未核验，或 diff 已过期 |

`unsure` 和 `confirmed` 一样会拦截：核验者判断不了的，就该交给人来判断。

## 严重程度

- **critical（致命）**：数据丢失、安全漏洞、金额或权限算错、主流程崩溃。
- **major（严重）**：用户会碰到的错误行为、破坏了与调用方的约定、真实路径上缺错误处理。
- **minor（轻微）**：实际不太会发生的边界情况、会在将来引出 bug 的误导性命名、新逻辑缺测试。
- **nit（挑剔）**：个人口味。除非用户把拦截线设为 `nit`，否则永远不拦截。

**拦截线**是会阻止推送的最低严重程度，默认 `major`。如果仓库没有既定偏好，问用户一次并记住。

## 操作步骤

### 1. 冻结改动

```bash
python3 scripts/hunkledger.py split --out .hunk-ledger
```

不加 `--diff` 时，“改动”= 上游分支上还没有的提交 + 未提交的修改 + 未跟踪的新文件（遵守 `.gitignore`）。
用 `--base main` 对比某个分支，或用 `--diff file.patch` / `--diff -` 读补丁。生成：

- `.hunk-ledger/ledger.json`：编号的 hunk（H1、H2……），带两侧行号和 diff 哈希。
- `.hunk-ledger/brief.md`：给“冷启动审查者”的简报，只包含 diff 和答题格式。

把 `.hunk-ledger/` 加进 `.git/info/exclude`，避免被提交。

改动很大（大约超过 800 行）时要说出来，并建议拆分推送。用户坚持的话照审，但在回执里注明。

### 2. 摸清影响范围

```bash
python3 scripts/hunkledger.py callers .hunk-ledger/ledger.json
```

它会列出 diff 中定义或修改的函数/方法在 **diff 之外** 的调用点。函数签名改了、有二十个调用方却一个都不在 diff 里，就是值得追的线索。
符号识别基于模式匹配（Python、JS/TS、Go、Rust、类 Java 语言），把它当线索清单，不当证明。

如果项目有自己的快速检查（lint、类型检查、覆盖改动文件的单元测试），也跑一遍。它们的报错只是 **候选**：只有能引用到对应改动行，才算问题。

### 3. 查找轮：用全新的上下文

审查者最好不是写代码的那个上下文。优先级：

1. 一个单独的子智能体或另一个模型会话，只拿到 `brief.md` 和仓库访问权。
2. 同一台机器上的另一个智能体产品。
3. 都没有时：你自己，但刻意放下之前的对话，只读 `brief.md` 和文件。问题标记 `"by": "self"`，并在回执里说明。

对 **每个 hunk**，读 hunk 本身和周围代码，然后问：

- **约定**：空值、零、null、负数、超大值、重复输入时，调用方现在拿到什么？
- **失败路径**：新调用失败、超时或只成功一半时怎样？有没有吞掉错误？
- **状态**：共享可变状态、执行顺序、重试、现在可能存着旧值的缓存。
- **边界**：不可信输入是否流到了查询、路径、shell、模板或权限判断？
- **删除**：每一行被删掉的代码原本在防什么？现在由谁来防？
- **调用方**：影响范围清单里有没有被这次改动弄坏的调用方？
- **测试**：有没有测试会执行到改动行？没有的话，算不算问题（通常是 minor）？

写出 `review.json`：

```json
{
  "hunks": {
    "H1": {"status": "finding"},
    "H2": {"status": "clean", "note": "只是改名，三个调用方都已更新"},
    "H3": {"status": "skipped", "note": "自动生成的锁文件"}
  },
  "findings": [
    {"id": "F1", "hunk": "H1", "file": "src/pay.py", "line": 11, "side": "new",
     "quote": "if total <= 0:", "severity": "major", "category": "bug",
     "claim": "零元订单（全额优惠券）现在会抛异常，而不是正常完成",
     "by": "finder"}
  ]
}
```

查找轮的规则：

- `clean` 的说明写清楚检查了什么，而不是“看着没问题”。
- `skipped` 是诚实，不丢人：生成代码、第三方代码、二进制文件、锁文件。手写逻辑几乎不该被跳过。
- 引用代码要一字不差（空白可以不计较）。被删除的行，用旧行号并设 `"side": "old"`。
- 一条问题只说一件事，不要打包。

### 4. 核验轮：尝试推翻每条问题

第二轮（同样最好换一个上下文）逐条去 **证明问题是错的**：看调用方、查类型、追踪取值，或者写个两行的测试。然后补上：

```json
"verify": {"verdict": "confirmed", "reason": "100% 优惠券时 checkout 传入 0，见 cart.py:88", "by": "verifier"}
```

- `refuted` 的问题仍在回执里标为已推翻，永不拦截。
- `unsure` 达到拦截线就会拦截。写清楚什么信息能让它有定论。
- 核验者可以调高或调低严重程度，但要写理由。

### 5. 校验审查记录，而不是代码

```bash
python3 scripts/hunkledger.py check .hunk-ledger/ledger.json .hunk-ledger/review.json
```

修改的是 **审查记录**，直到它完整：补齐缺的结论、修正引用、补上核验结论。此时先不要动代码。

### 6. 打印回执，然后停下

```bash
python3 scripts/hunkledger.py receipt .hunk-ledger/ledger.json .hunk-ledger/review.json --block major --recheck
```

把回执 **原样** 给用户看，然后只问一个问题：“修哪些？全部拦截项、指定几个编号，还是都不修？”
得到回答之前不改代码，除非用户一开始就说了“审完直接修”。

### 7. 修复，然后重新冻结

只修用户点名的问题。diff 已经变了，所以旧审查按设计就是过期的。重新 `split`、重新审。
内容完全没变的 hunk 可以沿用之前的 `clean` 说明，但每个 hunk 都必须重新列出。

### 8. 推送规则

- PASS：可以推。
- HOLD：用户看过拦截项、明确同意之前不推。
- INCOMPLETE：绝不能当成审查结果展示。准确说明缺了什么。

## 示例

一次改动涉及一个支付函数、一个 README 和一张 logo 图：

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

解读：F1（零元订单会报错）已确认且达到拦截线，所以结论是 HOLD；F2 低于拦截线，只列出不修；
F3 声称“负数不再被拒绝”，但核验者指出 `<= 0` 仍然拒绝负数，所以被推翻、不拦截；H4 是二进制图片，明确标为未审查。
智能体把回执给用户看，问“只修 F1，还是 F1 和 F2 都修？”，然后等待。对应的 diff、台账、简报和 `review.json` 见 `examples/`。

## 小贴士

- **推送越小，审得越好。** 台账超过约 40 个 hunk 时，建议拆分推送。
- **最糟的 bug 常藏在删除里。** 删掉的 `if` 或 `try`，哪怕看着很普通，也值得写一句说明。
- **“不确定”是合法答案。** 比朝任何方向自信地乱猜都好。
- **扫描器的噪声不是问题。** 没有引用，就不算问题。
- **拦截线要稳定。** 每次推送都挪一挪好拿到 PASS，就失去意义了。
- **别给自己的作业打两次分。** 同一个上下文既发现又核验同一条问题时，回执会警告，要告诉用户。
- **退出码是给钩子用的。** `pre-push` 钩子可以跑 `receipt --recheck`，遇到 1 或 2 就拒绝推送。
