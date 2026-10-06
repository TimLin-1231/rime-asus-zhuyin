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
local C_EN_BASE_CTX = 0.3  -- English base cost right after an English commit
local C_EN_PREFIX = 1.0    -- extra cost of an unfinished word (prefix of a common word)
local MAX_SYLLABLE = 5
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
  C_NUM = get("num", C_NUM)
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

-- English lexicon ------------------------------------------------------------
-- Kept in an OpenCC text dictionary (opencc/asus_en.json), i.e. in C++ memory:
-- as a Lua table its 56k entries made librime-lua's per-call GC cost ~40 ms
-- per keystroke. Lookups are memoised per input in a small, short-lived table.
local english_cc
local max_en_len = zy.max_en_len or 24
local memo, memo_input = {}, nil

local prefix_cc
local prefix_memo, prefix_memo_input = {}, nil

local function load_english()
  if english_cc == nil then
    local ok, cc = pcall(Opencc, "asus_en.json")
    english_cc = ok and cc or false
  end
  if prefix_cc == nil then  -- English autocomplete (opencc/asus_en_prefix.json)
    local ok, cc = pcall(Opencc, "asus_en_prefix.json")
    prefix_cc = ok and cc or false
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

-- en_ctx: the previous commit was English, so English is the expected language.
local function en_cost(tok, en_ctx)
  local entry = english(tok:lower())
  local base = en_ctx and C_EN_BASE_CTX or C_EN_BASE
  local best
  if entry then best = base + C_EN_RANK * math.log(entry.rank + 1, 10) end
  if en_ctx and SHORT_WORDS[tok:lower()] then best = math.min(best or math.huge, base) end
  -- Capitalised tokens (GPU, GPT4, DE-LSTM) are English even if unknown.
  -- Digits double as tone keys, so each one costs extra: "GPU2k7" must
  -- split as GPU + 的, not GPU2 + ㄜ˙.
  if tok:match("^%u[%w%-]*$") then
    local _, digits = tok:gsub("%d", "")
    best = math.min(best or math.huge, C_CAPS + C_DIGIT * digits)
  end
  return best
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
    local total = base + cost + switch
    local to_lang = (lang == "none") and from_lang or lang
    if total < best[j + 1][to_lang] then
      best[j + 1][to_lang] = total
      back[j + 1][to_lang] = { i = i, from = from_lang, kind = kind }
    end
  end
  for i = 1, n do
    for _, from in ipairs({ "none", "zh", "en", "num" }) do
      if best[i][from] < INF then
        -- Zhuyin syllables
        for j = i, math.min(n, i + MAX_SYLLABLE - 1) do
          local tok = run:sub(i, j)
          if tok:find("%u") then break end
          relax(i, from, j, "zh", zh_cost(tok, j == n), "zh")
        end
        -- English words / capitalised runs
        local caps = is_upper(run:sub(i, i))
        for j = i + 1, math.min(n, i + math.max(max_en_len, 32) - 1) do
          local tok = run:sub(i, j)
          if not (tok:match("^[%a][%a%-']*$") or (caps and tok:match("^%u[%w%-]*$"))) then
            break
          end
          local cost = en_cost(tok, en_ctx)
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
        -- stray key
        relax(i, from, i, "none", C_STRAY, "stray")
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
  -- merge adjacent Zhuyin syllables into one span
  local merged = {}
  for _, t in ipairs(tokens) do
    local last = merged[#merged]
    if last and last.kind == "zh" and t.kind == "zh" then
      last.j = t.j
    else
      merged[#merged + 1] = { kind = t.kind, i = t.i, j = t.j }
    end
  end
  return merged, special, cost
end

-- Plan for input[floor:], cached; positions are 0-based [start, end) in input.
local function plan_input(env, input, floor, en_ctx)
  local cache_key = (en_ctx and "e" or "z") .. floor .. ":" .. input
  if env.cache_key == cache_key then return env.cache_plan end
  if memo_input ~= input then memo, memo_input = {}, input end
  if prefix_memo_input ~= input then prefix_memo, prefix_memo_input = {}, input end
  local spans = {}
  -- Shortcut abbreviations (ㄉㄓ) are runs of lone initials by design; leave
  -- them to the stock segmentation so asus_shortcuts.lua can match them.
  if env.shortcuts[input] then
    env.cache_key, env.cache_plan = cache_key, spans
    return spans
  end
  local pos = floor + 1
  while pos <= #input do
    if in_run(input:sub(pos, pos)) then
      local e = pos
      while e < #input and in_run(input:sub(e + 1, e + 1)) do e = e + 1 end
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
          spans[#spans + 1] = { kind = t.kind, s = pos - 1 + t.i - 1, e = pos - 1 + t.j }
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
      if #spans == 0 then
        -- A plain Chinese plan is one stock segment per run of keys: a kept
        -- boundary inside a run (left by a number at "283" before 打電話)
        -- would split the sentence and hide context from the language model
        -- and rules. Boundaries at punctuation are librime's own.
        local e = seg._end
        if special or (e < #input and in_run(input:sub(e, e)) and in_run(input:sub(e + 1, e + 1))) then
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

function segmentor.init(env)
  load_english()
  load_costs(env.engine.schema.config)
  env.shortcuts = require("asus_shortcuts").load(rime_api.get_user_data_dir() .. "/shortcuts.txt")
  env.debug = env.engine.schema.config:get_bool("asus_lang/debug") or false
end

function segmentor.func(segmentation, env)
  local input = segmentation.input
  if type(input) == "function" then input = segmentation:input() end
  local start = segmentation:get_current_start_position()
  local en_ctx = env.engine.context:get_property("asus_en_ctx") == "1"
  local spans = plan_input(env, input, fixed_floor(segmentation), en_ctx)
  local new_input = env.seg_input ~= input
  env.seg_input = input
  local call_start = start
  if not kept_matches(segmentation, spans, start, new_input, input) then
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
      if span.kind == "en" then
        seg.tags = Set({ "asus_en", "abc" })
      elseif span.kind == "num" then
        seg.tags = Set({ "asus_num", "abc" })
      elseif span.kind == "stray" then
        seg.tags = Set({ "asus_stray" })
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
  if seg:has_tag("asus_stray") then
    local cand = Candidate("stray", seg.start, seg._end, "", "〔略過贅字 " .. input .. "〕")
    cand.quality = 1000
    yield(cand)
    return
  end
  if seg:has_tag("asus_num") then
    local cand = Candidate("number", seg.start, seg._end, input, "")
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
  if entry and not input:find("%u") and not entry.word:match("^%u%l*$") then
    shown = entry.word
  end
  -- Unfinished word: show the completion as a hint; Tab accepts it
  -- (asus_predict.lua calls complete_english).
  local hint = ""
  local p = completion_for(input:lower(), entry)
  if p then hint = "⇥" .. p.word end
  local cand = Candidate("english", seg.start, seg._end, shown, hint)
  cand.quality = 1000
  yield(cand)
  if entry and entry.word ~= shown then
    local alt = Candidate("english", seg.start, seg._end, entry.word, "")
    alt.quality = 999
    yield(alt)
  end
end

-- Remainder that completes the typed English prefix ("pyth" -> "on"), or nil.
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
  local n = 0
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
          yield(Candidate("number", cand.start, cand._end, (keys:gsub(".", FULL)), "全形"))
        else
          yield(Candidate("number", cand.start, cand._end, keys, "數字"))
        end
      end
    else
      yield(cand)
    end
  end
end

return { segmentor = segmentor, translator = translator, plan_run = plan_run, digits = digits,
         complete_english = complete_english }
