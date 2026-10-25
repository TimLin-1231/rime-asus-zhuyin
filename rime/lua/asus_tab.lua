-- asus_tab.lua
-- Tab switches the last word between English letters and Chinese, as asked
-- by the user (ai -> 摸 -> ai); a quick double Tab completes, as Tab alone
-- did before (pyth -> python, a remembered email address, the Chinese
-- continuation ⇥).
--
--   * Letters still waiting for a tone key (raw keys shown inline, asus_lang
--     raw_keys): Tab adds the first tone (Space), so ai becomes 摸 and
--     su3ai 你摸; Tab again takes it away.
--   * An English word (pyth, hello, wifi): Tab has its keys read as Zhuyin;
--     Tab again, English again.
--   * Chinese typed with letter keys only (ai shown as 麼 when raw keys are
--     off): Tab shows the letters; Tab again, Chinese again.
--   The reading is forced through the property asus_force, which the
--   segmentor of asus_lang.lua obeys while the input still starts with what
--   it was (typing on keeps the switch); a commit or an empty input drops it.
--   * Nothing to switch: Tab does nothing (it no longer completes on its own).
--   * Tab again within double_ms of the first: the first one is undone and
--     Tab goes on to asus_predict, which completes as before. (One that
--     nothing completes is swallowed by asus_keys.lua.)
-- Tab on a remembered email address (asus_email.lua) and on a number at
-- the end (asus_quick.lua: its forms) is theirs, as before: both come
-- first in the chain.
-- Only while composing with the caret at the end and the list closed; not
-- in English mode, the ` symbol keyboard or at the editing cursor. After a
-- commit Tab still accepts a prediction (asus_predict.lua): nothing is
-- being composed then.
--
--   processor  lua_processor@*asus_tab  (after asus_email and asus_quick,
--              before asus_predict)
-- Schema option asus_tab/double_ms (default 400).

local kAccepted, kNoop = 1, 2
local XK_Tab = 0xff09
local EXPANDED = "_asus_expanded"
local STATE = "asus_tab"  -- "<input after the switch>\t<how>": how = "space" | "force"
local FORCE = "asus_force"  -- read by asus_lang.lua's segmentor

local zy = require("asus_zhuyin_data")

-- Wall-clock milliseconds (see asus_shift.lua: os.clock is wall time on MSVC).
local now_ms
if rime_api.get_time_ms then
  now_ms = function() return rime_api.get_time_ms() end
else
  now_ms = function() return os.clock() * 1000 end
end

local function is_cjk(s)
  local any = false
  for _, cp in utf8.codes(s) do
    if cp < 0x3400 or cp > 0x9fff then return false end
    any = true
  end
  return any
end

local function is_letters(s)
  return s:match("^[%a][%a'%-]*$") ~= nil
end

-- Plan the input afresh: drops every selection (as asus_lang.finalize does).
local function replan(ctx)
  local input = ctx.input
  ctx.input = input:sub(1, #input - 1)
  ctx.input = input
end

-- Keys after the last tone key, if adding the first tone makes them end in a
-- syllable (the unfinished syllable is still shown as typed).
local function waiting_for_tone(input)
  local tail = input:match("([^ 6347]*)$")
  if tail == "" then return false end
  for len = math.min(4, #tail), 1, -1 do
    if zy.toned[tail:sub(-len) .. " "] then return true end
  end
  return false
end

-- Switch the last word; returns the state to remember, or nil if there was
-- nothing to switch.
local function switch(env)
  local ctx = env.engine.context
  local input = ctx.input
  local seg = ctx.composition:back()
  if not seg or not seg.menu or seg._end ~= #input then return nil end
  local top = seg:get_candidate_at(seg.selected_index)
  local shown = top and top.text or ""
  local keys = input:sub(seg.start + 1, seg._end)
  local kind
  if seg:has_tag("asus_en") then
    -- The word's keys read as Zhuyin. With raw keys shown, keys without a
    -- tone after them would still show as typed: the first tone is added,
    -- as for ai -> 摸.
    if require("asus_lang").load_raw_keys(env) and input:sub(-1):match("%a") then
      ctx:set_property(FORCE, table.concat({ input, seg.start, seg._end + 1, "zh" }, "\t"))
      ctx:push_input(" ")
      return ctx.input .. "\tspaceforce"
    end
    kind = "zh"
  elseif not is_cjk(shown) and waiting_for_tone(input) then
    -- (A raw-keys stand-in: Zhuyin in the bubble, the keys inline.)
    ctx:push_input(" ")
    return ctx.input .. "\tspace"
  elseif is_cjk((shown:gsub("　", ""))) and is_letters(keys) then
    kind = "en"  -- Chinese typed without tone keys (麼 for ai): the letters
  else
    return nil
  end
  -- asus_lang.lua's segmentor reads the span that way from now on.
  ctx:set_property(FORCE, table.concat({ input, seg.start, seg._end, kind }, "\t"))
  replan(ctx)
  return ctx.input .. "\tforce"
end

-- Undo the switch remembered as `state` (it must still match the input).
local function undo(ctx, state)
  local at, how = state:match("^(.*)\t(%a+)$")
  if at ~= ctx.input then return false end
  if how == "space" then
    ctx:pop_input(1)
  elseif how == "spaceforce" then
    ctx:set_property(FORCE, "")
    ctx:pop_input(1)
  else
    ctx:set_property(FORCE, "")
    replan(ctx)
  end
  return true
end

local function init(env)
  local ms = env.engine.schema.config:get_int("asus_tab/double_ms")
  env.double_ms = (ms and ms > 0) and ms or 400
  env.last_at = nil
  local ctx = env.engine.context
  env.commit_conn = ctx.commit_notifier:connect(function(c)
    c:set_property(STATE, "")
    c:set_property(FORCE, "")
  end)
  -- Emptied (Esc, BackSpace): the next input starts afresh.
  env.update_conn = ctx.update_notifier:connect(function(c)
    if c.input == "" and (c:get_property(FORCE) or "") ~= "" then
      c:set_property(STATE, "")
      c:set_property(FORCE, "")
    end
  end)
end

local function fini(env)
  if env.commit_conn then env.commit_conn:disconnect() end
  if env.update_conn then env.update_conn:disconnect() end
end

local function func(key, env)
  local ctx = env.engine.context
  if key:release() then return kNoop end
  if key.keycode ~= XK_Tab or key.modifier ~= 0 then
    env.last_at = nil  -- a double Tab is two Tabs in a row
    return kNoop
  end
  if not ctx:is_composing() or ctx.input == "" or ctx:get_option("ascii_mode") or ctx:get_option(EXPANDED)
      or ctx.input:sub(1, 1) == "`" or ctx.caret_pos ~= #ctx.input
      or (ctx:get_property("asus_edit") or "") ~= "" then
    return kNoop
  end
  local t = now_ms()
  local state = ctx:get_property(STATE) or ""
  -- A quick second Tab: back to what was shown, then the old Tab (complete).
  if env.last_at and t - env.last_at <= env.double_ms then
    env.last_at = nil
    if state ~= "" then undo(ctx, state) end
    ctx:set_property(STATE, "")
    return kNoop
  end
  env.last_at = t
  if state ~= "" and undo(ctx, state) then
    ctx:set_property(STATE, "")
    return kAccepted
  end
  ctx:set_property(STATE, switch(env) or "")
  return kAccepted
end

return { init = init, func = func, fini = fini }
