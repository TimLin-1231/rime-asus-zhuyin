-- asus_lang.lua
-- Mixed Chinese/English input without mode switching, plus stray-key removal.
--
-- Every letter key is also a Zhuyin key, so each run of keys is segmented by
-- dynamic programming into tokens with costs:
--   Zhuyin syllable  toned 0 | toneless C_TONELESS | unfinished at end C_PREFIX
--                    | lone initial (abbreviation) C_ABBR
--                    + C_AGAIN for a slip right after a toneless syllable
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
local C_FIX = 1.2        -- below a toneless syllable (1.5), above any real syllable
local C_FIX_DIGIT = 2.5  -- the same for a digit key + a whole syllable: above a number
local C_FIX_FINAL = 0.5  -- tone key before the last key, at the end when committing: below a number
-- A second slip right after a syllable typed without its tone key: another
-- such syllable, a typo syllable, or keys left unfinished at the end. Three
-- letters of an English word (doc + 改) otherwise read as ㄎㄟ and a 改 with
-- one key too many for 2.7, less than any English word between Chinese.
-- Below C_STRAY - C_FIX: two slips in a row still cost less than a stray key.
local C_AGAIN = 2.0
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
  C_FIX_DIGIT = get("fix_digit", C_FIX_DIGIT)
  C_AGAIN = get("again", C_AGAIN)
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

-- The memos only serve one input: librime-lua collects garbage on every
-- call, so whatever piles up here makes every later key slower.
local function fresh_memos(input)
  if memo_input ~= input then memo, memo_input = {}, input end
  if prefix_memo_input ~= input then
    prefix_memo, prefix_memo_input = {}, input
    fix_memo, guess_memo = {}, {}
  end
end

-- The words that most often follow an English word (opencc/asus_en_follow),
-- and the previous English word: the last one committed (asus_keys.lua keeps
-- it in the property asus_en_prev). After "machine", lea is learning.
local follow_cc
local prev_word = ""
local follow_memo = {}
local function followers()
  if prev_word == "" or not follow_cc then return nil end
  local hit = follow_memo[prev_word]
  if hit == nil then
    hit = follow_cc:convert_word(prev_word) or false
    follow_memo = { [prev_word] = hit }
  end
  return hit or nil
end
local function set_previous(ctx)
  prev_word = ctx:get_property("asus_en_prev") or ""
end
-- Do the keys start a word that often follows the previous English word?
local function follows_previous(tok)
  for _, w in ipairs(followers() or {}) do
    if #w > #tok and w:sub(1, #tok):lower() == tok then return true end
  end
  return false
end

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
  if follow_cc == nil then  -- words that follow a word (opencc/asus_en_follow.json)
    local ok, cc = pcall(Opencc, "asus_en_follow.json")
    follow_cc = ok and cc or false
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
    -- The ranks of this table order the suggestions. How readily the keys
    -- are taken for an unfinished English word goes by the lexicon's ranks,
    -- as words themselves do: the best one among the completions listed.
    if entry and english_cc then
      local best
      for _, form in ipairs(forms) do
        local w = form:match("^%d+:(.+)$")
        local lex = w and english_cc:convert_word(w:lower())
        local r = lex and lex[1] and tonumber(lex[1]:match("^(%d+):"))
        if r and (not best or r < best) then best = r end
      end
      entry.rank = best or entry.rank
    end
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
local C_FIX_SCORE = 1.5  -- a correction ranks as if 10^1.5 times rarer per edit (2.5: untill -> untilled before until)
-- Words to suggest for typed English key (lower case): completions of an
-- unfinished word and corrections of a misspelt one, best first.
local function guesses(key, entry)
  local memo_key = prev_word .. " " .. key
  local hit = guess_memo[memo_key]
  if hit then return hit end
  -- Remembered email addresses starting with what was typed come first.
  -- (asus_email.match: the same shortest prefix, asus_email/min_prefix, as
  -- the Tab hint in the bubble.)
  local mails = require("asus_email").match(key, 3)
  if key:find("@", 1, true) or key:find("%.%a+$") then
    guess_memo[memo_key] = mails
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
    guess_memo[memo_key] = out
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
  -- What usually follows the previous word goes first: its followers that
  -- start with what was typed (most frequent pair first), then guesses
  -- that are followers at all (a misspelt one among them).
  local follow = followers()
  if follow then
    local is_follower = {}
    for _, w in ipairs(follow) do
      local lw = w:lower()
      is_follower[lw] = true
      if #lw > #key and lw:sub(1, #key) == key and not seen[lw] and #out < GUESS_MAX then
        seen[lw] = true
        out[#out + 1] = w
      end
    end
    for _, e in ipairs(list) do
      local lw = e.word:lower()
      if is_follower[lw] and not seen[lw] and #out < GUESS_MAX then
        seen[lw] = true
        out[#out + 1] = e.word
      end
    end
  end
  for _, e in ipairs(list) do
    local lw = e.word:lower()
    if not seen[lw] then
      seen[lw] = true
      out[#out + 1] = e.word
      if #out >= GUESS_MAX then break end
    end
  end
  guess_memo[memo_key] = out
  return out
end

-- Automatic correction ---------------------------------------------------------
-- A misspelt English word is replaced by the word that was meant, when that
-- is safe to say: on Space after it (English mode, asus_english.lua; Chinese
-- mode, asus_keys.lua - shown first, BackSpace takes it back), and inside a
-- Chinese sentence as soon as something is typed after it (the translator
-- below; marked 更正, what was typed is the second candidate).
-- A correction costs log10(rank of the word) plus how
-- unlikely the slip is; it is made when the cost is low enough for the length
-- of what was typed and no other word comes close. What was typed is left
-- alone when it is a word (any word of the lexicon with a frequency, a word
-- the user taught, a word SymSpell's list has), when the user took the same
-- correction back before, and when it has capitals other than the first.
-- Tuned on tests/eval/autofix.txt and autofix_more.txt.
local AUTO_PROP = "asus_en_auto"  -- "typed<Tab>shown": a correction not committed yet
local KNOWN_RANK = 100000  -- lexicon words without a frequency rank above this
local S_SWAP, S_DOUBLE, S_DOUBLED, S_MISSING = 0, 0.1, 0.2, 0.3  -- teh; occured; untill; goverment
local S_VOWEL, S_NEAR, S_EXTRA_NEAR, S_OTHER = 0.4, 0.5, 0.6, 0.9  -- seperate; a neighbouring key
local S_FIRST = 1.0   -- the first letter is rarely the wrong one (dtype is not type)
local S_TWO = 0.7     -- two slips, long words only (tommorow)
local TWO_MIN = 8
local AUTO_LIMIT = { [4] = 2.5, [5] = 3.8 }  -- by length; short words have many neighbours (uint, cron)
local AUTO_LIMIT_LONG = 4.1
local AUTO_MARGIN = 0.5   -- the next best word must cost this much more
local AUTO_CAPITAL = 0.5  -- Recieve: a capital may start a name
-- Three-letter slips are only corrected from this list (aws is not was).
local AUTO_SHORT = { teh = "the", hte = "the", adn = "and", nad = "and", yuo = "you", nto = "not",
                     ofr = "for", wsa = "was", hsa = "has", hda = "had", btu = "but", aer = "are" }
local kept = {}  -- corrections taken back (BackSpace): left alone until Weasel restarts
local auto_on = true  -- asus_english/autocorrect
local NOAUTO = "asus_en_noauto"  -- app option: no automatic correction in this program
local FIX_MARK = "更正"

local KEY_AT = {}
for r, row in ipairs({ "qwertyuiop", "asdfghjkl", "zxcvbnm" }) do
  for c = 1, #row do KEY_AT[row:sub(c, c)] = { r, c } end
end
-- Do the two letter keys touch? (Each row sits half a key to the right of
-- the one above.)
local function adjacent(a, b)
  local p, q = KEY_AT[a], KEY_AT[b]
  if not p or not q then return false end
  local dr, dc = q[1] - p[1], q[2] - p[2]
  if dr == 0 then return dc == 1 or dc == -1 end
  if dr == 1 then return dc == 0 or dc == -1 end
  if dr == -1 then return dc == 0 or dc == 1 end
  return false
end

-- Cost of typing key for word, one slip apart; and whether the slip is one
-- of the plain kinds (two letters swapped, a letter left out or doubled).
local function slip(key, word)
  local lk, lw = #key, #word
  local i = 1
  while i <= lk and i <= lw and key:byte(i) == word:byte(i) do i = i + 1 end
  if lk == lw and key:sub(i, i) == word:sub(i + 1, i + 1) and key:sub(i + 1, i + 1) == word:sub(i, i)
      and key:sub(i + 2) == word:sub(i + 2) then
    return S_SWAP, true
  end
  local first = (i == 1) and S_FIRST or 0
  if lk == lw then
    local a, b = key:sub(i, i), word:sub(i, i)
    if adjacent(a, b) then return S_NEAR + first, false end
    -- A wrong vowel is a spelling slip in a later syllable (definately), not
    -- in the first (futher is further, not father).
    if a:match("[aeiou]") and b:match("[aeiou]") and key:sub(1, i - 1):find("[aeiou]") then
      return S_VOWEL + first, false
    end
    return S_OTHER + first, false
  elseif lw == lk + 1 then
    local ch = word:sub(i, i)
    if word:sub(i - 1, i - 1) == ch or word:sub(i + 1, i + 1) == ch then return S_DOUBLE + first, i > 1 end
    return S_MISSING + first, i > 1
  end
  local ch = key:sub(i, i)
  if key:sub(i - 1, i - 1) == ch or key:sub(i + 1, i + 1) == ch then return S_DOUBLED + first, i > 1 end
  if adjacent(ch, key:sub(i - 1, i - 1)) or adjacent(ch, key:sub(i + 1, i + 1)) then
    return S_EXTRA_NEAR + first, false
  end
  return S_OTHER + first, false
end

-- The word to put in place of typed (letters only), or nil to leave it.
-- inline: inside a Chinese sentence, where a lone i is a name (變數 i), not I.
local function autocorrect(typed, inline)
  local capital = typed:match("^%u%l*$") ~= nil
  if not capital and not typed:match("^%l+$") then return nil end
  local key = typed:lower()
  if kept[key] then return nil end
  local function shaped(word)  -- Teh -> The
    if capital and not word:match("^%u") then return word:sub(1, 1):upper() .. word:sub(2) end
    return word
  end
  if key == "i" then return not capital and not inline and "I" or nil end
  local short = CONTRACTION_OF[key] and contraction_form(CONTRACTION_OF[key]) or AUTO_SHORT[key]
  if short then return shaped(short) end
  load_english()
  if #key < FIX_MIN or not fix_cc then return nil end
  local entry = english(key)
  if entry and entry.rank < KNOWN_RANK then return nil end
  for _, e in ipairs(ranked(fix_cc:convert_word(key))) do
    if e.word:lower() == key then return nil end
  end
  local stem = key:sub(1, -2)
  local stem_entry = english(stem)
  local grown = key:sub(-1) ~= key:sub(-2, -2)  -- (untill, usefull: a doubled last letter is a slip)
  local best, second
  for _, e in ipairs(corrections(key)) do
    local lw = e.word:lower()
    local cost
    if e.dist == 1 then
      local s, plain = slip(key, lw)
      -- A word plus one more letter (systemd, repos, happend) is a word of
      -- its own: that letter is neither dropped nor replaced.
      local of_stem = grown and (lw == stem or (#lw == #key and lw:sub(1, -2) == stem
                                                 and stem_entry and stem_entry.rank < KNOWN_RANK))
      if not of_stem and (plain or #key > 5) then cost = math.log(e.rank + 1, 10) + s end
    elseif #key >= TWO_MIN then
      cost = math.log(e.rank + 1, 10) + S_TWO
    end
    if cost then
      if not best or cost < best.cost then
        best, second = { cost = cost, word = e.word }, best
      elseif not second or cost < second.cost then
        second = { cost = cost, word = e.word }
      end
    end
  end
  if not best then return nil end
  local limit = (AUTO_LIMIT[#key] or AUTO_LIMIT_LONG) - (capital and AUTO_CAPITAL or 0)
  if best.cost > limit or (second and second.cost - best.cost < AUTO_MARGIN) then return nil end
  return shaped(best.word)
end

-- The user took a correction back: typed is what they meant.
local function keep_word(typed)
  kept[typed:lower()] = true
end

-- The words that most often follow word (asus_en_follow), at most limit.
local function followers_of(word, limit)
  load_english()
  local out = {}
  local forms = follow_cc and follow_cc:convert_word(word:lower())
  for _, w in ipairs(forms or {}) do
    if #out >= limit then break end
    out[#out + 1] = w
  end
  return out
end

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
  -- (stage 10; left out where another word is as likely after a number:
  -- 3比2, 第3課, 3例, 1跟2, 3隊)
  ["hk4"] = "冊", ["ru4"] = "季", ["t/6"] = "成", ["ru6"] = "級", ["2j/4"] = "棟",
  ["w96"] = "台", ["z/ "] = "封", ["vu;4"] = "項", ["yk6"] = "則", ["wl4"] = "套",
  ["yj3"] = "組", ["ru,6"] = "節", ["w;6"] = "堂", ["ap6"] = "門", ["183"] = "把",
  ["2ji3"] = "朵", ["yji4"] = "座", ["q96"] = "排", ["fmp6"] = "群", ["qu "] = "批",
  ["dj03"] = "款", ["xo4"] = "類", ["ru,4"] = "屆", ["cjo6"] = "回", ["w;4"] = "趟",
  ["1u04"] = "遍", ["fm0 "] = "圈", ["2j04"] = "段", ["504"] = "站", ["ek6"] = "格",
  ["x06"] = "欄", ["xjp6"] = "輪", ["rm6"] = "局", ["q06"] = "盤", ["zj4"] = "副",
  ["tj04"] = "串", ["zj6"] = "幅", ["5ji "] = "桌", ["eji "] = "鍋", ["ej04"] = "罐",
  ["u4"] = "億", ["5l4"] = "兆", ["10 "] = "班", ["5 "] = "隻",
}
local MEASURE2 = {
  ["ej/ xu3"] = "公里", ["ej/ rup "] = "公斤", ["ej/ zp "] = "公分", ["ej/ t3"] = "公尺",
  ["vul3g6"] = "小時", ["zp 5j/ "] = "分鐘", ["ek4m,4"] = "個月",
}
local DI = "2u4"

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

-- Cost of tok as a Zhuyin syllable, and "toneless" / "open" (unfinished at
-- the end) for the two kinds that count as slips (C_AGAIN).
local function zh_cost(tok, at_end)
  if zy.toned[tok] then return 0 end
  if at_end and zy.prefix[tok] then return C_PREFIX, "open" end
  if zy.toneless[tok] then
    -- A one-key toneless syllable mid-input (ㄧ, ㄚ, ㄓ...) is what a stray
    -- key looks like; at the end it is just an unfinished syllable.
    return (#tok == 1) and C_TONELESS_1KEY or C_TONELESS, "toneless"
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
      -- (The lexicon's rank, as for any word: the correction table's ranks
      -- only order the suggestions.)
      local lex = english(fix.word:lower())
      best = base + C_EN_RANK * math.log((lex and lex.rank or fix.rank) + 1, 10) + C_EN_FIX
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
-- final: the input is being committed, so digit keys at its end are not
-- the start of a syllable still being typed (see finalize()).
local function plan_run(run, en_ctx, final)
  local n = #run
  local INF = math.huge
  -- best[i][lang]: cost of covering run[1..i-1]; lang of last token. "zx" is
  -- zh whose last syllable has no tone key: the next slip costs C_AGAIN more.
  -- (Not where no tone key is typed at all: that is a way of typing.)
  local again = run:find("[ 6347]") and C_AGAIN or 0
  local best, back = {}, {}
  for i = 1, n + 1 do best[i] = { zh = INF, zx = INF, en = INF, num = INF, none = INF }; back[i] = {} end
  best[1].none = 0
  local function relax(i, from_lang, j, lang, cost, kind, slip)
    local base = best[i][from_lang]
    if base == INF or cost == nil then return end
    local from = (from_lang == "zx") and "zh" or from_lang
    local switch = (from ~= "none" and lang ~= "none" and from ~= lang) and C_SWITCH or 0
    if from_lang == "en" and kind == "en" and best[i].en_word then switch = C_EN_JOIN end
    local total = base + cost + switch + ((slip and from_lang == "zx") and again or 0)
    local to_lang = (lang == "none") and from_lang or (slip == "toneless") and "zx" or lang
    if total < best[j + 1][to_lang] then
      best[j + 1][to_lang] = total
      back[j + 1][to_lang] = { i = i, from = from_lang, kind = kind }
      if to_lang == "en" then best[j + 1].en_word = (kind == "en") end
    end
  end
  for i = 1, n do
    for _, from in ipairs({ "none", "zh", "zx", "en", "num" }) do
      if best[i][from] < INF then
        -- Zhuyin syllables
        for j = i, math.min(n, i + MAX_SYLLABLE - 1) do
          local tok = run:sub(i, j)
          if tok:find("[%u'+#@_]") then break end
          local c, slip = zh_cost(tok, j == n and not (final and tok:match("^[125890]+$")))
          relax(i, from, j, "zh", c, "zh", slip)
          -- (Also where tok could be a lone initial with a tone key, C_ABBR.)
          if not zy.toned[tok] and #tok >= 2 and TONE_KEYS[tok:sub(-1)] and has_typo_fix(tok) then
            -- Digits alone (13 is not ㄅ + a missing key + ˇ), or digits and
            -- then a whole syllable (3點, 10日), are first of all a number:
            -- a number the user typed must never be taken for a slip.
            local rest = tok:match("^%d+(.*)$")
            local digit = rest and (rest == "" or zy.toned[rest])
            relax(i, from, j, "zh", digit and C_FIX_DIGIT or C_FIX, "fix", "fix")
          elseif #tok >= 3 and TONE_KEYS[tok:sub(-2, -2)] and not TONE_KEYS[tok:sub(-1)]
              and zy.toned[tok:sub(1, -3) .. tok:sub(-1) .. tok:sub(-2, -2)] then
            -- The tone key pressed before the syllable's last key (ㄨˇㄛ for
            -- 我). A syllable and then a digit is a number here too (是8月),
            -- except at the very end, where asus_typo.lua lets the language
            -- model choose between 見面 and 見密0 and keeps the digit if it
            -- was one.
            local digit = j < n and tok:match("%d$") and zy.toned[tok:sub(1, -2)]
            local cost = digit and C_FIX_DIGIT or C_FIX - 0.05
            if final and j == n then cost = C_FIX_FINAL end
            relax(i, from, j, "zh", cost, "fix", "fix")
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
          -- Right after an English word, the start of a word that usually
          -- follows it (new + yor, open + sou) is English whatever its rank.
          if j == n and en_ctx and #tok >= 3 and tok:match("^%l+$") and follows_previous(tok) then
            cost = math.min(cost or math.huge, C_EN_BASE_CTX + C_EN_PREFIX)
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
              if j == n and not final then
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
  for _, l in ipairs({ "none", "zh", "zx", "en", "num" }) do
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

-- Keys of the numeral a number is read as, for the language model: the
-- digit itself (第一集 and 第三級 are not the same to it), 十 / 十N for longer
-- numbers, 三 for anything else.
local NUMERALS = { ["0"] = "xu/6", ["1"] = "u ", ["2"] = "-4", ["3"] = "n0 ", ["4"] = "n4",
  ["5"] = "j3", ["6"] = "xu.4", ["7"] = "fu ", ["8"] = "18 ", ["9"] = "ru.3" }
local function numeral(digits, ordinal)
  -- 2 before a measure word is read 兩 (兩碗飯; 二晚飯 pulls towards 晚飯),
  -- but 二 after 第.
  if digits == "2" and not ordinal then return "xu;3", 1 end
  if #digits == 1 and NUMERALS[digits] then return NUMERALS[digits], 1 end
  local last = digits:match("^%d+(%d)$")
  if last == "0" then return "g6", 1 end
  if last then return "g6" .. NUMERALS[last], 2 end
  return NUMERALS["3"], 1
end

-- A digit or a Zhuyin initial? -------------------------------------------------
-- The keys 1 2 5 are ㄅ ㄉ ㄓ as well, so a digit right before a syllable that
-- starts with a vowel spells another syllable: 5 + ㄨㄢˋ (5萬) is ㄓㄨㄢˋ (賺),
-- 2 + ㄨㄟˋ (2位) is ㄉㄨㄟˋ (對), 1 + ㄧˋ (1億) is ㄅㄧˋ (必). The costs above
-- always read such keys as Chinese (a syllable costs nothing), which got
-- every 5萬 / 2位 / 12萬 wrong. Keys cannot tell the two apart; the language
-- model can: both readings are converted with two syllables of context on
-- each side, the number read as its numeral (只要賺就好 against 只要五萬就好,
-- 一段 against 一萬 after a 1), and the number reading is taken when its weight is
-- higher by NUM_NEW_MEASURE (a number where there was none, before a measure
-- word of the table: 萬 位 碗 億; it also has to make up for being one
-- syllable longer) or by NUM_LONGER (a number that grows by a digit: 1 + 段
-- -> 12 + 萬). A new number before any other word (NUM_NEW) is off: nothing
-- in the eval sets needs it, and at 5 it showed 早餐店的2按 for a moment
-- while 蛋餅 was being typed.
-- This is decided on the converted candidate (filter `context` below), not
-- in the plan: the number reading goes first and the Chinese one stays
-- second, one pick away, as with every other automatic replacement. Where
-- the language model keeps the Chinese reading, the number reading is the
-- second candidate instead; whichever the user picks is remembered for that
-- syllable after that character (asus_learn.lua, "d" entries) and decides
-- from then on.
local NUM_NEW, NUM_NEW_MEASURE, NUM_LONGER = math.huge, 0.5, 0
local NUM_NEW_START = 10  -- a new number as the very first thing typed
local NUMBER_MARK = "數字"  -- comment of a candidate with digit keys read as digits

-- Finished syllables from the start of keys (greedy), and how many keys they
-- take; what follows may be an unfinished syllable.
local function head_syllables(keys)
  local out, pos = {}, 1
  while pos <= #keys do
    local found
    for len = math.min(MAX_SYLLABLE, #keys - pos + 1), 1, -1 do
      if zy.toned[keys:sub(pos, pos + len - 1)] then found = len; break end
    end
    if not found then break end
    out[#out + 1] = keys:sub(pos, pos + found - 1)
    pos = pos + found
  end
  return out, pos - 1
end

local function lm_weight(env, keys)
  local w = env.lm[keys]
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
  if env.lm_n >= 2000 then env.lm, env.lm_n = {}, 0 end
  env.lm[keys] = w
  env.lm_n = env.lm_n + 1
  return w or nil
end

-- Where does an English word end? ---------------------------------------------
-- The key after a word is the first key of the next syllable, and often a
-- letter that makes another word while the rest still is a syllable: tim +
-- 改 (ㄍㄞˇ) is time + 矮 (ㄞˇ), your + 有 (ㄧㄡˇ) is you + 酒 (ㄐㄧㄡˇ), files + 有
-- is file + 鈕. Both readings cost the same but for the rank of the word, so
-- the commoner word always won. The Chinese tells them apart: the first
-- EDGE_SYLLABLES syllables after the word are converted both ways and the
-- other reading is taken when the language model's weight for it is higher
-- by EDGE_MARGIN, after allowing EDGE_RANK per factor of ten that its word
-- is rarer (weights are natural logarithms, so 2.3 would be the word's
-- frequency taken at face value; easy_en's ranks are too rough for that:
-- every plural has the same poor one).
-- Decided in the plan, unlike a digit before a syllable: the two readings
-- are different segments, so the other one cannot be offered as a second
-- candidate. Space after the word ends it there whatever comes next.
local EDGE_SYLLABLES = 3  -- two cannot tell 改一(下) from 矮一(下)
local EDGE_MARGIN = 2.0
local EDGE_RANK = 1.0

-- How far the end of the English token en moves into (+1) or away from (-1)
-- the Zhuyin token zh after it; 0 to leave the plan as it is.
local function edge_shift(env, run, en, zh)
  local word, rest = run:sub(en.i, en.j), run:sub(zh.i, zh.j)
  local entry = word:match("^%l+$") and english(word)
  if not entry or not env.tr then return 0 end
  local syls, used = head_syllables(rest)
  if #syls == 0 then return 0 end
  local n = math.min(#syls, EDGE_SYLLABLES)
  local base
  local best, best_gain = 0, EDGE_MARGIN
  local function try(shift, word2, rest2)
    local entry2 = #word2 >= 3 and word2:match("^%l+$") and english(word2)
    if not entry2 then return end
    local syls2, used2 = head_syllables(rest2)
    -- The same syllables after the first one, or it is another sentence.
    if #syls2 ~= #syls or used2 ~= used - shift then return end
    base = base or lm_weight(env, table.concat(syls, "", 1, n))
    local other = lm_weight(env, table.concat(syls2, "", 1, n))
    if not base or not other then return end
    local gain = other - base
      - EDGE_RANK * (math.log(entry2.rank + 1, 10) - math.log(entry.rank + 1, 10))
    if env.debug then
      log.info(string.format("asus_lang edge %s|%s %.2f / %s|%s %.2f gain=%.2f", word,
        table.concat(syls, "", 1, n), base, word2, table.concat(syls2, "", 1, n), other, gain))
    end
    if gain > best_gain then best, best_gain = shift, gain end
  end
  try(1, word .. rest:sub(1, 1), rest:sub(2))      -- the word takes a key
  try(-1, word:sub(1, -2), word:sub(-1) .. rest)   -- the word gives its last letter
  return best
end

-- Plan for input[floor:], cached; positions are 0-based [start, end) in input.
local function plan_input(env, input, floor, en_ctx, final)
  local cache_key = (en_ctx and ("e" .. prev_word) or "z") .. (final and "f" or "") .. floor .. ":" .. input
  if env.cache_key == cache_key then return env.cache_plan end
  fresh_memos(input)
  -- (Weights change with what was committed: none are kept across inputs.)
  if #input <= 1 and env.lm_n > 0 then env.lm, env.lm_n = {}, 0 end
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
      local toks, special, cost = plan_run(input:sub(pos, e), en_ctx and pos == 1, final and e == #input)
      for k = 1, #(toks or {}) - 1 do
        local en, zh = toks[k], toks[k + 1]
        if en.kind == "en" and zh.kind == "zh" then
          local shift = edge_shift(env, input:sub(pos, e), en, zh)
          en.j, zh.i = en.j + shift, zh.i + shift
        end
      end
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
      elseif sp.kind == "en" and (sp.enfix or false) ~= seg:has_tag("asus_enfix") then
        return false  -- a misspelt word that got, or lost, text after it: translated anew
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
-- A loop slower than that would never reach the count before the second is
-- over, so rewinds are also counted per key event: asus_shift.lua, the
-- first processor, calls key_tick() for every key pressed, and no key needs
-- GUARD_PER_KEY rewinds (moving the editing cursor, the most, takes a few).
local GUARD_PER_KEY = 100
local key_serial = 0  -- one Lua state per process, keys arrive one at a time

local function key_tick()
  key_serial = key_serial + 1
end

local function guard_tripped(env, input)
  local now = os.clock()  -- wall time on Windows (MSVC CRT)
  if env.guard_input ~= input or now - env.guard_since > GUARD_SECONDS then
    env.guard_input, env.guard_since, env.guard_count, env.guard_off = input, now, 0, env.guard_off
        and env.guard_input == input
  end
  if env.guard_key ~= key_serial then env.guard_key, env.guard_key_count = key_serial, 0 end
  env.guard_count = env.guard_count + 1
  env.guard_key_count = env.guard_key_count + 1
  if (env.guard_count > GUARD_REWINDS or env.guard_key_count > GUARD_PER_KEY) and not env.guard_off then
    env.guard_off = true
    log.error(string.format("asus_lang.lua: segmentation of %d keys did not settle after %d rewinds"
      .. " (%d for this key); using the stock segmentation for this input",
      #input, env.guard_count, env.guard_key_count))
  end
  return env.guard_off
end

function segmentor.init(env)
  load_english()
  load_costs(env.engine.schema.config)
  env.shortcuts = require("asus_shortcuts").load(rime_api.get_user_data_dir() .. "/shortcuts.txt")
  env.debug = env.engine.schema.config:get_bool("asus_lang/debug") or false
  env.guard_input, env.guard_since, env.guard_count, env.guard_off = nil, 0, 0, false
  env.guard_key, env.guard_key_count = -1, 0
  -- A translator of its own, for the language model (edge_shift).
  local config = env.engine.schema.config
  local on = config:get_bool("asus_lang/edge")
  if on == nil or on then
    local ok, tr = pcall(function()
      return Component.Translator(env.engine, "", "script_translator@translator")
    end)
    env.tr = ok and tr or nil
  end
  env.lm, env.lm_n = {}, 0
  EDGE_MARGIN = tonumber(config:get_string("asus_lang/edge_margin") or "") or EDGE_MARGIN
  EDGE_RANK = tonumber(config:get_string("asus_lang/edge_rank") or "") or EDGE_RANK
  EDGE_SYLLABLES = tonumber(config:get_string("asus_lang/edge_syllables") or "") or EDGE_SYLLABLES
end

function segmentor.func(segmentation, env)
  local input = segmentation.input
  if type(input) == "function" then input = segmentation:input() end
  local start = segmentation:get_current_start_position()
  local ctx = env.engine.context
  local en_ctx = ctx:get_property("asus_en_ctx") == "1"
  set_previous(ctx)
  if env.guard_off and env.guard_input == input then return true end  -- see guard_tripped
  local spans = plan_input(env, input, fixed_floor(segmentation), en_ctx,
                           ctx:get_property("asus_final") == input)
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
  -- A misspelt English word with something typed after it is finished: its
  -- segment is tagged asus_enfix and the translator puts the word meant
  -- first. (The tag is what makes librime translate the segment again when
  -- the next key arrives: kept segments keep their menus.)
  local auto = auto_on and not ctx:get_option(NOAUTO)
  for _, sp in ipairs(spans) do
    if sp.kind == "en" then
      local typed = input:sub(sp.s + 1, sp.e)
      sp.enfix = auto and sp.e < #input and typed:match("^%a+$") ~= nil and autocorrect(typed, true) ~= nil
    end
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
        -- (Also one of the same size but another kind: cpu read as Zhuyin
        -- until the key after it; AddSegment would only merge the tags and
        -- the Chinese menu would stay.)
        local special = last:has_tag("asus_en") or last:has_tag("asus_num") or last:has_tag("asus_stray")
        if last.start == start and last._end > last.start
            and (last._end ~= span.e or (span.kind ~= "zh") ~= special
                 or (span.kind == "en" and (span.enfix or false) ~= last:has_tag("asus_enfix"))) then
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
        seg.tags = Set(span.enfix and { "asus_en", "asus_enfix", "abc" } or { "asus_en", "abc" })
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
  local v = env.engine.schema.config:get_bool("asus_english/autocorrect")
  auto_on = (v == nil) or v
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
  local ctx = env.engine.context
  if seg:has_tag("asus_ascii") then
    -- (English mode has no plan_input to do this; without it each key got
    -- slower as words were typed: 2 ms -> 14 ms after 600 words.)
    fresh_memos(input)
  end
  -- A correction made on Space, shown but not committed yet: the last word
  -- of the input as it was then. BackSpace brings back what was typed
  -- (asus_english.lua).
  local whole, pending = (ctx:get_property(AUTO_PROP) or ""):match("^(.-)\t(.+)$")
  if whole == ctx.input and e == #whole then
    local cand = Candidate("english", s, e, pending, "⌫" .. input)
    cand.quality = 1000
    yield(cand)
    return
  end
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
  -- Inside a Chinese sentence, with something typed after it, a misspelt
  -- word is finished: the word meant goes first. (The last word of the
  -- input may still be growing; Space corrects that one, asus_keys.lua.)
  -- (asus_enfix: the segmentor above. At the editing cursor, asus_open,
  -- the segment runs to the end of the input and e is where the word ends.)
  local fixed = (seg:has_tag("asus_enfix") or (seg:has_tag("asus_open") and e < #ctx.input))
      and auto_on and not ctx:get_option(NOAUTO) and input:match("^%a+$") and autocorrect(input, true)
  local q = 1000
  local function more(word, comment)
    local c = Candidate("english", s, e, word, comment)
    c.quality = q
    q = q - 1
    yield(c)
  end
  if fixed then more(fixed, FIX_MARK) end
  -- The typed word, then guesses (completions, spelling corrections); the
  -- first guess is marked ⇥: Tab takes it (asus_predict.lua, english_tab).
  more(shown, "")
  if entry and entry.word ~= shown then more(entry.word, "") end
  set_previous(env.engine.context)
  for i, word in ipairs(guesses(input:lower(), entry)) do
    -- Keep the typed capitalisation: Pyth -> Python.
    if input:match("^%u") and not word:match("%u") then word = word:sub(1, 1):upper() .. word:sub(2) end
    if word ~= shown and word ~= fixed then more(word, i == 1 and "⇥" or "") end
  end
end

-- Remainder that completes the typed English prefix ("pyth" -> "on"), or nil.
-- First guess for the typed English word, or nil (what Tab takes).
local function first_guess(typed, ctx)
  load_english()
  if ctx then set_previous(ctx) end
  local key = typed:lower()
  local word = guesses(key, english(key))[1]
  if word and typed:match("^%u") and not word:match("%u") then
    word = word:sub(1, 1):upper() .. word:sub(2)
  end
  return word
end

-- Space after typed, shown as an English word: is it rather the first-tone
-- key of a syllable typed one slip off (gui␣ for ㄕㄨㄛ, ril␣ for ㄐㄧㄠ)? Yes
-- when reading it as such a syllable costs less than reading it as English.
local function space_is_tone(typed, after_other, en_ctx)
  if en_ctx or #typed + 1 > MAX_SYLLABLE or not typed:match("^%l+$") then return false end
  load_english()
  local tok = typed .. " "
  if not (zy.toned[tok] or has_typo_fix(tok)) then return false end
  local cost = en_cost(typed, false, true)
  if not cost then
    local p = english_prefix(typed)
    cost = p and (C_EN_BASE + C_EN_RANK * math.log(p.rank + 1, 10) + C_EN_PREFIX) or math.huge
  end
  return cost + (after_other and C_SWITCH or 0) > C_FIX
end

-- Called right before the input is committed. Digit keys after the last
-- tone key were read as a syllable still being typed (買1送1: 送 + ㄅ...);
-- now they are either a number, or the last key of a syllable whose tone key
-- came one key early (見面 typed ㄇㄧˋㄢ). The input is planned again as
-- final: shortening it and putting it back makes librime segment it anew.
local function finalize(ctx)
  local input = ctx.input
  if ctx.caret_pos ~= #input or ctx:get_option("ascii_mode") or input:sub(1, 1) == "`"
      or (ctx:get_property("asus_edit") or "") ~= "" then
    return
  end
  local tail = input:match("([^ 6347]*)$")
  if tail == "" or #tail >= #input or not tail:match("^[125890]+$") then return end
  ctx:set_property("asus_final", input)
  ctx.input = input:sub(1, #input - 1)
  ctx.input = input
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

-- Chinese next to a number -----------------------------------------------------
-- A number splits the input into segments, and librime converts each segment
-- on its own, so the word right after a number has no left context
-- (3 + ㄎㄜ: 科 or 顆? 第2 + ㄓㄤ: 張 or 章?). For a first syllable of the
-- measure-word table, the first character is chosen again:
--   1. The segment is converted once more behind what precedes the number,
--      the number read as its numeral (住三晚, 考三科, 第二章寫完了,
--      第二張照片), and the first character of that is the proposal.
--   2. It must be one of the words at home after a number (MEASURE and
--      MEASURE_ALSO: 元 圓 員, not 垣 from 三垣); else librime's own character
--      if it is one; else the table's word (2 + ㄅㄟ: 杯, 18 + ㄙㄨㄟˋ: 歲).
-- Nothing is forced beyond that table: 第2 + ㄓㄤ used to be 章 always
-- (第2章照片) and ㄐㄧˊ after 第N always 集 (第2集警戒). Tried and dropped:
-- taking the whole segment from the conversion with the numeral (共12人參加
-- -> 人身家), and proposing for syllables outside the table (3比2 -> 3筆2).
-- The result goes first and librime's own conversion stays second, one pick
-- away; the learned corrections (asus_learn.lua, a later filter) still have
-- the last word. Before a number only ㄉㄧˋ is touched: 第.
local MEASURE_ALSO = {
  -- only words that are measure words in their own right; spelling variants
  -- (周 for 週, 圓 for 元) are not offered
  ["5; "] = "章", ["ru6"] = "集", ["dk "] = "科棵", ["j03"] = "晚", ["1j4"] = "步", ["u,4"] = "夜",
  ["qu/6"] = "坪", ["t/6"] = "乘", ["5 "] = "支枝",
}
local CONTEXT_SYLLABLES = 2
local CACHE_MAX = 500

-- Up to n syllables from the start (from_end: the end) of keys; the rest may
-- be anything (an unfinished syllable while typing).
local function some_syllables(keys, n, from_end)
  local out = {}
  if from_end then
    local e = #keys
    while e >= 1 and #out < n do
      local found
      for len = math.min(MAX_SYLLABLE, e), 1, -1 do
        if zy.toned[keys:sub(e - len + 1, e)] then found = len; break end
      end
      if not found then break end
      table.insert(out, 1, keys:sub(e - found + 1, e))
      e = e - found
    end
  else
    local pos = 1
    while pos <= #keys and #out < n do
      local found
      for len = math.min(MAX_SYLLABLE, #keys - pos + 1), 1, -1 do
        if zy.toned[keys:sub(pos, pos + len - 1)] then found = len; break end
      end
      if not found then break end
      out[#out + 1] = keys:sub(pos, pos + found - 1)
      pos = pos + found
    end
  end
  return out
end

-- Keys of the plain Chinese segment ending (before = true) or starting at
-- pos, and the last character it shows.
local function chinese_at(ctx, pos, before)
  local ok, segs = pcall(function() return ctx.composition:toSegmentation():get_segments() end)
  for _, s in ipairs(ok and segs or {}) do
    if s._end > s.start and (before and s._end or s.start) == pos then
      if s:has_tag("asus_num") or s:has_tag("asus_en") or s:has_tag("asus_stray") or not s:has_tag("abc") then
        return nil
      end
      local cand = s:get_selected_candidate()
      local cs = cand and chars(cand.text) or {}
      return ctx.input:sub(s.start + 1, s._end), cs[#cs]
    end
  end
  return nil
end

local function convert(env, keys)
  local hit = env.conv[keys]
  if hit ~= nil then return hit or nil end
  local text = false
  local seg = Segment(0, #keys)
  seg.tags = Set({ "abc" })
  local t = env.tr:query(keys, seg)
  if t then
    for c in t:iter() do
      if c._end == #keys then text = c.text end
      break
    end
  end
  if text and env.tw then text = env.tw:convert(text) end  -- Taiwan forms, as the simplifier gives
  if env.conv_n >= CACHE_MAX then env.conv, env.conv_n = {}, 0 end
  env.conv[keys] = text
  env.conv_n = env.conv_n + 1
  return text or nil
end

-- cand's text with the character next to a number chosen again (see
-- above), or nil when there is no number there or nothing changes.
local function with_number_context(env, ctx, cand, keys)
  local comp = ctx.composition
  local cs = chars(cand.text)
  local changed = false
  local before = number_at(comp, cand.start, true)
  local first = before and some_syllables(keys, 2, false) or {}
  if #first > 0 and #cs >= 1 then
    local two = #first == 2 and #cs >= 2 and MEASURE2[first[1] .. first[2]]
    if two then
      local w = chars(two)
      if cs[1] ~= w[1] or cs[2] ~= w[2] then cs[1], cs[2], changed = w[1], w[2], true end
    else
      -- Only syllables of the table: elsewhere the stand-in numeral pulls
      -- towards measure words that are not meant (3比2 -> 3筆2).
      local word = MEASURE[first[1]]
      local prev, left = chinese_at(ctx, before.start, true)
      -- What the user picked here before goes first (asus_learn.lua).
      local learned = learn.after_number(left or "", first[1])
      if learned then
        if learned ~= cs[1] then cs[1], changed = learned, true end
      elseif word then
        local pick
        if env.tr then
          local tail = prev and some_syllables(prev, CONTEXT_SYLLABLES, true) or {}
          local num, n = numeral(ctx.input:sub(before.start + 1, before._end), left == "第")
          local text = convert(env, table.concat(tail) .. num .. keys)
          -- one character per finished syllable in front
          if text and utf8.len(text) == #tail + n + #cs then pick = chars(text)[#tail + n + 1] end
        end
        local at_home = word .. (MEASURE_ALSO[first[1]] or "")
        if not (pick and at_home:find(pick, 1, true)) then
          pick = at_home:find(cs[1], 1, true) and cs[1] or word
        end
        if pick ~= cs[1] then cs[1], changed = pick, true end
      end
    end
  end
  if keys:sub(-#DI) == DI and cs[#cs] ~= "第" and #cs >= 1 and number_at(comp, cand._end, false)
      and zy.toned[DI] and (#keys == #DI or TONE_KEYS[keys:sub(-#DI - 1, -#DI - 1)]) then
    cs[#cs], changed = "第", true
  end
  return changed and table.concat(cs) or nil
end

-- The key under which the user's choice between number and Chinese is
-- remembered for a segment (keys, starting at byte `start` of input), and
-- the syllable it is about: the first one that can be read both ways before
-- a measure word, with the syllable in front of it ("#" after a number, ""
-- at the start). Keys, not characters: the characters in front may differ
-- between the two readings (月薪 / 月新).
local function choice_key(input, start, keys)
  local syl = head_syllables(keys)
  for q, sy in ipairs(syl) do
    if sy:sub(1, 1):match("%d") and zy.toned[sy:sub(2)] and MEASURE[sy:sub(2)] then
      local left = syl[q - 1]
      if not left then
        local before = input:sub(1, start)
        left = some_syllables(before, 1, true)[1] or (before:match("%d$") and "#") or ""
      end
      return left .. "\t" .. sy, q
    end
  end
  return nil
end

-- cand's text with digit keys read as digits (see "A digit or a Zhuyin
-- initial?"), and whether that reading should go first: the language model
-- says so, or the user chose it here before. Otherwise the text (with the
-- likeliest digit key read as a digit) is only offered second. nil when no
-- key of the segment can be read both ways.
local function number_reading(env, ctx, cand, keys)
  if not env.tr or not env.num_lm then return nil end
  local syl, used = head_syllables(keys)
  if #syl == 0 then return nil end
  local prev_num = number_at(ctx.composition, cand.start, true)
  local ckey, cq = choice_key(ctx.input, cand.start, keys)
  local turned, any = {}, false
  local alt, alt_gain  -- the likeliest one the language model did not take
  for q, sy in ipairs(syl) do
    local c, rest = sy:sub(1, 1), sy:sub(2)
    if c:match("%d") and zy.toned[rest] and not turned[q - 1] then
      -- the number in front, when this is the first syllable after one
      local number = (q == 1 and prev_num) and ctx.input:sub(prev_num.start + 1, prev_num._end) or ""
      local left = {}
      if q > 1 then
        for k = math.max(1, q - 2), q - 1 do left[#left + 1] = syl[k] end
      elseif number ~= "" then
        local before = chinese_at(ctx, prev_num.start, true)
        left = before and some_syllables(before, CONTEXT_SYLLABLES, true) or {}
      end
      local ordinal = left[#left] == DI
      local l, r = table.concat(left), table.concat(syl, "", q + 1, math.min(#syl, q + 2))
      -- A number that grows is read as the same numeral both times, so that
      -- the two readings have as many syllables (一段 / 一萬).
      local n_is = number ~= "" and numeral(number, ordinal) or ""
      local n_num = number ~= "" and n_is or numeral(c, ordinal)
      local as_is = lm_weight(env, l .. n_is .. sy .. r)
      local as_num = lm_weight(env, l .. n_num .. rest .. r)
      local need = (number ~= "") and NUM_LONGER or MEASURE[rest] and NUM_NEW_MEASURE or NUM_NEW
      -- At the very start there is nothing on the left to go by, and 兩位...
      -- is a common way to begin (a personal word starting with 對 became
      -- 2位 at a gain of 8).
      if number == "" and #left == 0 and cand.start == 0 then need = math.max(need, NUM_NEW_START) end
      -- ...and never right before another number (賺25萬 is not 5萬25萬).
      local before_number = q == #syl and used == #keys and number_at(ctx.composition, cand._end, false)
      if env.debug and as_is and as_num then
        log.info(string.format("asus_lang digit %s|%s gain=%.2f %s keys=%s", number, sy, as_num - as_is,
          number ~= "" and "longer" or MEASURE[rest] and "measure" or "new", keys))
      end
      local choice = (q == cq) and learn.number_choice(ckey) or nil
      if as_num and as_is and not before_number and need < math.huge then
        if choice == "1" or (choice ~= "0" and as_num > as_is + need) then
          turned[q], any = true, true
        elseif not alt_gain or as_num - as_is > alt_gain then
          alt, alt_gain = q, as_num - as_is
        end
      end
    end
  end
  local first = any
  if not any then
    if not alt then return nil end
    turned[alt] = true
  end
  -- Convert with the digits as numerals, then put the digits back.
  local parts = {}
  for q, sy in ipairs(syl) do
    parts[#parts + 1] = turned[q] and (numeral(sy:sub(1, 1), syl[q - 1] == DI) .. sy:sub(2)) or sy
  end
  local text = convert(env, table.concat(parts) .. keys:sub(used + 1))
  local cs = text and chars(text) or {}
  local n = 0
  for q in pairs(turned) do n = n + 1 end
  if #cs < #syl + n then return nil end
  local i = 0
  for q, sy in ipairs(syl) do
    i = i + 1
    if turned[q] then
      cs[i] = sy:sub(1, 1)
      i = i + 1
      -- the word after it: one that is at home after a number
      local word = MEASURE[sy:sub(2)]
      if word and not (word .. (MEASURE_ALSO[sy:sub(2)] or "")):find(cs[i], 1, true) then cs[i] = word end
    end
  end
  return table.concat(cs), first
end

local context = {}
-- Digit keys read as digits (number_reading): its own filter, before
-- asus_typo's, which would otherwise "correct" 段元 to 隊員 first. The
-- candidate is marked NUMBER_MARK; asus_typo.lua leaves marked ones alone.
local numbers = {}

function context.init(env)
  local ok, tr = pcall(function()
    return Component.Translator(env.engine, "", "script_translator@translator")
  end)
  env.tr = ok and tr or nil
  env.conv, env.conv_n = {}, 0
  local ok_tw, tw = pcall(Opencc, "t2tw.json")
  env.tw = ok_tw and tw or nil
  local config = env.engine.schema.config
  local on = config:get_bool("asus_lang/number_context")
  env.enabled = (on == nil) or on
  on = config:get_bool("asus_lang/number_lm")
  env.num_lm = (on == nil) or on
  NUM_NEW = tonumber(config:get_string("asus_lang/num_new") or "") or NUM_NEW
  NUM_NEW_MEASURE = tonumber(config:get_string("asus_lang/num_new_measure") or "") or NUM_NEW_MEASURE
  NUM_LONGER = tonumber(config:get_string("asus_lang/num_longer") or "") or NUM_LONGER
  env.debug = config:get_bool("asus_lang/debug") or false
  env.lm, env.lm_n = {}, 0
  -- Commits change Rime's user dictionary: convert afresh.
  env.conn = env.engine.context.commit_notifier:connect(function()
    env.conv, env.conv_n, env.lm, env.lm_n = {}, 0, {}, 0
  end)
end

function context.fini(env)
  if env.conn then env.conn:disconnect() end
end

function context.func(input, env)
  local ctx = env.engine.context
  local first = true
  for cand in input:iter() do
    if first then
      first = false
      local better
      -- ("simplified": after OpenCC changed a character to its Taiwan form.
      -- A number reading from the filter below is left as it is.)
      if env.enabled and cand.comment ~= NUMBER_MARK
          and (cand.type == "sentence" or cand.type == "phrase" or cand.type == "user_phrase"
               or cand.type == "simplified") then
        better = with_number_context(env, ctx, cand, ctx.input:sub(cand.start + 1, cand._end))
      end
      if better then yield(ShadowCandidate(cand, cand.type, better, cand.comment)) end
      yield(cand)
    else
      yield(cand)
    end
  end
end

local function digits(input, env)
  local ctx = env.engine.context
  local n, fw = 0, false
  for cand in input:iter() do
    n = n + 1
    if n == 1 then
      local keys = ctx.input:sub(cand.start + 1, cand._end)
      local plain = keys:match("^%d+%.?%d*$") and keys:match("%d$")
      yield(cand)
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

function numbers.func(input, env)
  local ctx = env.engine.context
  local first = true
  for cand in input:iter() do
    if first then
      first = false
      local reading, goes_first
      if env.enabled and (cand.type == "sentence" or cand.type == "phrase" or cand.type == "user_phrase"
                          or cand.type == "simplified") then
        reading, goes_first = number_reading(env, ctx, cand, ctx.input:sub(cand.start + 1, cand._end))
      end
      -- The other reading is always there, second: pick it once and it is
      -- what comes first the next time (asus_learn.lua).
      if reading and goes_first then yield(ShadowCandidate(cand, cand.type, reading, NUMBER_MARK)) end
      yield(cand)
      if reading and not goes_first then yield(ShadowCandidate(cand, cand.type, reading, NUMBER_MARK)) end
    else
      yield(cand)
    end
  end
end
numbers.init, numbers.fini = context.init, context.fini

-- Steady text while a syllable is being typed ----------------------------------
-- Every key converts the whole input again, and an unfinished syllable is
-- little to go by: the first key of 雨 after 外面在下 made the sentence
-- 外面最愛血液 for one key (each key of it read as the first of a syllable),
-- then 外面在下 came back. 13% of the keys that finish no syllable changed
-- characters already shown (tools/eval_flicker.py).
-- So what was shown when the last syllable was finished stays until the next
-- one is: the best candidate is remembered whenever the input ends with a
-- tone key, and while keys of an unfinished syllable follow it, the text is
-- that, plus one character for the syllable being typed where the new
-- conversion has one to offer. The finished syllable then converts all of it
-- afresh; that is where the text may change.
-- Only the best candidate of plain Chinese at the end of the input, in the
-- compact bubble: an open list, the editing cursor, English and numbers are
-- left alone. asus_lang/steady: false switches it off.
local steady = {}
local STEADY_MAX = 200

function steady.init(env)
  local on = env.engine.schema.config:get_bool("asus_lang/steady")
  env.enabled = (on == nil) or on
  env.seen, env.seen_n = {}, 0
end

local function char_at(text, i)
  local a = utf8.offset(text, i)
  local b = a and utf8.offset(text, i + 1)
  return (a and b) and text:sub(a, b - 1) or ""
end

-- cand is the first candidate of a translation. Its steady text, or nil to
-- leave it as it is.
local function steady_text(env, ctx, cand)
  local input = ctx.input
  if cand._end ~= #input or ctx.caret_pos ~= #input then return nil end
  local seg = ctx.composition:back()
  if not seg or seg.start ~= cand.start or cand.start >= #input or seg:has_tag("asus_en")
      or seg:has_tag("asus_num") or seg:has_tag("asus_stray") or seg:has_tag("asus_symbol")
      or not seg:has_tag("abc") then
    return nil
  end
  -- A typo syllable in it (asus_typo.lua): the keys after the last tone key
  -- may be the end of a syllable whose tone key came early (ㄇㄧˋㄢ for 面),
  -- not an unfinished one, and that is decided on the whole input.
  if seg:has_tag("asus_fix") then return nil end
  local keys = input:sub(cand.start + 1)
  if not keys:match("^[%l%d ,./;%-]+$") then return nil end
  local tail = keys:match("[^ 6347]*$")
  if tail == "" then
    -- Every syllable finished: this is what is shown from here on.
    if env.seen_n >= STEADY_MAX then env.seen, env.seen_n = {}, 0 end
    local key = cand.start .. ":" .. keys
    if env.seen[key] == nil then env.seen_n = env.seen_n + 1 end
    env.seen[key] = cand.text
    return nil
  end
  local head_key = cand.start .. ":" .. keys:sub(1, #keys - #tail)
  local shown = env.seen[head_key]
  if not shown then return nil end
  local n, total = utf8.len(shown), utf8.len(cand.text)
  if not n or not total then return nil end
  -- The character just finished may still change with what follows it (名
  -- becomes 明 when 天 is begun): a conversion that keeps everything before
  -- it, and has one character for the syllable being typed, is taken as it is.
  local keep = utf8.offset(shown, n)  -- byte where the last character shown begins
  if keep and cand.text:sub(1, keep - 1) == shown:sub(1, keep - 1) and (total == n or total == n + 1) then
    -- (...and is what stays from now on, or the next key could bring 名 back.)
    local stop = utf8.offset(cand.text, n + 1)
    env.seen[head_key] = stop and cand.text:sub(1, stop - 1) or cand.text
    return nil
  end
  -- One character for the syllable being typed: the one right after the
  -- text shown where the new conversion begins with that text (it may run
  -- on for several, each key read as a syllable of its own), else its last.
  local more = ""
  if cand.text:sub(1, #shown) == shown then
    more = char_at(cand.text, n + 1)
  elseif total == n + 1 then
    more = char_at(cand.text, total)
  end
  return shown .. more
end

function steady.func(input, env)
  local ctx = env.engine.context
  if not env.enabled or ctx:get_option("_asus_expanded") or ctx:get_option("ascii_mode")
      or (ctx:get_property("asus_edit") or "") ~= "" then
    for cand in input:iter() do yield(cand) end
    return
  end
  if #ctx.input <= 1 and env.seen_n > 0 then env.seen, env.seen_n = {}, 0 end  -- a new composition
  local first = true
  for cand in input:iter() do
    if first then
      first = false
      local text = (cand.type ~= "english" and cand.type ~= "number") and steady_text(env, ctx, cand) or nil
      if text and text ~= cand.text then
        yield(ShadowCandidate(cand, cand.type, text, cand.comment))
      else
        yield(cand)
      end
    else
      yield(cand)
    end
  end
end

return { segmentor = segmentor, translator = translator, plan_run = plan_run, digits = digits,
         context = context, numbers = numbers, steady = steady, NUMBER_MARK = NUMBER_MARK,
         choice_key = choice_key,
         first_guess = first_guess, space_is_tone = space_is_tone, finalize = finalize,
         key_tick = key_tick,
         complete_english = complete_english,
         autocorrect = autocorrect, keep_word = keep_word, followers_of = followers_of,
         AUTO_PROP = AUTO_PROP, FIX_MARK = FIX_MARK }
