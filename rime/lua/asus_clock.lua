-- asus_clock.lua
-- Wall-clock milliseconds for timing between keys, on Windows and macOS:
-- double-tap Shift (asus_shift.lua), double Tab (asus_tab.lua).
--
--   now_ms()   wall-clock milliseconds; any origin, only differences count;
--              never runs backwards
--   fast_ms()  never blocks: now_ms() where that is cheap, otherwise process
--              CPU time - right for timing a busy loop (asus_lang.lua's
--              segmentation guard), wrong for the gap between two keys
--   source()   how now_ms() measures: "rime_api", "os.clock", "date",
--              "zsh" or "os.time" (for logs and tests)
--   windows    true when Lua was built for Windows (asus_english.lua's
--              named pipes exist only there)
--
-- Which clock now_ms() uses:
--   1. rime_api.get_time_ms (std::chrono::steady_clock), in librime-lua since
--      2025-06-28 (hchunhui/librime-lua 701cb4e): Squirrel 1.1.x and every
--      librime release since 1.16 bundle it. The librime-lua in Weasel 0.17.4
--      does not have it.
--   2. Windows (Lua built against the Microsoft C runtime; package.config
--      starts with "\"): os.clock(). The MS CRT clock() is the wall time since
--      the CRT started (rime.dll links it statically: when the server loaded
--      rime.dll), not CPU time, so this is what Weasel 0.17.4 has always used.
--      It runs out: clock_t is a 32-bit long, and past LONG_MAX ms (2^31 - 1,
--      24.86 days of server uptime) the UCRT returns (clock_t)-1 for good
--      (ucrt/time/clock.cpp in the Windows SDK), so os.clock() is -0.001 from
--      then on. A clock that stands still makes every two Shift taps with no
--      key between them a double tap, however far apart. So once os.clock()
--      is negative, now_ms() counts os.time() seconds instead (case 3 says
--      what whole seconds cost) and says so once in the log.
--   Either way the result never runs backwards: when the source jumps back
--   (a counter that wrapped, a wall clock that was set back), the time goes
--   on from the last value plus NEW_EPOCH_MS, so two events on either side
--   of the jump never look close together.
--   3. Anywhere else os.clock() is CPU time: it stands still while the input
--      method waits for the next key, so two Shift taps a second apart would
--      look simultaneous. Lua has no other sub-second clock, and os.time()
--      counts whole seconds (taps 100 ms apart across a second boundary look
--      1000 ms apart; taps 900 ms apart within one second look simultaneous).
--      So the time is read from a child process, chosen once by a probe:
--      GNU date "+%s%3N" (Linux), else zsh's $EPOCHREALTIME (macOS: /bin/zsh
--      ships with the system, its date has no sub-second format). A spawn
--      blocks the key for a few milliseconds, but it only happens for the
--      keys that are timed - a Shift press or release, a Tab while composing
--      - never for ordinary typing. If neither command works: os.time().
--      DaoHui for macOS ships a librime-lua with get_time_ms (case 1); this
--      path only keeps an older or self-built librime-lua usable.

local M = {}

local WINDOWS = package.config:sub(1, 1) == "\\"

local function api_ms()
  return rime_api.get_time_ms()
end

local function cpu_ms()
  return os.clock() * 1000
end

-- Gap that a backwards jump of the source is turned into (more than any
-- timing that a caller compares: 300-400 ms taps, a 1 s guard window).
local NEW_EPOCH_MS = 60000

-- raw() made monotonic: a backwards jump starts a new epoch NEW_EPOCH_MS on.
local function monotonic(raw)
  local offset, last = 0, nil
  return function()
    local t = raw() + offset
    if last and t < last then
      offset = offset + (last - t) + NEW_EPOCH_MS
      t = last + NEW_EPOCH_MS
    end
    last = t
    return t
  end
end

local crt_out = false  -- os.clock() has run out (clock() returned -1)

local function crt_ms()
  local c = os.clock()
  if c >= 0 then return c * 1000 end
  if not crt_out then
    crt_out = true
    if log then
      log.warning("asus_clock.lua: os.clock() ran out (the server has run for 24.8 days);"
        .. " double Shift / double Tab are timed in whole seconds until it restarts")
    end
  end
  return os.time() * 1000
end

-- Output of a shell command, or nil.
local function run(cmd)
  local ok, f = pcall(io.popen, cmd)
  if not ok or not f then return nil end
  local out = f:read("*a")
  f:close()
  return out
end

-- Child-process clocks: command, parser of its output (-> ms or nil).
local COMMANDS = {
  { name = "date", cmd = "date +%s%3N 2>/dev/null",
    parse = function(out)
      local ms = out and out:match("^(%d+)%s*$")
      return ms and #ms >= 13 and tonumber(ms) or nil  -- BSD date prints "...3N"
    end },
  { name = "zsh", cmd = "/bin/zsh -fc 'zmodload zsh/datetime && print -r -- $EPOCHREALTIME' 2>/dev/null",
    parse = function(out)
      local s, frac = (out or ""):match("^(%d+)%.(%d+)")
      return s and tonumber(s) * 1000 + tonumber((frac .. "000"):sub(1, 3)) or nil
    end },
}

local chosen  -- the COMMANDS entry that worked, or false: none did

local function spawn_ms()
  if chosen == nil then
    chosen = false
    for _, c in ipairs(COMMANDS) do
      if c.parse(run(c.cmd)) then chosen = c; break end
    end
    if not chosen and log then
      log.warning("asus_clock.lua: no millisecond clock (rime_api.get_time_ms, date, zsh);"
        .. " double Shift / double Tab are timed in whole seconds")
    end
  end
  local ms = chosen and chosen.parse(run(chosen.cmd))
  return ms or os.time() * 1000  -- (same origin as both commands: the epoch)
end

local now, fast, source
if rime_api and rime_api.get_time_ms then
  now, fast, source = api_ms, api_ms, "rime_api"
elseif WINDOWS then
  now = monotonic(crt_ms)
  fast, source = now, "os.clock"
else
  now, fast, source = monotonic(spawn_ms), cpu_ms, nil
end

M.now_ms, M.fast_ms = now, fast
M.source = function()
  if source == "os.clock" and crt_out then return "os.time" end
  if source then return source end
  spawn_ms()
  return chosen and chosen.name or "os.time"
end
M.windows = WINDOWS

return M
