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
local NUMBER_MARK = "數字"  -- asus_lang.lua (which requires this module, so not the other way round)

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

local TONES = { " ", "6", "3", "4", "7" }
local KEYS = {}  -- every non-tone key, in keyboard order
for _, row in ipairs(ROWS) do
  for i = 1, #row do
    if not TONE[row:sub(i, i)] then KEYS[#KEYS + 1] = row:sub(i, i) end
  end
end

-- Syllables tok can become by undoing one slip:
--   a non-tone key changed to a neighbour      (pressed the key next to it)
--   one non-tone key dropped                   (an extra key pressed)
--   two neighbouring keys swapped              (pressed in the wrong order)
--   one key added, for a tok ending in a tone  (a key not pressed)
--   another tone for the same keys             (ㄗㄚˋ: ㄗㄚˊ, or one of the above)
local function variants(tok)
  local out, seen = {}, {}
  local function add(v)
    if zy.toned[v] and not seen[v] then seen[v] = true; out[#out + 1] = v end
  end
  local n = #tok
  for p = 1, n do
    local k = tok:sub(p, p)
    if not TONE[k] then
      for _, nb in ipairs(NEAR[k] or {}) do add(tok:sub(1, p - 1) .. nb .. tok:sub(p + 1)) end
      add(tok:sub(1, p - 1) .. tok:sub(p + 1))
    end
  end
  for p = 1, n - 2 do
    local a, b = tok:sub(p, p), tok:sub(p + 1, p + 1)
    if a ~= b then add(tok:sub(1, p - 1) .. b .. a .. tok:sub(p + 2)) end
  end
  if n >= 3 and TONE[tok:sub(-2, -2)] and not TONE[tok:sub(-1)] then
    -- the tone key pressed one key early; or the last key is the extra one
    add(tok:sub(1, -3) .. tok:sub(-1) .. tok:sub(-2, -2))
    add(tok:sub(1, -2))
  end
  if TONE[tok:sub(-1)] then
    local body = tok:sub(1, -2)
    if #body > 0 then
      for _, t in ipairs(TONES) do add(body .. t) end
    end
    if n <= 4 then
      for p = 1, n do
        for _, k in ipairs(KEYS) do add(tok:sub(1, p - 1) .. k .. tok:sub(p)) end
      end
    end
  end
  return out
end

-- Other syllables a correctly spelt syllable may have been meant as:
-- { v = keys, kind = "sub" (a neighbouring key) | "ins" (a key not pressed) }.
local silent_memo = {}
local function silent_variants(s)
  local list = silent_memo[s]
  if list then return list end
  list = {}
  local seen = { [s] = true }
  local function add(v, kind)
    if zy.toned[v] and not seen[v] then seen[v] = true; list[#list + 1] = { v = v, kind = kind } end
  end
  for p = 1, #s do
    local k = s:sub(p, p)
    if not TONE[k] then
      for _, nb in ipairs(NEAR[k] or {}) do add(s:sub(1, p - 1) .. nb .. s:sub(p + 1), "sub") end
    end
  end
  if #s <= 4 then
    for p = 1, #s do
      for _, k in ipairs(KEYS) do add(s:sub(1, p - 1) .. k .. s:sub(p), "ins") end
    end
  end
  silent_memo[s] = list
  return list
end

local function init(env)
  local config = env.engine.schema.config
  local on = config:get_bool("asus_typo/enabled")
  env.enabled = (on == nil) or on
  env.margin = tonumber(config:get_string("asus_typo/margin") or "") or 30.0
  -- A key left out: many more syllables to choose from. (20 corrected 8
  -- more of 100 dev sentences, and one correct held-out sentence wrongly.)
  env.ins_margin = tonumber(config:get_string("asus_typo/ins_margin") or "") or 30.0
  -- The first syllable has no left context (as the last has no right one).
  env.start_margin = tonumber(config:get_string("asus_typo/start_margin") or "") or 30.0
  -- A correction that joins words (the language model makes fewer words of
  -- the window: 我|機|天 -> 我|今天) is the mark of a real slip; among the
  -- wrong corrections of correct dev sentences none that did gained over 14.
  env.merge_margin = tonumber(config:get_string("asus_typo/merge_margin") or "") or 18.0
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
  env.cache, env.words, env.cached = {}, {}, 0
  local ok_tw, tw = pcall(Opencc, "t2tw.json")
  env.tw = ok_tw and tw or nil
  -- Commits change Rime's user dictionary (and so the weights): start over.
  env.conn = env.engine.context.commit_notifier:connect(function()
    env.cache, env.words, env.cached = {}, {}, 0
  end)
end

local function fini(env)
  if env.conn then env.conn:disconnect() end
end

-- Log weight of the best conversion of keys (nil if nothing covers them),
-- and the number of words the language model made of it (1 for a phrase).
local function weight(env, keys)
  local w = env.cache[keys]
  if w ~= nil then return w or nil, env.words[keys] end
  w = false
  local n = 1
  local seg = Segment(0, #keys)
  seg.tags = Set({ "abc" })
  local t = env.tr:query(keys, seg)
  if t then
    for c in t:iter() do
      if c._end == #keys then
        local g = c:get_genuine()
        local ok, ph = pcall(function() return g:to_phrase() end)
        if ok and ph then w = ph.weight end
        local ok2, sen = pcall(function() return g:to_sentence() end)
        if ok2 and sen then n = #sen.entrys end
      end
      break
    end
  end
  if env.cached >= MAX_CACHE then env.cache, env.words, env.cached = {}, {}, 0 end
  env.cache[keys] = w
  env.words[keys] = n
  env.cached = env.cached + 1
  return w or nil, n
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
  local base, base_n = weight(env, before .. syl[i] .. after)
  if not base then return nil end
  local s = syl[i]
  local place = (hi - i < WINDOW) and env.end_margin or (i == 1) and env.start_margin or env.margin
  local margins = { sub = place, ins = place + env.ins_margin - env.margin }
  -- The best-scoring variant among those that clear their margin.
  local best, best_over, best_w, best_kind
  local top = {}  -- per kind, for the debug log
  for _, c in ipairs(silent_variants(s)) do
    local w, wn = weight(env, before .. c.v .. after)
    if w then
      if env.debug and (not top[c.kind] or w > top[c.kind].w) then top[c.kind] = { v = c.v, w = w, n = wn } end
      local need = margins[c.kind]
      if wn < base_n then need = math.min(need, env.merge_margin) end
      local over = w - base - need
      if over > 0 and (not best_w or w > best_w) then
        best, best_over, best_w, best_kind = c.v, over, w, need
      end
    end
  end
  if env.debug then
    for kind, t in pairs(top) do
      if t.w > base then
        log.info(string.format("asus_typo cand i=%d n=%d %s->%s base=%.2f gain=%.2f win=%d kind=%s words=%d>%d keys=%s",
          i, #syl, s, t.v, base, t.w - base, hi - lo + 1, kind, base_n, t.n, table.concat(syl)))
      end
    end
  end
  return best, best_w and (best_w - base), best and best_kind
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

-- Keys around keys[s..e] (1-based, inclusive): up to WINDOW syllables before
-- it and up to WINDOW finished syllables after it. An unfinished syllable at
-- the end is left out, so the window (and its cached weights) only changes
-- when a syllable is completed.
local function around(keys, s, e)
  local l, n, p = s, 0, s - 1
  while p >= 1 and n < WINDOW do
    local q = p - 1
    while q >= 1 and not TONE[keys:sub(q, q)] do q = q - 1 end
    l, n, p = q + 1, n + 1, q
  end
  local r, q = e, e + 1
  n = 0
  while q <= #keys and n < WINDOW do
    local t = q
    while t <= #keys and not TONE[keys:sub(t, t)] do t = t + 1 end
    if t > #keys then break end
    r, n, q = t, n + 1, t + 1
  end
  return keys:sub(l, s - 1), keys:sub(e + 1, r)
end

-- Keys of segment [a, b) with each typo syllable (asus_lang.lua, property
-- asus_typo_fix) replaced by its best-scoring variant, or nil. Variants are
-- scored on the syllables around them, left to right.
local function repaired(env, ctx, a, b)
  local prop = ctx:get_property("asus_typo_fix") or ""
  if prop == "" then return nil end
  local keys = ctx.input:sub(a + 1, b)
  local any, shift = false, 0  -- shift: how much longer keys has become
  local done = a               -- input position up to which keys are replaced
  local digit                  -- a number to put back at the end (see below)
  for s, e in prop:gmatch("(%d+):(%d+)") do
    s, e = tonumber(s), tonumber(e)
    if s >= a and e <= b and s >= done then
      local i, j = s - a + 1 + shift, e - a + shift
      local tok = keys:sub(i, j)
      local list = variants(tok)
      -- A key of the next syllable pressed before this one's tone key
      -- (ㄋㄧㄏˇㄠˇ for 你好): the extra key here is the first key there.
      -- Such variants replace both syllables, so all are scored with the
      -- next syllable included.
      local nj = j + 1
      while nj <= #keys and not TONE[keys:sub(nj, nj)] do nj = nj + 1 end
      local nxt = (nj <= #keys and nj > j) and keys:sub(j + 1, nj) or ""
      local moved = {}
      if nxt ~= "" and #nxt <= 4 then
        for p = 1, #tok - 1 do
          local k = tok:sub(p, p)
          local rest = tok:sub(1, p - 1) .. tok:sub(p + 1)
          if not TONE[k] and zy.toned[rest] and zy.toned[k .. nxt] then
            moved[#moved + 1] = rest .. k .. nxt
          end
        end
      end
      local stop = (#moved > 0) and nj or j  -- last key that is replaced
      local tail = keys:sub(j + 1, stop)
      local before, after = around(keys, i, stop)
      local best, best_w
      for _, v in ipairs(list) do
        local w = weight(env, before .. v .. tail .. after)
        if w and (not best_w or w > best_w) then best, best_w = v .. tail, w end
      end
      for _, v in ipairs(moved) do
        local w = weight(env, before .. v .. after)
        if w and (not best_w or w > best_w) then best, best_w = v, w end
      end
      best = best or (list[1] and list[1] .. tail)  -- nothing converts around it: any syllable
      -- A syllable and then a digit key, and the syllable alone reads best:
      -- the digit was a number (是9), it stays as typed after the syllable.
      if best and tok:match("%d$") and best == tok:sub(1, -2) .. tail then
        digit = tok:sub(-1)
      end
      if best then
        keys = keys:sub(1, i - 1) .. best .. keys:sub(stop + 1)
        shift = shift + #best - (stop - i + 1)
        done = e + (stop - j)
        any = true
      end
    end
  end
  return any and keys or nil, digit
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
      -- ("simplified": the same, after OpenCC changed a character to its
      -- Taiwan form, 爲 -> 為. Leaving those out skipped every sentence with
      -- 為 裡 著 麵... in it.)
      -- (Not a candidate in which asus_lang.lua read digit keys as digits:
      -- 2萬元 is not 段元 with a slip in it.)
      local fixed = (cand.type == "sentence" or cand.type == "phrase" or cand.type == "user_phrase"
                     or cand.type == "simplified")
          and cand.comment ~= NUMBER_MARK
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
        -- The text comes straight from the translator: Taiwan forms as
        -- the simplifier would have given it.
        if text and env.tw and ctx:get_option("zh_hant_tw") then text = env.tw:convert(text) end
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
  local fixed, digit = repaired(env, ctx, seg.start, seg._end)
  if not fixed then return end
  digit = digit or ""
  local s = Segment(0, #fixed)
  s.tags = Set({ "abc" })
  local t = env.tr:query(fixed, s)
  if not t then return end
  local n = 0
  for c in t:iter() do
    if c._end == #fixed then
      local cand = Candidate("typo", seg.start, seg._end, c.text .. digit, n == 0 and "更正" or "")
      cand.quality = 100 - n
      yield(cand)
      n = n + 1
      if n >= 3 then break end
    end
  end
end

return { filter = { init = init, func = func, fini = fini }, translator = translator,
         variants = variants }
