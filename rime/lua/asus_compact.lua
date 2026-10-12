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
  -- English: the typed word plus up to EN_GUESSES guesses, like a phone
  -- keyboard's suggestion bar. (Decided by the candidates themselves: this
  -- also runs for earlier segments, so composition:back() is not this one.)
  local english = 0
  for cand in input:iter() do
    if english > 0 then
      if cand.type ~= "english" or english > EN_GUESSES then return end
      yield(cand)
      english = english + 1
    elseif cand.type == "english" then
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
      if comment ~= "" then
        yield(ShadowCandidate(cand, cand.type, cand.text, comment))
      else
        yield(cand)
      end
      return  -- compact: best candidate only
    end
  end
end

return { init = init, func = func }
