-- asus_compact.lua
-- Compact candidate bubble, modelled after ASUS Smart Input.
--
-- While typing English, the typed word and up to three guesses are shown
-- (Tab takes the first, marked ⇥).
-- While typing, only the best candidate is shown (it is also previewed inline
-- via style/preedit_type: preview). If the input ends with an unfinished
-- syllable (no tone key yet), its Zhuyin reading is shown as the comment,
-- like the blue reading bubble in ASUS Smart Input.
-- Pressing Down (handled in asus_keys.lua) sets the _asus_expanded option and
-- this filter then passes the full candidate list through untouched. The `
-- symbol keyboard (asus_symbols.lua) is always shown in full.
--
-- The keys typed so far follow the comment ("你好　su3cl3", Space shown as
-- ␣), so a key pressed wrongly can be spotted; asus_compact/show_keys: false
-- hides them.
--
-- Must be the last filter.

local EXPANDED = "_asus_expanded"
local EN_GUESSES = 3
local complete = require("asus_predict").complete
local list = require("asus_list")
local email_match = require("asus_email").match

-- Keyboard key -> Zhuyin symbol, from bopomofo.schema.yaml preedit_format.
local KEYS = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-"
local SYMBOLS = {
  "ㄅ", "ㄆ", "ㄇ", "ㄈ", "ㄉ", "ㄊ", "ㄋ", "ㄌ", "ㄍ", "ㄎ", "ㄏ", "ㄐ", "ㄑ", "ㄒ",
  "ㄓ", "ㄔ", "ㄕ", "ㄖ", "ㄗ", "ㄘ", "ㄙ", "ㄧ", "ㄨ", "ㄩ", "ㄚ", "ㄛ", "ㄜ", "ㄝ",
  "ㄞ", "ㄟ", "ㄠ", "ㄡ", "ㄢ", "ㄣ", "ㄤ", "ㄥ", "ㄦ",
}
local KEY_TO_ZHUYIN = {}
for i = 1, #KEYS do
  KEY_TO_ZHUYIN[KEYS:sub(i, i)] = SYMBOLS[i]
end

-- Reading of the trailing syllable that has no tone key yet ("" if none).
local function pending_reading(input)
  local tail = input:match("[^ 6347']*$") or ""
  local out = {}
  for i = 1, #tail do
    local z = KEY_TO_ZHUYIN[tail:sub(i, i)]
    if not z then return "" end
    out[#out + 1] = z
  end
  return table.concat(out)
end

-- CJK Extension B+ and the compatibility supplement (e.g. 𦃕) are almost never
-- intended; librime's charset_filter is a no-op in 1.13.1 when listed in a
-- schema (it requires an empty name_space), so they are dropped here unless
-- the extended_charset option is on.
local function is_rare(text)
  for _, cp in utf8.codes(text) do
    if cp >= 0x20000 then return true end
  end
  return false
end

local MAX_KEYS = 24

local function init(env)
  local v = env.engine.schema.config:get_bool("asus_compact/show_keys")
  env.show_keys = (v == nil) or v
  local ctx = env.engine.context
  if ctx and ctx.update_notifier then
    env.update_conn = ctx.update_notifier:connect(function(ctx)
      if ctx:get_option(EXPANDED) or (ctx:get_property("asus_edit") or "") ~= "" then
        env.raw_active = false
        return
      end
      local seg = ctx.composition:back()
      if seg and seg.menu and env.raw_active then
        seg.selected_index = 1
      end
    end)
  end
end

local function fini(env)
  if env.update_conn then env.update_conn:disconnect() end
end

-- Typed keys for the bubble (the last MAX_KEYS of them).
local function typed(input)
  local s = input:gsub(" ", "␣")
  if #input > MAX_KEYS then
    s = "…" .. input:sub(-MAX_KEYS):gsub(" ", "␣")
  end
  return s
end

local function func(input, env)
  local ctx = env.engine.context
  local seg = ctx.composition:back()
  local keep_rare = ctx:get_option("extended_charset")
  if ctx:get_option(EXPANDED) or (seg and seg:has_tag("asus_symbol")) then
    env.raw_active = false
    -- (The highlighted row may stand in for a shorter candidate, with the
    -- text after it: asus_list.lua.)
    local row, first = -1, nil
    for cand in input:iter() do
      if keep_rare or not is_rare(cand.text) then
        row = row + 1
        first = first or cand
        yield(list.wrap(ctx, row, first, cand))
      end
    end
    return
  end
  local reading = pending_reading(ctx.input:sub(1, ctx.caret_pos))
  local raw_keys = (env.engine.schema.config:get_bool("asus_lang/raw_keys") == true)
    or (ctx and ctx:get_option("raw_keys") == true)
  -- Raw keys mode, an earlier segment of the unfinished part: keys that do
  -- not make one syllable (dc = ㄎ + ㄏ) are split into several segments, and
  -- each one shows its own keys inline (a stray key would show nothing).
  local raw_done  -- keys up to the last tone key, when this applies
  if raw_keys and reading ~= "" and ctx.caret_pos == #ctx.input
      and (ctx:get_property("asus_edit") or "") == "" then
    raw_done = ctx.input:find("[ 6347'][^ 6347']*$") or 0
  end
  -- English: the typed word plus up to EN_GUESSES guesses, like a phone
  -- keyboard's suggestion bar. (Decided by the candidates themselves: this
  -- also runs for earlier segments, so composition:back() is not this one.)
  local english = 0
  for cand in input:iter() do
    if raw_done and english == 0 and cand._end < #ctx.input then
      if cand._end > raw_done and cand.type ~= "english" and cand.type ~= "number" then
        local from = math.max(cand.start, raw_done)
        local _, n = ctx.input:sub(cand.start + 1, from):gsub("[ 6347]", "")
        local stop = utf8.offset(cand.text, n + 1)
        local text = (stop and cand.text:sub(1, stop - 1) or cand.text) .. ctx.input:sub(from + 1, cand._end)
        yield(ShadowCandidate(cand, cand.type, text, cand.comment))
        return
      end
    end
    if english > 0 then
      if cand.type ~= "english" or english > EN_GUESSES then return end
      yield(cand)
      english = english + 1
    elseif cand.type == "english" then
      env.raw_active = false
      yield(cand)
      english = 1
    elseif keep_rare or not is_rare(cand.text) then
      local comment = ""
      if cand.type ~= "english" and cand.comment == "" then
        comment = reading
        -- Autocomplete hint (Tab accepts, see asus_predict.lua).
        local cont = reading == "" and ctx:get_option("prediction") and complete(cand.text)
        if cont then comment = "⇥" .. cont end
      end
      -- A remembered email address starting with the keys typed (Tab types
      -- it, asus_email.lua).
      local mail = cand._end == #ctx.input and email_match(ctx.input)[1]
      if mail then comment = "⇥" .. mail end
      if env.show_keys and cand._end == #ctx.input and ctx.input ~= "" then
        comment = ((comment ~= "") and comment or cand.comment)
        comment = (comment ~= "" and (comment .. "　") or "") .. typed(ctx.input)
      end
      local is_trailing = (cand._end == #ctx.input) and (ctx.caret_pos == #ctx.input)
      if raw_keys and is_trailing and reading ~= "" and cand.type ~= "number"
         and cand.type ~= "edit" and (ctx:get_property("asus_edit") or "") == "" then
        env.raw_active = true
        local tail = ctx.input:sub(1, ctx.caret_pos):match("[^ 6347']*$") or ""
        local prefix = ""
        if #tail > 0 and #cand.text >= #tail and cand.text:sub(-#tail) == tail then
          prefix = cand.text:sub(1, #cand.text - #tail)
        end
        local display_text = prefix ~= "" and (prefix .. "　" .. reading) or reading
        local comment_keys = env.show_keys and typed(ctx.input) or ""
        local cand0 = ShadowCandidate(cand, cand.type, display_text, comment_keys)
        local cand1 = ShadowCandidate(cand, cand.type, cand.text, "")
        yield(cand0)
        yield(cand1)
        return
      else
        env.raw_active = false
      end
      if comment ~= "" then
        yield(ShadowCandidate(cand, cand.type, cand.text, comment))
      else
        yield(cand)
      end
      return  -- compact: best candidate only
    end
  end
end

return { init = init, func = func, fini = fini }
