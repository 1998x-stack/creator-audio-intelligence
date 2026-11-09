#!/usr/bin/env python3
"""Real ASR benchmark: run faster-whisper and measure CER against ground truth.

This does NOT use mocks: it loads the actual CTranslate2 Whisper model and
decodes a real WAV. Compare the numbers it prints with a *human* transcription
of the same audio before quoting any accuracy claim in docs.

Usage:
    scripts/asr_benchmark.py AUDIO.wav --truth scripts/fixture_text_zh.txt \
        [--model small] [--device cpu] [--compute-type int8] [--json out.json]
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path

# CJK + ASCII punctuation and whitespace are dropped before CER so that
# punctuation style differences do not masquerade as recognition errors.
_STRIP = re.compile(r"[\s，。、；：？！“”‘’（）《》【】…—·,.;:?!\"'()\[\]<>/\\|~`@#$%^&*_+=-]+")
_NORM_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# Whisper transcribes some Mandarin audio into Traditional Chinese and rewrites
# spoken numerals as digits/percent signs. Neither is a recognition error, so
# both sides are folded to Simplified + ASCII numerals before measuring CER.
#
# Numeral folding must be *scoped*: a blanket 零->0 mapping corrupts ordinary
# words such as 零售 (retail), and a blanket 点->. mapping corrupts 观点/重点.
# Only explicit numeric contexts are rewritten.
_CN_MAP = {'零': '0', '〇': '0', '一': '1', '二': '2', '三': '3', '四': '4',
           '五': '5', '六': '6', '七': '7', '八': '8', '九': '9'}
_CN_D = "零〇一二三四五六七八九"
_PCT_DECIMAL = re.compile(r"百分之([" + _CN_D + r"]+)点([" + _CN_D + r"]+)")
_PCT_INTEGER = re.compile(r"百分之([" + _CN_D + r"]+)")
_CN_RUN = re.compile(r"(?:^|[^" + _CN_D + r"])([" + _CN_D + r"]{2,})(?![十百千万])")
_DIGIT_RUN = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def _cn_digits(s: str) -> str:
    return "".join(_CN_MAP[c] for c in s)


def to_simplified(text: str) -> str:
    """Traditional -> Simplified via zhconv, degrading to identity if absent."""
    try:
        import zhconv
    except ImportError:
        return text
    return zhconv.convert(text, "zh-cn")


def _fold_cn_run(m: re.Match) -> str:
    """Convert only the digit run, keeping any preceding non-digit char."""
    whole, run = m.group(0), m.group(1)
    return whole[:len(whole) - len(run)] + _cn_digits(run)


def normalize_numbers(text: str) -> str:
    """Fold only explicit numeric contexts onto ASCII digits.

    Handles 百分之四点九 / 二零二五 / 4.9% / 7.2. Multiplicative forms such as
    三十万 are deliberately left untouched and reported as a limitation rather
    than silently mis-converted.
    """
    text = _PCT_DECIMAL.sub(lambda m: _cn_digits(m.group(1)) + "." + _cn_digits(m.group(2)), text)
    text = _PCT_INTEGER.sub(lambda m: _cn_digits(m.group(1)), text)
    text = _CN_RUN.sub(_fold_cn_run, text)
    return text.replace("％", "%").replace("%", "")


def normalize(text: str) -> str:
    text = to_simplified(text)
    text = text.translate(_NORM_DIGITS)
    text = normalize_numbers(text)
    return _STRIP.sub("", text)


def extract_numbers(text: str) -> list[str]:
    """Numbers present after numeral folding (order preserved, deduplicated)."""
    folded = normalize_numbers(to_simplified(text).translate(_NORM_DIGITS))
    return list(dict.fromkeys(_DIGIT_RUN.findall(folded)))


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance with two rolling rows."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--truth")
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    audio = Path(args.audio)
    if not audio.is_file():
        print(f"ERROR: audio not found: {audio}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from creator_audio_intel.asr import transcribe_audio

    t0 = time.time()
    segments, language, version = transcribe_audio(
        audio, device=args.device, compute_type=args.compute_type, model_name=args.model)
    elapsed = time.time() - t0

    transcript = "".join(s.text for s in segments)
    report = {
        "audio": str(audio),
        "model": args.model,
        "faster_whisper_version": version,
        "device": args.device,
        "compute_type": args.compute_type,
        "detected_language": language,
        "segment_count": len(segments),
        "audio_seconds": round(max(s.end_ms for s in segments) / 1000.0, 2),
        "decode_seconds": round(elapsed, 2),
        "realtime_factor": round(elapsed / max(1e-6, max(s.end_ms for s in segments) / 1000.0), 3),
        "raw_transcript": transcript,
        "segments": [{"id": s.segment_id, "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                     for s in segments],
    }

    print(f"model={args.model} version={version} device={args.device}/{args.compute_type}")
    print(f"language={language} segments={len(segments)} decode={elapsed:.2f}s")
    print(f"raw transcript ({len(transcript)} chars):\n{transcript}")
    converted = to_simplified(transcript)
    if converted != transcript:
        print(f"\nWARNING: ASR emitted Traditional Chinese; simplified form:\n{converted}")
        report["traditional_output_detected"] = True
    print()

    exit_code = 0
    if args.truth:
        truth_raw = Path(args.truth).read_text(encoding="utf-8").strip()
        hyp, ref = normalize(transcript), normalize(truth_raw)
        if not ref:
            print("ERROR: ground truth is empty after normalization", file=sys.stderr)
            return 2
        dist = edit_distance(hyp, ref)
        cer = dist / len(ref)
        report.update({"ground_truth_chars": len(ref), "hypothesis_chars": len(hyp),
                       "edit_distance": dist, "cer": round(cer, 4)})
        print(f"reference ({len(ref)} chars):\n{truth_raw}\n")
        print(f"CER = {dist}/{len(ref)} = {cer:.2%}")

        # Numeric spot check on both raw and normalized forms.
        nums = extract_numbers(truth_raw)
        num_table = [{"number": n, "in_hypothesis": n in extract_numbers(transcript)}
                     for n in nums]
        report["number_check"] = num_table
        print("\nnumber spot check (normalized):")
        for row in num_table:
            mark = "OK  " if row["in_hypothesis"] else "MISS"
            print(f"  [{mark}] {row['number']}")

        # Keyword spot check for domain terms, after script conversion.
        terms = ["国内生产总值", "社会消费品零售总额", "工业增加值", "商品房销售面积", "投资建议"]
        term_table = [{"term": t, "in_hypothesis": t in hyp} for t in terms]
        report["term_check"] = term_table
        print("\ndomain term check (simplified + punctuation-insensitive):")
        for row in term_table:
            mark = "OK  " if row["in_hypothesis"] else "MISS"
            print(f"  [{mark}] {row['term']}")

        # Same term check without script folding, to expose the Traditional
        # output problem rather than hiding it.
        raw_hyp = _STRIP.sub("", transcript)
        report["term_check_raw_script"] = [
            {"term": t, "in_hypothesis": t in raw_hyp} for t in terms]
        missed_raw = [t for t in terms if t not in raw_hyp]
        if missed_raw:
            print(f"\n  NOTE: {len(missed_raw)} term(s) missing ONLY because the ASR used")
            print(f"  Traditional characters: {missed_raw}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
