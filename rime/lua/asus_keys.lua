-- asus_keys.lua
-- Key handling for the ASUS-style Zhuyin schema (asus_zhuyin).
--
--   * A single Shift tap never switches modes. A quick double tap of Shift
--     (either side) or Ctrl+Space toggles ascii_mode. When switching to ascii
--     while composing, the converted Chinese text is committed first, so raw
--     keys are never dumped into the document.
--   * Shift+letter while composing is treated as the plain letter, because
--     uppercase letters are never valid Zhuyin keys (accidental Shift).
--   * Compact menu (see asus_compact.lua): Down expands the full candidate
--     list. While expanded, 1-9 select on the current page, Space confirms the
--     highlighted candidate and Esc collapses back to the compact bubble.
--
-- Must be the first processor so it sees Shift events before ascii_composer.

local kRejected, kAccepted, kNoop = 0, 1, 2

local XK_space = 0x20
local XK_Shift_L, XK_Shift_R = 0xffe1, 0xffe2
local XK_Control_L, XK_Control_R = 0xffe3, 0xffe4
local XK_Escape = 0xff1b
local XK_Down = 0xff54

-- Keys that browse the expanded menu without collapsing it.
local NAV_KEYS = {
  [0xff50] = true, -- Home
  [0xff51] = true, -- Left
  [0xff52] = true, -- Up
  [0xff53] = true, -- Right
  [0xff54] = true, -- Down
  [0xff55] = true, -- Page_Up
  [0xff56] = true, -- Page_Down
  [0xff57] = true, -- End
  [XK_Shift_L] = true, [XK_Shift_R] = true,
  [XK_Control_L] = true, [XK_Control_R] = true,
}

local EXPANDED = "_asus_expanded"
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
  if to_ascii and ctx:is_composing() then
    ctx:commit()
  end
  ctx:set_option("ascii_mode", to_ascii)
end

local function set_expanded(ctx, value)
  if ctx:get_option(EXPANDED) == value then return end
  ctx:set_option(EXPANDED, value)
  if ctx:is_composing() then
    ctx:refresh_non_confirmed_composition()
  end
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
  local ctx = env.engine.context
  ctx:set_option(EXPANDED, false)
  -- Always start the next composition in compact mode, whether the previous
  -- one was committed or cleared (Esc, BackSpace to empty, app focus change).
  -- Otherwise digits of the next syllable would act as select keys.
  env.commit_conn = ctx.commit_notifier:connect(function(c)
    c:set_option(EXPANDED, false)
  end)
  env.update_conn = ctx.update_notifier:connect(function(c)
    if not c:is_composing() and c:get_option(EXPANDED) then
      c:set_option(EXPANDED, false)
    end
  end)
end

local function fini(env)
  if env.commit_conn then env.commit_conn:disconnect() end
  if env.update_conn then env.update_conn:disconnect() end
end

local function func(key, env)
  local ctx = env.engine.context
  local code = key.keycode

  -- Mode switching -------------------------------------------------------
  if code == XK_Shift_L or code == XK_Shift_R then
    if track_shift(env, key) then
      toggle_ascii(ctx)
    end
    return kNoop  -- let ascii_composer see it (configured as noop)
  end
  if key:release() then
    return kNoop
  end
  -- Any other key breaks a tap sequence.
  env.shift_down, env.last_tap_at = nil, nil

  if code == XK_space and key:ctrl() and not key:alt() and not key:shift() then
    toggle_ascii(ctx)
    return kAccepted
  end

  if ctx:get_option("ascii_mode") or not ctx:is_composing() then
    return kNoop
  end

  -- Accidental Shift while composing Zhuyin --------------------------------
  if key:shift() and not key:ctrl() and not key:alt()
      and code >= 0x41 and code <= 0x5a then  -- A-Z
    ctx:push_input(string.char(code + 32))
    return kAccepted
  end

  -- Compact / expanded menu ----------------------------------------------
  local expanded = ctx:get_option(EXPANDED)
  if not expanded then
    if code == XK_Down and key.modifier == 0 and ctx:has_menu() then
      set_expanded(ctx, true)
      return kAccepted
    end
    return kNoop
  end

  if key.modifier == 0 and code >= 0x31 and code <= 0x39 then  -- 1-9
    local seg = ctx.composition:back()
    local page_size = env.engine.schema.page_size
    local page_start = seg.selected_index - seg.selected_index % page_size
    local index = page_start + (code - 0x31)
    if seg.menu:prepare(index + 1) > index then
      ctx:select(index)
      set_expanded(ctx, false)
    end
    return kAccepted  -- never insert the digit as Zhuyin while expanded
  end
  if code == XK_space and key.modifier == 0 then
    ctx:confirm_current_selection()
    set_expanded(ctx, false)
    return kAccepted
  end
  if code == XK_Escape then
    set_expanded(ctx, false)
    return kAccepted
  end
  if not NAV_KEYS[code] then
    set_expanded(ctx, false)  -- typing resumes in compact mode
  end
  return kNoop
end

return { init = init, func = func, fini = fini }
