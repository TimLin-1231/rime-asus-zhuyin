-- asus_fix.lua: AI correction of the composition (Ctrl+R).
--
-- Ctrl+R while composing asks a language model to correct what is shown:
-- homophones picked wrong, characters spoiled by a slipped, missing, extra
-- or swapped key, 的/得/地 and 在/再. Lua cannot open a connection, so the
-- front end does the asking:
--   1. Ctrl+R puts "<id>\t<chat completions JSON body>" in the context
--      property asus_fix_request; the bubble shows "AI 修正中…".
--   2. The front end (DaoHui for macOS: SquirrelInputController) takes the
--      request (empties the property), POSTs the body to the local model
--      server, puts "<id>\t<answer>" (or "<id>\t!<message>") in
--      asus_fix_result and sends the key F23 (0xFFD4).
--   3. F23: if the input is still the one asked about, the answer is tidied
--      and checked: it must still read as the keys typed (guard()). A
--      correction is then shown as the whole composition, one segment
--      (asus_fix), with the original as the second candidate. Enter commits
--      it, Esc goes back to the original, any other key goes back and works
--      on the original.
--   4. An answer that changes nothing (or that guard() refuses) is followed
--      by a second request, put in asus_fix_request while F23 is handled:
--      a tone key may have slipped (一血 for 一些), so the model is held to
--      the characters of each typed syllable in any tone. The front end
--      looks for a request after every key it sends, F23 included.
-- Only a front end that can ask sets the option _asus_fix; without it Ctrl+R
-- is left alone. asus_fix/enabled: false turns the feature off.
-- The prompt, tidy() and guard() follow tools/eval_fix.py, where they were
-- measured; keep them in step.
--
--   processor   lua_processor@*asus_fix*processor   (right after asus_shift)
--   segmentor   lua_segmentor@*asus_fix*segmentor   (first)
--   translator  lua_translator@*asus_fix*translator (first)
--   filter      lua_filter@*asus_fix*filter         (after asus_compact)

local fix_data = require("asus_fix_data")

local kAccepted, kNoop = 1, 2
local XK_F23, XK_Return, XK_KP_Enter, XK_Escape, XK_r = 0xffd4, 0xff0d, 0xff8d, 0xff1b, 0x72
local REQUEST, RESULT = "asus_fix_request", "asus_fix_result"
local STATE = "asus_fix_state"  -- "busy\t<input>" or "note\t<input>\t<message>"
local SHOWN = "asus_fix_shown"  -- "<input>\t<correction>\t<original>" while one is shown
local SUPPORT = "_asus_fix"     -- option set by a front end that can ask the model
local TIMEOUT = 60              -- seconds an unanswered request is waited for
local DICT = "terra_pinyin.asus"

local SYSTEM = "你是台灣繁體中文注音輸入法的校正助手。使用者用注音打了一句話，輸入法把它轉成了中文，"
  .. "但可能有錯：選錯同音字、按錯鍵或少打、多打、打反一個鍵造成的錯字、「的／得／地」或「在／再」用錯。"
  .. "請根據使用者打的注音和整句的意思，寫出使用者想打的那句話。"
  .. "只改真的錯的字，盡量少改：不要改寫、不要潤飾、不要增刪標點、不要換成同義詞。"
  .. "英文單字、數字和符號保持原樣。原句沒有錯就原樣照抄。只輸出改好的句子，不要解釋。"
-- Written for tools/eval_fix.py; none of them is in an eval set.
local EXAMPLES = {
  { "ㄨㄛˇ ㄇㄣ˙ ㄒㄧㄚˋ ㄓㄡ ㄗㄞˋ ㄐㄧㄢˋ ㄇㄧㄢˋ", "我們下週在見面", "我們下週再見面" },
  { "ㄊㄚ ㄔㄤˋ ㄉㄜ˙ ㄈㄟ ㄔㄤˊ ㄏㄠˇ ㄊㄧㄥ", "她唱的非常好聽", "她唱得非常好聽" },
  { "ㄓㄜˋ ㄍㄜ˙ ㄐㄧㄚˋ ㄎㄜˊ ㄏㄣˇ ㄆㄧㄢˊ ㄧˊ", "這個價殼很便宜", "這個價格很便宜" },
  { "ㄓㄜˋ ㄐㄧㄚ ㄘㄢ ㄊㄧㄥ ㄉㄜ˙ ㄈㄨˊ ㄨˋ ㄏㄣˇ ㄏㄠˇ", "這家餐廳的服物很好", "這家餐廳的服務很好" },
  { "ㄒㄧㄚˋ ㄌㄧˇ ㄅㄞˋ ㄉㄜ˙ demo ㄒㄧㄢ ㄑㄩˇ ㄒㄧㄠ", "下禮拜的demo先取消", "下禮拜的demo先取消" },
}
-- The second pass (eval_fix.py --two-pass): the hint goes after SYSTEM, the
-- example before EXAMPLES.
local TONE_HINT = "注音的聲調也可能按錯（例如一聲按成三聲），請依整句的意思選字。"
local TONE_EXAMPLE = { "ㄨㄛˇ ㄇㄣ˙ ㄧˋ ㄑㄧˇ ㄑㄩˋ ㄍㄨㄥ ㄩㄢˇ ㄙㄢˋ ㄅㄨˋ", "我們一起去公遠散步", "我們一起去公園散步" }
local SAME_SYLLABLE = 6  -- the IME's commonest characters of a syllable, per tone
local TONE_KEYS = { " ", "6", "3", "4", "7" }

-- Characters ------------------------------------------------------------------
local function chars(s)
  local t = {}
  for _, c in utf8.codes(s) do t[#t + 1] = utf8.char(c) end
  return t
end

local function trim(s)
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

-- Dachen (大千) layout; Space is the first tone.
local DACHEN, TONES = {}, { [" "] = "", ["6"] = "ˊ", ["3"] = "ˇ", ["4"] = "ˋ", ["7"] = "˙" }
do
  local keys = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-"
  local sym = chars("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ")
  for i = 1, #keys do DACHEN[keys:sub(i, i)] = sym[i] end
end

-- English words and numbers in text, as they are.
local function literal_runs(text)
  local runs = {}
  for run in text:gmatch("%w+") do runs[#runs + 1] = run end
  return runs
end

-- The keys as Zhuyin, a space after each tone key; the English words and
-- numbers the IME shows stay as typed (eval_fix.typed_zhuyin).
local function typed_zhuyin(keys, text)
  local lit, li = literal_runs(text), 1
  local lower, out, i = keys:lower(), {}, 1
  while i <= #keys do
    local w = lit[li] and lit[li]:lower()
    if w and lower:sub(i, i + #w - 1) == w then
      out[#out + 1] = " " .. keys:sub(i, i + #w - 1) .. " "
      i, li = i + #w, li + 1
    else
      local ch = keys:sub(i, i)
      out[#out + 1] = TONES[ch] and (TONES[ch] .. " ") or DACHEN[ch:lower()] or ch
      i = i + 1
    end
  end
  return trim((table.concat(out):gsub("%s+", " ")))
end

-- The request ------------------------------------------------------------------
local function json_str(s)
  local esc = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }
  return '"' .. s:gsub('[%c"\\]', function(c) return esc[c] or string.format("\\u%04x", c:byte()) end) .. '"'
end

local function message(role, content)
  return '{"role":"' .. role .. '","content":' .. json_str(content) .. "}"
end

-- The chat request; with a grammar (llama.cpp's server), the second pass.
local function request_body(zhuyin, text, grammar)
  local msgs = { message("system", grammar and (SYSTEM .. TONE_HINT) or SYSTEM) }
  local examples = grammar and { TONE_EXAMPLE } or {}
  for _, ex in ipairs(EXAMPLES) do examples[#examples + 1] = ex end
  for _, ex in ipairs(examples) do
    msgs[#msgs + 1] = message("user", "注音：" .. ex[1] .. "\n輸入法：" .. ex[2])
    msgs[#msgs + 1] = message("assistant", ex[3])
  end
  msgs[#msgs + 1] = message("user", "注音：" .. zhuyin .. "\n輸入法：" .. text)
  local n = utf8.len(text) or #text
  return '{"messages":[' .. table.concat(msgs, ",") .. '],"max_tokens":' .. (2 * n + 32)
      .. ',"temperature":0,"cache_prompt":true'
      .. (grammar and (',"grammar":' .. json_str(grammar)) or "") .. "}"
end

-- The typed keys of each character, or nil unless every character is
-- Chinese and the keys split into exactly one syllable per character.
local function syllables_of(keys, text)
  local syls = {}
  for s in keys:gmatch("[^ 6347]+[ 6347]") do syls[#syls + 1] = s end
  if table.concat(syls) ~= keys or #syls ~= utf8.len(text) then return nil end
  for _, cp in utf8.codes(text) do
    if cp < 0x3400 or cp > 0x9fff then return nil end
  end
  return syls
end

-- The IME's commonest single characters of a syllable (keys without the
-- tone) in every tone, as the bubble would show them.
local function same_syllable(env, base)
  if env.same[base] then return env.same[base] end
  if env.tr == nil then
    local ok, tr = pcall(function()
      return Component.Translator(env.engine, "", "script_translator@translator")
    end)
    env.tr = ok and tr or false
    local ok_tw, tw = pcall(Opencc, "t2tw.json")
    env.tw = ok_tw and tw or false  -- Taiwan forms, as the simplifier gives
  end
  local found, seen = {}, {}
  for _, tone in ipairs(TONE_KEYS) do
    local q = base .. tone
    local seg = Segment(0, #q)
    seg.tags = Set({ "abc" })
    local t = env.tr and env.tr:query(q, seg)
    local n = 0
    if t then
      for c in t:iter() do
        if n >= SAME_SYLLABLE then break end
        if c._end == #q and utf8.len(c.text) == 1 then
          local ch = env.tw and env.tw:convert(c.text) or c.text
          n = n + 1
          if not seen[ch] and not ch:find('["\\]') then seen[ch], found[#found + 1] = true, ch end
        end
      end
    end
  end
  env.same[base] = found
  return found
end

-- Each character of text, or another of the same syllable in any tone.
local function same_syllable_grammar(env, keys, text)
  local syls = syllables_of(keys, text)
  if not syls then return nil end
  local root, rules, i = {}, {}, 0
  for _, cp in utf8.codes(text) do
    local ch = utf8.char(cp)
    i = i + 1
    local alts = { '"' .. ch .. '"' }
    for _, c in ipairs(same_syllable(env, syls[i]:sub(1, -2))) do
      if c ~= ch then alts[#alts + 1] = '"' .. c .. '"' end
    end
    root[#root + 1] = "s" .. (i - 1)
    rules[#rules + 1] = "s" .. (i - 1) .. " ::= " .. table.concat(alts, " | ")
  end
  return "root ::= " .. table.concat(root, " ") .. "\n" .. table.concat(rules, "\n")
end

-- The answer -------------------------------------------------------------------
local QUOTES = { "「", "」", '"', "'", "“", "”" }
local END_PUNCT = { ["。"] = true, ["！"] = true, ["？"] = true, ["，"] = true }

-- The model's sentence, or the original when the answer is not one.
local function tidy(out, got)
  out = trim(out:gsub("\r", ""))
  out = trim(out:match("^[^\n]*") or "")
  local changed = true
  while changed do
    changed = false
    for _, q in ipairs(QUOTES) do
      if out:sub(1, #q) == q then out, changed = out:sub(#q + 1), true end
      if #out >= #q and out:sub(-#q) == q then out, changed = out:sub(1, -#q - 1), true end
    end
  end
  if not got:find(" ", 1, true) then out = out:gsub(" ", "") end
  for _, prefix in ipairs({ "輸入法：", "改正：", "修正：" }) do
    if out:sub(1, #prefix) == prefix then out = trim(out:sub(#prefix + 1)) end
  end
  local n_out, n_got = utf8.len(out) or 0, utf8.len(got) or 0
  if out == "" or n_out > 2 * n_got + 4 or n_out < n_got // 2 then return got end
  local c = chars(out)
  while #c > 0 and END_PUNCT[c[#c]] and got:sub(-#c[#c]) ~= c[#c] do c[#c] = nil end
  return table.concat(c)
end

-- Edits between two key strings: a key wrong, missing, extra, or two
-- neighbouring keys swapped each count one (optimal string alignment).
local function osa(a, b)
  local d = {}
  for i = 0, #a do d[i] = { [0] = i } end
  for j = 1, #b do d[0][j] = j end
  for i = 1, #a do
    for j = 1, #b do
      local cost = (a:byte(i) == b:byte(j)) and 0 or 1
      local v = math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
      if i > 1 and j > 1 and a:byte(i) == b:byte(j - 1) and a:byte(i - 1) == b:byte(j) then
        v = math.min(v, d[i - 2][j - 2] + 1)
      end
      d[i][j] = v
    end
  end
  return d[#a][#b]
end

local reverse  -- ReverseLookup of the schema's dictionary, opened once

-- Key strings that may type character ch (all its readings), or nil.
local function readings(ch)
  if reverse == nil then
    local ok, r = pcall(ReverseLookup, DICT)
    reverse = ok and r or false
  end
  if not reverse then return nil end
  local found, seen = {}, {}
  for s in (reverse:lookup(ch) or ""):gmatch("%S+") do
    local k = fix_data.keys[s]
    if k and not seen[k] then seen[k], found[#found + 1] = true, k end
  end
  return #found > 0 and found or nil
end

-- How far text reads from the typed keys: each character takes whichever of
-- its readings fits best, English words and numbers count as typed,
-- punctuation is skipped. nil when a character has no reading.
local function read_distance(keys, text)
  local tokens = {}
  for _, cp in utf8.codes(text) do
    local ch = utf8.char(cp)
    if ch:match("^%w$") then
      local last = tokens[#tokens]
      if last and last.word then last[1] = last[1] .. ch:lower() else tokens[#tokens + 1] = { ch:lower(), word = true } end
    elseif cp >= 0x3400 and cp <= 0x9fff or cp >= 0x20000 then
      local r = readings(ch)
      if not r then return nil end
      tokens[#tokens + 1] = r
    else
      local last = tokens[#tokens]
      if last then last.word = nil end  -- punctuation ends an English word
    end
  end
  local n = #keys
  local prev = {}
  for i = 0, n do prev[i] = i end  -- keys before the first character: extra
  for _, options in ipairs(tokens) do
    -- Only alignments near the best one so far are followed (a corrected
    -- sentence is a few edits from the keys or rejected anyway); a long
    -- sentence would otherwise take a fifth of a second.
    local cutoff = math.huge
    for i = 0, n do cutoff = math.min(cutoff, prev[i]) end
    cutoff = cutoff + 4
    local cur = {}
    for j = 0, n do
      local best = math.huge
      for _, o in ipairs(options) do
        for i = math.max(0, j - #o - 2), j do
          if prev[i] <= cutoff then
            local v = prev[i] + osa(keys:sub(i + 1, j), o)
            if v < best then best = v end
          end
        end
      end
      cur[j] = best
    end
    prev = cur
  end
  local best = math.huge
  for i = 0, n do best = math.min(best, prev[i] + (n - i)) end
  return best
end

-- Which of these is meant cannot be read from the keys or the sentence.
local PRONOUNS = {}
for _, p in ipairs(chars("他她它牠祂你妳您")) do PRONOUNS[p] = true end

local function swaps_pronoun(got, out)
  local a, b = chars(got), chars(out)
  local i = 1
  while i <= #a and i <= #b and a[i] == b[i] do i = i + 1 end
  local j, k = #a, #b
  while j >= i and k >= i and a[j] == b[k] do j, k = j - 1, k - 1 end
  if j - i == k - i then
    for x = i, j do
      if a[x] ~= b[x] and PRONOUNS[a[x]] and PRONOUNS[b[x]] then return true end
    end
    return false
  end
  local pa, pb = {}, {}
  for x = i, j do if PRONOUNS[a[x]] then pa[#pa + 1] = a[x] end end
  for x = i, k do if PRONOUNS[b[x]] then pb[#pb + 1] = b[x] end end
  return #pa > 0 and #pb > 0 and table.concat(pa) ~= table.concat(pb)
end

local s2tw  -- OpenCC simplified -> Taiwan traditional, opened once

-- Does text hold simplified characters (三种)? The dictionary has readings
-- for them, so the reading check alone lets them through.
local function has_simplified(text)
  if s2tw == nil then
    local ok, o = pcall(Opencc, "s2tw.json")
    s2tw = ok and o or false
  end
  if not s2tw then return false end
  local ok, converted = pcall(function() return s2tw:convert(text) end)
  return ok and converted ~= text
end

-- The correction still reads as the keys typed, at most one key edit worse
-- than what the IME showed; English words and numbers stay unless they are
-- what a slip left behind (早2攢 for 早點); no 他/她, 你/您 swaps and no
-- simplified characters the IME's text did not have.
local function guard(out, got, keys)
  if swaps_pronoun(got, out) then return false end
  if has_simplified(out) and not has_simplified(got) then return false end
  keys = keys:lower()
  local d_out, d_got = read_distance(keys, out), read_distance(keys, got)
  if not d_out or not d_got then return false end
  if table.concat(literal_runs(out), " ") ~= table.concat(literal_runs(got), " ") then
    return d_out < d_got
  end
  return d_out <= d_got + 1
end

-- "城市→程式、在→再": what the correction changed.
local function changes(a, b)
  local x, y = chars(a), chars(b)
  local i = 1
  while i <= #x and i <= #y and x[i] == y[i] do i = i + 1 end
  local j, k = #x, #y
  while j >= i and k >= i and x[j] == y[k] do j, k = j - 1, k - 1 end
  local out = {}
  if #x == #y then
    local s = nil
    for p = i, j + 1 do
      local differs = p <= j and x[p] ~= y[p]
      if differs and not s then s = p end
      if not differs and s then
        out[#out + 1] = table.concat(x, "", s, p - 1) .. "→" .. table.concat(y, "", s, p - 1)
        s = nil
      end
    end
  else
    out[1] = table.concat(x, "", i, math.max(j, i - 1)) .. "→" .. table.concat(y, "", i, math.max(k, i - 1))
  end
  while #out > 3 do out[#out] = nil end
  return table.concat(out, "、")
end

-- Processor --------------------------------------------------------------------
local processor = {}

function processor.init(env)
  local on = env.engine.schema.config:get_bool("asus_fix/enabled")
  env.enabled = (on == nil) or on
  env.serial = 0
  env.same = {}  -- same_syllable(), by syllable
end

local function set_state(ctx, value)
  if (ctx:get_property(STATE) or "") ~= value then ctx:set_property(STATE, value) end
end

-- Puts a request for the front end; with a grammar, the second pass.
local function ask(env, ctx, input, text, grammar)
  env.serial = env.serial + 1
  local id = env.serial .. "." .. os.time()
  env.pending = { id = id, input = input, text = text, time = os.time(), second = grammar ~= nil }
  ctx:set_property(REQUEST, id .. "\t" .. request_body(typed_zhuyin(input, text), text, grammar))
  set_state(ctx, "busy\t" .. input)
  ctx:refresh_non_confirmed_composition()
end

local function leave_shown(ctx, refresh)
  ctx:set_property(SHOWN, "")
  if refresh then ctx:refresh_non_confirmed_composition() end
end

local function on_answer(env, ctx)
  local raw = ctx:get_property(RESULT) or ""
  ctx:set_property(RESULT, "")
  local id, answer = raw:match("^([^\t]*)\t(.*)$")
  local p = env.pending
  if not p or id ~= p.id then return end
  env.pending = nil
  set_state(ctx, "")
  if ctx.input ~= p.input then  -- typed on meanwhile: too late
    ctx:refresh_non_confirmed_composition()
    return
  end
  local note
  if answer:sub(1, 1) == "!" then
    note = answer:sub(2)
  else
    local fixed = tidy(answer, p.text)
    if fixed ~= p.text and guard(fixed, p.text, p.input) then
      ctx:set_property(SHOWN, p.input .. "\t" .. fixed .. "\t" .. p.text)
    else
      local grammar = not p.second and same_syllable_grammar(env, p.input, p.text)
      if grammar then
        ask(env, ctx, p.input, p.text, grammar)  -- once more, any tone
        return
      end
      note = "沒有要改的"
    end
  end
  if note then set_state(ctx, "note\t" .. p.input .. "\t" .. note) end
  ctx:refresh_non_confirmed_composition()
end

function processor.func(key, env)
  local ctx = env.engine.context
  local code = key.keycode
  if code == XK_F23 then
    if not key:release() then on_answer(env, ctx) end
    return kAccepted
  end
  if key:release() then return kNoop end
  -- A note ("沒有要改的") stays until the next key.
  if (ctx:get_property(STATE) or ""):sub(1, 5) == "note\t" then set_state(ctx, "") end
  local p = env.pending
  if p and os.time() - p.time > TIMEOUT then
    env.pending = nil
    set_state(ctx, "")
  end

  local shown = ctx:get_property(SHOWN) or ""
  if shown ~= "" then
    local input = shown:match("^([^\t]*)")
    if input ~= ctx.input then
      leave_shown(ctx, false)
    elseif code == XK_Return or code == XK_KP_Enter then
      leave_shown(ctx, false)  -- (the composition still holds the correction)
      ctx:commit()
      return kAccepted
    elseif code == XK_Escape then
      leave_shown(ctx, true)
      return kAccepted
    elseif code >= 0xffe1 and code <= 0xffee then
      return kNoop  -- Shift, Ctrl, Alt alone
    else
      leave_shown(ctx, true)
      return kNoop  -- the key works on the original
    end
  end

  if code == XK_r and key:ctrl() and not key:alt() and not key:shift() and not key:super() then
    if not env.enabled or not ctx:get_option(SUPPORT) or not ctx:is_composing() or ctx.input == ""
        or ctx:get_option("ascii_mode") or ctx.input:sub(1, 1) == "`"
        or ctx.caret_pos ~= #ctx.input then
      return kNoop
    end
    -- Pressed again for the same input: still waiting. For another input (the
    -- last answer never came, or came too late) ask anew; an answer to the
    -- old request no longer matches the id.
    if env.pending and env.pending.input == ctx.input then return kAccepted end
    local text = ctx:get_commit_text()
    if text == "" then return kAccepted end
    ask(env, ctx, ctx.input, text)
    return kAccepted
  end
  return kNoop
end

-- Segmentor: the correction shown is one segment over the whole input ----------
local segmentor = {}

function segmentor.func(segmentation, env)
  local shown = env.engine.context:get_property(SHOWN) or ""
  if shown == "" then return true end
  local input = segmentation.input
  if type(input) == "function" then input = segmentation:input() end
  if shown:match("^([^\t]*)") ~= input or segmentation:get_current_start_position() ~= 0 then return true end
  local seg = Segment(0, #input)
  seg.tags = Set({ "asus_fix" })
  segmentation:add_segment(seg)
  return false
end

-- Translator: the correction, then the original ---------------------------------
local translator = {}

-- The two rows for the asus_fix segment, or nothing.
local function rows(ctx, seg)
  local _, fixed, orig = (ctx:get_property(SHOWN) or ""):match("^([^\t]*)\t([^\t]*)\t(.*)$")
  if not fixed then return {} end
  local c1 = Candidate("ai_fix", seg.start, seg._end, fixed, "AI 修正：" .. changes(orig, fixed) .. "（Enter 送出，Esc 原句）")
  local c2 = Candidate("ai_orig", seg.start, seg._end, orig, "原句")
  c1.quality, c2.quality = 1000, 999
  return { c1, c2 }
end

function translator.func(input, seg, env)
  if not seg:has_tag("asus_fix") then return end
  for _, c in ipairs(rows(env.engine.context, seg)) do yield(c) end
end

-- Filter: "AI 修正中…" / a note on the first candidate ----------------------------
local filter = {}

function filter.func(input, env)
  local ctx = env.engine.context
  -- The correction shown: its two rows exactly as asked, whatever the filters
  -- before made of them (asus_grammar would turn 預期的 back into 預期得,
  -- asus_typo adds its own 更正 row).
  local seg = ctx.composition:back()
  if seg and seg:has_tag("asus_fix") then
    local fixed = rows(ctx, seg)
    if #fixed > 0 then
      for _, c in ipairs(fixed) do yield(c) end
      return
    end
  end
  local state = ctx:get_property(STATE) or ""
  local kind, for_input, note = state:match("^(%a+)\t([^\t]*)\t?(.*)$")
  local text = nil
  if kind == "busy" and for_input == ctx.input then
    text = "AI 修正中…"
  elseif kind == "note" and for_input == ctx.input then
    text = "AI：" .. note
  end
  local first = true
  for cand in input:iter() do
    if first and text then
      local c = cand.comment ~= "" and (cand.comment .. "　" .. text) or text
      yield(ShadowCandidate(cand, cand.type, cand.text, c))
    else
      yield(cand)
    end
    first = false
  end
end

return { processor = processor, segmentor = segmentor, translator = translator, filter = filter,
         typed_zhuyin = typed_zhuyin, request_body = request_body, tidy = tidy, guard = guard,
         read_distance = read_distance, changes = changes }
