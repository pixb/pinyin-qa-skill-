#!/usr/bin/env python3
"""注音质检对比：agent 理解拼音 vs Whisper 音频读音 → 分级 + 报告。

输入（均在 <project>/config/pinyin_qa/ 下，可覆盖）:
  understanding.json   agent 凭自身中文理解逐字标注的拼音（可分块
                       understanding*.json，自动按行号合并）
  asr/NNN.json         transcribe.py 的产物（音频实际读音的词典拼音）

输出（默认写 --output 同目录）:
  summary.json   机器可读：counts / char_accuracy / 逐行 grade 与 diff 明细
  report.md      人读报告：结论总览 / 错音明细 / 漏覆盖 / 容错放行 / 处置建议
  report.jsonl   逐行原始记录（一行一条）

分级（固化在本文件，勿在别处重新推导）:
  P0 该行 ≥1 处错音（多音字读音集合容错后仍不一致）→ 修注音 + 重生成
  P1 该行无错音，但有字 Whisper 漏覆盖（audio_missing）→ 人工试听
  OK 全部一致

对比口径（详见 references/comparison.md）:
  - 拼音序列 Levenshtein 对齐（rapidfuzz），多音字按 pypinyin 读音集合容错
  - 默认无声调比对；--strict-tone 启用声调（两边都归一到 数字声调 TONE3 形式）
  - 同音异形字按拼音等价（识别成「徐普提」不算差异）
  - 容错放行（模型读音 ∈ 识别字读音集合但 ≠ 词典首读）单独列出，信息性

退出码:
  0 对比完成
  1 输入错误（understanding/asr 缺失、数量不一致、格式非法）
  2 --fail-on-p0 且存在 P0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from pypinyin import Style, pinyin
from rapidfuzz.distance import Levenshtein

CJK_RE = re.compile(r"[一-鿿]")
LEGAL_GRADES = ("P0", "P1", "OK")
TONE_DIGIT_RE = re.compile(r"[1-5]$")
NON_PY_RE = re.compile(r"[^a-z0-9]")


# ── 拼音归一 ─────────────────────────────────────────────────────

def norm_py(s: str, strict: bool) -> str:
    """拼音归一化：小写、去空格、ü→v、NFD 去组合符。

    无声调（默认）：再剥末尾声调数字 [1-5]。
    strict：保留数字（模型侧写数字，音频侧 transcribe 已用 TONE3 出数字）。
    """
    s = s.strip().lower().replace("ü", "v").replace("u:", "v").replace(" ", "")
    if not strict:
        s = TONE_DIGIT_RE.sub("", s)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return s


def readings_of(char: str, strict: bool) -> set[str]:
    """识别字的合法读音集合（多音字容错的依据）。"""
    style = Style.TONE3 if strict else Style.NORMAL
    return {norm_py(r, strict) for r in pinyin(char, heteronym=True, style=style)[0]}


def suggest_annotation(toned_py: str) -> str:
    """模型带调拼音 → IndexTTS2 注音写法（大写+数字，轻声补 5）。"""
    s = toned_py.strip().lower().replace("ü", "v").replace(" ", "")
    if not TONE_DIGIT_RE.search(s):
        s += "5"
    return NON_PY_RE.sub("", s).upper()


# ── 输入装载 ─────────────────────────────────────────────────────

def load_understanding(path: Path) -> dict[int, dict]:
    """装载理解文件。path 可为单 json 或目录（目录下 understanding*.json 合并）。"""
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.glob("understanding*.json"))
        if not files:
            raise FileNotFoundError(f"目录下无 understanding*.json: {path}")
    else:
        raise FileNotFoundError(f"understanding 不存在: {path}（先完成 Step 2 理解标注）")

    lines: dict[int, dict] = {}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        entries = data.get("lines")
        if not isinstance(entries, list):
            raise ValueError(f"{f.name}: 缺少 'lines' 数组")
        for e in entries:
            n = e.get("line")
            if not isinstance(n, int):
                raise ValueError(f"{f.name}: line 必须是整数: {e!r}")
            if n in lines:
                raise ValueError(f"{f.name}: 行号 {n} 重复（分块理解请按行号切分）")
            text, py = e.get("text", ""), e.get("pinyin")
            if not isinstance(py, list) or not all(isinstance(x, str) for x in py):
                raise ValueError(f"{f.name}: 行 {n} pinyin 必须是字符串数组")
            han = CJK_RE.findall(text)
            if len(han) != len(py):
                raise ValueError(
                    f"{f.name}: 行 {n} pinyin 数量({len(py)}) 与 text 汉字数({len(han)}) 不一致")
            lines[n] = {"text": text, "pinyin": py, "source": f.name}
    return lines


def load_asr(asr_dir: Path, line: int) -> dict | None:
    p = asr_dir / f"{line:03d}.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ── 单行对比 ─────────────────────────────────────────────────────

def compare_line(model: dict, audio: dict, strict: bool) -> dict:
    """一行（一段音频）的逐字对比，返回原始记录（未分级）。"""
    ref_chars = CJK_RE.findall(model["text"])
    model_py = model["pinyin"]
    audio_chars = [c["char"] for c in audio["chars"]]
    field = "pinyin_tone" if strict else "pinyin"
    audio_py = [c.get(field) or c["pinyin"] for c in audio["chars"]]

    # 先归一化再对齐：对齐决定「哪个音频音节对应原稿哪个字」，必须在归一
    # 序列上做（原始序列带调/无声调、同形异音会让 Levenshtein 结构选错）。
    model_norm = [norm_py(p, strict) for p in model_py]
    audio_norm = [norm_py(p, strict) for p in audio_py]
    aligned: list[tuple[str, str] | None] = [None] * len(model_norm)
    for op in Levenshtein.opcodes(model_norm, audio_norm):
        tag, i1, i2, j1, j2 = op.tag, op.src_start, op.src_end, op.dest_start, op.dest_end
        if tag == "equal":
            for k in range(i2 - i1):
                aligned[i1 + k] = (audio_chars[j1 + k], audio_py[j1 + k])
        elif tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                aligned[i1 + k] = (audio_chars[j1 + k], audio_py[j1 + k])
        # delete/insert：原稿位置无音频对应 → 保持 None（audio_missing）

    wrong: list[dict] = []
    missing: list[dict] = []
    tolerated: list[dict] = []
    covered = correct = 0
    for i, ch in enumerate(ref_chars):
        a = aligned[i]
        if a is None:
            missing.append({"char_index": i + 1, "char": ch, "model": model_py[i]})
            continue
        covered += 1
        a_char, a_py = a
        m_norm, a_norm = model_norm[i], norm_py(a_py, strict)
        if m_norm == a_norm:
            correct += 1
            continue
        if m_norm in readings_of(a_char, strict):
            # 模型读音是识别字的合法读音之一：词典首读 ≠ 模型读音 → 信息性放行
            correct += 1
            tolerated.append({
                "char_index": i + 1, "char": ch, "model": model_py[i],
                "audio": a_py, "audio_char": a_char,
            })
            continue
        ctx_from = max(0, i - 5)
        wrong.append({
            "char_index": i + 1, "char": ch, "model": model_py[i],
            "audio": a_py, "audio_char": a_char,
            "suggest": suggest_annotation(model_py[i]),
            "context": ref_chars[ctx_from:i + 6],
        })

    accuracy = round(correct / covered, 4) if covered else 1.0
    if wrong:
        grade, reasons = "P0", [f"错音 ×{len(wrong)}"]
    elif missing:
        grade, reasons = "P1", [f"Whisper 漏覆盖 ×{len(missing)} 字"]
    else:
        grade, reasons = "OK", []
    if tolerated:
        reasons.append(f"多音字容错放行 ×{len(tolerated)}（信息性）")

    return {
        "grade": grade,
        "reasons": reasons,
        "accuracy": accuracy,
        "correct": correct,
        "wrong": wrong,
        "audio_missing": missing,
        "tolerated": tolerated,
        "ref_han_count": len(ref_chars),
        "covered": covered,
        "whisper_text": audio.get("text", ""),
    }


# ── 报告 ────────────────────────────────────────────────────────

def write_reports(summary_path: Path, project_name: str, records: list[dict], strict: bool) -> dict:
    """summary 写到 summary_path（run_evals 的 {output} 必须精确落点）。"""
    out_dir = summary_path.parent
    records = sorted(records, key=lambda r: r["line"])
    counts = {"P0": 0, "P1": 0, "OK": 0}
    wrong_n = missing_n = tol_n = covered_n = correct_n = 0
    for r in records:
        counts[r["grade"]] += 1
        wrong_n += len(r["wrong"])
        missing_n += len(r["audio_missing"])
        tol_n += len(r["tolerated"])
        covered_n += r["covered"]
        correct_n += r["correct"]
    total = len(records)
    char_accuracy = round(correct_n / covered_n, 4) if covered_n else 1.0

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "skill": "pinyin-qa-skill",
        "project": project_name,
        "strict_tone": strict,
        "line_count": total,
        "graded_lines": total,
        "counts": counts,
        "char_accuracy": char_accuracy,
        "correct_count": correct_n,
        "covered_count": covered_n,
        "wrong_count": wrong_n,
        "audio_missing_count": missing_n,
        "tolerated_count": tol_n,
        "files": [
            {
                "line": r["line"],
                "file": f"{r['line']:03d}.wav",
                "grade": r["grade"],
                "reasons": r["reasons"],
                "accuracy": r["accuracy"],
                "correct": r["correct"],
                "covered": r["covered"],
                "wrong": r["wrong"],
                "audio_missing": r["audio_missing"],
                "tolerated": r["tolerated"],
            }
            for r in records
        ],
    }
    (summary_path).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "report.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({"line": r["line"], **r}, ensure_ascii=False) + "\n")

    md = format_report(project_name, summary, records, strict)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    return summary


def format_report(project_name: str, s: dict, records: list[dict], strict: bool) -> str:
    c = s["counts"]
    p0_lines = [r for r in records if r["grade"] == "P0"]
    missing_lines = [r for r in records if r["audio_missing"]]
    total = s["line_count"]
    pct = lambda n: f"{n / total * 100:.1f}%" if total else "0.0%"  # noqa: E731
    tone_mode = "严格（含声调）" if strict else "默认（忽略声调）"

    md = [
        "# 注音质检报告",
        "",
        f"- 项目: `{project_name}`",
        f"- 比对行数: {total}（每行 = 一段音频）",
        f"- 字级准确率（相对音频读音）: **{s['char_accuracy']:.2%}**",
        f"- 声调比对: {tone_mode}",
        "",
        "## 1. 结论总览",
        "",
        "| 级别 | 行数 | 占比 | 含义 |",
        "|---|---|---|---|",
        f"| **P0** | {c['P0']} | {pct(c['P0'])} | 错音：模型理解与音频读音不一致（多音字容错后）→ 修注音 + 重生成 |",
        f"| **P1** | {c['P1']} | {pct(c['P1'])} | Whisper 漏覆盖（无错音但有字未识别）→ 人工试听核对 |",
        f"| **OK** | {c['OK']} | {pct(c['OK'])} | 一致 |",
        "",
        f"**存在错音的行（P0）: {c['P0']}/{total}**",
        "",
        "## 2. 关键发现",
        "",
        f"- **{s['wrong_count']} 处错音**（分布于 {c['P0']} 行）",
        f"- **{s['audio_missing_count']} 字 Whisper 漏覆盖**（涉及 {len(missing_lines)} 行；"
        "整行无错音才计 P1，不计入准确率）",
        f"- **{s['tolerated_count']} 处多音字容错放行**（模型读音是识别字合法读音但非词典首读——信息性，见 §5）",
        "",
    ]

    if p0_lines:
        md += [
            "## 3. 错音明细（P0）",
            "",
            "| 行 | 音频 | 字位 | 字 | 模型理解 | 音频读音 | 识别字 | 建议注音 | 上下文 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in p0_lines:
            for w in r["wrong"]:
                ctx = "".join(w["context"])
                md.append(
                    f"| {r['line']} | {r['line']:03d}.wav | {w['char_index']} | {w['char']} "
                    f"| {w['model']} | {w['audio']} | {w['audio_char']} | **{w['suggest']}** | {ctx} |")
        md.append("")

    if missing_lines:
        md += [
            "## 4. 漏覆盖明细（Whisper 未识别字位；整行无错音才计 P1）",
            "",
            "| 行 | 音频 | 行级 | 漏字数 | 缺位字 |",
            "|---|---|---|---|---|",
        ]
        for r in missing_lines:
            chars = "".join(m["char"] for m in r["audio_missing"])
            md.append(
                f"| {r['line']} | {r['line']:03d}.wav | {r['grade']} "
                f"| {len(r['audio_missing'])} | {chars} |")
        md.append("")

    tol_all = [(r, t) for r in records for t in r["tolerated"]]
    if tol_all:
        md += [
            "## 5. 多音字容错放行（信息性，不计错音）",
            "",
            "| 行 | 字 | 模型理解 | 音频词典首读 | 识别字 |",
            "|---|---|---|---|---|",
        ]
        for r, t in tol_all:
            md.append(f"| {r['line']} | {t['char']} | {t['model']} | {t['audio']} | {t['audio_char']} |")
        md.append("")

    md += [
        "## 6. 处置建议",
        "",
        "1. **P0 错音**：注音**字典优先**——把规则加进 `scripts/annotate-polyphones.py` 的"
        " WORD_RULES/CHAR_RULES（写法见 `references/annotation-rules.md`）→ 重跑注音脚本 →"
        " 按行重生成音频；语境专属无法字典化的直接改 `config/script_annotated.txt`。",
        "2. **循环回归**：重生成后只对改动行重转写（transcribe `--lines N`，mtime 缓存自动失效），"
        " 再跑 compare，直到 P0=0（默认上限 5 轮）。",
        "3. **P1 漏覆盖**：人工试听对应音频确认读音（Whisper 没听清不等于音频有错）。",
        "4. **容错放行**：词典首读 ≠ 模型读音但属合法读音——若该行无注音，TTS 可能按首读输出，"
        " 结合 polyphone_report.json 判断是否需要补注音。",
        "",
        "## 7. 方法与口径",
        "",
        "- 对齐：模型拼音序列 vs 音频拼音序列的 Levenshtein 对齐（rapidfuzz opcode）",
        "- 容错：模型读音 ∈ 识别字的 pypinyin 读音集合 → 视为一致（多音字词典首读≠实际读音的防假阳）",
        "- 同音异形：识别字与原稿字不同但拼音一致 → 对齐即一致",
        "- 漏覆盖：原稿位置无音频对应 → audio_missing，不计入准确率",
        "- 声调：默认忽略（Whisper 输出字形不含声调，声调级错误本法不可检）；"
        " --strict-tone 时两边均归一为数字声调（TONE3）",
        "",
    ]
    return "\n".join(md)


# ── main ────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="对比 agent 理解拼音与 Whisper 音频读音")
    ap.add_argument("--project-dir", required=True, help="项目根目录")
    ap.add_argument("--understanding", default=None,
                    help="理解文件或目录（默认 <project>/config/pinyin_qa）")
    ap.add_argument("--asr-dir", default=None,
                    help="ASR 缓存目录（默认 <project>/config/pinyin_qa/asr）")
    ap.add_argument("--output", default=None,
                    help="summary.json 路径（默认 <project>/config/pinyin_qa/summary.json）")
    ap.add_argument("--report-dir", default=None,
                    help="报告目录（默认与 summary.json 同目录）")
    ap.add_argument("--strict-tone", action="store_true", help="严格比对声调")
    ap.add_argument("--fail-on-p0", action="store_true", help="存在 P0 时退出码 2")
    args = ap.parse_args()

    project = Path(args.project_dir).resolve()
    base = project / "config" / "pinyin_qa"
    und_path = Path(args.understanding) if args.understanding else base
    asr_dir = Path(args.asr_dir) if args.asr_dir else base / "asr"
    out_path = Path(args.output) if args.output else base / "summary.json"
    out_dir = Path(args.report_dir) if args.report_dir else out_path.parent

    try:
        understanding = load_understanding(und_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if not understanding:
        print("❌ understanding 为空", file=sys.stderr)
        return 1

    records: list[dict] = []
    missing_cache: list[int] = []
    for line in sorted(understanding):
        audio = load_asr(asr_dir, line)
        if audio is None:
            missing_cache.append(line)
            continue
        records.append({"line": line, **compare_line(understanding[line], audio, args.strict_tone)})

    if missing_cache:
        shown = ", ".join(str(x) for x in missing_cache[:20])
        more = f" 等 {len(missing_cache)} 行" if len(missing_cache) > 20 else ""
        print(f"❌ 缺 ASR 缓存（先跑 transcribe.py）: {shown}{more}", file=sys.stderr)
        return 1

    summary = write_reports(out_path, project.name, records, args.strict_tone)
    c = summary["counts"]
    print(f"✅ 对比完成 {summary['graded_lines']} 行 | "
          f"P0={c['P0']} P1={c['P1']} OK={c['OK']} | "
          f"字级准确率 {summary['char_accuracy']:.2%}")
    print(f"   报告: {out_dir / 'report.md'}")
    print(f"   汇总: {out_path}")

    if args.fail_on_p0 and c["P0"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
