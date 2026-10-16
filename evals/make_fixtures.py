#!/usr/bin/env python3
"""生成 golden fixtures：三个微型项目目录（understanding.json + asr/*.json）。

纯 JSON 写出，无音频合成、无随机性——输出必须字节稳定。
case 设计依据（pypinyin 异读集合实测，norm 无声调）：

  case-1-match (val)   3 行全 OK，含 2 处多音字容错放行：
                       音乐 yue∈{le,yue}、阿弥陀佛 e∈{a,e}
  case-2-wrong  (val)  2 行全 P0，2 处错音：
                       成长：模型 zhang ∉ readings(常)={chang}（ASR 跨字误识）
                       阿耨：模型 nuo  ∉ readings(耨)={nou}（同字异音节）
  case-3-missing(test) 1 行 P1（Whisper 漏首字「慈」→ audio_missing）+ 1 行 OK

用法: python3 evals/make_fixtures.py   （在 skill 根目录执行）
"""
from __future__ import annotations

import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden"

CASES: dict[str, dict] = {
    "case-1-match": {
        "lines": [
            {"line": 1, "text": "须菩提", "pinyin": ["xu1", "pu2", "ti2"],
             "audio": ["xu1", "pu2", "ti2"], "audio_chars": ["须", "菩", "提"]},
            {"line": 2, "text": "音乐", "pinyin": ["yin1", "yue4"],
             "audio": ["yin1", "le4"], "audio_chars": ["音", "乐"]},
            {"line": 3, "text": "阿弥陀佛", "pinyin": ["e2", "mi2", "tuo2", "fo2"],
             "audio": ["a1", "mi2", "tuo2", "fo2"], "audio_chars": ["阿", "弥", "陀", "佛"]},
        ],
    },
    "case-2-wrong": {
        "lines": [
            {"line": 1, "text": "成长", "pinyin": ["cheng2", "zhang3"],
             "audio": ["cheng2", "chang2"], "audio_chars": ["成", "常"]},
            {"line": 2, "text": "阿耨", "pinyin": ["a1", "nuo4"],
             "audio": ["a1", "nou4"], "audio_chars": ["阿", "耨"]},
        ],
    },
    "case-3-missing": {
        "lines": [
            {"line": 1, "text": "慈悲济世", "pinyin": ["ci2", "bei2", "ji4", "shi4"],
             "audio": ["bei2", "ji4", "shi4"], "audio_chars": ["悲", "济", "世"]},
            {"line": 2, "text": "观自在", "pinyin": ["guan1", "zi4", "zai4"],
             "audio": ["guan1", "zi4", "zai4"], "audio_chars": ["观", "自", "在"]},
        ],
    },
}


def write_case(case_id: str, case: dict) -> None:
    root = GOLDEN / case_id / "input" / "config" / "pinyin_qa"
    asr_dir = root / "asr"
    asr_dir.mkdir(parents=True, exist_ok=True)

    understanding = {
        "lines": [
            {"line": e["line"], "text": e["text"], "pinyin": e["pinyin"]}
            for e in case["lines"]
        ]
    }
    (root / "understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for e in case["lines"]:
        n = e["line"]
        asr = {
            "line": n,
            "file": f"{n:03d}.wav",
            "audio_mtime": 1760000000.0,
            "whisper_model": "small",
            "language": "zh",
            "text": "".join(e["audio_chars"]),
            "chars": [
                {"char": c, "pinyin": strip_tone(p), "pinyin_tone": p}
                for c, p in zip(e["audio_chars"], e["audio"])
            ],
        }
        (asr_dir / f"{n:03d}.json").write_text(
            json.dumps(asr, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{case_id}: {len(case['lines'])} 行 → {root.relative_to(GOLDEN.parent.parent)}")


def strip_tone(p: str) -> str:
    """'cheng2' -> 'cheng'（transcribe.py 的 pinyin 字段本就是无声调）。"""
    return p.rstrip("12345")


if __name__ == "__main__":
    for cid, case in CASES.items():
        write_case(cid, case)
    print("done")
