-- asus_shortcuts.lua
-- Text expansion ("快捷文字本"): a Zhuyin abbreviation expands to long text.
--
-- File: <user data dir>/shortcuts.txt (local only, not in git), one entry per
-- line:   abbreviation<TAB>expansion
-- The abbreviation is Zhuyin, e.g. "ㄉㄓ" (initials only) or "ㄉㄧˋ ㄓˇ".
-- "\n" in the expansion becomes a line break. Lines starting with # are
-- comments. Changes take effect after redeploy.
-- When the whole input matches an abbreviation, the expansion is offered as
-- the first candidate (so it is what the compact bubble and inline preview
-- show); the normal conversion stays available below it.

local SYMBOLS = {
  "ㄅ", "ㄆ", "ㄇ", "ㄈ", "ㄉ", "ㄊ", "ㄋ", "ㄌ", "ㄍ", "ㄎ", "ㄏ", "ㄐ", "ㄑ", "ㄒ",
  "ㄓ", "ㄔ", "ㄕ", "ㄖ", "ㄗ", "ㄘ", "ㄙ", "ㄧ", "ㄨ", "ㄩ", "ㄚ", "ㄛ", "ㄜ", "ㄝ",
  "ㄞ", "ㄟ", "ㄠ", "ㄡ", "ㄢ", "ㄣ", "ㄤ", "ㄥ", "ㄦ", "ˉ", "ˊ", "ˇ", "ˋ", "˙",
}
local KEYS = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/- 6347"
local TO_KEY = {}
for i, s in ipairs(SYMBOLS) do TO_KEY[s] = KEYS:sub(i, i) end

-- "ㄉㄧˋ ㄓˇ" -> "2u45" ; returns nil if a character is not Zhuyin.
local function zhuyin_to_keys(z)
  local out = {}
  for _, ch in utf8.codes(z) do
    local c = utf8.char(ch)
    if c ~= " " and c ~= "\u{3000}" then
      local k = TO_KEY[c]
      if not k then return nil end
      out[#out + 1] = k
    end
  end
  return table.concat(out)
end

local function load(path)
  local map = {}
  local f = io.open(path, "r")
  if not f then return map end
  for line in f:lines() do
    line = line:gsub("\r$", "")
    if line ~= "" and not line:match("^#") then
      local abbr, text = line:match("^([^\t]+)\t(.+)$")
      local keys = abbr and zhuyin_to_keys(abbr)
      if keys and keys ~= "" then
        text = text:gsub("\\n", "\n")
        local list = map[keys] or {}
        list[#list + 1] = text
        map[keys] = list
      end
    end
  end
  f:close()
  return map
end

local function init(env)
  env.map = load(rime_api.get_user_data_dir() .. "/shortcuts.txt")
end

local function func(input, seg, env)
  local list = env.map[input]
  if not list then return end
  for _, text in ipairs(list) do
    local cand = Candidate("shortcut", seg.start, seg._end, text, "〔快捷〕")
    cand.quality = 1000
    yield(cand)
  end
end

return { init = init, func = func, load = load }
