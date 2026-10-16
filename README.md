# pinyin-qa-skill

拼音注音质检闭环（opencode/claude-code 通用 skill，git submodule 形式挂载于
主仓 `.agents/skills/`）：

```
agent 逐字理解标注（禁用一切库/脚本）
        ↓  understanding.json
Whisper 转写音频 → pypinyin 词典读音（mtime 缓存）
        ↓  asr/NNN.json
归一序列 Levenshtein 对齐 + 多音字读音集合容错
        ↓  P0 错音 / P1 漏覆盖 / OK
字典优先修注音（主仓 annotate-polyphones.py WORD_RULES）
        ↓  重生成受影响行 → 缓存自动失效 → 回归
P0 = 0 收口（上限 5 轮）
```

## 快速开始

```bash
pip install -r requirements.txt
python3 scripts/run_pipeline.py --check-prereqs
# 先手写 <项目>/config/pinyin_qa/understanding.json（agent 逐字理解标注，禁库）
python3 scripts/run_pipeline.py --project-dir <项目目录>
# 读 <项目>/config/pinyin_qa/report.md
```

## 文件

| 路径 | 作用 |
|---|---|
| `SKILL.md` / `AGENTS.md` | skill 契约（工作流、分级、Gotchas） |
| `scripts/run_pipeline.py` | 单入口编排器：前置校验 → 转写 → 对比（数据接线在代码内） |
| `scripts/transcribe.py` | 步骤：Whisper 批量转写 + 逐字词典拼音缓存 |
| `scripts/compare.py` | 步骤：对齐、容错、分级、报告（唯一口径权威） |
| `scripts/run_evals.py` | 通用 eval runner（`--rollout` 全绿 14 / +holdout 21） |
| `references/annotation-rules.md` | 注音写法与「字典优先」修复路径 |
| `references/comparison.md` | 对齐/容错/分级口径与方法局限 |
| `evals/` | 3 个 golden 分支 case（容错 / 错音 / 漏覆盖 holdout） |

## 设计要点

- **理解与词典分工**：agent 侧人肉标注（skill 灵魂，禁 pypinyin）；pypinyin
  只用于音频侧读音与容错读音集合。
- **归一对齐**：先 `norm_py` 再 opcodes；raw 带调 vs 无声调直接对齐会结构选错。
- **容错天花板如实报告**：多音字容错放行（tolerated）与声调不可检写进
  Gotchas 与 report §7，不粉饰。
- **eval 钉分支**：case-1 容错、case-2 错音（跨字+同字）、case-3 漏覆盖
  （holdout），`suggest` 格式契约由 criterion 硬断言。

## 安装

使用安装脚本：

```bash
./install.sh                          # auto-detect platform, user-level
./install.sh --platform opencode      # explicit platform, user-level
./install.sh --dry-run                # preview without changes
```

该脚本会自动检测平台、将 `scripts/run_pipeline.py` 等文件写入对应平台目录（如
`.claude/skills/`, `.agents/skills/`, `.opencode/skills/` 等），并提供激活指引。
```
