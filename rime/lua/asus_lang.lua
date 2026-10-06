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
--   + C_SWITCH whenever the language changes.
-- Only when the cheapest plan contains English or stray tokens does the
-- segmentor take over; pure Chinese input keeps the stock segmentation.
-- English segments are tagged {asus_en, abc} so the Chinese reading of the
-- same keys stays available below the English candidate.
--
--   segmentor   lua_segmentor@*asus_lang*segmentor   (before abc_segmentor)
--   translator  lua_translator@*asus_lang*translator
-- Data: opencc/asus_en.json(.txt) and lua/asus_zhuyin_data.lua
-- (tools/build_lang_data.py).

local zy = require("asus_zhuyin_data")

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
end

local ZHUYIN_KEYS = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/- 6347"
local INITIAL_KEYS = "1qaz2wsxedcrfv5tgbyhn"
local IS_KEY, IS_INITIAL = {}, {}
for i = 1, #ZHUYIN_KEYS do IS_KEY[ZHUYIN_KEYS:sub(i, i)] = true end
for i = 1, #INITIAL_KEYS do IS_INITIAL[INITIAL_KEYS:sub(i, i)] = true end

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
  local entry = false
  local forms = prefix_cc:convert_word(key)
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
  for i = 1, n + 1 do best[i] = { zh = INF, en = INF, none = INF }; back[i] = {} end
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
    for _, from in ipairs({ "none", "zh", "en" }) do
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
        -- stray key
        relax(i, from, i, "none", C_STRAY, "stray")
      end
    end
  end
  local lang, cost = "none", INF
  for _, l in ipairs({ "none", "zh", "en" }) do
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
local function kept_matches(segmentation, spans, start)
  local by_start = {}
  for _, sp in ipairs(spans) do by_start[sp.s] = sp end
  for _, seg in ipairs(segmentation:get_segments()) do
    if seg.start >= start then break end
    if not is_fixed(seg) then
      local special = seg:has_tag("asus_en") or seg:has_tag("asus_stray")
      local sp = by_start[seg.start]
      if #spans == 0 then
        if special then return false end
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
  if not kept_matches(segmentation, spans, start) then
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
  for _, span in ipairs(spans) do
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
      elseif span.kind == "stray" then
        seg.tags = Set({ "asus_stray" })
      else
        seg.tags = Set({ "abc" })
      end
      segmentation:add_segment(seg)
      return false  -- this span is decided; skip the stock segmentors
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

return { segmentor = segmentor, translator = translator, plan_run = plan_run,
         complete_english = complete_english }
