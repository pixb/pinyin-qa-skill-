#!/usr/bin/env python3
"""单入口编排器：转写 → 对比（步骤顺序与数据接线固化在代码里，agent 只跑一条命令）。

用法:
  python3 scripts/run_pipeline.py --project-dir content/productions/xxx/0008-...
  python3 scripts/run_pipeline.py --project-dir ... --lines 2,8,39 --fail-on-p0   # 循环门禁
  python3 scripts/run_pipeline.py --project-dir ... --output tmp/out/summary.json # 自定 summary 落点
  python3 scripts/run_pipeline.py --check-prereqs                                 # 只查依赖

步骤（代码按序调用，不经 agent 手递数据）:
  0. --check-prereqs: 依赖检查后直接退出
  1. 前置校验: config/pinyin_qa/understanding*.json 必须存在（agent 的人肉理解标注），
     缺失则快速失败——不让昂贵的 Whisper 加载白跑
  2. run_transcribe: 按音频 mtime 缓存转写（无 wav 目录 → 跳过，直接用既有 asr 缓存；
     golden 用例即缓存态输入）
  3. run_compare: 归一对齐比对 → summary/report 落盘（--output 透传）

退出码（与两个步骤函数一致）:
  0 对比完成；1 输入错误 / 转写失败；2 --fail-on-p0 且存在 P0。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from compare import run_compare
from success_ledger import record_event
from transcribe import check_prereqs, run_transcribe


def run_pipeline(
    project_dir: str | Path,
    *,
    output: str | Path | None = None,
    lines: str | None = None,
    model: str = "small",
    language: str = "zh",
    force: bool = False,
    strict_tone: bool = False,
    fail_on_p0: bool = False,
) -> int:
    """按序执行: 前置校验 → 转写（可跳过）→ 对比。返回退出码。"""
    project = Path(project_dir).resolve()
    if not project.is_dir():
        print(f"❌ 项目目录不存在: {project}", file=sys.stderr)
        return 1

    base = project / "config" / "pinyin_qa"
    if not (base.is_dir() and any(base.glob("understanding*.json"))):
        print("❌ 缺 config/pinyin_qa/understanding*.json —— "
              "先完成 Step 1 理解标注（agent 逐字人肉标注，禁用 pypinyin/脚本生成）",
              file=sys.stderr)
        return 1

    audio_dir = project / "assets" / "audio"
    if audio_dir.is_dir() and any(audio_dir.glob("*.wav")):
        code = run_transcribe(
            project, model=model, language=language, lines=lines, force=force)
        if code != 0:
            return 1
    else:
        print("ℹ️  无 wav 音频（缓存模式）：跳过转写，直接用既有 asr 缓存")

    return run_compare(
        project,
        output=output, strict_tone=strict_tone, fail_on_p0=fail_on_p0,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="单入口：按序执行 转写 → 对比（数据接线在代码内）")
    ap.add_argument("--project-dir", default=None,
                    help="项目根目录（含 assets/ 与 config/；--check-prereqs 时可省略）")
    ap.add_argument("--output", default=None,
                    help="summary.json 路径（默认 <project>/config/pinyin_qa/summary.json）")
    ap.add_argument("--lines", default=None,
                    help="只转写这些行号：'2,8,39' 或 '10-20' 或混合")
    ap.add_argument("--model", default="small", help="Whisper 模型: tiny/base/small/medium（默认 small）")
    ap.add_argument("--language", default="zh", help="语言代码（默认 zh）")
    ap.add_argument("--force", action="store_true", help="忽略缓存，强制重转")
    ap.add_argument("--strict-tone", action="store_true", help="严格比对声调")
    ap.add_argument("--fail-on-p0", action="store_true", help="存在 P0 时退出码 2")
    ap.add_argument("--check-prereqs", action="store_true",
                    help="只检查依赖（whisper/pypinyin/rapidfuzz）是否可导入，然后退出")
    args = ap.parse_args()

    if args.check_prereqs:
        return check_prereqs()
    if not args.project_dir:
        print("❌ --project-dir 必填", file=sys.stderr)
        return 1

    started = time.monotonic()
    code = run_pipeline(
        args.project_dir,
        output=args.output, lines=args.lines,
        model=args.model, language=args.language, force=args.force,
        strict_tone=args.strict_tone, fail_on_p0=args.fail_on_p0,
    )

    # summary 落盘成功（0/2）才记账；记账失败绝不改变业务退出码
    if code in (0, 2):
        try:
            record_event(
                os.environ.get("ASC_RUN_EVENT", "skill_run"),
                skill="pinyin-qa-skill",
                run_id=os.environ.get("ASC_RUN_ID"),
                result="success",
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, ValueError) as e:
            print(f"⚠️ success-ledger 记录失败（已忽略）: {e}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
