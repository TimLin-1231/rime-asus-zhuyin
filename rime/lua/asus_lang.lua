-- asus_lang.lua
-- Mixed Chinese/English input without mode switching, plus stray-key removal.
--
-- Every letter key is also a Zhuyin key, so each run of keys is segmented by
-- dynamic programming into tokens with costs:
--   Zhuyin syllable  toned 0 | toneless C_TONELESS | unfinished at end C_PREFIX
--                    | lone initial (abbreviation) C_ABBR
--   English word     C_EN_BASE + C_EN_RANK * log10(rank + 1)  (rank 0 = personal)
--   typed with caps  C_CAPS + C_DIGIT per digit (uppercase can never be Zhuyin)
--   stray key        C_STRAY (dropped from the output)
--   typo syllable    C_FIX: not a syllable, but one key changed to a key next
--                    to it on the keyboard makes it one (ㄒㄧㄝ˙ typed with a
--                    neighbour key); asus_typo.lua picks the replacement with
--                    the language model (segment tag asus_fix)
--   number           C_NUM + C_NUM_DIGIT per character (digits, optionally
--                    one decimal point), plus C_NUM_END when it ends the input
--                    in what could still be an unfinished syllable (typing ㄅㄢˋ
--                    passes through "10"), plus C_NUM_OPEN right after an
--                    unfinished syllable (謝謝 ㄒㄧㄝ˙: the 7 is its tone)
--   + C_SWITCH whenever the language changes.
-- Every digit key is also a Zhuyin key (1 ㄅ, 2 ㄉ, 5 ㄓ, 8 ㄚ, 9 ㄞ, 0 ㄢ, tones
-- 3 4 6 7), so a number only wins where the keys are not good Zhuyin:
-- 2026 / 100 / 3.14 are numbers, 204 (但) and 283 (打) stay Chinese.
-- Only when the cheapest plan contains English, number or stray tokens does the
-- segmentor take over; pure Chinese input keeps the stock segmentation.
-- English segments are tagged {asus_en, abc} and numbers {asus_num, abc}, so
-- the Chinese reading of the same keys stays available below them; a Chinese
-- segment typed only with digit keys gets the digits as its second candidate
-- (filter lua_filter@*asus_lang*digits).
--
--   segmentor   lua_segmentor@*asus_lang*segmentor   (before abc_segmentor)
--   translator  lua_translator@*asus_lang*translator
-- Data: opencc/asus_en.json(.txt) and lua/asus_zhuyin_data.lua
-- (tools/build_lang_data.py).

local zy = require("asus_zhuyin_data")
local learn = require("asus_learn")

-- ㄓㄔㄕㄖㄗㄘㄙ are syllables on their own, so almost any letter string parses
-- as toneless Zhuyin; toneless syllables must cost more than an unfinished one.
local C_TONELESS = 1.5
local C_TONELESS_1KEY = 4.5  -- above C_STRAY
local C_PREFIX = 1.0
local C_ABBR = 5.0  -- above C_STRAY: a lone initial mid-word is more likely a stray key
local C_EN_BASE = 1.0
local C_EN_RANK = 0.5
local C_CAPS = 0.5
local C_DIGIT = 1.0
local C_STRAY = 4.0
local C_SWITCH = 0.5
local C_EN_JOIN = 1.5   -- two English words with nothing between them (CP + Ujp)
local C_EN_BASE_CTX = 0.3  -- English base cost right after an English commit
local C_EN_PREFIX = 1.0    -- extra cost of an unfinished word (prefix of a common word)
local C_EN_FIX = 1.5       -- extra cost of a misspelt word (one letter off a common word)
local C_FIX_MIN_LEN = 5    -- shortest misspelling taken as English
local MAX_SYLLABLE = 5
local C_FIX = 2.5        -- below toneless + stray, above any real syllable
local C_NUM = 0.2        -- + 2 x C_SWITCH between Chinese: below a toneless syllable
local C_NUM_DIGIT = 0.05 -- longer numbers cost a little more: 2838 = 打 + 8
local C_NUM_END = 1.0
local C_NUM_OPEN = 4.0   -- = C_STRAY: such a digit is a tone or a stray key

-- Optional overrides from the schema (asus_lang/costs/<name>), for tuning.
local function load_costs(config)
  local function get(name, default)
    return tonumber(config:get_string("asus_lang/costs/" .. name) or "") or default
  end
  C_TONELESS = get("toneless", C_TONELESS)
  C_TONELESS_1KEY = get("toneless_1key", C_TONELESS_1KEY)
  C_PREFIX = get("prefix", C_PREFIX)
  C_ABBR = get("abbr", C_ABBR)
  C_EN_BASE = get("en_base", C_EN_BASE)
  C_EN_RANK = get("en_rank", C_EN_RANK)
  C_STRAY = get("stray", C_STRAY)
  C_SWITCH = get("switch", C_SWITCH)
  C_EN_FIX = get("en_fix", C_EN_FIX)
  C_NUM = get("num", C_NUM)
  C_FIX = get("fix", C_FIX)
  C_NUM_DIGIT = get("num_digit", C_NUM_DIGIT)
  C_NUM_END = get("num_end", C_NUM_END)
  C_NUM_OPEN = get("num_open", C_NUM_OPEN)
end

local ZHUYIN_KEYS = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/- 6347"
local INITIAL_KEYS = "1qaz2wsxedcrfv5tgbyhn"
local IS_KEY, IS_INITIAL = {}, {}
for i = 1, #ZHUYIN_KEYS do IS_KEY[ZHUYIN_KEYS:sub(i, i)] = true end
for i = 1, #INITIAL_KEYS do IS_INITIAL[INITIAL_KEYS:sub(i, i)] = true end

local TONE_KEYS = { [" "] = true, ["6"] = true, ["3"] = true, ["4"] = true, ["7"] = true }

local function is_upper(c) return c:match("%u") ~= nil end
local function in_run(c) return IS_KEY[c] or is_upper(c) end
-- Key k of input belongs to a run. So do, right after a letter or digit:
-- an apostrophe (I'm, don't, user's), + and # (C++, C#), @ and _ (email
-- addresses).
local JOINS = { ["'"] = "%a", ["+"] = "[%w+]", ["#"] = "%w", ["@"] = "[%w._%-]", ["_"] = "%w" }
local function in_run_at(input, k)
  local c = input:sub(k, k)
  if in_run(c) then return true end
  local after = JOINS[c]
  return after ~= nil and k > 1 and input:sub(k - 1, k - 1):match(after) ~= nil
end

-- English lexicon ------------------------------------------------------------
-- Kept in an OpenCC text dictionary (opencc/asus_en.json), i.e. in C++ memory:
-- as a Lua table its 56k entries made librime-lua's per-call GC cost ~40 ms
-- per keystroke. Lookups are memoised per input in a small, short-lived table.
local english_cc
local max_en_len = zy.max_en_len or 24
local memo, memo_input = {}, nil

local prefix_cc
local prefix_memo, prefix_memo_input = {}, nil

local fix_cc
local fix_memo, guess_memo = {}, {}

local function load_english()
  if english_cc == nil then
    local ok, cc = pcall(Opencc, "asus_en.json")
    english_cc = ok and cc or false
  end
  if prefix_cc == nil then  -- English autocomplete (opencc/asus_en_prefix.json)
    local ok, cc = pcall(Opencc, "asus_en_prefix.json")
    prefix_cc = ok and cc or false
  end
  if fix_cc == nil then  -- spelling correction (opencc/asus_en_fix.json)
    local ok, cc = pcall(Opencc, "asus_en_fix.json")
    fix_cc = ok and cc or false
  end
end

-- Best common word starting with key (>= 3 lowercase letters): {word=, rank=} or nil.
local function english_prefix(key)
  if #key < 3 or not prefix_cc then return nil end
  local hit = prefix_memo[key]
  if hit ~= nil then return hit or nil end
  local entry = learn.complete_english(key) or false  -- learned words first
  local forms = not entry and prefix_cc:convert_word(key)
  local value = forms and forms[1]
  if value then
    local rank, word = value:match("^(%d+):(.+)$")
    if rank then entry = { word = word, rank = tonumber(rank) } end
  end
  prefix_memo[key] = entry
  return entry or nil
end

-- key (lowercase) -> {word=, rank=} or nil
local function english(key)
  if #key > max_en_len then return nil end
  local hit = memo[key]
  if hit ~= nil then return hit or nil end
  -- Words the user has committed as English (lua/asus_learn.lua) rank first.
  local learned = learn.english(key)
  if learned then
    memo[key] = learned
    return learned
  end
  local entry = false
  if english_cc then
    local forms = english_cc:convert_word(key)
    local value = forms and forms[1]
    if value then
      -- (a and b:match()) would keep only match's first capture
      local rank, word = value:match("^(%d+):(.+)$")
      if rank then entry = { word = word, rank = tonumber(rank) } end
    end
  end
  memo[key] = entry
  return entry or nil
end

-- "rank:word rank:word" forms -> list of {word=, rank=}
local function ranked(forms)
  local out = {}
  for _, value in ipairs(forms or {}) do
    local rank, word = value:match("^(%d+):(.+)$")
    if rank then out[#out + 1] = { word = word, rank = tonumber(rank) } end
  end
  return out
end

-- Edit distance with adjacent swaps (optimal string alignment).
local function edit_distance(a, b)
  local la, lb = #a, #b
  if math.abs(la - lb) > 2 then return 3 end
  local d = {}
  for i = 0, la do d[i] = { [0] = i } end
  for j = 0, lb do d[0][j] = j end
  for i = 1, la do
    for j = 1, lb do
      local cost = (a:sub(i, i) == b:sub(j, j)) and 0 or 1
      local v = math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
      if i > 1 and j > 1 and a:sub(i, i) == b:sub(j - 1, j - 1) and a:sub(i - 1, i - 1) == b:sub(j, j) then
        v = math.min(v, d[i - 2][j - 2] + 1)
      end
      d[i][j] = v
    end
  end
  return d[la][lb]
end

-- Spelling corrections for key (>= FIX_MIN letters): {word=, rank=, dist=},
-- best first. Keys of opencc/asus_en_fix are words and their one-letter
-- deletions, so looking up key and its deletions finds every word one
-- extra / missing / wrong / swapped letter away.
local FIX_MIN = 4
local function corrections(key)
  if #key < FIX_MIN or not fix_cc or not key:match("^%l+$") then return {} end
  local hit = fix_memo[key]
  if hit then return hit end
  local seen, out = {}, {}
  local probes = { key }
  for i = 1, #key do probes[#probes + 1] = key:sub(1, i - 1) .. key:sub(i + 1) end
  for _, probe in ipairs(probes) do
    for _, e in ipairs(ranked(fix_cc:convert_word(probe))) do
      local lw = e.word:lower()
      if not seen[lw] and lw ~= key then
        seen[lw] = true
        local dist = edit_distance(key, lw)
        if dist <= 2 then
          e.dist = dist
          out[#out + 1] = e
        end
      end
    end
  end
  table.sort(out, function(a, b)
    if a.dist ~= b.dist then return a.dist < b.dist end
    return a.rank < b.rank
  end)
  fix_memo[key] = out
  return out
end

-- Completions of key (>= 3 letters), best first; learned words first.
local function completions(key)
  local out = {}
  local learned = learn.complete_english(key)
  if learned then out[1] = learned end
  if #key >= 3 and prefix_cc then
    for _, e in ipairs(ranked(prefix_cc:convert_word(key))) do
      if #e.word > #key and (not learned or e.word ~= learned.word) then out[#out + 1] = e end
    end
  end
  return out
end

-- Contractions (the lexicon has letters only). Typed without the apostrophe
-- (dont, im) they are offered as the first guess.
local CONTRACTIONS = {}
local CONTRACTION_OF = {}  -- "dont" -> "don't"
for w in ("i'm i'll i've i'd you're you'll you've you'd he's he'll he'd she's she'll "
          .. "she'd it's it'll we're we'll we've we'd they're they'll they've they'd "
          .. "that's that'll there's here's what's where's who's how's let's "
          .. "don't doesn't didn't can't couldn't won't wouldn't shouldn't isn't "
          .. "aren't wasn't weren't haven't hasn't hadn't mustn't needn't ain't "
          .. "y'all o'clock"):gmatch("%S+") do
  CONTRACTIONS[w] = true
  local bare = w:gsub("'", "")
  -- (well, were, shell, hell, ill... are words themselves)
  if bare ~= "well" and bare ~= "were" and bare ~= "shell" and bare ~= "hell" and bare ~= "ill"
      and bare ~= "id" and bare ~= "its" and bare ~= "lets" and bare ~= "wed" and bare ~= "hed"
      and bare ~= "shed" and bare ~= "wont" and bare ~= "cant" and bare ~= "whos" then
    CONTRACTION_OF[bare] = w
  end
end
CONTRACTION_OF["cant"] = "can't"  -- "cant" is a word, but rarely the one meant
CONTRACTION_OF["wont"] = "won't"

-- Typed form of a contraction: I'm keeps the capital I.
local function contraction_form(w)
  if w:sub(1, 2) == "i'" then return "I" .. w:sub(2) end
  return w
end

local GUESS_MAX = 8
local COMMON = 20000   -- a completion this common goes before corrections
local C_FIX_SCORE = 2.5  -- a correction ranks as if 10^2.5 times rarer per edit
-- Words to suggest for typed English key (lower case): completions of an
-- unfinished word and corrections of a misspelt one, best first.
local function guesses(key, entry)
  local hit = guess_memo[key]
  if hit then return hit end
  -- Remembered email addresses starting with what was typed come first.
  local mails = (#key >= 3) and learn.emails_for(key, 3) or {}
  if key:find("@", 1, true) or key:find("%.%a+$") then
    guess_memo[key] = mails
    return mails
  end
  if key:find("'", 1, true) then
    -- don' -> don't; user' -> user's
    local out = {}
    for w in pairs(CONTRACTIONS) do
      if #w > #key and w:sub(1, #key) == key then out[#out + 1] = contraction_form(w) end
    end
    table.sort(out, function(a, b) return #a < #b or (#a == #b and a < b) end)
    if key:match("^%a+'$") and english(key:sub(1, -2)) and #out == 0 then out[1] = key .. "s" end
    while #out > GUESS_MAX do table.remove(out) end
    guess_memo[key] = out
    return out
  end
  local comps = completions(key)
  local fixes = {}
  if not entry or entry.rank >= 100000 then fixes = corrections(key) end
  local list
  if comps[1] and comps[1].rank <= COMMON then
    list = {}
    for _, e in ipairs(comps) do list[#list + 1] = e end
    for _, e in ipairs(fixes) do list[#list + 1] = e end
  else
    list = {}
    for _, e in ipairs(comps) do list[#list + 1] = { e = e, score = math.log(e.rank + 1, 10) } end
    for _, e in ipairs(fixes) do
      list[#list + 1] = { e = e, score = math.log(e.rank + 1, 10) + C_FIX_SCORE * e.dist }
    end
    table.sort(list, function(a, b) return a.score < b.score end)
    for i, x in ipairs(list) do list[i] = x.e end
  end
  local out, seen = {}, { [key] = true }
  for _, m in ipairs(mails) do out[#out + 1] = m end
  if CONTRACTION_OF[key] then  -- dont -> don't
    out[#out + 1] = contraction_form(CONTRACTION_OF[key])
  end
  for _, e in ipairs(list) do
    local lw = e.word:lower()
    if not seen[lw] then
      seen[lw] = true
      out[#out + 1] = e.word
      if #out >= GUESS_MAX then break end
    end
  end
  guess_memo[key] = out
  return out
end

-- Can one key of tok, changed to a neighbouring key, make it a syllable?
local typo_memo = {}
local function has_typo_fix(tok)
  local hit = typo_memo[tok]
  if hit == nil then
    hit = #require("asus_typo").variants(tok) > 0
    typo_memo[tok] = hit
  end
  return hit
end

local function zh_cost(tok, at_end)
  if zy.toned[tok] then return 0 end
  if at_end and zy.prefix[tok] then return C_PREFIX end
  if zy.toneless[tok] then
    -- A one-key toneless syllable mid-input (ㄧ, ㄚ, ㄓ...) is what a stray
    -- key looks like; at the end it is just an unfinished syllable.
    return (#tok == 1) and C_TONELESS_1KEY or C_TONELESS
  end
  if #tok <= 2 and IS_INITIAL[tok:sub(1, 1)]
      and (#tok == 1 or (" 6347"):find(tok:sub(2, 2), 1, true)) then
    return C_ABBR  -- lone initial, optionally with a tone key
  end
  return nil
end

-- Short English words are excluded from the lexicon (ru, su, cl are common
-- Zhuyin), but right after English was committed ("I have a ...") these are
-- far more likely than a toneless syllable.
local SHORT_WORDS = {}
for w in ("a i am an as at be by do go he hi if in is it me my no of oh ok on "
          .. "or so to up us we"):gmatch("%a+") do SHORT_WORDS[w] = true end

-- Can keys be read as toneless Zhuyin syllables averaging >= 1.5 keys each?
-- (pyhton only splits into one-key syllables, which is not how Chinese is
-- typed; clgck = ㄏㄠ ㄕ ㄏㄜ is.)
local function zhuyin_like(keys)
  local n = #keys
  local fewest = { [0] = 0 }  -- fewest syllables covering keys[1..i]
  for i = 1, n do
    for len = 1, math.min(MAX_SYLLABLE - 1, i) do
      local prev = fewest[i - len]
      local syl = keys:sub(i - len + 1, i)
      if prev and (zy.toneless[syl] or zy.toned[syl]) and (not fewest[i] or prev + 1 < fewest[i]) then
        fewest[i] = prev + 1
      end
    end
  end
  return fewest[n] ~= nil and n / fewest[n] >= 1.5
end

-- en_ctx: the previous commit was English, so English is the expected language.
local function word_cost(tok, en_ctx, at_end)
  local base = en_ctx and C_EN_BASE_CTX or C_EN_BASE
  if tok:find("'", 1, true) then
    -- I'm, don't; user's (an English word + 's); don' while still typing.
    local low = tok:lower()
    if CONTRACTIONS[low] then return base + C_EN_RANK * 2 end
    local stem, tail = low:match("^(%a+)'(%a*)$")
    local e = stem and english(stem)
    if not e and stem and (#stem >= 3 or SHORT_WORDS[stem]) and not zhuyin_like(stem) then
      e = english_prefix(stem)
    end
    if not stem or (tail ~= "s" and not (tail == "" and at_end)) then return nil end
    if tail == "" then
      for w in pairs(CONTRACTIONS) do
        if w:sub(1, #low) == low then return base + C_EN_RANK * 2 + C_EN_PREFIX end
      end
    end
    if not e then return nil end
    return base + C_EN_RANK * math.log(e.rank + 1, 10) + (tail == "" and C_EN_PREFIX or 0)
  end
  local entry = english(tok:lower())
  local best
  if entry then best = base + C_EN_RANK * math.log(entry.rank + 1, 10) end
  if en_ctx and SHORT_WORDS[tok:lower()] then best = math.min(best or math.huge, base) end
  -- dont, doesnt, im: a contraction typed without its apostrophe (Tab adds it)
  if CONTRACTION_OF[tok:lower()] then best = math.min(best or math.huge, base + C_EN_RANK * 2) end
  -- A misspelt word one letter off a common word (recieve) is English too,
  -- unless the keys read naturally as toneless Zhuyin (clgck = ㄏㄠㄕㄏㄜ).
  if not best and #tok >= C_FIX_MIN_LEN and tok:match("^%l+$") and not zhuyin_like(tok) then
    local fix = corrections(tok)[1]
    if fix and fix.dist == 1 then
      best = base + C_EN_RANK * math.log(fix.rank + 1, 10) + C_EN_FIX
    end
  end
  -- Capitalised tokens (GPU, GPT4, DE-LSTM) are English even if unknown.
  -- Digits double as tone keys, so each one costs extra: "GPU2k7" must
  -- split as GPU + 的, not GPU2 + ㄜ˙.
  -- (Capitals then small letters is an acronym and then Zhuyin, CPU + jp
  -- = CPU溫, unless it is a plural: GPUs, APIs.)
  local caps_tail = tok:match("^%u%u+(%l[%w%-]*)$")
  if tok:match("^%u[%w%-]*$") and (not caps_tail or caps_tail == "s" or caps_tail == "es") then
    local _, digits = tok:gsub("%d", "")
    best = math.min(best or math.huge, C_CAPS + C_DIGIT * digits)
  end
  -- C++, C#, F#
  if tok:match("^%u%w*[+#]+$") and #tok <= 4 then
    best = math.min(best or math.huge, C_CAPS)
  end
  return best
end

-- Endings of names with a dot: domains and file names (node.js, google.com).
local DOT_ENDS = {}
for w in ("com org net edu gov tw io ai dev app me co uk jp cn hk us de fr kr sg cc tv info biz "
          .. "js ts py md txt pdf html htm css json xml yaml yml csv lua sh cpp hpp h c java go rs "
          .. "exe dll zip png jpg jpeg gif svg mp3 mp4 doc docx xls xlsx ppt pptx ipynb log ini cfg "
          .. "toml sql db"):gmatch("%S+") do DOT_ENDS[w] = true end

local C_EN_PUNCT = 0.2  -- a comma / full stop typed right after an English word
local C_DOTTED = 1.5    -- node.js, www.google.com
local C_EMAIL = 0.5     -- name@host
local C_EMAIL_CHAR = 0.1  -- per character before the @ (where the address starts)

-- Cost of tok as English: a word, or words joined by - (e-mail), a name with
-- dots (node.js), an email address, or any of these plus , or . after it.
local function en_cost(tok, en_ctx, at_end)
  local base = en_ctx and C_EN_BASE_CTX or C_EN_BASE
  if tok:find("@", 1, true) then
    local user = tok:match("^(%w[%w._%%+%-]*)@[%w.%-]*$")
    if not user then return nil end
    -- A known address starting here wins over reading its start as Zhuyin.
    local known = #learn.emails_for(tok, 1) > 0 and 0 or C_EMAIL_CHAR * #user
    return C_EMAIL + known
  end
  local stem = tok:match("^(.*[%w'+#])[.,]$")
  if stem then
    local c = en_cost(stem, en_ctx, false)
    return c and c + C_EN_PUNCT
  end
  if tok:find(".", 1, true) then
    local first, last = tok:match("^(%a[%w%-]*)%..-(%w*)$")
    if not first or tok:find("..", 1, true) then return nil end
    if DOT_ENDS[last:lower()] then return base + C_DOTTED end
    -- Still typing it: www.goo, github.c
    local fl = first:lower()
    if at_end and (fl == "www" or (#fl >= 3 and english(fl))) and last:match("^%a*$") then
      return base + C_DOTTED + C_EN_PREFIX
    end
    return nil
  end
  if tok:find("-", 1, true) and not tok:match("^%u") then
    -- e-mail, x-ray, real-time: every part a word (or one letter), one of
    -- them a real word; the last may be unfinished at the end of the input.
    if tok:find("^%-") or tok:find("%-%-") or tok:find("%-$") then return nil end
    local parts = {}
    for part in tok:gmatch("[^%-]+") do parts[#parts + 1] = part:lower() end
    local worst, real = 0, false
    for k, part in ipairs(parts) do
      if not part:match("^%a+$") then return nil end
      local e = english(part)
      local extra = 0
      if not e and k == #parts and at_end then e = english_prefix(part); extra = C_EN_PREFIX end
      if e then
        real = real or #part >= 2
        worst = math.max(worst, C_EN_RANK * math.log(e.rank + 1, 10) + extra)
      elseif #part > 1 and not SHORT_WORDS[part] then
        return nil
      end
    end
    if not real then return nil end
    return base + worst
  end
  return word_cost(tok, en_ctx, at_end)
end

-- Cheapest segmentation of run (a string of keys). Returns a list of
-- {kind = "zh"|"en"|"stray", i, j} with 1-based inclusive i..j, and whether
-- the plan is "special" (contains en or stray tokens).
local function plan_run(run, en_ctx)
  local n = #run
  local INF = math.huge
  -- best[i][lang]: cost of covering run[1..i-1]; lang of last token
  local best, back = {}, {}
  for i = 1, n + 1 do best[i] = { zh = INF, en = INF, num = INF, none = INF }; back[i] = {} end
  best[1].none = 0
  local function relax(i, from_lang, j, lang, cost, kind)
    local base = best[i][from_lang]
    if base == INF or cost == nil then return end
    local switch = (from_lang ~= "none" and lang ~= "none" and from_lang ~= lang) and C_SWITCH or 0
    if from_lang == "en" and kind == "en" and best[i].en_word then switch = C_EN_JOIN end
    local total = base + cost + switch
    local to_lang = (lang == "none") and from_lang or lang
    if total < best[j + 1][to_lang] then
      best[j + 1][to_lang] = total
      back[j + 1][to_lang] = { i = i, from = from_lang, kind = kind }
      if to_lang == "en" then best[j + 1].en_word = (kind == "en") end
    end
  end
  for i = 1, n do
    for _, from in ipairs({ "none", "zh", "en", "num" }) do
      if best[i][from] < INF then
        -- Zhuyin syllables
        for j = i, math.min(n, i + MAX_SYLLABLE - 1) do
          local tok = run:sub(i, j)
          if tok:find("[%u'+#@_]") then break end
          local c = zh_cost(tok, j == n)
          relax(i, from, j, "zh", c, "zh")
          if c == nil and #tok >= 2 and TONE_KEYS[tok:sub(-1)] and has_typo_fix(tok) then
            relax(i, from, j, "zh", C_FIX, "fix")
          end
        end
        -- English words / capitalised runs
        local caps = is_upper(run:sub(i, i))
        for j = i + 1, math.min(n, i + math.max(max_en_len, 32) - 1) do
          local tok = run:sub(i, j)
          if not (tok:match("^%a[%a%-'.,]*$") or (caps and tok:match("^%u[%w%-]*[+#]*[.,]?$"))
                  or tok:match("^%a[%w%-]*%.[%w.%-]*$") or tok:match("^%a[%a+#]*[.,]?$")) then
            break
          end
          local cost = en_cost(tok, en_ctx, j == n)
          if not cost and j == n and tok:match("^%l+$") then
            -- Unfinished English word at the end ("pyth" -> python).
            local p = english_prefix(tok)
            if p then
              cost = (en_ctx and C_EN_BASE_CTX or C_EN_BASE)
                + C_EN_RANK * math.log(p.rank + 1, 10) + C_EN_PREFIX
            end
          end
          relax(i, from, j, "en", cost, "en")
        end
        -- email addresses (s108@gmail.com): the @ decides where it starts
        if run:find("@", i, true) and run:sub(i, i):match("%w") then
          for j = (run:find("@", i, true)), n do
            local tok = run:sub(i, j)
            if not tok:match("^%w[%w._%%+%-]*@[%w.%-]*$") then break end
            if j == n or not run:sub(j + 1, j + 1):match("[%w.%-]") then
              relax(i, from, j, "en", en_cost(tok, en_ctx, j == n), "en")
            end
          end
        end
        if is_upper(run:sub(i, i)) then
          relax(i, from, i, "en", C_CAPS, "en")  -- single capital letter
        elseif en_ctx and SHORT_WORDS[run:sub(i, i)] then
          relax(i, from, i, "en", C_EN_BASE_CTX, "en")  -- "a" after English
        end
        -- numbers: 2026, 3.14
        if run:sub(i, i):match("%d") then
          local prev = (i > 1) and run:sub(i - 1, i - 1) or " "
          local open = not TONE_KEYS[prev] and not prev:match("%d")
          for j = i, n do
            local tok = run:sub(i, j)
            if not tok:match("^%d+%.?%d*$") then break end
            if tok:match("%d$") then
              local cost = C_NUM + C_NUM_DIGIT * #tok
              if j == n then
                -- Still typing a syllable? (10 -> 辦, 2832 -> 打 + ㄉ...)
                for k = 1, math.min(3, #tok) do
                  local tail = tok:sub(-k)
                  if zy.prefix[tail] or zy.toneless[tail] then cost = cost + C_NUM_END; break end
                end
              end
              if open then cost = cost + C_NUM_OPEN end
              relax(i, from, j, "num", cost, "num")
            end
          end
        end
        -- stray key; an apostrophe (+ # @ _) no word takes is kept as it is
        if JOINS[run:sub(i, i)] then
          relax(i, from, i, "en", C_STRAY, "lit")
        else
          relax(i, from, i, "none", C_STRAY, "stray")
        end
      end
    end
  end
  local lang, cost = "none", INF
  for _, l in ipairs({ "none", "zh", "en", "num" }) do
    if best[n + 1][l] < cost then lang, cost = l, best[n + 1][l] end
  end
  if cost == INF then return nil, false, cost end
  local tokens, j, special = {}, n + 1, false
  while j > 1 do
    local b = back[j][lang]
    table.insert(tokens, 1, { kind = b.kind, i = b.i, j = j - 1 })
    if b.kind ~= "zh" then special = true end
    j, lang = b.i, b.from
  end
  -- merge adjacent Zhuyin syllables into one span (typo syllables included,
  -- remembered in .fixes)
  local merged = {}
  for _, t in ipairs(tokens) do
    local kind = (t.kind == "fix") and "zh" or (t.kind == "lit") and "en" or t.kind
    local last = merged[#merged]
    if last and last.kind == "zh" and kind == "zh" then
      last.j = t.j
    else
      last = { kind = kind, i = t.i, j = t.j, fixes = {} }
      merged[#merged + 1] = last
    end
    if t.kind == "fix" then last.fixes[#last.fixes + 1] = { i = t.i, j = t.j } end
  end
  return merged, special, cost
end

-- Plan for input[floor:], cached; positions are 0-based [start, end) in input.
local function plan_input(env, input, floor, en_ctx)
  local cache_key = (en_ctx and "e" or "z") .. floor .. ":" .. input
  if env.cache_key == cache_key then return env.cache_plan end
  if memo_input ~= input then memo, memo_input = {}, input end
  if prefix_memo_input ~= input then
    prefix_memo, prefix_memo_input = {}, input
    fix_memo, guess_memo = {}, {}
  end
  local spans = {}
  -- Shortcut abbreviations (ㄉㄓ) are runs of lone initials by design; leave
  -- them to the stock segmentation so asus_shortcuts.lua can match them.
  if env.shortcuts[input] then
    env.cache_key, env.cache_plan = cache_key, spans
    return spans
  end
  local pos = floor + 1
  while pos <= #input do
    if in_run_at(input, pos) then
      local e = pos
      while e < #input and in_run_at(input, e + 1) do e = e + 1 end
      -- English context only applies to the run the composition starts with.
      local toks, special, cost = plan_run(input:sub(pos, e), en_ctx and pos == 1)
      if env.debug then
        local parts = {}
        for _, t in ipairs(toks or {}) do parts[#parts + 1] = t.kind .. ":" .. input:sub(pos + t.i - 1, pos + t.j - 1) end
        log.info(string.format("asus_lang run=%q cost=%.2f tokens=%s", input:sub(pos, e), cost or -1,
          table.concat(parts, " ")))
      end
      if toks and special then
        for _, t in ipairs(toks) do
          local fixes = {}
          for _, f in ipairs(t.fixes or {}) do
            fixes[#fixes + 1] = (pos - 1 + f.i - 1) .. ":" .. (pos - 1 + f.j)
          end
          spans[#spans + 1] = { kind = t.kind, s = pos - 1 + t.i - 1, e = pos - 1 + t.j, fixes = fixes }
        end
      end
      pos = e + 1
    else
      pos = pos + 1
    end
  end
  env.cache_key, env.cache_plan = cache_key, spans
  return spans
end

-- Segments the user picked from the menu are never re-planned.
local function is_fixed(seg)
  return seg.status == "kSelected" or seg.status == "kConfirmed"
end

local function fixed_floor(segmentation)
  local floor = 0
  for _, seg in ipairs(segmentation:get_segments()) do
    if is_fixed(seg) then floor = seg._end else break end
  end
  return floor
end

-- librime keeps segments from the previous keystroke and only segments the
-- new tail. Typing on can change the best plan for keys already segmented
-- ("bec" is a word, "because" too), so kept segments before `start` must
-- still agree with the plan; otherwise segmentation is rewound to `floor`.
local function kept_matches(segmentation, spans, start, new_input, input)
  local by_start = {}
  for _, sp in ipairs(spans) do
    -- With a number in the plan, segments kept from the previous key are
    -- redone (measure words and 第 depend on the neighbouring number).
    if new_input and sp.kind == "num" and start > 0 then return false end
    by_start[sp.s] = sp
  end
  for _, seg in ipairs(segmentation:get_segments()) do
    if seg.start >= start then break end
    if not is_fixed(seg) then
      local special = seg:has_tag("asus_en") or seg:has_tag("asus_stray") or seg:has_tag("asus_num")
      local sp = by_start[seg.start]
      local in_keys = false  -- does the segment cover keys of a run?
      for k = seg.start + 1, seg._end do
        if in_run_at(input, k) then in_keys = true; break end
      end
      if not in_keys and not special then
        -- Punctuation between runs is librime's own (I'm before the fix
        -- for apostrophes looped forever: rewind, re-add, mismatch again).
      elseif #spans == 0 then
        -- A plain Chinese plan is one stock segment per run of keys: a kept
        -- boundary inside a run (left by a number at "283" before 打電話)
        -- would split the sentence and hide context from the language model
        -- and rules. Boundaries at punctuation are librime's own.
        local e = seg._end
        if special or (e < #input and in_run_at(input, e) and in_run_at(input, e + 1)) then
          return false
        end
      elseif not sp or sp.e ~= seg._end or (sp.kind ~= "zh") ~= special then
        return false
      end
    end
  end
  return true
end

local function rewind(segmentation)
  while not segmentation:empty() do
    local last = segmentation:back()
    if is_fixed(last) and last._end > last.start then break end
    segmentation:pop_back()
  end
  if not segmentation:empty() then segmentation:forward() end
end

-- Segmentor ------------------------------------------------------------------
local segmentor = {}

-- Safety net. librime calls the segmentors until the input is covered, and
-- rewinding is the one thing here that moves backwards: a kept segment that
-- never agrees with the plan is rewound and added again without end, which
-- freezes the whole input method (it happened twice: I'm, API的用法 + Down).
-- More than GUARD_REWINDS rewinds of one input within GUARD_SECONDS cannot
-- be typing, so that input is left to the stock segmentors: the conversion
-- may be poor, but the next key works.
local GUARD_REWINDS, GUARD_SECONDS = 300, 1.0

local function guard_tripped(env, input)
  local now = os.clock()  -- wall time on Windows (MSVC CRT)
  if env.guard_input ~= input or now - env.guard_since > GUARD_SECONDS then
    env.guard_input, env.guard_since, env.guard_count, env.guard_off = input, now, 0, false
  end
  env.guard_count = env.guard_count + 1
  if env.guard_count > GUARD_REWINDS and not env.guard_off then
    env.guard_off = true
    log.error(string.format("asus_lang.lua: segmentation of %d keys did not settle after %d rewinds;"
      .. " using the stock segmentation for this input", #input, GUARD_REWINDS))
  end
  return env.guard_off
end

function segmentor.init(env)
  load_english()
  load_costs(env.engine.schema.config)
  env.shortcuts = require("asus_shortcuts").load(rime_api.get_user_data_dir() .. "/shortcuts.txt")
  env.debug = env.engine.schema.config:get_bool("asus_lang/debug") or false
  env.guard_input, env.guard_since, env.guard_count, env.guard_off = nil, 0, 0, false
end

function segmentor.func(segmentation, env)
  local input = segmentation.input
  if type(input) == "function" then input = segmentation:input() end
  local start = segmentation:get_current_start_position()
  local ctx = env.engine.context
  local en_ctx = ctx:get_property("asus_en_ctx") == "1"
  if env.guard_off and env.guard_input == input then return true end  -- see guard_tripped
  local spans = plan_input(env, input, fixed_floor(segmentation), en_ctx)
  -- Editing inside the composition (asus_edit.lua): one segment from the
  -- cursor to the end, so the menu lists what starts at the cursor.
  local edit_span = tonumber(ctx:get_property("asus_edit_span") or "")
  if edit_span and edit_span == start and edit_span < #input then
    local kind = (ctx:get_property("asus_edit_unit_end") or ""):match("(%a+)$")  -- "<unit end><sep><kind>"
    -- The plan before the cursor stays: an English word kept there must
    -- still match it, or segmentation is rewound and redone forever.
    local before = {}
    for _, sp in ipairs(spans) do
      if sp.e <= edit_span then before[#before + 1] = sp end
    end
    before[#before + 1] = { kind = kind == "num" and "open_num" or kind == "en" and "open_en" or "zh",
                            s = edit_span, e = #input }
    spans = before
  end
  -- Typo syllables for asus_typo.lua: "start:end" key ranges.
  local fixes = {}
  for _, sp in ipairs(spans) do
    for _, f in ipairs(sp.fixes or {}) do fixes[#fixes + 1] = f end
  end
  ctx:set_property("asus_typo_fix", table.concat(fixes, ","))
  local new_input = env.seg_input ~= input
  env.seg_input = input
  local call_start = start
  if not kept_matches(segmentation, spans, start, new_input, input) then
    if guard_tripped(env, input) then return true end
    rewind(segmentation)
    start = segmentation:get_current_start_position()
  end
  if env.debug then
    local parts = {}
    for _, s in ipairs(spans) do parts[#parts + 1] = s.kind .. "[" .. s.s .. "," .. s.e .. ")" end
    local last = (not segmentation:empty()) and segmentation:back() or nil
    log.info(string.format("asus_lang input=%q start=%d last=%s plan=%s", input, start,
      last and ("[" .. last.start .. "," .. last._end .. ")") or "-", table.concat(parts, " ")))
  end
  for idx, span in ipairs(spans) do
    if span.s == start then
      -- While typing, librime keeps the open segment from the previous
      -- keystroke and AddSegment always prefers the longer one, so a
      -- differently sized segment at this position has to be dropped first.
      -- The empty placeholder librime puts at `start` must stay: it is what
      -- makes `start` the current position (GetCurrentStartPosition returns
      -- back().start). After dropping a kept segment, forward() recreates it.
      if not segmentation:empty() then
        local last = segmentation:back()
        if last.start == start and last._end > last.start and last._end ~= span.e then
          segmentation:pop_back()
          if not segmentation:empty() then segmentation:forward() end
        end
      end
      local seg = Segment(span.s, span.e)
      if span.kind == "open_num" then
        seg.tags = Set({ "asus_num", "asus_open", "abc" })
      elseif span.kind == "open_en" then
        seg.tags = Set({ "asus_en", "asus_open", "abc" })
      elseif span.kind == "en" then
        seg.tags = Set({ "asus_en", "abc" })
      elseif span.kind == "num" then
        seg.tags = Set({ "asus_num", "abc" })
      elseif span.kind == "stray" then
        seg.tags = Set({ "asus_stray" })
      elseif span.fixes and #span.fixes > 0 then
        seg.tags = Set({ "abc", "asus_fix" })
      else
        seg.tags = Set({ "abc" })
      end
      segmentation:add_segment(seg)
      -- librime stops segmenting when a call ends at or before the position
      -- it started from, so after a rewind the spans up to there are all
      -- added in this call.
      local nxt = spans[idx + 1]
      if not (span.e <= call_start and nxt and nxt.s == span.e and segmentation:forward()) then
        return false  -- this span is decided; skip the stock segmentors
      end
      start = span.e
    end
  end
  return true
end

-- Completion to offer for typed key: when it is not a word, or only a rare
-- one ("beca", "datas") and the completion is at least 10x more common.
local function completion_for(key, entry)
  local p = english_prefix(key)
  if not p or #p.word <= #key then return nil end
  if entry and entry.rank <= p.rank * 10 then return nil end
  return p
end

-- Translator -----------------------------------------------------------------
local translator = {}

function translator.init(env)
  load_english()
end

function translator.func(input, seg, env)
  -- A number / English word at the editing cursor (asus_edit.lua): the
  -- segment runs to the end of the input, its candidates end with the word.
  local s, e = seg.start, seg._end
  if seg:has_tag("asus_open") then
    local unit_end = tonumber((env.engine.context:get_property("asus_edit_unit_end") or ""):match("^(%d+)"))
    if unit_end and unit_end > s and unit_end <= e then
      e = unit_end
      input = input:sub(1, e - s)
    end
  end
  if seg:has_tag("asus_stray") then
    local cand = Candidate("stray", s, e, "", "〔略過贅字 " .. input .. "〕")
    cand.quality = 1000
    yield(cand)
    return
  end
  if seg:has_tag("asus_num") then
    local cand = Candidate("number", s, e, input, "")
    cand.quality = 1000
    yield(cand)
    return
  end
  if not seg:has_tag("asus_en") then return end
  local entry = english(input:lower())
  -- Typed capitals are kept as typed. Lowercase input keeps the lexicon form
  -- only for acronyms / inner capitals (API, PINN, iPhone), not for plain
  -- capitalisation (lexicon "Demo" -> demo).
  local shown = input
  if seg:has_tag("asus_ascii") then
    -- English mode (asus_english.lua): exactly as typed; forms are guesses.
  elseif entry and not input:find("%u") and not entry.word:match("^%u%l*$") then
    shown = entry.word
  elseif CONTRACTIONS[input] then
    shown = contraction_form(input)  -- i'm -> I'm
  end
  -- The typed word, then guesses (completions, spelling corrections); the
  -- first guess is marked ⇥: Tab takes it (asus_predict.lua, english_tab).
  local cand = Candidate("english", s, e, shown, "")
  cand.quality = 1000
  yield(cand)
  local q = 999
  local function more(word, comment)
    local c = Candidate("english", s, e, word, comment)
    c.quality = q
    q = q - 1
    yield(c)
  end
  if entry and entry.word ~= shown then more(entry.word, "") end
  for i, word in ipairs(guesses(input:lower(), entry)) do
    -- Keep the typed capitalisation: Pyth -> Python.
    if input:match("^%u") and not word:match("%u") then word = word:sub(1, 1):upper() .. word:sub(2) end
    if word ~= shown then more(word, i == 1 and "⇥" or "") end
  end
end

-- Remainder that completes the typed English prefix ("pyth" -> "on"), or nil.
-- First guess for the typed English word, or nil (what Tab takes).
local function first_guess(typed)
  load_english()
  local key = typed:lower()
  local word = guesses(key, english(key))[1]
  if word and typed:match("^%u") and not word:match("%u") then
    word = word:sub(1, 1):upper() .. word:sub(2)
  end
  return word
end

local function complete_english(typed)
  load_english()
  local p = completion_for(typed:lower(), english(typed:lower()))
  if not p or #p.word <= #typed then return nil end
  return p.word:sub(#typed + 1)
end

-- Filter -------------------------------------------------------------------
--   * A Chinese segment typed only with digit keys gets the digits as its
--     second candidate (204 -> 但, then 204); a number gets full-width digits.
--   * Measure words: the Chinese right after a number is converted without
--     the number as context (2盃, 18碎), so a first syllable read as a common
--     measure word becomes that word (2杯, 18歲); ㄉㄧˋ right before a number
--     becomes 第, and ㄓㄤ after 第 + number is 章.
-- Segments are found by the candidate's span: earlier segments are
-- re-translated too, so composition:back() is not necessarily this one.
local FULL = { ["0"] = "０", ["1"] = "１", ["2"] = "２", ["3"] = "３", ["4"] = "４",
  ["5"] = "５", ["6"] = "６", ["7"] = "７", ["8"] = "８", ["9"] = "９", ["."] = "．" }

-- Dachen keys (MOE readings) -> measure word. 隻/支 (both ㄓ) are left to
-- the language model.
local MEASURE = {
  ["ek4"] = "個", ["1o "] = "杯", ["5; "] = "張", ["njo4"] = "歲", ["m06"] = "元",
  ["dj94"] = "塊", ["2u03"] = "點", ["zp "] = "分", ["aul3"] = "秒", ["h4"] = "次",
  ["wu0 "] = "天", ["su06"] = "年", ["m,4"] = "月", ["b4"] = "日", ["cl4"] = "號",
  ["x.6"] = "樓", ["1p3"] = "本", ["ru04"] = "件", ["zp4"] = "份", ["u,4"] = "頁",
  ["2j4"] = "度", ["au/6"] = "名", ["jo4"] = "位", ["bp6"] = "人", ["ru8 "] = "家",
  ["ru0 "] = "間", ["xu;4"] = "輛", ["1j4"] = "部", ["gj; "] = "雙", ["wul6"] = "條",
  ["qu04"] = "片", ["j03"] = "碗", ["qu/6"] = "瓶", ["1l "] = "包", ["g.3"] = "首",
  ["qu0 "] = "篇", ["5k6"] = "折", ["1o4"] = "倍", ["h/6"] = "層", ["t;3"] = "場",
  ["5. "] = "週", ["5j/3"] = "種", ["wu6"] = "題", ["rm4"] = "句", ["dk "] = "顆",
  ["vu; "] = "箱", ["ck6"] = "盒", ["rup "] = "斤", ["j04"] = "萬", ["fu0 "] = "千",
  ["193"] = "百",
}
local MEASURE2 = {
  ["ej/ xu3"] = "公里", ["ej/ rup "] = "公斤", ["ej/ zp "] = "公分", ["ej/ t3"] = "公尺",
  ["vul3g6"] = "小時", ["zp 5j/ "] = "分鐘", ["ek4m,4"] = "個月",
}
local DI, ZHANG = "2u4", "5; "

local function chars(s)
  local out = {}
  for _, cp in utf8.codes(s) do out[#out + 1] = utf8.char(cp) end
  return out
end

-- Number segment ending at (before = true) or starting at pos.
local function number_at(comp, pos, before)
  local ok, segs = pcall(function() return comp:toSegmentation():get_segments() end)
  for _, s in ipairs(ok and segs or {}) do
    if s._end > s.start and s:has_tag("asus_num") and (before and s._end or s.start) == pos then
      return s
    end
  end
  return nil
end

local function measure_words(ctx, cand, keys)
  local syl = require("asus_learn").syllables(keys)
  local cs = chars(cand.text)
  if not syl or #syl ~= #cs then return nil end
  local changed = false
  local num = number_at(ctx.composition, cand.start, true)
  if num then
    local two = #syl >= 2 and MEASURE2[syl[1] .. syl[2]]
    if two then
      local w = chars(two)
      if cs[1] ~= w[1] or cs[2] ~= w[2] then cs[1], cs[2] = w[1], w[2]; changed = true end
    else
      local w = MEASURE[syl[1]]
      local before = ctx.input:sub(1, num.start)
      if syl[1] == ZHANG and before:sub(-#DI) == DI then w = "章" end
      if w and cs[1] ~= w then cs[1] = w; changed = true end
    end
  end
  if syl[#syl] == DI and cs[#cs] ~= "第" and number_at(ctx.composition, cand._end, false) then
    cs[#cs] = "第"
    changed = true
  end
  return changed and table.concat(cs) or nil
end

local function digits(input, env)
  local ctx = env.engine.context
  local n, fw = 0, false
  for cand in input:iter() do
    n = n + 1
    if n == 1 then
      local keys = ctx.input:sub(cand.start + 1, cand._end)
      local plain = keys:match("^%d+%.?%d*$") and keys:match("%d$")
      local fixed = cand.type ~= "number" and measure_words(ctx, cand, keys)
      if fixed then
        yield(ShadowCandidate(cand, cand.type, fixed, cand.comment))
      else
        yield(cand)
      end
      if plain then
        if cand.type == "number" then
          fw = true
          yield(Candidate("number", cand.start, cand._end, (keys:gsub(".", FULL)), "全形"))
        else
          yield(Candidate("number", cand.start, cand._end, keys, "數字"))
        end
      end
    else
      yield(cand)
      -- A number further down (the editing cursor's list, asus_edit.lua).
      if cand.type == "number" and not fw then
        local keys = ctx.input:sub(cand.start + 1, cand._end)
        if keys:match("^%d+%.?%d*$") and keys:match("%d$") then
          fw = true
          yield(Candidate("number", cand.start, cand._end, (keys:gsub(".", FULL)), "全形"))
        end
      end
    end
  end
end

return { segmentor = segmentor, translator = translator, plan_run = plan_run, digits = digits,
         first_guess = first_guess,
         complete_english = complete_english }
