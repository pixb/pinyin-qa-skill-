# pinyin-qa eval spec

Loss function for the pinyin QA comparison pipeline. Every criterion is a
deterministic command; the golden cases pin the exact grades the fixed
comparison rules must produce (all branches: tolerated polyphone / wrong
reading / whisper dropout).

```json
{
  "skill": "pinyin-qa-skill",
  "run": "python3 scripts/run_pipeline.py --project-dir {input} --output {output}",
  "criteria": [
    {
      "id": "valid-summary-json",
      "text": "summary.json is valid JSON with the required top-level keys and skill id",
      "type": "command",
      "cmd": "python3 -c \"import json,sys; s=json.load(open(sys.argv[1])); req={'skill','project','strict_tone','line_count','graded_lines','counts','char_accuracy','correct_count','covered_count','wrong_count','audio_missing_count','tolerated_count','files'}; missing=req-set(s); assert not missing, 'missing keys: '+repr(missing); assert s['skill']=='pinyin-qa-skill'; assert s['line_count']>=1 and s['graded_lines']==s['line_count']\" {output}"
    },
    {
      "id": "counts-consistent",
      "text": "counts has exactly the legal grades {P0,P1,OK}, sums to graded_lines, one entry per file, and each grade iff its branch condition holds",
      "type": "command",
      "cmd": "python3 -c \"import json,sys; s=json.load(open(sys.argv[1])); legal={'P0','P1','OK'}; c=s['counts']; assert set(c)==legal, c; assert sum(c.values())==s['graded_lines']==len(s['files']); assert 0.0<=s['char_accuracy']<=1.0; assert all((e['wrong'] and e['grade']=='P0') or (not e['wrong'] and e['audio_missing'] and e['grade']=='P1') or (not e['wrong'] and not e['audio_missing'] and e['grade']=='OK') for e in s['files'])\" {output}"
    },
    {
      "id": "accuracy-math",
      "text": "char_accuracy equals round(correct/covered,4), per-file accuracy matches its own counts, and issue totals sum across files",
      "type": "command",
      "cmd": "python3 -c \"import json,sys; s=json.load(open(sys.argv[1])); fs=s['files']; assert s['correct_count']==sum(e['correct'] for e in fs); assert s['covered_count']==sum(e['covered'] for e in fs); assert all(e['accuracy']==(round(e['correct']/e['covered'],4) if e['covered'] else 1.0) for e in fs); exp=round(s['correct_count']/s['covered_count'],4) if s['covered_count'] else 1.0; assert s['char_accuracy']==exp; assert s['wrong_count']==sum(len(e['wrong']) for e in fs) and s['audio_missing_count']==sum(len(e['audio_missing']) for e in fs) and s['tolerated_count']==sum(len(e['tolerated']) for e in fs)\" {output}"
    },
    {
      "id": "wrong-entries-actionable",
      "text": "every wrong entry carries char/model/audio/audio_char/char_index and an uppercase tone-digit suggest annotation",
      "type": "command",
      "cmd": "python3 -c \"import json,sys,re; s=json.load(open(sys.argv[1])); ws=[w for e in s['files'] for w in e['wrong']]; assert all(w['model'] and w['audio'] and w['audio_char'] and w['char'] for w in ws); assert all(isinstance(w['char_index'],int) and w['char_index']>0 for w in ws); assert all(re.fullmatch('[A-Z]+[1-5]', w['suggest']) for w in ws), [w['suggest'] for w in ws]\" {output}"
    },
    {
      "id": "report-md-emitted",
      "text": "when report.md sits next to the bound output it must carry the headline counts and the method section (always true in rollout; baselines have no sibling report)",
      "type": "command",
      "cmd": "python3 -c \"import json,sys; from pathlib import Path; s=json.load(open(sys.argv[1])); md=Path(sys.argv[1]).parent/'report.md'; text=md.read_text(encoding='utf-8') if md.is_file() else ''; ok=(not text) or ('# 注音质检报告' in text and '**P0** | %d' % s['counts']['P0'] in text and '## 7. 方法与口径' in text); assert ok, 'report.md present but malformed'\" {output}"
    },
    {
      "id": "report-jsonl-emitted",
      "text": "when report.jsonl sits next to the bound output its row count must equal line_count (always true in rollout; baselines have no sibling report)",
      "type": "command",
      "cmd": "python3 -c \"import json,sys; from pathlib import Path; s=json.load(open(sys.argv[1])); p=Path(sys.argv[1]).parent/'report.jsonl'; rows=[l for l in p.read_text(encoding='utf-8').splitlines() if l.strip()] if p.is_file() else []; ok=(not p.is_file()) or len(rows)==s['line_count']; assert ok, 'report.jsonl row count mismatch'\" {output}"
    },
    {
      "id": "summary-paths-basename-only",
      "text": "summary.json never leaks absolute paths: every files[].file is a bare basename and project has no separator",
      "type": "command",
      "cmd": "python3 -c \"import json,sys; s=json.load(open(sys.argv[1])); names=[e['file'] for e in s['files']]; assert all('/' not in n and chr(92) not in n for n in names), names[:3]; assert '/' not in s['project'] and chr(92) not in s['project'], s['project']\" {output}"
    }
  ],
  "golden": [
    {
      "id": "case-1-match",
      "input": "golden/case-1-match/input",
      "expected": "golden/case-1-match/expected.json",
      "split": "val",
      "compare": "exact"
    },
    {
      "id": "case-2-wrong",
      "input": "golden/case-2-wrong/input",
      "expected": "golden/case-2-wrong/expected.json",
      "split": "val",
      "compare": "exact"
    },
    {
      "id": "case-3-missing",
      "input": "golden/case-3-missing/input",
      "expected": "golden/case-3-missing/expected.json",
      "split": "test",
      "compare": "exact"
    }
  ]
}
```

## Notes

- **Fixtures are pure JSON** (no audio synthesis): each `input/` is a miniature
  project dir with `config/pinyin_qa/understanding.json` + `asr/*.json`.
  Regenerate with `python3 evals/make_fixtures.py`, then rebuild baselines with
  `python3 scripts/run_pipeline.py --project-dir evals/golden/<case>/input
  --output evals/golden/<case>/expected.json` (fixture dirs carry no wav, so the
  orchestrator skips transcription and runs compare from the shipped asr cache)
  and delete the sibling `report.md`/`report.jsonl` (baselines must have no
  sidecars so criteria 5/6 no-op in baseline mode and assert for real only
  under `--rollout`).
- `run` needs no flags: default (non-strict) comparison is the contract under
  test; strict-tone mode is exercised by the 0008 smoke run, not by evals.
- Branch coverage: case-1 pins the **tolerated** branch (音乐 `yue∈{le,yue}`,
  阿弥陀佛 `e∈{a,e}` → all OK with tolerated_count=2); case-2 pins **wrong**
  (成长: `zhang∉readings(常)` cross-char; 阿耨: `nuo∉readings(耨)`
  same-char → 2 P0, accuracy 0.5); case-3 pins **audio_missing** (Whisper
  dropped 慈 → P1, OK companion line) and is the holdout.
- `wrong-entries-actionable` hard-pins the annotation format contract
  (`[A-Z]+[1-5]`) that `references/annotation-rules.md` promises — a change to
  `suggest_annotation` must keep this green.
- Case 3 (`split: test`) is the holdout: run with `--include-holdout` at
  release time; never tune compare.py against it inside an optimization loop.
  Its baseline is hand-promoted (the promote path skips holdout cases).
- `jq` unavailable in this environment: all criteria use `python3 -c`.
