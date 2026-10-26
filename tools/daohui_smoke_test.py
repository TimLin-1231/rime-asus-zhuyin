"""Real-window check of the installed 島輝 (DaoHui): types into a new Notepad
with DaoHui active and reads the text back.

    python tools/daohui_smoke_test.py

Takes the keyboard focus for about 20 seconds (do not touch keyboard or
mouse); keys are only sent while its own Notepad is in front. DaoHui is made
the active input method for the session through TSF (as asus_compare.py
does) and stays active afterwards.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asus_compare  # noqa: E402
from tsf_test import VK_RETURN, Stop, Typist, user32  # noqa: E402

VK_TAB, VK_DOWN = 0x09, 0x28
# include/DaoHuiBrand.h in the DaoHui fork (D:\src\daohui)
asus_compare.IMES["daohui"] = ("{FBE69A4F-6AE3-456A-A1B9-F13ACE2ACBD8}",
                               "{9A01A79E-97A6-491A-8E0B-7B54EC00E330}", "daohui.dll")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    back = user32.GetForegroundWindow()
    today = time.localtime()
    roc_date = f"民國{today.tm_year - 1911}年{today.tm_mon}月{today.tm_mday}日"
    cases = [
        # (what is checked, keys/steps, expected text)
        ("plain Zhuyin", [("keys", "su3cl3"), ("vk", VK_RETURN)], "你好"),
        ("Tab switch: ai -> 摸", [("keys", "ai"), ("vk", VK_TAB), ("vk", VK_RETURN)], "摸"),
        ("mixed Chinese/English", [("keys", "ji3m/4pythonql3g6u04"), ("vk", VK_RETURN)], "我用python跑實驗"),
        ("number forms: 1234 Tab 3", [("keys", "1234"), ("vk", VK_TAB), ("keys", "3")], "壹仟貳佰參拾肆"),
        ("date: 今天 Down 5", [("keys", "rup wu0 "), ("vk", VK_DOWN), ("keys", "5")], roc_date),
    ]
    results: list[tuple[str, bool, str]] = []
    pad = None
    try:
        pad = asus_compare.open_with("daohui")
        results.append(("Notepad loaded daohui.dll only", True, ""))
        t = Typist([pad])
        for name, steps, want in cases:
            t.clear()
            for kind, arg in steps:
                if kind == "keys":
                    t.keys(arg)
                else:
                    # arrow keys are extended keys: without the flag the scan
                    # code is the keypad's (Down = KP_2, which types a 2)
                    t.vk(arg, extended=arg in (VK_DOWN,))
            time.sleep(0.5)
            got = pad.text().replace("\r\n", "\n").strip("\n")
            results.append((name, got == want, f"got {got!r}, want {want!r}"))
        t.vk(0x1B)
    except Stop as e:
        print("STOPPED:", e)
    finally:
        if pad:
            pad.close()
        if back:
            user32.SetForegroundWindow(back)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {'' if ok else detail}")
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} passed")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
