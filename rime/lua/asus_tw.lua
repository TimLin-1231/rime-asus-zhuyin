-- asus_tw.lua
-- Taiwan spellings first. The language model sometimes builds a mainland
-- word out of single characters (宕機, 坐標, 賬號) where Taiwan writes a
-- word with the same reading (當機, 座標, 帳號). opencc/asus_tw.* lists such
-- words (tools/build_lang_data.py: a real word in essay.txt that McBopomofo's
-- Taiwan corpus never uses, one character away from a common Taiwan word with
-- one of its readings). The best candidate is rewritten when the typed keys
-- are exactly that reading; what the user once picked is left alone.
--
--   filter  lua_filter@*asus_tw  (after asus_typo, before asus_grammar)
-- asus_tw/enabled: false turns it off.

local MAX_LEN = 4

local function chars(s)
  local out = {}
  for _, cp in utf8.codes(s) do out[#out + 1] = utf8.char(cp) end
  return out
end

local cc  -- shared by all sessions

local function init(env)
  local v = env.engine.schema.config:get_bool("asus_tw/enabled")
  env.enabled = (v == nil) or v
  if env.enabled and cc == nil then
    local ok, c = pcall(Opencc, "asus_tw.json")
    cc = ok and c or false
  end
end

-- Character offsets where the language model's words start or end (a word
-- to replace must not cross one: 晚上|一起 has no 上一).
local function boundaries(cand, n)
  local at = { [0] = true, [n] = true }
  local ok, entries = pcall(function() return cand:get_genuine():to_sentence().entrys end)
  if ok and entries then
    local pos = 0
    for _, e in ipairs(entries) do
      pos = pos + utf8.len(e.text)
      at[pos] = true
    end
  end
  return at
end

-- Taiwan spelling of cand's text (syllables syl), or nil.
local function rewrite(cand, syl)
  local text = cand.text
  local cs = chars(text)
  if #cs ~= #syl then return nil end
  local at = boundaries(cand, #cs)
  local learn = require("asus_learn")
  local changed = false
  local i = 1
  while i <= #cs do
    local hit = false
    for len = math.min(MAX_LEN, #cs - i + 1), 2, -1 do
      if not (at[i - 1] and at[i + len - 1]) then goto next end
      local x = table.concat(cs, "", i, i + len - 1)
      local forms = cc:convert_word(x)
      local v = forms and forms[1]
      if v then
        local y, keys = v:match("^(.-),(.*)$")
        local typed = table.concat(syl, "", i, i + len - 1)
        if y and keys:gsub("_", " ") == typed and not learn.chosen(typed, x) then
          local ys = chars(y)
          for j = 1, len do cs[i + j - 1] = ys[j] end
          changed, hit = true, true
          i = i + len
          break
        end
      end
      ::next::
    end
    if not hit then i = i + 1 end
  end
  return changed and table.concat(cs) or nil
end

local function func(input, env)
  local ctx = env.engine.context
  if not env.enabled or not cc or (ctx:get_property("asus_edit") or "") ~= "" then
    for cand in input:iter() do yield(cand) end
    return
  end
  local first, top = true, nil
  for cand in input:iter() do
    if first then
      first = false
      local syl = require("asus_learn").syllables(ctx.input:sub(cand.start + 1, cand._end))
      local text = syl and rewrite(cand, syl)
      if text then
        top = text
        yield(ShadowCandidate(cand, cand.type, text, cand.comment))
        yield(cand)  -- what the language model chose, one pick away
      else
        yield(cand)
      end
    elseif cand.text ~= top then
      yield(cand)
    end
  end
end

return { init = init, func = func }
