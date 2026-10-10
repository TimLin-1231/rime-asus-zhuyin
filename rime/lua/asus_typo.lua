-- asus_typo.lua
-- Corrects a key typed one key off (ㄈ -> ㄌ: 吃飯 -> 喫爛), like a phone
-- keyboard's autocorrect, using the language model's sentence weight.
--
-- For every syllable of a fully toned Chinese segment, each non-tone key is
-- tried as a neighbouring key on the keyboard. A variant that is still a
-- valid syllable is scored on a window of syllables around it (the log
-- weight of script_translator's best sentence); if it beats the typed keys
-- by more than asus_typo/margin, the syllable is corrected. The corrected
-- sentence becomes the first candidate and what was typed stays second, one
-- pick away. Window weights are cached, so only new syllables cost queries,
-- and nothing runs until the last syllable has its tone key. A window with
-- text the user once picked for those keys (a name picked character by
-- character, a term; asus_learn.lua remembers picks) is never "corrected".
--
-- Keys that are no syllable at all (asus_lang.lua tags such segments
-- asus_fix) get no candidate from script_translator, so the translator here
-- converts them: each typo syllable is replaced by the variant (neighbour
-- key, or one key dropped) that gives the best sentence weight.
--
--   translator  lua_translator@*asus_typo*translator
--   filter      lua_filter@*asus_typo*filter  (before asus_grammar)
-- asus_typo/enabled: false turns it off.

local zy = require("asus_zhuyin_data")

local ROWS = { "1234567890-", "qwertyuiop", "asdfghjkl;", "zxcvbnm,./" }
local TONE = { [" "] = true, ["6"] = true, ["3"] = true, ["4"] = true, ["7"] = true }
local WINDOW = 2          -- syllables on each side of the one being checked
local MAX_CACHE = 4000

-- Keys next to each key (same or adjacent row, at most one column away),
-- never a tone key.
-- (Built in keyboard order, not pairs(): ties between variants must not
-- depend on Lua's hash order.)
local NEAR = {}
do
  local keys = {}
  for r, row in ipairs(ROWS) do
    for i = 1, #row do keys[#keys + 1] = { row:sub(i, i), r, i } end
  end
  for _, a in ipairs(keys) do
    local list = {}
    for _, b in ipairs(keys) do
      if b[1] ~= a[1] and math.abs(a[2] - b[2]) <= 1 and math.abs(a[3] - b[3]) <= 1 and not TONE[b[1]] then
        list[#list + 1] = b[1]
      end
    end
    NEAR[a[1]] = list
  end
end

local function syllables(keys)
  return require("asus_learn").syllables(keys)
end

-- Syllables tok can become by changing one non-tone key to a neighbour, or
-- by dropping one non-tone key (an extra key pressed with the right one).
-- None when only the tone is unusual (ㄍㄟ˙): librime converts those itself.
local function variants(tok)
  local out, seen = {}, {}
  local body = tok:sub(1, -2)
  if TONE[tok:sub(-1)] and #body > 0 then
    for t in pairs(TONE) do
      if zy.toned[body .. t] then return out end
    end
  end
  local function add(v)
    if zy.toned[v] and not seen[v] then seen[v] = true; out[#out + 1] = v end
  end
  for p = 1, #tok do
    local k = tok:sub(p, p)
    if not TONE[k] then
      for _, n in ipairs(NEAR[k] or {}) do add(tok:sub(1, p - 1) .. n .. tok:sub(p + 1)) end
      add(tok:sub(1, p - 1) .. tok:sub(p + 1))
    end
  end
  return out
end

local function init(env)
  local config = env.engine.schema.config
  local on = config:get_bool("asus_typo/enabled")
  env.enabled = (on == nil) or on
  env.margin = tonumber(config:get_string("asus_typo/margin") or "") or 30.0
  -- Near the end of the input the window has no right context, so the gain
  -- a real typo shows there is smaller (喫爛 at the very end).
  env.end_margin = tonumber(config:get_string("asus_typo/end_margin") or "") or 18.0
  env.debug = config:get_bool("asus_typo/debug") or false
  env.min_window = tonumber(config:get_string("asus_typo/min_window") or "") or 3
  if env.enabled then
    local ok, tr = pcall(function()
      return Component.Translator(env.engine, "", "script_translator@translator")
    end)
    env.tr = ok and tr or nil
  end
  env.cache, env.cached = {}, 0
  -- Commits change Rime's user dictionary (and so the weights): start over.
  env.conn = env.engine.context.commit_notifier:connect(function()
    env.cache, env.cached = {}, 0
  end)
end

local function fini(env)
  if env.conn then env.conn:disconnect() end
end

-- Log weight of the best conversion of keys (nil if nothing covers them).
local function weight(env, keys)
  local w = env.cache[keys]
  if w ~= nil then return w or nil end
  w = false
  local seg = Segment(0, #keys)
  seg.tags = Set({ "abc" })
  local t = env.tr:query(keys, seg)
  if t then
    for c in t:iter() do
      if c._end == #keys then
        local ok, ph = pcall(function() return c:get_genuine():to_phrase() end)
        if ok and ph then w = ph.weight end
      end
      break
    end
  end
  if env.cached >= MAX_CACHE then env.cache, env.cached = {}, 0 end
  env.cache[keys] = w
  env.cached = env.cached + 1
  return w or nil
end

-- Did the user pick any of the shown text in syllables lo..hi before (a name
-- picked character by character, a term)? Then leave the window alone.
local function picked_nearby(syl, cs, lo, hi)
  if not cs then return false end
  local learn = require("asus_learn")
  for j = lo, hi do
    for len = 1, math.min(4, hi - j + 1) do
      if learn.chosen(table.concat(syl, "", j, j + len - 1), table.concat(cs, "", j, j + len - 1)) then
        return true
      end
    end
  end
  return false
end

-- Best correction of syllable i: the new syllable keys, or nil.
local function correct_syllable(env, syl, i, cs)
  local lo, hi = math.max(1, i - WINDOW), math.min(#syl, i + WINDOW)
  if hi - lo + 1 < env.min_window then return nil end  -- too little context
  if picked_nearby(syl, cs, lo, hi) then return nil end
  local before = table.concat(syl, "", lo, i - 1)
  local after = table.concat(syl, "", i + 1, hi)
  local base = weight(env, before .. syl[i] .. after)
  if not base then return nil end
  local s = syl[i]
  local margin = (hi - i < WINDOW) and env.end_margin or env.margin
  local best, best_w
  for p = 1, #s do
    local k = s:sub(p, p)
    if not TONE[k] then
      for _, n in ipairs(NEAR[k] or {}) do
        local v = s:sub(1, p - 1) .. n .. s:sub(p + 1)
        if zy.toned[v] then
          local w = weight(env, before .. v .. after)
          if w and w > base + margin and (not best_w or w > best_w) then
            best, best_w = v, w
          end
        end
      end
    end
  end
  if env.debug and best then
    log.info(string.format("asus_typo cand i=%d n=%d %s->%s base=%.2f gain=%.2f win=%d keys=%s",
      i, #syl, s, best, base, best_w - base, hi - lo + 1, table.concat(syl)))
  end
  return best, best_w and (best_w - base), margin
end

-- Corrected keys for a segment whose conversion is `text`, or nil.
local function corrected(env, keys, text)
  local syl = syllables(keys)
  if not syl or #syl == 0 then return nil end
  local cs = {}
  for _, cp in utf8.codes(text) do cs[#cs + 1] = utf8.char(cp) end
  if #cs ~= #syl then cs = nil end
  local fixes = {}
  for i = 1, #syl do
    local v, gain, margin = correct_syllable(env, syl, i, cs)
    if v then fixes[#fixes + 1] = { i = i, v = v, gain = gain, margin = margin } end
  end
  if #fixes == 0 then return nil end
  -- One typo can make a neighbour look fixable too: within a window keep
  -- only the largest gain.
  table.sort(fixes, function(a, b) return a.gain > b.gain end)
  local taken, out = {}, {}
  for _, f in ipairs(fixes) do
    local clash = false
    for _, t in ipairs(taken) do
      if math.abs(t.i - f.i) <= WINDOW then clash = true end
    end
    if not clash then taken[#taken + 1] = f end
  end
  for i, s in ipairs(syl) do out[i] = s end
  local need = 0
  for _, t in ipairs(taken) do out[t.i] = t.v; need = math.max(need, t.margin) end
  local new = table.concat(out)
  -- The whole segment must read better too.
  local w0, w1 = weight(env, keys), weight(env, new)
  if not (w0 and w1 and w1 > w0 + need) then return nil end
  return new
end

-- Keys of segment [a, b) with each typo syllable (asus_lang.lua, property
-- asus_typo_fix) replaced by its best-scoring variant, or nil.
local function repaired(env, ctx, a, b)
  local prop = ctx:get_property("asus_typo_fix") or ""
  if prop == "" then return nil end
  local keys = ctx.input:sub(a + 1, b)
  local any = false
  for s, e in prop:gmatch("(%d+):(%d+)") do
    s, e = tonumber(s), tonumber(e)
    if s >= a and e <= b then
      local tok = keys:sub(s - a + 1, e - a)
      local best, best_w
      for _, v in ipairs(variants(tok)) do
        local k2 = keys:sub(1, s - a) .. v .. keys:sub(e - a + 1)
        local w = weight(env, k2)
        if w and (not best_w or w > best_w) then best, best_w = k2, w end
      end
      if best then keys, any = best, true end
    end
  end
  return any and keys or nil
end

local function func(input, env)
  local ctx = env.engine.context
  if not env.enabled or not env.tr or (ctx:get_property("asus_edit") or "") ~= "" then
    for cand in input:iter() do yield(cand) end
    return
  end
  local first = true
  for cand in input:iter() do
    if first then
      first = false
      local keys = ctx.input:sub(cand.start + 1, cand._end)
      local fixed = (cand.type == "sentence" or cand.type == "phrase" or cand.type == "user_phrase")
          and corrected(env, keys, cand.text)
      local text
      if fixed then
        local seg = Segment(0, #fixed)
        seg.tags = Set({ "abc" })
        local t = env.tr:query(fixed, seg)
        if t then
          for c in t:iter() do
            if c._end == #fixed then text = c.text end
            break
          end
        end
      end
      if text and text ~= cand.text then
        yield(Candidate("typo", cand.start, cand._end, text, "更正"))
      end
      yield(cand)
    else
      yield(cand)
    end
  end
end

-- Translator for segments with typo syllables.
local translator = {}

function translator.init(env)
  init(env)
end

translator.fini = fini

function translator.func(input, seg, env)
  if not env.enabled or not env.tr or not seg:has_tag("asus_fix") then return end
  local ctx = env.engine.context
  local fixed = repaired(env, ctx, seg.start, seg._end)
  if not fixed then return end
  local s = Segment(0, #fixed)
  s.tags = Set({ "abc" })
  local t = env.tr:query(fixed, s)
  if not t then return end
  local n = 0
  for c in t:iter() do
    if c._end == #fixed then
      local cand = Candidate("typo", seg.start, seg._end, c.text, n == 0 and "更正" or "")
      cand.quality = 100 - n
      yield(cand)
      n = n + 1
      if n >= 3 then break end
    end
  end
end

return { filter = { init = init, func = func, fini = fini }, translator = translator,
         variants = variants }
