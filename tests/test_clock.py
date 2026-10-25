"""The clock and platform checks that keep the Lua side portable
(rime/lua/asus_clock.lua, the named-pipe guard in asus_english.lua).

    python tests/test_clock.py

Runs inside librime (Weasel's rime.dll) like every other test. The macOS /
Linux branches are exercised with stand-ins: a probe processor, appended to
the schema for this test only, swaps package.config (which says the Lua
build is not Windows), io.popen (canned output of date / zsh, nothing is
spawned) and rime_api, loads asus_clock.lua afresh, writes what it saw to
<user dir>/clock_probe.txt and puts everything back. The double-tap timing
itself is covered by test_stage11.py (Shift) and test_tab.py (Tab).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from rime_harness import open_rime  # noqa: E402

PROBE = r'''
local lines = {}
local function check(name, ok, detail)
  lines[#lines + 1] = name .. "\t" .. (ok and "PASS" or "FAIL") .. "\t" .. tostring(detail or "")
end

-- Fake io.popen: answers[i] is what the i-th command prints (nil: popen fails).
local function fake_popen(answers, calls)
  return function(cmd)
    calls[#calls + 1] = cmd
    for k, out in pairs(answers) do
      if cmd:find(k, 1, true) then
        if out == false then return nil end
        return { read = function() return out end, close = function() return true end }
      end
    end
    return { read = function() return "" end, close = function() return true end }
  end
end

local function run_probe()
  local real = require("asus_clock")
  local saved = { config = package.config, popen = io.popen, api = rime_api, clock = real }
  local function fresh(posix, api, answers)
    local calls = {}
    package.config = posix and "/\n;\n?\n!\n-\n" or saved.config
    rime_api = api
    io.popen = fake_popen(answers or {}, calls)
    package.loaded.asus_clock = nil
    local m = require("asus_clock")
    return m, calls
  end
  local ok, err = pcall(function()
    -- This system (Weasel 0.17.4: no get_time_ms).
    local src = real.source()
    check("windows: real clock is os.clock or rime_api", src == "os.clock" or src == "rime_api", src)
    check("windows: windows flag", real.windows == true, tostring(real.windows))

    -- get_time_ms wins everywhere.
    local api = setmetatable({ get_time_ms = function() return 4242 end }, { __index = saved.api })
    local m, calls = fresh(true, api)
    check("get_time_ms: source rime_api", m.source() == "rime_api", m.source())
    check("get_time_ms: now_ms and fast_ms use it", m.now_ms() == 4242 and m.fast_ms() == 4242,
          m.now_ms() .. " " .. m.fast_ms())
    check("get_time_ms: nothing spawned", #calls == 0, #calls)

    -- Windows without get_time_ms: os.clock, as before.
    local noapi = setmetatable({ get_time_ms = false }, { __index = saved.api })
    m, calls = fresh(false, noapi)
    check("windows, no get_time_ms: os.clock", m.source() == "os.clock" and #calls == 0, m.source())

    -- Linux: GNU date answers with milliseconds.
    m, calls = fresh(true, noapi, { ["date "] = "1761370000123\n" })
    check("posix: not windows", m.windows == false, tostring(m.windows))
    check("posix + GNU date: source date", m.source() == "date", m.source())
    check("posix + GNU date: now_ms parsed", m.now_ms() == 1761370000123, m.now_ms())
    local n = #calls
    m.now_ms()
    check("posix: one spawn per now_ms after the probe", #calls == n + 1, #calls - n)
    local f = m.fast_ms()
    check("posix: fast_ms never spawns (CPU time)", #calls == n + 1 and math.abs(f - os.clock() * 1000) < 50, f)

    -- macOS: BSD date has no %3N ("...3N"), zsh's $EPOCHREALTIME answers.
    m, calls = fresh(true, noapi, { ["date "] = "17613700003N\n", ["zsh"] = "1761370000.123456789\n" })
    check("posix + BSD date + zsh: source zsh", m.source() == "zsh", m.source())
    check("posix + zsh: now_ms parsed", m.now_ms() == 1761370000123, m.now_ms())
    m, calls = fresh(true, noapi, { ["date "] = "x", ["zsh"] = "1761370000.5\n" })
    check("posix + zsh: short fraction padded", m.now_ms() == 1761370000500, m.now_ms())

    -- Nothing works (popen fails): whole seconds, no error.
    m, calls = fresh(true, noapi, { ["date "] = false, ["zsh"] = false })
    local t = m.now_ms()
    check("posix, no command: source os.time", m.source() == "os.time", m.source())
    check("posix, no command: now_ms is os.time in ms", math.abs(t - os.time() * 1000) <= 1000, t)
    io.popen = function() error("popen not allowed") end
    package.loaded.asus_clock = nil
    m = require("asus_clock")
    check("posix, popen raises: still a number", type(m.now_ms()) == "number", m.source())

    -- asus_english: named pipes only on Windows.
    local english = require("asus_english")
    local function env_with(windows)
      package.loaded.asus_clock = { windows = windows, now_ms = real.now_ms, fast_ms = real.fast_ms }
      local env = { engine = {
        schema = { config = { get_bool = function() return nil end, get_string = function() return nil end } },
        context = { commit_notifier = { connect = function() return { disconnect = function() end } end } },
      } }
      english.processor.init(env)
      english.processor.fini(env)
      return env
    end
    local w = env_with(true)
    check("english, windows: continuation on, pipe named", w.live == true and w.pipe == "\\\\.\\pipe\\asus_zhuyin_assist",
          tostring(w.live) .. " " .. tostring(w.pipe))
    local p = env_with(false)
    check("english, macOS: continuation off, no pipe", p.live == false and p.pipe == nil,
          tostring(p.live) .. " " .. tostring(p.pipe))
  end)
  package.config, io.popen, rime_api = saved.config, saved.popen, saved.api
  package.loaded.asus_clock = saved.clock
  check("probe ran without a Lua error", ok, err)
end

local function init(env)
  lines = {}  -- (one probe per session; selecting the schema starts another)
  local ok, err = pcall(run_probe)
  if not ok then check("probe ran without a Lua error", false, err) end
  local f = io.open(rime_api.get_user_data_dir() .. "/clock_probe.txt", "w")
  f:write(table.concat(lines, "\n"), "\n")
  f:close()
end

return { init = init, func = function() return 2 end }
'''

PATCH = ("patch:\n  asus_learn/enabled: false\n  translator/enable_user_dict: false\n"
         "  engine/processors/+:\n    - lua_processor@*clock_probe\n")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    r = open_rime("clock", extra={"asus_zhuyin.custom.yaml": PATCH, "lua/clock_probe.lua": PROBE})
    user = Path(r.traits.user_data_dir.decode())
    results: list[tuple[str, bool, str]] = []
    out = user / "clock_probe.txt"
    if not out.exists():
        results.append(("the probe processor ran", False, f"no {out}"))
    else:
        for line in out.read_text(encoding="utf-8").splitlines():
            name, verdict, detail = (line.split("\t") + ["", ""])[:3]
            results.append((name, verdict == "PASS", detail))
    # The session still works after the probe put everything back.
    r.tap("Shift_L"); r.tap("Shift_L")
    results.append(("double Shift still toggles after the probe", r.snapshot().ascii_mode, ""))
    r.close()
    lua_errors = [line for f in r.log_dir.glob("*.log*")
                  for line in f.read_text(encoding="utf-8", errors="replace").splitlines()
                  if line.startswith("E") and "lua" in line.lower()]
    results.append(("no Lua runtime errors in librime log", not lua_errors, lua_errors[0] if lua_errors else ""))
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    print(f"{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
