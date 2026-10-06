-- asus_compact.lua
-- Compact candidate bubble, modelled after ASUS Smart Input.
--
-- While typing, only the best candidate is shown (it is also previewed inline
-- via style/preedit_type: preview). If the input ends with an unfinished
-- syllable (no tone key yet), its Zhuyin reading is shown as the comment,
-- like the blue reading bubble in ASUS Smart Input.
-- Pressing Down (handled in asus_keys.lua) sets the _asus_expanded option and
-- this filter then passes the full candidate list through untouched. The `
-- symbol keyboard (asus_symbols.lua) is always shown in full.
--
-- Must be the last filter.

local EXPANDED = "_asus_expanded"
local complete = require("asus_predict").complete

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

local function func(input, env)
  local ctx = env.engine.context
  local seg = ctx.composition:back()
  local keep_rare = ctx:get_option("extended_charset")
  if ctx:get_option(EXPANDED) or (seg and seg:has_tag("asus_symbol")) then
    for cand in input:iter() do
      if keep_rare or not is_rare(cand.text) then yield(cand) end
    end
    return
  end
  local reading = pending_reading(ctx.input:sub(1, ctx.caret_pos))
  for cand in input:iter() do
    if keep_rare or not is_rare(cand.text) then
      local comment = ""
      if cand.type ~= "english" and cand.comment == "" then
        comment = reading
        -- Autocomplete hint (Tab accepts, see asus_predict.lua).
        local cont = reading == "" and ctx:get_option("prediction") and complete(cand.text)
        if cont then comment = "⇥" .. cont end
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

return { func = func }
