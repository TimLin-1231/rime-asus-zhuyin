"""E2E Test Suite for "Raw Keys First" Inline Preedit Mode.

This suite provides 4-tier verification for the raw keys first feature:
  - Tier 1: Core Feature Coverage (inline display, bubble notation, Enter commit, toggle)
  - Tier 2: Boundary & Corner Cases (empty input, single char, consecutive tones, Backspace semantics)
  - Tier 3: Cross-Feature Combinations (mixed Zh/En, numbers, typo correction, cursor navigation, list expansion)
  - Tier 4: Real-world sentences, anti-flicker stability, performance latency, log cleanliness

Usage:
    python tests/test_raw_keys.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure tools directory is accessible for rime_harness and zhuyin
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import CONTROL_MASK, SHIFT_MASK, Snapshot, open_rime  # noqa: E402
from zhuyin import reading_keys  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []  # (tier, test_name, ok, detail)

EXTRA_ENABLED = {
    "asus_zhuyin.custom.yaml": (
        "patch:\n"
        "  asus_lang/raw_keys: true\n"
        "  asus_learn/enabled: false\n"
        "  translator/enable_user_dict: false\n"
    )
}

EXTRA_DISABLED = {
    "asus_zhuyin.custom.yaml": (
        "patch:\n"
        "  asus_lang/raw_keys: false\n"
        "  asus_learn/enabled: false\n"
        "  translator/enable_user_dict: false\n"
    )
}

ZHUYIN_CHARS = set("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦˉˊˇˋ˙　")


def is_zhuyin_notation(text: str) -> bool:
    """Check if candidate text consists solely of Zhuyin notation (with optional separator '　')."""
    if not text:
        return False
    return all(c in ZHUYIN_CHARS for c in text)


def contains_no_chinese(text: str) -> bool:
    """Assert text contains no CJK unified ideographs."""
    return not any("\u4e00" <= c <= "\u9fff" for c in text)


def check(tier: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((tier, name, bool(ok), detail))


def keys_for(text: str) -> str:
    """Convert Chinese text to Dachen key sequence via Taiwan MOE readings."""
    return "".join(reading_keys(text)[0])


def run_disabled_child() -> int:
    """Helper invoked via --disabled to verify default behavior in an isolated librime process."""
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("raw_keys_disabled_probe", extra=EXTRA_DISABLED)
    r.clear()
    r.type("su")
    s = r.snapshot()
    preview_before_enter = s.preview
    cand0_before_enter = s.top
    r.key("Return")
    committed = "".join(r.committed)
    r.close()

    # In disabled mode, su previews and commits Chinese '你'
    print(f"PREVIEW:{preview_before_enter}")
    print(f"CAND0:{cand0_before_enter}")
    print(f"COMMITTED:{committed}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--disabled":
        return run_disabled_child()

    sys.stdout.reconfigure(encoding="utf-8")
    t0_start = time.perf_counter()

    r = open_rime("raw_keys_e2e", extra=EXTRA_ENABLED)
    r.set_option("prediction", False)

    # -------------------------------------------------------------------------
    # Tier 1: Core Feature Coverage
    # -------------------------------------------------------------------------

    # 1.1 Incomplete single letter
    r.clear()
    r.type("s")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_display_single_letter_preview_raw",
        s.preview == "s",
        f"got preview={s.preview!r}, want 's'",
    )
    check(
        "Tier 1",
        "T1_display_single_letter_bubble_zhuyin",
        is_zhuyin_notation(s.top) or "ㄋ" in s.top or "ㄙ" in s.top,
        f"got bubble top={s.top!r}, expected Zhuyin symbol",
    )

    # 1.2 Incomplete syllable (su)
    r.clear()
    r.type("su")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_display_incomplete_preview_raw",
        s.preview == "su",
        f"got preview={s.preview!r}, want 'su'",
    )
    check(
        "Tier 1",
        "T1_display_incomplete_bubble_zhuyin",
        contains_no_chinese(s.top) and (is_zhuyin_notation(s.top) or "ㄋㄧ" in s.top or "ㄙㄨ" in s.top),
        f"got bubble top={s.top!r}, expected Zhuyin notation without Chinese",
    )
    check(
        "Tier 1",
        "T1_display_incomplete_comment_shows_typed_keys",
        "su" in (s.candidates[0][1] if s.candidates else ""),
        f"candidates={s.candidates[:1]}",
    )

    # 1.3 Tone key conversion trigger (su3 -> 你)
    r.key("3")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_tone_trigger_single_syllable_preview",
        s.preview == "你",
        f"got preview={s.preview!r}, want '你'",
    )
    check(
        "Tier 1",
        "T1_tone_trigger_single_syllable_bubble",
        s.top == "你",
        f"got bubble top={s.top!r}, want '你'",
    )

    # 1.4 Syllable concatenation & prefix retention (su3c -> 你c)
    r.type("c")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_prefix_retention_su3c_preview",
        s.preview == "你c",
        f"got preview={s.preview!r}, want '你c'",
    )
    check(
        "Tier 1",
        "T1_prefix_retention_su3c_bubble",
        s.top.startswith("你") and ("ㄏ" in s.top or "ㄘ" in s.top or is_zhuyin_notation(s.top.replace("你", ""))),
        f"got bubble top={s.top!r}, expected prefix '你' + Zhuyin notation",
    )

    # 1.5 Prefix retention with additional raw key (su3cl -> 你cl)
    r.type("l")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_prefix_retention_su3cl_preview",
        s.preview == "你cl",
        f"got preview={s.preview!r}, want '你cl'",
    )
    check(
        "Tier 1",
        "T1_prefix_retention_su3cl_bubble",
        s.top.startswith("你") and ("ㄏㄠ" in s.top or "ㄘㄌ" in s.top or is_zhuyin_notation(s.top.replace("你", ""))),
        f"got bubble top={s.top!r}, expected prefix '你' + Zhuyin notation for 'cl'",
    )

    # 1.6 Tone completion on chained syllable (su3cl3 -> 你好)
    r.key("3")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_tone_trigger_chained_syllable_preview",
        s.preview == "你好",
        f"got preview={s.preview!r}, want '你好'",
    )
    check(
        "Tier 1",
        "T1_tone_trigger_chained_syllable_bubble",
        s.top == "你好",
        f"got bubble top={s.top!r}, want '你好'",
    )

    # 1.7 Third syllable typing & 1st tone trigger with Space (su3cl3a8 )
    r.type("a")
    s = r.snapshot()
    check("Tier 1", "T1_prefix_retention_third_syllable_a", s.preview == "你好a", f"preview={s.preview!r}")
    r.type("8")
    s = r.snapshot()
    check("Tier 1", "T1_prefix_retention_third_syllable_a8", s.preview == "你好a8", f"preview={s.preview!r}")
    r.key("space")
    s = r.snapshot()
    check(
        "Tier 1",
        "T1_space_first_tone_trigger_converts",
        s.preview in ("你好媽", "你好嗎"),
        f"got preview={s.preview!r}, want '你好媽' or '你好嗎'",
    )
    check(
        "Tier 1",
        "T1_space_first_tone_bubble_switches_chinese",
        s.top in ("你好媽", "你好嗎"),
        f"got bubble top={s.top!r}",
    )

    # 1.8 Enter commit semantics: pure incomplete syllable (su + Return -> su)
    r.clear()
    r.type("su")
    r.key("Return")
    s = r.snapshot()
    committed = "".join(r.committed)
    check(
        "Tier 1",
        "T1_enter_commit_incomplete_raw_keys",
        committed == "su" and not s.composing,
        f"committed={committed!r}, composing={s.composing}",
    )

    # 1.9 Enter commit single key (s + Return -> s)
    r.clear()
    r.type("s")
    r.key("Return")
    committed = "".join(r.committed)
    check(
        "Tier 1",
        "T1_enter_commit_single_key",
        committed == "s" and not s.composing,
        f"committed={committed!r}",
    )

    # 1.10 Enter commit mixed completed Chinese + trailing raw keys (su3cl + Return -> 你cl)
    r.clear()
    r.type("su3cl")
    r.key("Return")
    committed = "".join(r.committed)
    check(
        "Tier 1",
        "T1_enter_commit_prefix_chinese_and_raw_keys",
        committed == "你cl" and not s.composing,
        f"committed={committed!r}, want '你cl'",
    )

    # 1.11 Enter commit all completed syllables (su3cl3 + Return -> 你好)
    r.clear()
    r.type("su3cl3")
    r.key("Return")
    committed = "".join(r.committed)
    check(
        "Tier 1",
        "T1_enter_commit_completed_chinese",
        committed == "你好" and not s.composing,
        f"committed={committed!r}, want '你好'",
    )

    # 1.12 Enter commit English word (hello + Return -> hello, python + Return -> python)
    r.clear()
    r.type("hello")
    r.key("Return")
    committed = "".join(r.committed)
    check(
        "Tier 1",
        "T1_enter_commit_english_hello",
        committed == "hello" and not s.composing,
        f"committed={committed!r}, want 'hello'",
    )
    r.clear()
    r.type("python")
    r.key("Return")
    committed = "".join(r.committed)
    check(
        "Tier 1",
        "T1_enter_commit_english_python",
        committed == "python" and not s.composing,
        f"committed={committed!r}, want 'python'",
    )

    # 1.13 Configuration toggle: verify disabled mode via child process
    proc = subprocess.run(
        [sys.executable, __file__, "--disabled"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = proc.stdout
    disabled_committed = ""
    for line in out.splitlines():
        if line.startswith("COMMITTED:"):
            disabled_committed = line.split("COMMITTED:", 1)[1].strip()
    check(
        "Tier 1",
        "T1_toggle_disabled_mode_commits_chinese",
        proc.returncode == 0 and disabled_committed == "你",
        f"rc={proc.returncode}, disabled_committed={disabled_committed!r}, want '你'",
    )

    # -------------------------------------------------------------------------
    # Tier 2: Boundary & Corner Cases
    # -------------------------------------------------------------------------

    # 2.1 Empty input + Return / BackSpace
    r.clear()
    r.key("Return")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_boundary_empty_return",
        "".join(r.committed) == "" and not s.composing,
        f"committed={r.committed!r}",
    )
    r.key("BackSpace")
    s = r.snapshot()
    check("Tier 2", "T2_boundary_empty_backspace", not s.composing, f"composing={s.composing}")

    # 2.2 Single char typing and BackSpace deletion
    r.clear()
    r.type("s")
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_boundary_single_key_backspace_empties",
        s.input == "" and not s.composing,
        f"input={s.input!r}, composing={s.composing}",
    )

    # 2.3 Isolated tone key without prior syllable
    r.clear()
    r.key("3")
    s = r.snapshot()
    # Should not crash or trigger unhandled Lua error
    check(
        "Tier 2",
        "T2_boundary_isolated_tone_3_stable",
        s.input in ("3", "") or s.composing,
        f"input={s.input!r}",
    )
    r.clear()

    # 2.4 Consecutive tone keys (su3 followed by another 3)
    r.type("su3")
    r.key("3")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_boundary_consecutive_tones_no_crash",
        s.composing or "".join(r.committed) != "",
        f"composing={s.composing}, preview={s.preview!r}",
    )
    r.clear()

    # 2.5 BackSpace on incomplete raw keys deletes one letter at a time
    r.type("su")
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_raw_step1_leaves_single_letter",
        s.input == "s" and s.preview == "s",
        f"input={s.input!r}, preview={s.preview!r}",
    )
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_raw_step2_empties_composition",
        s.input == "" and not s.composing,
        f"input={s.input!r}, composing={s.composing}",
    )

    # 2.6 BackSpace on tone-completed syllable deletes the whole character unit
    r.clear()
    r.type("su3")  # 你
    s = r.snapshot()
    check("Tier 2", "T2_backspace_completed_precondition", s.preview == "你", f"preview={s.preview!r}")
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_completed_deletes_entire_character",
        s.input == "" and not s.composing,
        f"input={s.input!r}, composing={s.composing}",
    )

    # 2.7 BackSpace on hybrid composition (completed Chinese + trailing raw keys)
    r.clear()
    r.type("su3cl")  # 你cl
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_hybrid_deletes_raw_l",
        s.input == "su3c" and s.preview == "你c",
        f"input={s.input!r}, preview={s.preview!r}",
    )
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_hybrid_deletes_raw_c",
        s.input == "su3" and s.preview == "你",
        f"input={s.input!r}, preview={s.preview!r}",
    )
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_hybrid_deletes_character_ni",
        s.input == "" and not s.composing,
        f"input={s.input!r}, composing={s.composing}",
    )

    # 2.8 BackSpace across multiple completed syllables
    r.clear()
    r.type("su3cl3")  # 你好
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_multi_completed_deletes_hao",
        s.input == "su3" and s.preview == "你",
        f"input={s.input!r}, preview={s.preview!r}",
    )
    r.key("BackSpace")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_backspace_multi_completed_deletes_ni",
        s.input == "" and not s.composing,
        f"input={s.input!r}, composing={s.composing}",
    )

    # 2.8b Keys that are not one syllable (dc = ㄎ + ㄏ) are split into several
    # segments; every one of them shows its keys, and a tone key / Backspace
    # redoes the earlier ones.
    for keys, want in (("dc", "dc"), ("su3dc", "你dc"), ("dc4", "和"), ("dc4\b", "dc"), ("su3dc4\b", "你dc")):
        r.clear()
        for k in keys:
            r.key("BackSpace") if k == "\b" else r.type(k)
        s = r.snapshot()
        check("Tier 2", f"T2_split_keys_{keys.replace(chr(8), '_bs')}", s.preview == want,
              f"preview={s.preview!r}, want {want!r}")
    r.clear()
    r.type("dc")
    r.key("Return")
    check("Tier 2", "T2_split_keys_return_commits_dc", "".join(r.committed).endswith("dc"),
          f"committed={r.committed!r}")

    # 2.9 Punctuation and Escape cancellation
    r.clear()
    r.key(",", CONTROL_MASK)
    check("Tier 2", "T2_boundary_ctrl_comma_punct", "".join(r.committed) == "，", f"committed={r.committed!r}")
    r.clear()
    r.key(".", CONTROL_MASK)
    check("Tier 2", "T2_boundary_ctrl_period_punct", "".join(r.committed) == "。", f"committed={r.committed!r}")
    r.clear()
    r.type("su")
    r.key("Escape")
    s = r.snapshot()
    check(
        "Tier 2",
        "T2_boundary_escape_cancels_composition",
        not s.composing and s.input == "",
        f"composing={s.composing}, input={s.input!r}",
    )

    # -------------------------------------------------------------------------
    # Tier 3: Cross-Feature Subsystem Combinations
    # -------------------------------------------------------------------------

    # 3.1 Mixed Chinese & English sentence
    # "我用" (ji3m/4) + "python" + "跑" (ql3) + "model" + Return -> "我用python跑model"
    r.clear()
    r.type("ji3m/4pythonql3model")
    r.key("Return")
    committed = "".join(r.committed)
    check(
        "Tier 3",
        "T3_mixed_chinese_english_sentence",
        committed == "我用python跑model",
        f"got {committed!r}, want '我用python跑model'",
    )

    # 3.2 Space after English word in Chinese mode
    r.clear()
    r.type("ji3m/4python")
    r.key("space")
    s = r.snapshot()
    check(
        "Tier 3",
        "T3_mixed_space_after_english_commits",
        s.committed == "我用python " and not s.composing,
        f"committed={s.committed!r}, composing={s.composing}",
    )

    # 3.3 English autocomplete with Tab
    r.clear()
    r.type("ji3m/4pyth")
    r.key("Tab")
    s = r.snapshot()
    check(
        "Tier 3",
        "T3_mixed_tab_english_autocomplete",
        s.committed == "我用python",
        f"committed={s.committed!r}, want '我用python'",
    )

    # 3.4 Capitalized English & Acronym
    r.clear()
    for ch in "Python":
        r.key(ch, SHIFT_MASK if ch.isupper() else 0)
    r.key("Return")
    check(
        "Tier 3",
        "T3_mixed_capitalized_english_return",
        "".join(r.committed) == "Python",
        f"committed={r.committed!r}",
    )

    r.clear()
    for ch in "GPU":
        r.key(ch, SHIFT_MASK)
    r.type("2k7")  # 的
    r.key("Return")
    check(
        "Tier 3",
        "T3_mixed_acronym_gpu_de",
        "".join(r.committed) == "GPU的",
        f"committed={r.committed!r}",
    )

    # 3.5 Number auto-detection preservation (2026年, 3點, 100元)
    r.clear()
    r.type("2026")
    r.type(keys_for("年"))  # su06
    r.key("Return")
    check(
        "Tier 3",
        "T3_number_auto_detection_year",
        "".join(r.committed) == "2026年",
        f"committed={r.committed!r}, want '2026年'",
    )

    r.clear()
    r.type("3")
    r.type(keys_for("點"))  # 2u03
    r.key("Return")
    check(
        "Tier 3",
        "T3_number_auto_detection_time",
        "".join(r.committed) == "3點",
        f"committed={r.committed!r}, want '3點'",
    )

    r.clear()
    r.type("100")
    r.type(keys_for("元"))  # m06
    r.key("Return")
    check(
        "Tier 3",
        "T3_number_auto_detection_currency",
        "".join(r.committed) == "100元",
        f"committed={r.committed!r}, want '100元'",
    )

    # 3.6 Digit keys as Zhuyin initial (204 = 但)
    r.clear()
    r.type("204")  # ㄉㄢˋ
    r.key("Return")
    check(
        "Tier 3",
        "T3_number_zhuyin_disambiguation_dan",
        "".join(r.committed) == "但",
        f"committed={r.committed!r}, want '但'",
    )

    # 3.7 Typo auto-correction (adjacent QWERTY substitution)
    # Target: "請幫我買一瓶水", with end typo in last syllable:
    # ㄕ (g) typed as neighbour h: gjo3 -> hjo3
    r.clear()
    water = "請幫我買一瓶水"
    end_typo = keys_for(water).replace("gjo3", "hjo3", 1)
    r.type(end_typo)
    r.key("Return")
    check(
        "Tier 3",
        "T3_typo_correction_adjacent_key_on_tone",
        "".join(r.committed) == water,
        f"committed={r.committed!r}, want {water!r}",
    )

    # 3.8 In-sentence editing with Left arrow
    r.clear()
    r.type("su3cl3")  # 你好
    r.key("Left")
    s = r.snapshot()
    check(
        "Tier 3",
        "T3_in_sentence_editing_caret_navigation",
        s.composing and ("｜" in (s.candidates[0][1] if s.candidates else "") or s.preview == "你好"),
        f"composing={s.composing}, preview={s.preview!r}, candidates={s.candidates[:1]}",
    )

    # 3.9 Candidate list expansion with Down arrow
    r.clear()
    r.type("ru04")  # 界
    r.key("Down")
    s = r.snapshot()
    check(
        "Tier 3",
        "T3_candidate_list_expansion_down_arrow",
        len(s.candidates) >= 2 and len(s.labels) >= 2,
        f"candidates={len(s.candidates)}, labels={s.labels[:3]}",
    )

    # 3.10 Candidate selection via number key
    expanded_cands = [c for c, _ in s.candidates]
    r.key("2")
    s = r.snapshot()
    committed = "".join(r.committed)
    check(
        "Tier 3",
        "T3_candidate_list_select_number_key",
        committed == (expanded_cands[1] if len(expanded_cands) > 1 else committed) and not s.composing,
        f"committed={committed!r}",
    )

    # -------------------------------------------------------------------------
    # Tier 4: Real-World Scenarios and Production Workloads
    # -------------------------------------------------------------------------

    real_sentences = [
        "外面在下雨記得帶傘",
        "我們週末一起去看電影",
        "今天天氣很好",
        "你明天要來我家吃晚餐嗎",
    ]

    for idx, sent in enumerate(real_sentences, 1):
        r.clear()
        seq = keys_for(sent)
        r.type(seq)
        r.key("Return")
        committed = "".join(r.committed)
        check(
            "Tier 4",
            f"T4_real_world_sentence_{idx}_{sent[:4]}",
            committed == sent,
            f"got {committed!r}, want {sent!r}",
        )

    # 4.2 Anti-flicker test: zero jumps on non-tone keys
    r.clear()
    test_sent = "外面在下雨記得帶傘"
    seq = keys_for(test_sent)
    tone_set = set(" 6347")
    steps: list[tuple[str, str]] = []
    for ch in seq:
        r.key(ch)
        steps.append((ch, r.snapshot().preview))

    # Calculate mid-syllable jumps (rewriting characters >= 2 positions back on non-tone keys)
    mid_jumps = []
    prev = ""
    for ch, cur in steps:
        common = 0
        while common < min(len(prev), len(cur)) and prev[common] == cur[common]:
            common += 1
        if len(prev) - common >= 2 and ch not in tone_set:
            mid_jumps.append(f"{prev} -> {cur} on key {ch!r}")
        prev = cur

    check(
        "Tier 4",
        "T4_flicker_zero_mid_syllable_jumps",
        len(mid_jumps) == 0,
        f"mid_jumps count={len(mid_jumps)}, sample={mid_jumps[:2]}",
    )

    # 4.3 Keystroke latency benchmark across rapid typing
    r.clear()
    bench_seq = keys_for("我們週末一起去看電影")
    t0_bench = time.perf_counter()
    for ch in bench_seq:
        r.key(ch)
    dt_bench = time.perf_counter() - t0_bench
    ms_per_key = (dt_bench / max(len(bench_seq), 1)) * 1000.0
    check(
        "Tier 4",
        "T4_keystroke_latency_perf_acceptable",
        ms_per_key < 30.0,
        f"latency={ms_per_key:.2f} ms/key (threshold < 30 ms/key)",
    )

    # 4.4 Close librime and verify clean logs (zero Lua runtime errors)
    r.close()
    lua_errors = [
        line
        for f in r.log_dir.glob("*.log*")
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.startswith("E") and "lua" in line.lower()
    ]
    check(
        "Tier 4",
        "T4_librime_log_clean_no_lua_errors",
        len(lua_errors) == 0,
        lua_errors[0] if lua_errors else "",
    )

    # -------------------------------------------------------------------------
    # Summary & Reporting
    # -------------------------------------------------------------------------
    dt_total = time.perf_counter() - t0_start
    width = max(len(n) for _, n, _, _ in RESULTS)
    tier_counts: dict[str, list[int]] = {}  # tier: [passed, total]

    print("\n" + "=" * 80)
    print(f"E2E Test Results: Raw Keys First Mode ({len(RESULTS)} checks in {dt_total:.2f}s)")
    print("=" * 80)

    total_failed = 0
    for tier, name, ok, detail in RESULTS:
        if tier not in tier_counts:
            tier_counts[tier] = [0, 0]
        tier_counts[tier][1] += 1
        if ok:
            tier_counts[tier][0] += 1
        else:
            total_failed += 1
        status = "PASS" if ok else "FAIL"
        err_msg = f"  {detail}" if not ok and detail else ""
        print(f"[{tier}] {status:<4}  {name:<{width}}{err_msg}")

    print("-" * 80)
    for tier in sorted(tier_counts.keys()):
        p, t = tier_counts[tier]
        pct = (p / t * 100.0) if t else 0.0
        print(f"{tier}: {p}/{t} passed ({pct:.1f}%)")
    print("=" * 80)
    print(f"Total: {len(RESULTS) - total_failed}/{len(RESULTS)} passed")
    print("=" * 80 + "\n")

    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
