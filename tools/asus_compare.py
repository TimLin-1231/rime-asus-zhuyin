"""Black-box comparison: the same keys typed into a real Notepad, once with
華碩注音 (ASUS Smart Input) and once with 小狼毫 (this project), committed
text read back and compared.

Dev sets by default; --heldout uses the held-out *_test sets instead and then
prints and stores totals only (no texts, no keys). Each case: Esc,
clear, type the keys, Enter, read the text. Both input methods are switched
to in turn through TSF (ITfInputProcessorProfileMgr::ActivateProfile for the
session); 小狼毫 is made active again at the end, whatever happens.

Takes the keyboard focus for several minutes: do not touch keyboard or mouse.
Keys are only sent while its own Notepad is in the foreground; otherwise it
stops. Both input methods will see (and may learn from) the typed sentences.

    python tools/asus_compare.py                   both, all cases
    python tools/asus_compare.py --limit 3         a short trial
    python tools/asus_compare.py --ime asus        one of them only
    python tools/asus_compare.py --heldout         held-out sets, totals only
    python tools/asus_compare.py --switch-only weasel   just switch

Writes reports/asus_compare.tsv (set, keys, expected, asus, rime) and prints
accuracy per set plus the cases where the two differ; with --heldout only
reports/asus_compare_heldout.json (counts per set).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import random
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_ime import inject_typo, load_set  # noqa: E402
from tsf_test import VK_RETURN, Notepad, Stop, Typist, user32  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "tests" / "eval"
OUT = ROOT / "reports" / "asus_compare.tsv"
OUT_HELDOUT = ROOT / "reports" / "asus_compare_heldout.json"

LANG_ZH_TW = 0x0404
IMES = {
    # name: (TIP clsid, profile guid, DLL file name loaded into the app)
    "asus": ("{D9012138-88BF-46F4-B599-E69D8BA2FED6}", "{2939307A-285F-47F9-B909-8089DF8A7FDD}", "asusime.dll"),
    "weasel": ("{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}", "{3D02CAB6-2B8E-4781-BA20-1C9267529467}", "weasel.dll"),
}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        g = cls()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g


CLSID_TF_InputProcessorProfiles = "{33C53A50-F456-4884-B049-85FD643ECFED}"
IID_ITfInputProcessorProfileMgr = "{71C6E74C-0F28-11D8-A82A-00065B84435C}"
TF_PROFILETYPE_INPUTPROCESSOR = 1
TF_IPPMF_FORSESSION = 0x20000000
TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE = 0x4


def activate(name: str) -> None:
    """Make an input method the active one for the whole desktop session."""
    clsid, profile, _ = IMES[name]
    ole32 = ctypes.oledll.ole32
    ole32.CoInitialize(None)
    mgr = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(GUID.of(CLSID_TF_InputProcessorProfiles)), None, 1,
                           ctypes.byref(GUID.of(IID_ITfInputProcessorProfileMgr)), ctypes.byref(mgr))
    vtbl = ctypes.cast(mgr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, wt.DWORD, wt.WORD,
                               ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.c_void_p, wt.DWORD)
    activate_profile = proto(vtbl[3])
    try:
        activate_profile(mgr, TF_PROFILETYPE_INPUTPROCESSOR, LANG_ZH_TW, ctypes.byref(GUID.of(clsid)),
                         ctypes.byref(GUID.of(profile)), None,
                         TF_IPPMF_FORSESSION | TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE)
    finally:
        ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)(vtbl[2])(mgr)  # Release
    time.sleep(0.5)


def loaded_tips(pid: int) -> set[str]:
    names = {dll for _, _, dll in IMES.values()}
    try:
        return {Path(m.path).name.lower() for m in psutil.Process(pid).memory_maps()} & names
    except psutil.Error:
        return set()


def build_cases(heldout: bool = False) -> list[tuple[str, str, str]]:
    sfx = "_test" if heldout else ""
    base = load_set(EVAL / f"sentences{sfx}.tsv")
    if heldout:  # same typos as eval_ime's test_typo
        typo = [(inject_typo(k, random.Random(20261007 + i)), t) for i, (k, t) in enumerate(base)]
    else:
        rng = random.Random(20261006)  # same typos as eval_ime's dev_typo
        typo = [(inject_typo(k, rng), t) for k, t in base]
    picks = [
        ("clean", base[0::3][:30]),
        ("typo", typo[1::3][:15]),
        ("mixed", load_set(EVAL / f"mixed{sfx}.tsv")[0::2][:15]),
        ("particles", load_set(EVAL / f"particles{sfx}.tsv")[0::4][:10]),
        ("numbers", load_set(EVAL / f"numbers{sfx}.tsv")[0::4][:10]),
        ("numbers_amb", load_set(EVAL / f"numbers_amb{sfx}.tsv")[0::3][:10]),
    ]
    return [(name, k, t) for name, rows in picks for k, t in rows]


def open_with(name: str) -> Notepad:
    """A fresh Notepad that loaded only this input method. A session switch
    does not always reach the next new window (it seems to follow the
    foreground thread), so switch again with Notepad in front and retry."""
    want = {IMES[name][2]}
    for _ in range(4):
        activate(name)
        pad = Notepad()
        pad.activate()
        time.sleep(0.5)
        tips = loaded_tips(pad.proc.pid)
        if tips == want:
            return pad
        activate(name)  # now with this Notepad in front
        pad.close()
        time.sleep(0.5)
    raise Stop(f"could not switch to {name}: a new Notepad loaded {sorted(tips)}")


def run(name: str, cases: list[tuple[str, str, str]]) -> list[str]:
    pad = open_with(name)
    out: list[str] = []
    try:
        t = Typist([pad])
        t.clear()
        t.keys("su3cl3"); t.vk(VK_RETURN); time.sleep(0.4)  # ㄋㄧˇ ㄏㄠˇ
        got = pad.text().strip()
        tips = loaded_tips(pad.proc.pid)
        if got != "你好":
            raise Stop(f"{name}: su3cl3 + Enter gave {got!r} (loaded: {sorted(tips)}); "
                       f"not in Chinese mode, or the switch did not happen")
        if tips != {IMES[name][2]}:
            raise Stop(f"{name}: Notepad loaded {sorted(tips)}, expected only {IMES[name][2]}")
        for i, (_, keys, _) in enumerate(cases, 1):
            t.clear()
            t.keys(keys)
            t.vk(VK_RETURN)
            time.sleep(0.4)
            out.append(pad.text().replace("\r\n", "\n").strip("\n"))
            print(f"\r{name}: {i}/{len(cases)}", end="", flush=True)
        t.vk(0x1B)
        print()
    finally:
        pad.close()
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ime", choices=["asus", "weasel", "both"], default="both")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--heldout", action="store_true", help="held-out sets, totals only")
    ap.add_argument("--switch-only", choices=list(IMES))
    args = ap.parse_args()
    if args.switch_only:
        activate(args.switch_only)
        return 0

    cases = build_cases(args.heldout)
    if args.limit:
        cases = cases[:args.limit]
    back = user32.GetForegroundWindow()
    results: dict[str, list[str]] = {}
    try:
        for name in (["asus", "weasel"] if args.ime == "both" else [args.ime]):
            results[name] = run(name, cases)
    except Stop as e:
        print("\nSTOPPED:", e)
    finally:
        activate("weasel")
        if back:
            user32.SetForegroundWindow(back)

    asus = results.get("asus", [])
    rime = results.get("weasel", [])
    if not asus and not rime:
        return 1
    sets = list(dict.fromkeys(s for s, _, _ in cases))
    totals = {}
    for s in sets + ["all"]:
        idx = [i for i, c in enumerate(cases) if s in ("all", c[0])]
        acc = lambda got: sum(1 for i in idx if i < len(got) and got[i] == cases[i][2])
        totals[s] = {"n": len(idx), "asus": acc(asus), "rime": acc(rime)}
    print(f"\n{'set':<12} {'n':>3} {'ASUS':>6} {'Rime':>6}")
    for s, v in totals.items():
        print(f"{s:<12} {v['n']:>3} {v['asus']:>6} {v['rime']:>6}")
    if args.heldout:
        record = {"date": time.strftime("%Y-%m-%d %H:%M"), "typed": {"asus": len(asus), "rime": len(rime)},
                  "totals": totals}
        OUT_HELDOUT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nwritten: {OUT_HELDOUT.relative_to(ROOT)} (totals only)")
        return 0
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write("set\tkeys\texpected\tasus\trime\n")
        for i, (s, k, t) in enumerate(cases):
            a = asus[i] if i < len(asus) else ""
            r = rime[i] if i < len(rime) else ""
            f.write(f"{s}\t{k}\t{t}\t{a}\t{r}\n")

    if asus and rime:
        groups = {"ASUS right, Rime wrong": [], "Rime right, ASUS wrong": [], "both wrong": []}
        for i, (s, k, t) in enumerate(cases[:min(len(asus), len(rime))]):
            a_ok, r_ok = asus[i] == t, rime[i] == t
            if a_ok and not r_ok:
                groups["ASUS right, Rime wrong"].append(i)
            elif r_ok and not a_ok:
                groups["Rime right, ASUS wrong"].append(i)
            elif not a_ok and not r_ok:
                groups["both wrong"].append(i)
        for title, idx in groups.items():
            print(f"\n== {title} ({len(idx)})")
            for i in idx:
                s, k, t = cases[i]
                print(f"[{s}] {t}\n   ASUS {asus[i]}\n   Rime {rime[i]}")
    print(f"\nwritten: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
