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
--         (asus_learn/global_after: 2 drops the "once" case) -- but never
--         when the rejected word is one the user types themselves (seen in
--         their commits, "w" entries): 城市 stays 城市 after learning 程式
--     A rewritten conversion is marked 學 in the bubble.
--     A single character, or a pair differing only in 在/再/的/得/地, only
--     changes when both neighbours match. Correcting a rewrite back counts
--     against it; the unrewritten conversion stays second in the menu.
--   * English: letters committed as typed (Ctrl+Enter / Shift+Enter) and
--     English words committed inline; asus_lang.lua prefers them over Zhuyin
--     and Tab completes them.
--   * Next words: commits are split into words (opencc/asus_seg); a word pair
--     seen twice, or a prediction accepted once, is predicted first by
--     asus_predict.lua.
--   * Email addresses: every address committed (or typed in English mode,
--     lua/asus_email.lua) is remembered, together with rime/emails.txt, so
--     typing the first few characters offers the whole address.
--   * After a number: the word picked from the list right after a number
--     (第3 + ㄐㄧˊ: 集, not 級) is what asus_lang.lua proposes the next time
--     the same character precedes the number.
--   * Number or Chinese: for a digit key that is also a Zhuyin initial
--     (5 + ㄨㄢˋ: 5萬 or 賺), the reading picked from the list is remembered
--     for that syllable after that character.
--   * Shift+Delete / Ctrl+Delete on the highlighted candidate (or prediction)
--     forgets what was learned about it, including Rime's own user phrase.
--   * Shift+BackSpace in the composition (asus_edit.lua) forgets the
--     corrections that produced the word left of the editing cursor (or at
--     the end): forget_spans() looks the "c" entries up by those keys (a
--     prefix query and an "r" fetch per span, never a scan) and drops the
--     pairs whose new text is what is shown there, with their "x" context
--     and "r" reverse entries; forget_user_phrase() deletes Rime's own user
--     phrase for it. When that input is committed, the conversion shown
--     after forgetting is not learned as a correction back (a pick there is).
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
local OWN_WORD = 0.9      -- commits that make a word the user's own (one, slightly decayed)
local EN_SHOW = 0.9       -- weight an English word needs to be preferred
local MAX_EN = 2000       -- learned English words kept in memory
local MAX_EMAIL = 500     -- learned email addresses kept in memory
local EMAIL_LIST_W = 100  -- weight of an address listed in rime/emails.txt

-- Shared by all sessions (librime-lua runs one Lua state per process).
local db
local seg_cc
local enabled = true
local half_life = 90 * 86400
local global_after = 1   -- corrections before a pair applies everywhere (if never undone)
local en_words           -- lower-case key -> {word=, w=, t=}
local emails             -- lower-case address -> {text=, w=}
local orig = nil         -- last top conversion of a whole input: {input=, text=}
local applied = nil      -- rewrites in the current top: {input=, list of {k,x,c}}
-- Spans forgotten with Shift+BackSpace, until the next commit: {input=,
-- list of {s,e}} (key offsets). (Not dropped when the input is emptied:
-- asus_edit.lua empties it for a moment while it converts afresh.)
local forgot = nil
local holding = false    -- asus_edit.lua converts the input afresh: orig stays as it was

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
      -- (word_toned: ㄒㄧ˙ of 東西, a syllable no single character has.)
      if zy.toned[s] or (zy.word_toned and zy.word_toned[s]) then found = s; break end
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

-- Email addresses ------------------------------------------------------------
local EMAIL = "^[%w][%w._%%+%-]*@[%w%-]+%.[%w.%-]*%a$"
local function is_email(s)
  return #s <= 254 and s:match(EMAIL) ~= nil and not s:find("..", 1, true)
end

local function load_emails()
  emails = {}
  local f = io.open(rime_api.get_user_data_dir() .. "/emails.txt", "r")
  if f then
    for line in f:lines() do
      local e = line:gsub("^%s+", ""):gsub("%s+$", "")
      if e:sub(1, 1) ~= "#" and is_email(e) then
        emails[e:lower()] = { text = e, w = EMAIL_LIST_W }
      end
    end
    f:close()
  end
  if not db then return end
  local now = os.time()
  local q = db:query("m\t")
  if not q then return end
  local n = 0
  for k, v in q:iter() do
    if k:sub(1, 2) ~= "m\t" then break end
    for text, w in pairs(parse(v, now)) do
      local key = k:sub(3)
      local cur = emails[key]
      emails[key] = { text = cur and cur.text or text, w = w + (cur and cur.w or 0) }
      n = n + 1
    end
    if n >= MAX_EMAIL then break end
  end
  q = nil
  collectgarbage()  -- release the LevelDB iterator now
end

-- Remember an address the user typed (w: how much this use counts).
local function learn_email(text, w)
  if not enabled or not db or not emails or not is_email(text) then return end
  local now = os.time()
  local key = text:lower()
  bump("m\t" .. key, text, w or 1, now, 1)
  local cur = emails[key]
  local learned = fetch("m\t" .. key, now)[text] or 0
  emails[key] = { text = text, w = learned + ((cur and cur.w >= EMAIL_LIST_W) and EMAIL_LIST_W or 0) }
end

-- Addresses starting with `prefix` (any case), most used first.
local function emails_for(prefix, limit)
  if not enabled or not emails or prefix == "" then return {} end
  local p = prefix:lower()
  local list = {}
  for k, e in pairs(emails) do
    if #k > #p and k:sub(1, #p) == p then list[#list + 1] = e end
  end
  table.sort(list, function(a, b)
    if a.w ~= b.w then return a.w > b.w end
    return a.text < b.text
  end)
  local out = {}
  for i = 1, math.min(limit or 3, #list) do out[i] = list[i].text end
  return out
end

local function forget_email(text)
  if not db or not emails then return end
  db:erase("m\t" .. text:lower())
  emails[text:lower()] = nil
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
    load_emails()
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
-- Does the user type x themselves (a word of their recent commits)?
local function own_word(x, now)
  return (fetch("w\t" .. x, now).n or 0) >= OWN_WORD
end

local function decide(k, x, l, r, now)
  local cands = fetch("c\t" .. k .. "\t" .. x, now)
  local own = nil  -- looked up once, only when needed
  local best, best_score
  for c, wf in pairs(cands) do
    local wr = fetch("c\t" .. k .. "\t" .. c, now)[x] or 0
    local strict = context_only(x, c)
    local ok = not strict and ((wf >= 1.5 and wf > wr + 1)
                               or (global_after <= 1 and wf >= 0.9 and wr < PRUNE))
    if ok then
      if own == nil then own = own_word(x, now) end
      ok = not own
    end
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
    if ok and (best_score == nil or wf - wr > best_score
               or (wf - wr == best_score and c < best)) then  -- ties: stable order
      best, best_score = c, wf - wr
    end
  end
  return best
end

-- x was learned for keys k instead of the user's own word w (Rime's own
-- user dictionary then prefers x everywhere: 竹科 in 竹刻作品). Back to w
-- unless this context is one where x was chosen.
local function revert(k, x, l, r, now)
  local w = db:fetch("r\t" .. k .. "\t" .. x)
  if not w or w == "1" or w == x or not own_word(w, now) or context_only(w, x) then return nil end
  local f = fetch("x\t" .. k .. "\t" .. w .. "\t" .. x, now)
  if (f["L" .. l] or 0) > 0.1 or (f["R" .. r] or 0) > 0.1 then return nil end
  return w
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
      if not c and len >= 2 then c = revert(k, x, l, cs[i + len] or "", now) end
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
  -- The user chose c for keys k instead of x (asus_typo.lua, and below).
  db:update("r\t" .. k .. "\t" .. c, x)
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
local function learn_corrections(ctx, final, now, picks, forgot)
  if not orig or orig.input ~= ctx.input or final == orig.text then return end
  local syl, starts = syllables(ctx.input)
  if not syl then return end
  local fc, oc = chars(final), chars(orig.text)
  if #fc ~= #syl or #oc ~= #syl then return end
  local index = {}
  for i, off in ipairs(starts) do index[off] = i end  -- byte offset -> syllable
  -- Syllables i..j touch a span forgotten in this input (Shift+BackSpace)?
  local function forgotten(i, j)
    if not forgot or forgot.input ~= ctx.input then return false end
    for _, f in ipairs(forgot.list) do
      if starts[i] < f[2] and starts[j + 1] > f[1] then return true end
    end
    return false
  end
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
      if o ~= f and not forgotten(i, j) then
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
      -- (The conversion shown after a forget is no correction; a pick is.)
      if run.w < 1 and forgotten(i, j) then return end
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
  env.num_picks = {}  -- the same, "1" / "0": the number or the Chinese reading was picked
  if not enabled then return end
  local ctx = env.engine.context
  env.update_conn = ctx.update_notifier:connect(function(c)
    if c.input == "" then env.picks, env.num_picks = {}, {} end
  end)
  env.commit_conn = ctx.commit_notifier:connect(function(c)
    local picks, num_picks = env.picks, env.num_picks
    env.picks, env.num_picks = {}, {}
    local forgot_now = forgot
    forgot = nil
    if not db then return end
    local now = os.time()
    local text = c:get_commit_text() or ""
    local input = c.input
    for e in text:gmatch("[%w._%%+%-]+@[%w.%-]+") do
      learn_email((e:gsub("%.+$", "")), 1)  -- (a full stop after it is not part of it)
    end
    -- English mode (asus_english.lua): nothing but email addresses.
    if c:get_option("ascii_mode") then
      env.prev_word = nil
      return
    end
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
        -- (A word corrected on Space carries that space, asus_keys.lua.)
        local word = cand and cand.type == "english" and cand.text:match("^(.-)%s*$")
        if word and #word >= 3 then
          learn_english(word, 0.5, now)
        end
      end
    end
    -- The word picked right after a number ("q" entries; corrections are
    -- only learned in inputs that are Chinese throughout). Told by the text
    -- in front of the segment, not by segment tags: with the list opened at
    -- the editing cursor everything before it is one locked segment.
    if ok and segs then
      local prefix = ""
      for _, seg in ipairs(segs) do
        local cand = seg:get_selected_candidate()
        local head = prefix:match("^(.-)[%d.]+$")
        if head and cand and picks[seg.start .. ":" .. seg._end] and is_cjk_text(cand.text) then
          local first
          local keys = input:sub(seg.start + 1, seg._end)
          for len = math.min(5, #keys), 1, -1 do
            if zy.toned[keys:sub(1, len)] then first = keys:sub(1, len); break end
          end
          local left = chars(head)
          left = left[#left] or ""
          if not is_cjk_text(left) then left = "" end
          if first then db:update("q\t" .. left .. "\t" .. first, chars(cand.text)[1]) end
        end
        -- Number or Chinese for a digit key that is both (asus_lang.lua,
        -- "d" entries): the reading picked, when both were on offer.
        local choice = num_picks[seg.start .. ":" .. seg._end]
        if cand and choice then
          local key = require("asus_lang").choice_key(input, seg.start, input:sub(seg.start + 1, seg._end))
          if key then db:update("d\t" .. key, choice) end
        end
        prefix = prefix .. (cand and cand.text or "")
      end
    end
    learn_corrections(c, text, now, picks, forgot_now)
    local words = words_of(text)
    if words then
      -- Every 2-4 character piece counts as typed by the user (word lists
      -- split some words: 關停 = 關 + 停).
      local cs = chars(text)
      local seen = {}
      for i = 1, #cs do
        for j = i + 1, math.min(#cs, i + 3) do
          local w = table.concat(cs, "", i, j)
          if not seen[w] then seen[w] = true; bump("w\t" .. w, "n", 1, now) end
        end
      end
    end
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

-- Shift+BackSpace (asus_edit.lua): forget the corrections that produced the
-- text of each span {s=, e=, text=} (key offsets in input). Looked up by the
-- span's keys: one prefix query ("c\t<keys>\t") and one fetch ("r") each.
-- Returns the spans where something was learned (now forgotten).
local function forget_spans(input, spans)
  if not enabled or not db then return {} end
  local now = os.time()
  local found, edits = {}, {}
  for _, sp in ipairs(spans) do
    local syl = syllables(input:sub(sp.s + 1, sp.e))
    if syl and is_cjk_text(sp.text) and utf8.len(sp.text) == #syl then
      local k = input:sub(sp.s + 1, sp.e)
      local prefix = "c\t" .. k .. "\t"
      local xs = {}
      local q = db:query(prefix)
      if q then
        for key, v in q:iter() do
          if key:sub(1, #prefix) ~= prefix then break end
          local x = key:sub(#prefix + 1)
          if x ~= sp.text and parse(v, now)[sp.text] then xs[#xs + 1] = x end
        end
      end
      q = nil
      -- (The reverse entry alone: chosen once, the pair has faded since.)
      if #xs > 0 or db:fetch("r\t" .. k .. "\t" .. sp.text) then
        found[#found + 1] = sp
        edits[#edits + 1] = { k, sp.text, xs }
      end
    end
  end
  if #edits == 0 then return found end
  collectgarbage()  -- release the LevelDB iterators before writing
  for _, e in ipairs(edits) do
    for _, x in ipairs(e[3]) do forget_pair(e[1], x, e[2], now) end
    db:erase("r\t" .. e[1] .. "\t" .. e[2])
  end
  forgot = (forgot and forgot.input == input) and forgot or { input = input, list = {} }
  for _, sp in ipairs(found) do forgot.list[#forgot.list + 1] = { sp.s, sp.e } end
  if applied and applied.input == input then applied = nil end
  return found
end

-- Delete Rime's own user phrase `text` for `keys` (what Shift+Delete does
-- for the highlighted candidate). env: the caller's, which keeps a translator
-- and a Memory of the schema's dictionary for it.
local function forget_user_phrase(env, keys, text)
  if not enabled or not db then return false end
  if env.forget_tr == nil then
    local ok, tr = pcall(function()
      return Component.Translator(env.engine, "", "script_translator@translator")
    end)
    env.forget_tr = ok and tr or false
    local ok2, mem = pcall(function() return Memory(env.engine, env.engine.schema, "translator") end)
    env.forget_mem = ok2 and mem or false
    local ok3, tw = pcall(Opencc, "t2tw.json")
    env.forget_tw = ok3 and tw or false
  end
  if not env.forget_tr or not env.forget_mem then return false end
  local seg = Segment(0, #keys)
  seg.tags = Set({ "abc" })
  local t = env.forget_tr:query(keys, seg)
  if not t then return false end
  local done, n = false, 0
  for c in t:iter() do
    n = n + 1
    if n > 60 then break end
    if c._end == #keys and c.type == "user_phrase" then
      local tw = env.forget_tw and env.forget_tw:convert(c.text) or c.text
      if c.text == text or tw == text then
        local ok, ph = pcall(function() return c:get_genuine():to_phrase() end)
        if ok and ph then
          local ok2 = pcall(function() env.forget_mem:update_userdict(ph.entry, -1, "") end)
          done = done or ok2
        end
      end
    end
  end
  return done
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
  if cand and cand.type == "quick" then return end  -- a date or number form (asus_quick.lua)
  if cand and cand.type ~= "sentence" then
    env.picks[seg.start .. ":" .. cand._end] = true
  end
  -- Number or Chinese for a digit key that is both (asus_lang.lua): which
  -- of the two readings on offer is being picked.
  local mark = require("asus_lang").NUMBER_MARK
  if cand and cand.comment:find(mark, 1, true) then
    env.num_picks[seg.start .. ":" .. cand._end] = "1"
  elseif cand and not cand.text:match("%d") then
    -- The number reading was what the segment showed (first in the list;
    -- at the editing cursor asus_edit.lua lists it as the current text,
    -- without the mark) and something without the digit is picked.
    local top = seg:get_candidate_at(0)
    if index > 0 and top and top.text:match("%d") then
      env.num_picks[seg.start .. ":" .. cand._end] = "0"
    end
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
  if cand.type == "email" or (cand.type == "english" and is_email(cand.text)) then
    forget_email(cand.text)
    ctx:refresh_non_confirmed_composition()
    return kAccepted
  end
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
            out = ShadowCandidate(cand, cand.type, text, cand.comment ~= "" and cand.comment or "學")
            top = text
            if seg.start == 0 then applied = { input = ctx.input, list = used } end
          elseif seg.start == 0 then
            applied = nil
          end
        end
        -- (Not while asus_edit.lua rebuilds the composition around its cursor.)
        if seg.start == 0 and seg._end == #ctx.input and (ctx:get_property("asus_edit") or "") == ""
            and not holding then
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
local function predict(text, limit, min_len)
  if not enabled or not db then return {} end
  min_len = min_len or 1
  local now = os.time()
  local cps = {}
  for _, cp in utf8.codes(text) do cps[#cps + 1] = cp end
  local scored, seen = {}, {}
  for len = math.min(4, #cps), min_len, -1 do
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

-- The character the user picked for syllable keys right after a number that
-- follows the character left ("" at the start), or nil.
local function after_number(left, keys)
  if not enabled or not db then return nil end
  local v = db:fetch("q\t" .. left .. "\t" .. keys)
  return (v and v ~= "") and v or nil
end

-- "1" (number) or "0" (Chinese): what the user picked for a syllable whose
-- first key is a digit and a Zhuyin initial (key: asus_lang.choice_key).
local function number_choice(key)
  if not enabled or not db or not key then return nil end
  local v = db:fetch("d\t" .. key)
  return (v == "1" or v == "0") and v or nil
end

-- Has the user ever picked `text` for `keys`? (Such text is never treated
-- as a typo by asus_typo.lua.)
local function chosen(keys, text)
  return enabled and db and db:fetch("r\t" .. keys .. "\t" .. text) ~= nil or false
end

return { processor = processor, filter = filter, predict = predict, banned = banned, chosen = chosen,
         after_number = after_number, number_choice = number_choice,
         accepted = accepted, english = english, complete_english = complete_english,
         syllables = syllables, is_email = is_email, learn_email = learn_email,
         emails_for = emails_for, forget_email = forget_email,
         forget_spans = forget_spans, forget_user_phrase = forget_user_phrase, MAX_RUN = MAX_RUN,
         words_of = words_of,
         hold = function(on) holding = on and true or false end,
         open = function(config) open(config) end }
