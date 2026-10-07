-- asus_learn.lua
-- Self-learning on top of Rime's own user dictionary. Everything is stored
-- locally in asus_learn.userdb (LevelDB, in the Rime user folder).
--
--   * Corrections: when the user changes a conversion (picks candidates) and
--     commits, each changed word becomes a pair "rejected -> chosen" for the
--     same Zhuyin keys, together with the characters around it. The filter
--     then rewrites later conversions that contain the rejected text:
--       - same left or right neighbour as when learned: right away
--       - anywhere: corrected twice, or once and never the other way round
--         (asus_learn/global_after: 2 drops the "once" case)
--     A single character, or a pair differing only in 在/再/的/得/地, only
--     changes when both neighbours match. Correcting a rewrite back counts
--     against it; the unrewritten conversion stays second in the menu.
--   * English: letters committed as typed (Ctrl+Enter / Shift+Enter) and
--     English words committed inline; asus_lang.lua prefers them over Zhuyin
--     and Tab completes them.
--   * Next words: commits are split into words (opencc/asus_seg); a word pair
--     seen twice, or a prediction accepted once, is predicted first by
--     asus_predict.lua.
--   * Shift+Delete / Ctrl+Delete on the highlighted candidate (or prediction)
--     forgets what was learned about it, including Rime's own user phrase.
-- Weights halve every half_life_days (asus_learn/half_life_days, default 90).
--
--   processor  lua_processor@*asus_learn*processor  (before asus_keys)
--   filter     lua_filter@*asus_learn*filter        (after asus_grammar)
-- asus_learn/enabled: false turns all of it off (used by the evaluation).

local zy = require("asus_zhuyin_data")

local kAccepted, kNoop = 1, 2
local XK_Delete, XK_space, XK_Return = 0xffff, 0x20, 0xff0d
local EXPANDED = "_asus_expanded"  -- set by asus_keys.lua while the menu is expanded

local MAX_PAIR = 4        -- characters in a learned pair (sub-words of a pick)
local MAX_RUN = 8         -- longest changed run learned as a whole
local PRUNE = 0.05        -- weights below this are dropped
local KEEP_NEXT = 8       -- next words kept per previous word
local NEXT_SHOW = 1.5     -- weight a next word needs to be predicted
local EN_SHOW = 0.9       -- weight an English word needs to be preferred
local MAX_EN = 2000       -- learned English words kept in memory

-- Shared by all sessions (librime-lua runs one Lua state per process).
local db
local seg_cc
local enabled = true
local half_life = 90 * 86400
local global_after = 1   -- corrections before a pair applies everywhere (if never undone)
local en_words           -- lower-case key -> {word=, w=, t=}
local orig = nil         -- last top conversion of a whole input: {input=, text=}
local applied = nil      -- rewrites in the current top: {input=, list of {k,x,c}}

-- Storage helpers ------------------------------------------------------------
-- Values are "name w t|name w t|..." (names never contain spaces or "|").
local function decayed(w, t, now)
  return w * 0.5 ^ (math.max(0, now - t) / half_life)
end

local function parse(v, now)
  local out = {}
  if not v or v == "" then return out end
  for item in v:gmatch("[^|]+") do
    local name, w, t = item:match("^(.-) ([%d%.e%-]+) (%d+)$")
    if name then
      local cur = decayed(tonumber(w), tonumber(t), now)
      if cur >= PRUNE then out[name] = cur end
    end
  end
  return out
end

local function serialize(map, now, keep)
  local names = {}
  for name in pairs(map) do names[#names + 1] = name end
  table.sort(names, function(a, b) return map[a] > map[b] end)
  local parts = {}
  for i, name in ipairs(names) do
    if keep and i > keep then break end
    parts[#parts + 1] = string.format("%s %.3f %d", name, map[name], now)
  end
  return table.concat(parts, "|")
end

local function fetch(key, now)
  if not db then return {} end
  return parse(db:fetch(key), now)
end

local function store(key, map, now, keep)
  local v = serialize(map, now, keep)
  if v == "" then db:erase(key) else db:update(key, v) end
end

local function bump(key, name, inc, now, keep)
  if not db then return end
  local map = fetch(key, now)
  map[name] = (map[name] or 0) + inc
  store(key, map, now, keep)
end

-- Text helpers ---------------------------------------------------------------
local function chars(s)
  local out = {}
  for _, cp in utf8.codes(s) do out[#out + 1] = utf8.char(cp) end
  return out
end

local function is_cjk_text(s)
  local any = false
  for _, cp in utf8.codes(s) do
    if cp < 0x3400 or cp > 0x9fff then return false end
    any = true
  end
  return any
end

-- Fully toned Zhuyin keys -> syllables and the byte offset where each starts
-- (0-based, plus one past the end); nil if the keys are not all syllables.
local function syllables(input)
  local out, starts, pos = {}, {}, 1
  while pos <= #input do
    local found
    for len = math.min(5, #input - pos + 1), 1, -1 do
      local s = input:sub(pos, pos + len - 1)
      if zy.toned[s] then found = s; break end
    end
    if not found then return nil end
    out[#out + 1] = found
    starts[#starts + 1] = pos - 1
    pos = pos + #found
  end
  starts[#starts + 1] = #input
  return out, starts
end

local function words_of(text)
  if not seg_cc or not is_cjk_text(text) then return nil end
  local out = {}
  for w in (seg_cc:convert(text) or ""):gmatch("[^|]+") do out[#out + 1] = w end
  return out
end

-- Setup ----------------------------------------------------------------------
local function load_english()
  en_words = {}
  if not db then return end
  local now = os.time()
  local q = db:query("e\t")
  if not q then return end
  local n = 0
  for k, v in q:iter() do
    if k:sub(1, 2) ~= "e\t" then break end
    for word, w in pairs(parse(v, now)) do
      en_words[k:sub(3)] = { word = word, w = w }
      n = n + 1
    end
    if n >= MAX_EN then break end
  end
  q = nil
  collectgarbage()  -- release the LevelDB iterator now, not at some later GC
end

-- The database stays open for the life of the process (all sessions share
-- it); every update is written to LevelDB's log right away.
local function open(config)
  local e = config:get_bool("asus_learn/enabled")
  enabled = (e == nil) or e
  local days = tonumber(config:get_string("asus_learn/half_life_days") or "")
  if days then half_life = days * 86400 end
  global_after = tonumber(config:get_string("asus_learn/global_after") or "") or global_after
  if not enabled then return end
  if db == nil then
    local ok, d = pcall(UserDb, "asus_learn", "userdb")
    if ok and d and d:open() then db = d else db = false end
    load_english()
  end
  if seg_cc == nil then
    local ok, cc = pcall(Opencc, "asus_seg.json")
    seg_cc = ok and cc or false
  end
end

-- Corrections ----------------------------------------------------------------
-- Pairs that only differ in these characters (我再→我在) follow the 在/再 rule
-- of asus_grammar.lua: context only, both neighbours must match.
local GRAMMAR = { ["在"] = true, ["再"] = true, ["的"] = true, ["得"] = true, ["地"] = true }

local function context_only(x, c)
  local xs, cs = chars(x), chars(c)
  if #xs == 1 then return true end
  for i = 1, #xs do
    if xs[i] ~= cs[i] and not (GRAMMAR[xs[i]] and GRAMMAR[cs[i]]) then return false end
  end
  return true
end

-- Should text x (keys k, between characters l and r) become something else?
local function decide(k, x, l, r, now)
  local cands = fetch("c\t" .. k .. "\t" .. x, now)
  local best, best_score
  for c, wf in pairs(cands) do
    local wr = fetch("c\t" .. k .. "\t" .. c, now)[x] or 0
    local strict = context_only(x, c)
    local ok = not strict and ((wf >= 1.5 and wf > wr + 1)
                               or (global_after <= 1 and wf >= 0.9 and wr < PRUNE))
    if not ok then
      local f = fetch("x\t" .. k .. "\t" .. x .. "\t" .. c, now)
      local b = fetch("x\t" .. k .. "\t" .. c .. "\t" .. x, now)
      if strict then
        local key = "B" .. l .. r
        ok = (f[key] or 0) > (b[key] or 0) + 0.1
      else
        ok = math.max(f["L" .. l] or 0, f["R" .. r] or 0)
             > math.max(b["L" .. l] or 0, b["R" .. r] or 0) + 0.1
      end
    end
    if ok and (best_score == nil or wf - wr > best_score) then
      best, best_score = c, wf - wr
    end
  end
  return best
end

-- Rewrite text (one character per syllable) with the learned pairs; returns
-- the new text and the pairs used, or nil.
local function rewrite(text, syl, left)
  local cs = chars(text)
  local n = #cs
  if n ~= #syl or not db then return nil end
  local now = os.time()
  local out, used = {}, {}
  local i = 1
  while i <= n do
    local hit = false
    for len = math.min(MAX_RUN, n - i + 1), 1, -1 do
      local k = table.concat(syl, "", i, i + len - 1)
      local x = table.concat(cs, "", i, i + len - 1)
      local l = out[#out] or left
      local c = decide(k, x, l, cs[i + len] or "", now)
      if c then
        for _, ch in ipairs(chars(c)) do out[#out + 1] = ch end
        used[#used + 1] = { k, x, c }
        i = i + len
        hit = true
        break
      end
    end
    if not hit then
      out[#out + 1] = cs[i]
      i = i + 1
    end
  end
  if #used == 0 then return nil end
  return table.concat(out), used
end

local function learn_pair(k, x, c, l, r, w, now)
  bump("c\t" .. k .. "\t" .. x, c, w, now)
  db:update("r\t" .. k .. "\t" .. c, "1")  -- the user chose c for keys k (asus_typo.lua)
  local key = "x\t" .. k .. "\t" .. x .. "\t" .. c
  local map = fetch(key, now)
  if context_only(x, c) then
    map["B" .. l .. r] = (map["B" .. l .. r] or 0) + w
  else
    map["L" .. l] = (map["L" .. l] or 0) + w
    map["R" .. r] = (map["R" .. r] or 0) + w
  end
  store(key, map, now)
end

-- Compare the conversion shown before the user's picks with what was
-- committed, and learn every changed word.
local function learn_corrections(ctx, final, now, picks)
  if not orig or orig.input ~= ctx.input or final == orig.text then return end
  local syl, starts = syllables(ctx.input)
  if not syl then return end
  local fc, oc = chars(final), chars(orig.text)
  if #fc ~= #syl or #oc ~= #syl then return end
  local index = {}
  for i, off in ipairs(starts) do index[off] = i end  -- byte offset -> syllable
  -- Changed spans: picked segments (merged when adjacent, so a name picked
  -- character by character is one word), and changed runs inside the
  -- automatically converted rest.
  local runs = {}
  local ok, segs = pcall(function() return ctx.composition:toSegmentation():get_segments() end)
  if not ok or not segs then return end
  local prev_pick = false
  for _, seg in ipairs(segs) do
    local a, b = index[seg.start], index[seg._end]
    if not a or not b then return end
    b = b - 1
    local changed = false
    for i = a, b do if fc[i] ~= oc[i] then changed = true end end
    local picked = picks[seg.start .. ":" .. seg._end] or false
    if changed and picked then
      if prev_pick and runs[#runs].b == a - 1 then runs[#runs].b = b
      else runs[#runs + 1] = { a = a, b = b, w = 1 } end
    elseif changed then
      local i = a
      while i <= b do
        if fc[i] ~= oc[i] then
          local j = i
          while j < b and fc[j + 1] ~= oc[j + 1] do j = j + 1 end
          if j > i then runs[#runs + 1] = { a = i, b = j, w = 0.5 } end
          i = j + 1
        else
          i = i + 1
        end
      end
    end
    prev_pick = changed and picked
  end
  -- Undoing something learned earlier counts against it at the length it was
  -- learned (晚→挽 learned from single-character picks, undone by picking 晚上).
  local seen = {}
  for i = 1, #fc do
    for j = i, math.min(#fc, i + MAX_RUN - 1) do
      local o, f = table.concat(oc, "", i, j), table.concat(fc, "", i, j)
      if o ~= f then
        local k = table.concat(syl, "", i, j)
        if fetch("c\t" .. k .. "\t" .. f, now)[o] then
          learn_pair(k, o, f, fc[i - 1] or "", fc[j + 1] or "", 1, now)
          seen[k .. "\t" .. o] = true
        end
      end
    end
  end
  for _, run in ipairs(runs) do
    local function emit(i, j)
      local k, o = table.concat(syl, "", i, j), table.concat(oc, "", i, j)
      if seen[k .. "\t" .. o] then return end
      seen[k .. "\t" .. o] = true
      learn_pair(k, o, table.concat(fc, "", i, j), fc[i - 1] or "", fc[j + 1] or "", run.w, now)
    end
    if run.a == run.b then
      emit(run.a, run.b)
    else
      for i = run.a, run.b do
        for j = i + 1, math.min(run.b, i + MAX_PAIR - 1) do
          local diff = false
          for p = i, j do if fc[p] ~= oc[p] then diff = true end end
          if diff then emit(i, j) end
        end
      end
      local n = run.b - run.a + 1
      if n > MAX_PAIR and n <= MAX_RUN then emit(run.a, run.b) end
    end
  end
end

-- English --------------------------------------------------------------------
local function learn_english(word, w, now)
  local key = word:lower()
  bump("e\t" .. key, word, w, now, 1)
  local cur = fetch("e\t" .. key, now)[word]
  if cur then en_words[key] = { word = word, w = cur } end
end

-- Next words -----------------------------------------------------------------
local function is_word(s)
  local forms = seg_cc and seg_cc:convert_word(s)
  return forms ~= nil and #forms > 0
end

local function learn_next(prev, word, w, now)
  local n = utf8.len(word)
  if n < 2 or n > 4 then return end
  bump("n\t" .. prev, word, w, now, KEEP_NEXT)
  -- A longer word may hide the pair the user will see: 交報告 = 交 + 報告.
  if n >= 3 then
    local cs = chars(word)
    for p = 1, n - 2 do
      local a, b = table.concat(cs, "", 1, p), table.concat(cs, "", p + 1)
      if is_word(a) and is_word(b) then
        bump("n\t" .. a, b, w, now, KEEP_NEXT)
        break
      end
    end
  end
end

-- Processor ------------------------------------------------------------------
local processor = {}

function processor.init(env)
  open(env.engine.schema.config)
  env.prev_word = nil
  env.picks = {}  -- "start:end" of segments the user picked a candidate for
  if not enabled then return end
  local ctx = env.engine.context
  env.update_conn = ctx.update_notifier:connect(function(c)
    if c.input == "" then env.picks = {} end
  end)
  env.commit_conn = ctx.commit_notifier:connect(function(c)
    local picks = env.picks
    env.picks = {}
    if not db then return end
    local now = os.time()
    local text = c:get_commit_text() or ""
    local input = c.input
    -- Raw letters committed as typed (Ctrl+Enter / Shift+Enter).
    if text == input and #input >= 3 and input:match("^%a[%a%-]*$") then
      learn_english(input, 1, now)
      env.prev_word = nil
      return
    end
    -- English words committed inline.
    local ok, segs = pcall(function() return c.composition:toSegmentation():get_segments() end)
    if ok and segs then
      for _, seg in ipairs(segs) do
        local cand = seg:get_selected_candidate()
        if cand and cand.type == "english" and #cand.text >= 3 then
          learn_english(cand.text, 0.5, now)
        end
      end
    end
    learn_corrections(c, text, now, picks)
    local words = words_of(text)
    if words then
      local prev = env.prev_word
      for _, w in ipairs(words) do
        if prev then learn_next(prev, w, 1, now) end
        prev = w
      end
      env.prev_word = prev
    else
      env.prev_word = nil
    end
  end)
end

function processor.fini(env)
  for _, conn in ipairs({ env.commit_conn, env.update_conn }) do
    conn:disconnect()
  end
end

-- Forget everything learned that produced `text`.
local function forget_chosen(text, now)
  for _, prefix in ipairs({ "c\t", "x\t" }) do
    local q = db:query(prefix)
    local edits = {}
    if q then
      for k, v in q:iter() do
        if k:sub(1, 2) ~= prefix then break end
        if prefix == "c\t" then
          local map = parse(v, now)
          if map[text] then
            map[text] = nil
            edits[#edits + 1] = { k, map }
          end
        elseif k:sub(-#text - 1) == "\t" .. text then
          edits[#edits + 1] = { k, {} }
        end
      end
    end
    q = nil
    collectgarbage()  -- release the LevelDB iterator before writing
    for _, e in ipairs(edits) do store(e[1], e[2], now) end
  end
end

local function forget_pair(k, x, c, now)
  db:erase("r\t" .. k .. "\t" .. c)
  local key = "c\t" .. k .. "\t" .. x
  local map = fetch(key, now)
  map[c] = nil
  store(key, map, now)
  db:erase("x\t" .. k .. "\t" .. x .. "\t" .. c)
end

-- Remember which segment the user is about to pick a candidate for. Done
-- here, before asus_keys selects it: by the time select_notifier reaches Lua,
-- the engine has already moved on to the next segment (or committed).
local function note_pick(key, env)
  local ctx = env.engine.context
  if key.modifier ~= 0 or not ctx:get_option(EXPANDED) then return end
  local code = key.keycode
  local seg = ctx.composition:back()
  if not seg or not seg.menu then return end
  local index
  if code >= 0x31 and code <= 0x39 then  -- 1-9 on the current page
    local page_size = env.engine.schema.page_size
    index = seg.selected_index - seg.selected_index % page_size + (code - 0x31)
  elseif code == XK_space or code == XK_Return then
    index = seg.selected_index
  else
    return
  end
  local cand = seg:get_candidate_at(index)
  if cand and cand.type ~= "sentence" then
    env.picks[seg.start .. ":" .. cand._end] = true
  end
end

function processor.func(key, env)
  if not enabled or not db or key:release() then return kNoop end
  if key.keycode ~= XK_Delete or not (key:shift() or key:ctrl()) or key:alt() then
    note_pick(key, env)
    return kNoop
  end
  local ctx = env.engine.context
  if not ctx:is_composing() then return kNoop end
  local seg = ctx.composition:back()
  if not seg then return kNoop end
  local now = os.time()
  if seg:has_tag("prediction") then
    local cand = seg:get_candidate_at(seg.selected_index)
    local word = cand and cand.comment or ""
    if word ~= "" then
      db:update("b\t" .. word, "1")  -- never predict it again
      ctx:clear()
    end
    return kAccepted
  end
  local cand = ctx:get_selected_candidate()
  if not cand then return kAccepted end
  if cand.type == "english" then
    local k = cand.text:lower()
    db:erase("e\t" .. k)
    en_words[k] = nil
  elseif applied and applied.input == ctx.input and seg.start == 0 and seg.selected_index == 0 then
    -- The top conversion was rewritten: undo those rewrites.
    for _, p in ipairs(applied.list) do forget_pair(p[1], p[2], p[3], now) end
    applied = nil
  else
    forget_chosen(cand.text, now)
  end
  ctx:delete_current_selection()  -- Rime's own user phrase, if any
  ctx:refresh_non_confirmed_composition()
  return kAccepted
end

-- Filter ---------------------------------------------------------------------
local function filter(input, env)
  local ctx = env.engine.context
  local comp = ctx.composition
  local seg = (enabled and db and not comp:empty()) and comp:back() or nil
  local first, top = true, nil
  for cand in input:iter() do
    if first then
      first = false
      local out = cand
      if seg and cand.start == seg.start and cand._end == seg._end then
        local syl = syllables(ctx.input:sub(seg.start + 1, seg._end))
        if syl then
          local left = ""
          if seg.start > 0 then  -- last character of the segments before
            local ok, segs = pcall(function() return comp:toSegmentation():get_segments() end)
            for _, s in ipairs(ok and segs or {}) do
              local c = s._end <= seg.start and s:get_selected_candidate()
              if c and c.text ~= "" then
                local cs = chars(c.text)
                left = cs[#cs]
              end
            end
          end
          local text, used = rewrite(cand.text, syl, left)
          if text then
            out = ShadowCandidate(cand, cand.type, text, cand.comment)
            top = text
            if seg.start == 0 then applied = { input = ctx.input, list = used } end
          elseif seg.start == 0 then
            applied = nil
          end
        end
        -- (Not while asus_edit.lua rebuilds the composition around its cursor.)
        if seg.start == 0 and seg._end == #ctx.input and (ctx:get_property("asus_edit") or "") == "" then
          orig = { input = ctx.input, text = out.text }
        end
      end
      yield(out)
      -- Keep the unrewritten conversion one pick away.
      if out ~= cand then yield(cand) end
    elseif cand.text ~= top then
      yield(cand)
    end
  end
end

-- API for asus_predict.lua / asus_lang.lua -------------------------------------
-- Learned next words for committed text (best first, at most limit).
local function predict(text, limit)
  if not enabled or not db then return {} end
  local now = os.time()
  local cps = {}
  for _, cp in utf8.codes(text) do cps[#cps + 1] = cp end
  local scored, seen = {}, {}
  for len = math.min(4, #cps), 1, -1 do
    local prev = utf8.char(table.unpack(cps, #cps - len + 1, #cps))
    for w, weight in pairs(fetch("n\t" .. prev, now)) do
      if weight >= NEXT_SHOW and not seen[w] and not db:fetch("b\t" .. w) then
        seen[w] = true
        scored[#scored + 1] = { w, weight + len }  -- longer context first
      end
    end
  end
  table.sort(scored, function(a, b) return a[2] > b[2] end)
  local out = {}
  for i = 1, math.min(limit, #scored) do out[i] = scored[i][1] end
  return out
end

local function banned(word)
  return enabled and db and db:fetch("b\t" .. word) ~= nil or false
end

-- A prediction or autocompletion was accepted after `text`.
local function accepted(text, word)
  if not enabled or not db then return end
  local words = words_of(text)
  if words and #words > 0 then learn_next(words[#words], word, 2, os.time()) end
end

-- Learned English word for a lower-case key: {word=, rank=0} or nil.
local function english(key)
  if not enabled or not en_words then return nil end
  local e = en_words[key]
  if e and e.w >= EN_SHOW then return { word = e.word, rank = 0 } end
  return nil
end

-- Best learned completion for a typed lower-case prefix (>= 3 letters).
local function complete_english(prefix)
  if not enabled or not en_words or #prefix < 3 then return nil end
  local best
  for k, e in pairs(en_words) do
    if e.w >= EN_SHOW and #k > #prefix and k:sub(1, #prefix) == prefix
        and (not best or e.w > best.w) then
      best = e
    end
  end
  return best and { word = best.word, rank = 0 } or nil
end

-- Has the user ever picked `text` for `keys`? (Such text is never treated
-- as a typo by asus_typo.lua.)
local function chosen(keys, text)
  return enabled and db and db:fetch("r\t" .. keys .. "\t" .. text) ~= nil or false
end

return { processor = processor, filter = filter, predict = predict, banned = banned, chosen = chosen,
         accepted = accepted, english = english, complete_english = complete_english,
         syllables = syllables }
