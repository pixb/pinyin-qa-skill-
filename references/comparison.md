# 对比口径（comparison）

`scripts/compare.py` 的分级依据。**固化在脚本内，本文件只解释「为什么」**——
修改口径 = 改 compare.py，不许在别处重新推导。

## 1. 数据流

```
agent 逐字理解          音频实际读音
understanding.json      asr/NNN.json (transcribe.py)
（自己逐字标拼音，       （Whisper 出字形 → pypinyin 出
 禁用任何库/脚本）        词典读音，见 §2）
        │                       │
        └────── compare.py ─────┘
                   │
     summary.json / report.md / report.jsonl
     P0 错音 | P1 漏覆盖 | OK 一致
```

## 2. 音频侧为什么是「Whisper + 词典读音」

音频本身不携带拼音，只有字形能被 ASR 拿到。所以音频读音 =
Whisper 识别出的字 → pypinyin 查**它**的读音（transcribe.py 用 `Style.TONE3`
出数字声调、`Style.NORMAL` 出无声调，不用 `Style.TONE` 音调符号形）。

这条口径**不可检声调错误**：Whisper 输出「不」，听成 bù 还是 bú 它都写「不」。
声调级质检需要带声调的 ASR 或人工听（见 §5 局限）。

## 3. 对齐与三种差异

模型拼音序列 `M`（原稿字序） vs 音频拼音序列 `A`（识别字序），用
rapidfuzz `Levenshtein.opcodes` 对齐（拼音级）：

| 对齐结果 | 计数字段 | 含义 |
|---|---|---|
| `equal`/`replace` 且拼音归一后相等 | `correct` | 一致（含**同音异形**：识别成「徐普提」≠「须菩提」但拼音同） |
| 归一后不等，但模型读音 ∈ 识别字的多音字读音集合 | `tolerated` | **多音字容错放行**（§4），信息性，不算错 |
| 归一后不等且不在读音集合 | `wrong` | **错音 → P0** |
| `delete`/`insert` 导致原稿位无对应 | `audio_missing` | **Whisper 漏覆盖**（§5） |

归一化 `norm_py`：小写、去空格、`ü→v`、去组合符；默认再剥末尾声调数字，
`--strict-tone` 保留（此时两边都已是 TONE3 数字形）。

## 4. 多音字容错（防假阳的取舍）

词典首读 ≠ 实际读音是常态（「阿弥陀佛」的 `阿` pypinyin 首读 `a1`，
实际 `e2`）。若直接比首读，会把**正确音频**判成错音。

容错规则：`norm(model_pinyin) ∈ readings_of(识别字)` → 一致。
`readings_of` = 该字 `pinyin(heteronym=True)` 的读音集合。

**代价（已知取舍，不可消除）**：
- Whisper 把错音**识别回原稿字**时（同音字），读音集合包含原读音 →
  真错音被放行。此时检测完全依赖 Whisper 的字形选择。
- 容错放行的条目全部列入报告 §5，供人工复核（模型读音非词典首读但属
  合法读音——若该行无注音，TTS 可能按首读输出，结合 polyphone_report.json 判断）。

**不要**为了让报告好看去放宽或收紧这个集合。

## 5. 分级与局限

| 级别 | 条件 | 处置 |
|---|---|---|
| **P0** | 该行 `wrong ≥ 1` | 修注音（字典优先，见 annotation-rules.md）+ 重生成 + 循环回归 |
| **P1** | `wrong = 0` 但 `audio_missing ≥ 1` | 人工试听——Whisper 没听清 ≠ 音频有错 |
| **OK** | 全一致（tolerated 不破坏 OK） | 放行 |

**局限（写进 SKILL.md 的 Gotchas，不许默许绕过）**：
1. **声调错误不可检**——ASR 输出字形无声调；要检声调用 `--strict-tone`
   也只能检到「词典读音的声调级差异」，仍受 §2 同样约束。
2. **多音字错音的检出率 = Whisper 是否识别成「异读同音」的字**；
   识别回原字 → §4 容错放行。这是方法天花板，报告如实列出 tolerated。
3. **模型（agent）自己也可能标错**——P0 意味着「理解与音频不一致」，
   未必是音频错。修复前先判断哪边对：看上下文 + polyphone_report.json，
   拿不准就标出来交人工，**不要**反过来改理解去迎合错音。
4. **agent 侧理解是逐字人肉标注**，天然可能带错；本 skill 的价值是把
   「理解 vs 实际」的分歧**暴露**出来，而不是保证理解正确。

## 6. 汇总口径

- `char_accuracy` = `correct / covered`（`covered = 一致 + 容错`，
  不含 `audio_missing`）
- `counts` 合法值固定为 `{P0, P1, OK}` 三键，脚本内固化
- `report.md` 的行号 = 原稿行号 = wav 文件名数字（1-based，三者恒等）
