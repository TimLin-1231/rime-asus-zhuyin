-- asus_compact.lua
-- Compact candidate bubble, modelled after ASUS Smart Input.
--
-- While typing, only the best candidate is shown (it is also previewed inline
-- via style/preedit_type: preview). If the input ends with an unfinished
-- syllable (no tone key yet), its Zhuyin reading is shown as the comment,
-- like the blue reading bubble in ASUS Smart Input.
-- Pressing Down (handled in asus_keys.lua) sets the _asus_expanded option and
-- this filter then passes the full candidate list through untouched.
--
-- Must be the last filter.

local EXPANDED = "_asus_expanded"

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

local function func(input, env)
  local ctx = env.engine.context
  if ctx:get_option(EXPANDED) then
    for cand in input:iter() do
      yield(cand)
    end
    return
  end
  local reading = pending_reading(ctx.input:sub(1, ctx.caret_pos))
  for cand in input:iter() do
    if reading ~= "" and cand.comment == "" then
      yield(ShadowCandidate(cand, cand.type, cand.text, reading))
    else
      yield(cand)
    end
    return  -- compact: best candidate only
  end
end

return { func = func }
