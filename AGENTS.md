# AGENTS.md — pinyin-qa-skill

> 拼音注音质检：逐字理解标注（禁库）→ Whisper 转写 → 归一对齐比对 →
> 分级 P0/P1/OK → 字典优先修注音 → 重生成 → 循环到 P0=0。
> 文本级漏读吞音归 `tts-verify`；声学缺陷归 `tts-audio-qa-skill`。

## Activation

用户给出项目目录，要求拼音质检、注音检查、多音字注音验证、pinyin QA、读音
比对（激活词见 SKILL.md frontmatter）。先跑
`python3 scripts/run_pipeline.py --check-prereqs`，缺依赖就提示
`pip install -r requirements.txt` 并停止。

## Usage

1. **理解标注（人肉，禁用一切库/脚本）**：逐字读 `config/script.txt`，自己
   判断读音写 `$PROJ/config/pinyin_qa/understanding.json`
   （`{"lines":[{"line","text","pinyin":[带调小写如 fou3]}]}`，长度强校验，
   可分块 `understanding-NN.json`，行号不许重复）。**不许用 pypinyin 生成**。
2. **转写+对比（唯一 happy-path，一步跑完）**：
   `python3 scripts/run_pipeline.py --project-dir $PROJ`
   （内部按序执行：understanding 前置校验 → mtime 缓存转写 → 归一对齐分级；
   `--lines 2,8,39` 指定行，`--fail-on-p0` 循环门禁，`--strict-tone` 可选，
   `--output` 自定 summary 落点）。
3. **读 `report.md`**：§3 错音明细（行/字位/模型/音频/建议注音/上下文）、
   §4 漏覆盖、§5 容错放行、§6 处置。处置按
   `references/annotation-rules.md`——**字典优先**（改主仓
   `scripts/annotate-polyphones.py` WORD_RULES → 重跑注音 → 重生成受影响行），
   直改 `script_annotated.txt` 是会被覆盖的兜底。
4. **循环**：重生成行的音频 mtime 变新 → 缓存自动失效 →
   `python3 scripts/run_pipeline.py --project-dir $PROJ --lines <改动行> --fail-on-p0`，
   到 P0=0 收口（上限 5 轮，卡住就汇报）。
   **不要重新推导分级/容错口径**——规则固化在 `scripts/compare.py`
   （run_pipeline 的对比步骤即调用该实现）。

## Gotchas

- **understanding 是本 skill 的灵魂约束**：agent 凭自身中文理解逐字标注；
  一旦发现用 pypinyin/脚本批量生成（与词典逐字雷同）即违规，必须手写重来。
- **P0 = 分歧 ≠ 音频错**：先判断哪边对（polyphone_report.json + 上下文），
  拿不准交人工；禁止改 understanding 迎合音频，禁止改 compare.py 消错音。
- **归一对齐不可回退**：compare.py 必须「先 norm_py 再 Levenshtein」
  （raw 带调 vs 无声调对齐会结构选错，实测把漏字配成错音）；改后
  `run_evals.py --rollout` 必须 14 passed、`--include-holdout` 21 passed。
- **容错天花板**：Whisper 识别回原稿字 → 多音字容错放行；声调级不可检。
  tolerated 列表是信息项，不计错，但要人工扫一眼（无注音行可能被 TTS 读首读）。
- **`suggest` 契约 `[A-Z]+[1-5]`**（轻声补 5），eval `wrong-entries-actionable`
  钉死；产物**无时间戳、无绝对路径**（eval 基线可复现前提）。
- **mtime 缓存**：asr 记录转写时 wav mtime，重生成自动失效；`--force` 全量重转。
- **eval 的 case-3-missing 是 holdout**（split: test）：默认跑不到，验收用
  `--include-holdout`；优化循环里不得对着它调参数。baselines 旁不留
  report.md/report.jsonl（baseline 模式 criteria 靠无兄弟文件 no-op）。
- **jq 不可用**：eval criteria 全 `python3 -c`。
