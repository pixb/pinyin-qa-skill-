#!/usr/bin/env python3
"""Whisper 批量转写：音频 → 识别文本 → 逐字拼音，按音频 mtime 缓存。

本目录的 ASR 是注音质检的固定读音参照（移植自 pix-vl-audio-eval/asr，
口径不变：Whisper 出字形，pypinyin 出词典读音）。

流程:
  1. 枚举 <audio-dir>/NNN.wav（行号 = 文件名数字）
  2. 缓存有效则跳过（cache 里记录转写时的音频 mtime，音频重生成即失效）
  3. Whisper 转写 → 提取汉字 → pypinyin 逐字拼音（无声调 NORMAL + 数字声调 TONE3）
  4. 写 config/pinyin_qa/asr/NNN.json

用法:
  python3 scripts/transcribe.py --project-dir content/productions/xxx/0008-...
  python3 scripts/transcribe.py --project-dir ... --lines 2,8,39   # 只转指定行
  python3 scripts/transcribe.py --project-dir ... --force          # 忽略缓存全量重转

产物: <project>/config/pinyin_qa/asr/NNN.json
  {"line": 2, "file": "002.wav", "audio_mtime": ..., "whisper_model": "small",
   "language": "zh", "text": "...",
   "chars": [{"char": "须", "pinyin": "xu", "pinyin_tone": "xu1"}, ...]}

退出码: 0 全部成功（含全部命中缓存）；1 存在转写失败。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

try:  # 守卫导入：--check-prereqs 必须在缺依赖时也能运行并给出安装指引
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - 依赖缺失路径
    Style = lazy_pinyin = None  # type: ignore[assignment]

CJK_RE = re.compile(r"[一-鿿]")
NUM_RE = re.compile(r"^(\d+)$")


def parse_lines(spec: str | None) -> set[int] | None:
    """解析 --lines：'2,8,39' 或 '10-20' 或两者混合。None = 全部。"""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out


def cache_valid(cache_path: Path, audio_path: Path) -> bool:
    """缓存有效 = 存在 + 是结构化 json + 音频 mtime 未前进。"""
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    cached_mtime = cached.get("audio_mtime")
    if not isinstance(cached_mtime, (int, float)):
        return False
    current = audio_path.stat().st_mtime
    # 音频被重新生成（mtime 变新）→ 失效；容差防平台精度抖动
    return current <= cached_mtime + 1e-3


def run_transcribe(
    project_dir: str | Path,
    *,
    audio_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    model: str = "small",
    language: str = "zh",
    lines: str | None = None,
    force: bool = False,
) -> int:
    """步骤函数（供 run_pipeline.py 导入调用）：缓存判断 → Whisper → 逐字拼音 → 写缓存。

    Returns:
        0 全部成功（含全部命中缓存）；1 存在转写失败或输入缺失。
    """
    project = Path(project_dir).resolve()
    if not project.is_dir():
        print(f"❌ 项目目录不存在: {project}", file=sys.stderr)
        return 1
    audio_path = Path(audio_dir) if audio_dir else project / "assets" / "audio"
    out_path = Path(out_dir) if out_dir else project / "config" / "pinyin_qa" / "asr"
    if not audio_path.is_dir():
        print(f"❌ 音频目录不存在: {audio_path}", file=sys.stderr)
        return 1
    out_path.mkdir(parents=True, exist_ok=True)

    wanted = parse_lines(lines)
    wavs: list[tuple[int, Path]] = []
    for p in sorted(audio_path.glob("*.wav")):
        m = NUM_RE.match(p.stem)
        if not m:
            print(f"⚠️  跳过非编号文件: {p.name}")
            continue
        line = int(m.group(1))
        if wanted is not None and line not in wanted:
            continue
        wavs.append((line, p))
    if not wavs:
        print("❌ 没有匹配的 wav 文件", file=sys.stderr)
        return 1

    done = skipped = 0
    failures: list[int] = []
    whisper_model = None  # 懒加载，全部命中缓存时不加载模型

    for idx, (line, wav) in enumerate(wavs, 1):
        cache_path = out_path / f"{line:03d}.json"
        if not force and cache_valid(cache_path, wav):
            skipped += 1
            continue
        if whisper_model is None:
            if lazy_pinyin is None:
                print("❌ 缺 pypinyin → pip install -r requirements.txt", file=sys.stderr)
                return 1
            print(f"⏳ 加载 Whisper 模型 {model} ...")
            import whisper  # 延迟到确需转写时

            whisper_model = whisper.load_model(model)
        try:
            hyp = whisper_model.transcribe(str(wav), language=language, fp16=False)["text"].strip()
            chars = [
                {"char": c,
                 "pinyin": lazy_pinyin(c)[0],
                 "pinyin_tone": lazy_pinyin(c, style=Style.TONE3)[0]}
                for c in CJK_RE.findall(hyp)
            ]
            payload = {
                "line": line,
                "file": wav.name,
                "audio_mtime": round(wav.stat().st_mtime, 3),
                "whisper_model": model,
                "language": language,
                "text": hyp,
                "chars": chars,
            }
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            done += 1
            print(f"[{idx}/{len(wavs)}] {wav.name} ✓ {len(chars)}字 {hyp[:40]}")
        except Exception as e:  # noqa: BLE001 — 单文件失败不中断批处理
            failures.append(line)
            print(f"[{idx}/{len(wavs)}] {wav.name} ✗ {e}", file=sys.stderr)

    print(f"\n完成: 新转写 {done}, 缓存命中 {skipped}, 失败 {len(failures)}")
    print(f"缓存目录: {out_path}")
    if failures:
        print(f"失败行: {failures}", file=sys.stderr)
        return 1
    return 0


def check_prereqs() -> int:
    """依赖检查：齐全 → 0；缺失 → 1 并给出安装指引（find_spec 查元数据不执行模块）。"""
    missing = [mod for mod in ("pypinyin", "whisper", "rapidfuzz")
               if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"❌ 缺依赖: {', '.join(missing)} → pip install -r requirements.txt",
              file=sys.stderr)
        return 1
    print("✅ 依赖齐: whisper / pypinyin / rapidfuzz（首次转写会联网下载 Whisper 模型）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Whisper 批量转写 + 逐字拼音缓存")
    ap.add_argument("--project-dir", default=None,
                    help="项目根目录（含 assets/ 与 config/；--check-prereqs 时可省略）")
    ap.add_argument("--audio-dir", default=None, help="音频目录（默认 <project>/assets/audio）")
    ap.add_argument("--out-dir", default=None, help="缓存目录（默认 <project>/config/pinyin_qa/asr）")
    ap.add_argument("--model", default="small", help="Whisper 模型: tiny/base/small/medium（默认 small）")
    ap.add_argument("--language", default="zh", help="语言代码（默认 zh）")
    ap.add_argument("--lines", default=None, help="只转写这些行号：'2,8,39' 或 '10-20' 或混合")
    ap.add_argument("--force", action="store_true", help="忽略缓存，强制重转")
    ap.add_argument("--check-prereqs", action="store_true",
                    help="只检查依赖（whisper/pypinyin/rapidfuzz）是否可导入，然后退出")
    args = ap.parse_args()

    if args.check_prereqs:
        return check_prereqs()

    if not args.project_dir:
        print("❌ --project-dir 必填", file=sys.stderr)
        return 1
    return run_transcribe(
        args.project_dir,
        audio_dir=args.audio_dir, out_dir=args.out_dir,
        model=args.model, language=args.language,
        lines=args.lines, force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
