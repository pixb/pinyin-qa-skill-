---
name: pinyin-qa-skill
description: >-
  拼音注音质检闭环：agent 凭自身中文理解逐字标注拼音（禁用一切库/脚本）→
  Whisper 转写音频出词典读音 → 拼音级对齐逐字比对（多音字读音集合容错）→
  按固化规则分级 P0/P1/OK 出报告 → 字典优先修注音 → 重生成错音音频 →
  循环回归直到 P0=0。产物持久在项目 config/pinyin_qa/。
  激活词：拼音质检、注音检查、注音对比、pinyin QA、读音比对、多音字注音、
  polyphone QA、检查注音对不对、音频读音验证。
license: MIT
metadata:
  author: pix
  version: 1.0.0
  created: 2026-10-16
  last_reviewed: 2026-10-16
  review_interval_days: 90
  dependencies:
    - name: openai-whisper
      type: pypi
      description: 音频转写（首次转写联网下载模型，之后本地）。
    - name: pypinyin
      type: pypi
      description: 音频侧词典读音（含多音字异读集合容错）。
    - name: rapidfuzz
      type: pypi
      description: 拼音序列 Levenshtein 对齐。
activation: /pinyin-qa-skill
provenance:
  maintainer: pix
  version: 1.0.0
  created: 2026-10-16
  source_references:
    - pix-vl-audio-eval asr/transcribe.py + models/_template/compare.py（对齐与容错口径移植）
    - 0008 项目「生实信FOU3」注音修复实战（annotate-polyphones.py WORD_RULES 路径）
---

# /pinyin-qa-skill — 拼音注音质检

验证「TTS 音频实际读音」与「脚本注音是否让 TTS 读对」：**逐字理解标注 →
Whisper 转写 → 逐字对比 → 分级报告 → 修注音 → 重生成 → 循环回归**。
分级全部固化在 `scripts/compare.py`，agent 只解读报告与处置，不重新推导口径。

**范围边界**：
- 本 skill 回答「音频读音 vs 该读的音（理解/注音）对不对、错在哪字、怎么修」。
- 文本级漏读/吞音/整句缺失 → `tts-verify`（ASR 转写 vs 原文全字比对）。
- 声学缺陷（爆音/尾噪/削波）→ `tts-audio-qa-skill`。
- 脚本错别字/术语审校 → `proofread-script`。

## When to Use This Skill

- 用户说「质检注音/检查拼音标注/多音字注音对不对/音频读音验证」并给出项目目录
- 修注音（改字典或改 annotated 文本）并重生成音频后，要回归验证 P0 是否归零
- 触发词：拼音质检、注音检查、注音对比、pinyin QA、读音比对、多音字注音
- **不要**触发：验证整句漏读吞音（→ tts-verify）、查爆音尾噪（→ tts-audio-qa-skill）、生成 TTS 本身

## Prerequisites

```bash
python3 scripts/transcribe.py --check-prereqs   # whisper / pypinyin / rapidfuzz
```

缺依赖时 `pip install -r requirements.txt`。首次转写需联网下载 Whisper 模型
（默认 `small`），之后完全离线。

## Usage — 完整闭环

```bash
PROJ=content/productions/xxx/0000-章节名     # 你的项目目录（下同）

# 0) 依赖自检
python3 scripts/transcribe.py --check-prereqs

# 1) 【理解标注】agent 逐字读 config/script.txt，凭自身中文理解写
#    $PROJ/config/pinyin_qa/understanding.json ——【禁用一切库/脚本，
#    不许用 pypinyin/任何工具生成】，格式：
#    {"lines":[{"line":1,"text":"原文该行","pinyin":["xu1","pu2",...]}, ...]}
#    要求 len(pinyin)==该行汉字数（脚本会强校验）；行多可分块写
#    understanding-01.json / -02.json（按行号切，行号不许重复）。

# 2) 音频转写（结果按 wav mtime 缓存于 config/pinyin_qa/asr/）
python3 scripts/transcribe.py --project-dir $PROJ            # 全量
python3 scripts/transcribe.py --project-dir $PROJ --lines 2,8,39   # 指定行

# 3) 对比分级（默认写 config/pinyin_qa/{summary.json,report.md,report.jsonl}）
python3 scripts/compare.py --project-dir $PROJ

# 4) 读 report.md：§1 总览 / §3 错音明细（P0）/ §4 漏覆盖（P1）/ §6 处置建议

# 5) 修 P0 —— 注音字典优先（见 references/annotation-rules.md）：
#    改主仓 scripts/annotate-polyphones.py 的 WORD_RULES → 重跑注音脚本
#    → 按 report §3 的行号重生成音频（gen-tts-sequential.py --line N）

# 6) 回归循环：重生成的行音频 mtime 变新 → 缓存自动失效
python3 scripts/transcribe.py --project-dir $PROJ --lines <改动行>   # 只补转改动行
python3 scripts/compare.py --project-dir $PROJ --fail-on-p0          # P0>0 → exit 2
# 重复 5-6 直到 P0=0（默认上限 5 轮，超过则停下来向用户汇报卡点）
```

依赖/回归自检：

```bash
python3 scripts/run_evals.py --rollout                    # 期望 14 passed
python3 scripts/run_evals.py --rollout --include-holdout  # 期望 21 passed
```

## 产物（持久在项目内，跨会话复用）

| 文件 | 内容 |
|---|---|
| `config/pinyin_qa/understanding*.json` | agent 逐字理解拼音（Step 1 手写） |
| `config/pinyin_qa/asr/NNN.json` | Whisper 转写 + 逐字词典拼音（mtime 缓存） |
| `config/pinyin_qa/summary.json` | 机器可读：counts / char_accuracy / 逐行 grade 与 diff |
| `config/pinyin_qa/report.md` | 人读报告：总览 / 错音明细 / 漏覆盖 / 容错放行 / 处置 / 口径 |
| `config/pinyin_qa/report.jsonl` | 逐行原始记录 |

## Grading（固化在 `scripts/compare.py`——勿在别处重新推导）

| 级别 | 触发 | 处置 |
|---|---|---|
| **P0** | 该行 ≥1 处错音（归一后不等且模型读音 ∉ 识别字读音集合） | 修注音（字典优先）+ 重生成 + 循环回归 |
| **P1** | 无错音但有字 Whisper 漏覆盖（audio_missing） | 人工试听确认读音，不自动改 |
| **OK** | 全部一致（多音字容错放行不破坏 OK） | 放行 |

合法级别固定 `{P0, P1, OK}`；多问题取最严重级。`char_accuracy =
correct / covered`（漏覆盖不计入分母）。

## Workflow Recipes

**首次全量质检**：check-prereqs → 手写 understanding（分块防漏，行数与
script.txt 汉字行对齐）→ transcribe 全量 → compare → 按 report §3/§6 给出
修复清单（字典规则改哪条、重生成哪些行）→ 用户确认后修+重生成 → 循环。

**修复回归**：只对改动行 `transcribe --lines N`（mtime 自动失效，其余命中
缓存）→ `compare --fail-on-p0` → exit 0 即通过；report.md 每轮覆盖更新。
拿不准是理解错还是音频错时（P0 的两义性），对照 `polyphone_report.json` 与
上下文判断，**不要**改 understanding 去迎合音频。

**声调级（可选）**：`compare.py --strict-tone`（两边归一为数字声调 TONE3）
——仅在明确要检声调词典差异时用；Whisper 字形本身不带声调，声调级错误本法
不可检（见 references/comparison.md §5 局限）。

## Gotchas

- **理解标注必须 agent 人肉逐字写**：这是 skill 的灵魂——`understanding.json`
  禁止用 pypinyin/任何脚本生成。pypinyin 只出现在**音频侧**（transcribe）与
  对齐容错（compare）。评审时若发现 understanding 与词典逐字雷同，视为违规。
- **对齐跑在归一序列上**：raw 序列带调（understanding）vs 无声调（asr
  `pinyin` 字段）长度不同时 Levenshtein 会结构选错（实测把「漏首字」错配成
  3 处错音）。任何对 compare.py 的改动必须保持「先 norm_py 再 opcodes」，
  改后跑 `run_evals.py --rollout`（3 golden 钉死全部分支）。
- **多音字容错是方法天花板**：Whisper 把错音识别回原稿字 → 读音集合容错
  放行（不=没检出过，报告 §5 列 tolerated 供复核）；声调级错误 ASR 结构上
  不可检。不要为好看的数字放宽/收紧 `readings_of`。
- **P0 是「分歧」不是「音频错」**：先判断哪边对（查 polyphone_report.json、
  读上下文），拿不准标出来交人工；**禁止**反向改 understanding 迎合音频。
- **`suggest` 格式契约 = `[A-Z]+[1-5]`**（大写+声调数字，轻声补 5）：eval
  `wrong-entries-actionable` 钉死；改 `suggest_annotation` 前先跑全量 eval。
- **mtime 缓存语义**：`asr/NNN.json` 记录转写时 wav 的 mtime，音频重生成
  （mtime 变新）自动失效；平台时钟抖动容差 1e-3s。手工删缓存或 `--force`
  可强制重转。
- **summary.json 无时间戳、无绝对路径**（file 用行号 basename）：eval 基线
  可复现前提，别加 `generated_at` 之类字段。
- **`jq` 不可用的环境**：eval criteria 全部 `python3 -c`，别改成 jq。
- **understanding 行号全局唯一**：分块文件按行号切分，重复行直接报错退出；
  `line` 必须是 int，`pinyin` 必须是 str 数组且与 text 汉字数严格相等。
- **eval 默认 holdout 不跑**：case-3-missing 是 `split: test`，发布前用
  `--include-holdout` 单独验收（21 passed），优化循环里永远不看它。
- **修复首选 WORD_RULES 字典条目**（语境锚，惠及其他项目）；直改
  `script_annotated.txt` 只是兜底——会被下次重跑注音覆盖，最终仍要收进字典。

## References

- Read `references/annotation-rules.md` for 注音写法规范与「字典优先」修复路径
- Read `references/comparison.md` for 对齐/容错/分级的完整口径与局限
- Run `python3 scripts/transcribe.py --project-dir ...` to 转写（Whisper+词典拼音缓存）
- Run `python3 scripts/compare.py --project-dir ...` to 对比分级（规则唯一权威实现）
- Run `python3 scripts/run_evals.py --rollout` for 回归（14 passed / +holdout 21）
- Read `evals/pinyin-qa.eval.md` for 损失函数与 golden 分支覆盖
