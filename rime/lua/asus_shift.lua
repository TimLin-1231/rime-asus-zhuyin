-- asus_shift.lua
-- Double-tap Shift switches between Chinese and English mode.
--
-- A single Shift tap never switches modes; a quick double tap of Shift
-- (either side) toggles ascii_mode. What is being typed is committed first
-- (Chinese as converted, English mode's word as typed), so raw keys are
-- never dumped into the document.
--
-- This has to be the first processor: a tap is a Shift press and release
-- with no other key in between, and every later processor may take a key
-- for itself. When this lived in asus_keys.lua, English mode's letters
-- (asus_english.lua, an earlier processor) never reached it, so Shift+O,
-- Shift+K typed quickly counted as two taps and "OK" ended in Chinese mode.
--
-- Being first, it also tells asus_lang.lua where one key event ends and the
-- next begins (key_tick, for the segmentation guard).
--
--   processor  lua_processor@*asus_shift  (first)

local kNoop = 2
local XK_Shift_L, XK_Shift_R = 0xffe1, 0xffe2
local TAP_MAX_MS = 300     -- a Shift press held longer than this is not a tap
local DOUBLE_TAP_MS = 400  -- max gap between the two taps

-- Wall-clock milliseconds. rime_api.get_time_ms is missing from the
-- librime-lua bundled with Weasel 0.17.4; on Windows the MSVC CRT clock()
-- behind os.clock() measures elapsed wall time (not CPU time), so it is a
-- valid fallback there.
local now_ms
if rime_api.get_time_ms then
  now_ms = function() return rime_api.get_time_ms() end
else
  now_ms = function() return os.clock() * 1000 end
end

local function toggle_ascii(ctx)
  local to_ascii = not ctx:get_option("ascii_mode")
  if ctx:is_composing() then
    ctx:commit()
  end
  ctx:set_option("ascii_mode", to_ascii)
end

-- Returns true when the Shift event completed a double tap.
local function track_shift(env, key)
  local t = now_ms()
  local code = key.keycode
  if not key:release() then
    if key:ctrl() or key:alt() then
      env.shift_down = nil
    elseif env.shift_down ~= code then  -- ignore auto-repeat
      env.shift_down, env.shift_down_at = code, t
    end
    return false
  end
  local is_tap = env.shift_down == code and (t - env.shift_down_at) <= TAP_MAX_MS
  env.shift_down = nil
  if not is_tap then
    env.last_tap_at = nil
    return false
  end
  if env.last_tap_at and (t - env.last_tap_at) <= DOUBLE_TAP_MS then
    env.last_tap_at = nil
    return true
  end
  env.last_tap_at = t
  return false
end

local function init(env)
  env.shift_down, env.shift_down_at, env.last_tap_at = nil, 0, nil
end

local function func(key, env)
  local code = key.keycode
  if not key:release() then require("asus_lang").key_tick() end
  if code == XK_Shift_L or code == XK_Shift_R then
    if track_shift(env, key) then
      toggle_ascii(env.engine.context)
    end
  elseif not key:release() then
    env.shift_down, env.last_tap_at = nil, nil  -- any other key breaks a tap sequence
  end
  return kNoop  -- every key goes on (ascii_composer has Shift configured as noop)
end

return { init = init, func = func, toggle_ascii = toggle_ascii }
