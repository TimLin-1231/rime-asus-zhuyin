-- asus_edit.lua
-- Editing inside the composition, as in ASUS Smart Input / Microsoft Bopomofo.
--
--   Home / End     jump to the start / back to the end
--   Left / Right   move a cursor one character at a time (a number or an
--                  English word is one step); the bubble shows the text
--                  around the cursor and the reading of the next character
--   Down           candidates for the words starting at the cursor; picking
--                  one replaces only that part, the text after it stays
--   BackSpace      deletes the character before the cursor
--   typing         inserts at the cursor
--   Esc            leaves the cursor (back to the end of the input)
--
-- While the cursor is in use, the composition is a list of units (one per
-- character / number / English word) with their keys and current text, kept
-- in the context property "asus_edit" (shared with the filter). The text
-- before the cursor is locked by selecting a "fixed" candidate for it; every
-- later segment shows its units' current text as its first candidate (type
-- "edit"), so nothing after the cursor changes unless the user changes it.
-- Down asks asus_lang.lua (property asus_edit_span) to make one segment from
-- the cursor to the end, so its menu lists the words starting there.
--
--   processor  lua_processor@*asus_edit*processor  (before asus_keys)
--   filter     lua_filter@*asus_edit*filter        (before asus_compact)

local kAccepted, kNoop = 1, 2
local XK_Left, XK_Right, XK_Down = 0xff51, 0xff53, 0xff54
local XK_Home, XK_End = 0xff50, 0xff57
local XK_BackSpace, XK_Escape, XK_space = 0xff08, 0xff1b, 0x20

local EXPANDED = "_asus_expanded"
local STATE, FIX, SPAN, OPEN_END = "asus_edit", "asus_edit_fix", "asus_edit_span", "asus_edit_unit_end"
local US, FS = "\30", "\31"  -- separators in the serialised state

local zy = require("asus_zhuyin_data")

local KEYS = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-"
local SYMBOLS = {
  "ㄅ", "ㄆ", "ㄇ", "ㄈ", "ㄉ", "ㄊ", "ㄋ", "ㄌ", "ㄍ", "ㄎ", "ㄏ", "ㄐ", "ㄑ", "ㄒ",
  "ㄓ", "ㄔ", "ㄕ", "ㄖ", "ㄗ", "ㄘ", "ㄙ", "ㄧ", "ㄨ", "ㄩ", "ㄚ", "ㄛ", "ㄜ", "ㄝ",
  "ㄞ", "ㄟ", "ㄠ", "ㄡ", "ㄢ", "ㄣ", "ㄤ", "ㄥ", "ㄦ",
}
local TONES = { [" "] = "ˉ", ["6"] = "ˊ", ["3"] = "ˇ", ["4"] = "ˋ", ["7"] = "˙" }
local ZHUYIN = {}
for i = 1, #KEYS do ZHUYIN[KEYS:sub(i, i)] = SYMBOLS[i] end

-- Zhuyin for keys (digit keys inside a syllable are Zhuyin, a final tone key
-- is a tone mark).
local function reading(keys)
  local out = {}
  for i = 1, #keys do
    local k = keys:sub(i, i)
    out[#out + 1] = (i == #keys and i > 1 and TONES[k]) or ZHUYIN[k] or k
  end
  return table.concat(out)
end

local function chars(s)
  local out = {}
  for _, cp in utf8.codes(s) do out[#out + 1] = utf8.char(cp) end
  return out
end

local function syllables(keys)
  return require("asus_learn").syllables(keys)
end

local function segments(ctx)
  local ok, segs = pcall(function() return ctx.composition:toSegmentation():get_segments() end)
  return ok and segs or {}
end

-- State --------------------------------------------------------------------
-- { input=, caret= (unit index the cursor is before; #units+1 = end),
--   units = { {s=, e=, text=, kind=} }, ins = {s=, e=} or nil }
local function load(ctx)
  local v = ctx:get_property(STATE)
  if v == nil or v == "" then return nil end
  local parts = {}
  for p in (v .. US):gmatch("(.-)" .. US) do parts[#parts + 1] = p end
  local st = { input = parts[1], caret = tonumber(parts[2]), units = {} }
  local is, ie = parts[3]:match("^(%d+),(%d+)$")
  if is then st.ins = { s = tonumber(is), e = tonumber(ie) } end
  for i = 4, #parts do
    local s, e, kind, text = parts[i]:match("^(%d+)" .. FS .. "(%d+)" .. FS .. "(%a*)" .. FS .. "(.*)$")
    if s then st.units[#st.units + 1] = { s = tonumber(s), e = tonumber(e), kind = kind, text = text } end
  end
  if st.input ~= ctx.input then return nil end
  return st
end

local function save(ctx, st)
  if not st then ctx:set_property(STATE, ""); return end
  local parts = { st.input, tostring(st.caret), st.ins and (st.ins.s .. "," .. st.ins.e) or "" }
  for _, u in ipairs(st.units) do
    parts[#parts + 1] = table.concat({ u.s, u.e, u.kind, u.text }, FS)
  end
  ctx:set_property(STATE, table.concat(parts, US))
end

-- Units from what the composition shows now.
local function units_of(ctx)
  local input, out = ctx.input, {}
  for _, seg in ipairs(segments(ctx)) do
    if seg._end > seg.start then
      local cand = seg:get_selected_candidate()
      local text = cand and cand.text or ""
      local keys = input:sub(seg.start + 1, seg._end)
      local kind = seg:has_tag("asus_num") and "num" or seg:has_tag("asus_en") and "en"
          or seg:has_tag("asus_stray") and "stray" or "zh"
      local syl, starts = nil, nil
      if kind == "zh" then syl, starts = syllables(keys) end
      local cs = chars(text)
      if syl and #syl == #cs then
        for i, ch in ipairs(cs) do
          out[#out + 1] = { s = seg.start + starts[i], e = seg.start + starts[i + 1], text = ch, kind = "zh" }
        end
      else
        out[#out + 1] = { s = seg.start, e = seg._end, text = text, kind = kind }
      end
    end
  end
  return out
end

local function text_between(st, a, b)
  local out = {}
  for _, u in ipairs(st.units) do
    if u.s >= a and u.e <= b then out[#out + 1] = u.text end
  end
  return table.concat(out)
end

-- Key offset where the cursor is.
local function caret_offset(st)
  local u = st.units[st.caret]
  return u and u.s or #st.input
end

-- Composition control --------------------------------------------------------
local function set_list(ctx, expanded)
  ctx:set_option(EXPANDED, expanded)
  ctx:set_option("_linear", expanded)
  ctx:set_option("_auto_commit", false)
end

-- End of the selected segments counted from the start, and whether one of
-- them reaches past `limit`.
local function locked_end(ctx, limit)
  local e = 0
  for _, s in ipairs(segments(ctx)) do
    if s.status ~= "kSelected" and s.status ~= "kConfirmed" then break end
    if s._end > limit then return e, true end
    e = s._end
  end
  return e, false
end

-- Select one "fixed" candidate with `text` for keys [s, e).
local function select_fixed(ctx, s, e, text)
  ctx:set_property(SPAN, tostring(s))
  ctx:set_property(FIX, s .. FS .. e .. FS .. text)
  local was = ctx:get_option(EXPANDED)
  ctx:set_option(EXPANDED, true)  -- asus_compact would trim the menu to one
  ctx:set_option("_auto_commit", false)
  ctx:refresh_non_confirmed_composition()
  local seg = ctx.composition:back()
  local done = false
  if seg and seg.start == s then
    for i = 0, 300 do
      local c = seg:get_candidate_at(i)
      if not c then break end
      -- (Matched by span and text: a later uniquifier may re-wrap it.)
      if c.start == s and c._end == e and c.text == text then ctx:select(i); done = true; break end
    end
  end
  ctx:set_property(FIX, "")
  ctx:set_property(SPAN, "")
  ctx:set_option(EXPANDED, was)
  return done
end

-- Lock the text before `p` (selected), nothing after it.
local function lock_to(ctx, st, p)
  local e, past = locked_end(ctx, p)
  if past then
    -- Shortening the input drops the selections that reach past p.
    local input = ctx.input
    ctx.input = input:sub(1, p)
    ctx.input = input
    e = locked_end(ctx, p)
  end
  if e < p then select_fixed(ctx, e, p, text_between(st, e, p)) end
  ctx:refresh_non_confirmed_composition()
end

local function apply(ctx, st)
  save(ctx, st)
  lock_to(ctx, st, st.ins and st.ins.s or caret_offset(st))
end

-- Text the inserted keys converted to (recorded by the filter), as units.
local function finish_insert(ctx, st)
  if not st.ins then return end
  local conv = ctx:get_property("asus_edit_conv") or ""
  local keys = st.input:sub(st.ins.s + 1, st.ins.e)
  local syl, starts = syllables(keys)
  local cs = chars(conv)
  local new = {}
  if syl and #syl == #cs then
    for i, ch in ipairs(cs) do
      new[#new + 1] = { s = st.ins.s + starts[i], e = st.ins.s + starts[i + 1], text = ch, kind = "zh" }
    end
  else
    new[1] = { s = st.ins.s, e = st.ins.e, text = conv ~= "" and conv or reading(keys), kind = "zh" }
  end
  for i = #new, 1, -1 do table.insert(st.units, st.caret, new[i]) end
  st.caret = st.caret + #new
  st.ins = nil
end

local function shift_units(st, from, delta)
  for _, u in ipairs(st.units) do
    if u.s >= from then u.s, u.e = u.s + delta, u.e + delta end
  end
end

local function leave(ctx)
  save(ctx, nil)
  ctx:set_property("asus_edit_auto", "")
  ctx:set_property(SPAN, "")
  ctx:set_property(OPEN_END, "")
end

-- Processor --------------------------------------------------------------------
local processor = {}

function processor.init(env)
  local ctx = env.engine.context
  env.conn = ctx.commit_notifier:connect(function(c) leave(c) end)
  -- A composition can also end without a commit (Esc, cleared when the
  -- focus moves); a stale state would keep the other filters waiting.
  env.update_conn = ctx.update_notifier:connect(function(c)
    if c.input == "" and (c:get_property(STATE) or "") ~= "" then leave(c) end
  end)
end

function processor.fini(env)
  if env.conn then env.conn:disconnect() end
  if env.update_conn then env.update_conn:disconnect() end
end

local function is_key_char(code)
  if code < 0x21 or code > 0x7e then return false end
  local ch = string.char(code)
  return ZHUYIN[ch] ~= nil or TONES[ch] ~= nil or ch:match("%a") ~= nil
end

-- Words of the language model's sentence behind cand. Filters wrap it in
-- several ShadowCandidates and get_genuine() removes one layer at a time.
local function sentence_entries(cand)
  local c = cand
  for _ = 1, 8 do
    local ok, sen = pcall(function() return c:to_sentence() end)
    if ok and sen then return sen.entrys end
    local ok2, g = pcall(function() return c:get_genuine() end)
    if not (ok2 and g) then return nil end
    c = g
  end
  return nil
end

-- Index of the unit where the last word starts: the language model's last
-- word for Chinese (the entries of its sentence), the last unit for a number
-- or an English word.
local function last_word_start(ctx, units)
  if #units == 0 then return nil end
  local seg = ctx.composition:back()
  if not seg or seg._end ~= #ctx.input then return nil end
  local first = nil  -- first unit of the last segment
  for i, u in ipairs(units) do
    if u.s >= seg.start then first = i; break end
  end
  if not first then return nil end
  if units[#units].kind ~= "zh" or units[#units].s == seg.start then return math.max(first, #units) end
  local cand = seg:get_selected_candidate()
  local entries = cand and sentence_entries(cand)
  if not (entries and #entries > 0) then return first end
  local n = utf8.len(entries[#entries].text) or 1
  return math.max(first, #units - n + 1)
end

local function open_list(ctx, st)
  finish_insert(ctx, st)
  local u = st.units[st.caret]
  if not u then return false end
  save(ctx, st)
  lock_to(ctx, st, u.s)
  ctx:set_property(OPEN_END, (u.kind == "num" or u.kind == "en") and (u.e .. FS .. u.kind) or "")
  ctx:set_property(SPAN, tostring(u.s))
  set_list(ctx, true)
  ctx:refresh_non_confirmed_composition()
  return true
end

local function close_list(ctx)
  ctx:set_property(SPAN, "")
  ctx:set_property(OPEN_END, "")
  set_list(ctx, false)
  ctx:refresh_non_confirmed_composition()
end

-- A candidate was picked from the list opened at the cursor.
local function picked(ctx, st, index)
  local seg = ctx.composition:back()
  local cand = seg and seg:get_candidate_at(index)
  if not cand then return end
  if cand.type == "edit" then close_list(ctx); return end  -- kept as it was
  local p, q = cand.start, cand._end
  ctx:set_option("_auto_commit", false)
  ctx:select(index)
  ctx:set_property(SPAN, "")
  ctx:set_property(OPEN_END, "")
  -- Replace the units in [p, q) by the picked text.
  local keep, first = {}, nil
  for _, u in ipairs(st.units) do
    if u.e <= p or u.s >= q then keep[#keep + 1] = u
    elseif not first then first = #keep + 1 end
  end
  local keys = st.input:sub(p + 1, q)
  local syl, starts = syllables(keys)
  local cs = chars(cand.text)
  local new = {}
  if syl and #syl == #cs then
    for i, ch in ipairs(cs) do
      new[#new + 1] = { s = p + starts[i], e = p + starts[i + 1], text = ch, kind = "zh" }
    end
  else
    new[1] = { s = p, e = q, text = cand.text, kind = cand.type == "number" and "num"
               or cand.type == "english" and "en" or "zh" }
  end
  first = first or (#keep + 1)
  for i = #new, 1, -1 do table.insert(keep, first, new[i]) end
  st.units = keep
  st.caret = first + #new
  set_list(ctx, false)
  if st.caret > #st.units then
    leave(ctx)
  else
    save(ctx, st)
  end
  ctx:refresh_non_confirmed_composition()
end

function processor.func(key, env)
  local ctx = env.engine.context
  if key:release() or not ctx:is_composing() or ctx:get_option("ascii_mode") then return kNoop end
  if ctx.input:sub(1, 1) == "`" then return kNoop end
  local back = ctx.composition:back()
  if back and back:has_tag("prediction") then return kNoop end
  local code, mods = key.keycode, key.modifier
  local st = load(ctx)
  if not st and ctx:get_property(STATE) ~= "" then leave(ctx) end  -- input changed elsewhere
  local expanded = ctx:get_option(EXPANDED)

  -- The list opened at the cursor.
  if st and expanded then
    if mods == 0 and code >= 0x31 and code <= 0x39 then
      local seg = ctx.composition:back()
      local page = env.engine.schema.page_size
      local index = seg.selected_index - seg.selected_index % page + (code - 0x31)
      if seg.menu:prepare(index + 1) > index then picked(ctx, st, index) end
      return kAccepted
    end
    if mods == 0 and (code == XK_space or code == 0xff0d) then
      picked(ctx, st, ctx.composition:back().selected_index)
      -- Enter also commits the whole input, as in the plain list.
      if code == 0xff0d and ctx:is_composing() then ctx:commit() end
      return kAccepted
    end
    if code == XK_Escape then
      close_list(ctx)
      if ctx:get_property("asus_edit_auto") == "1" then
        -- Opened by Down at the end: Esc puts everything back as it was
        -- (nothing selected, as before Down).
        lock_to(ctx, st, 0)
        leave(ctx)
      end
      return kAccepted
    end
    return kNoop  -- arrows / paging browse the list (asus_keys, librime)
  end
  if expanded or mods ~= 0 and not (key:shift() and code >= 0x41 and code <= 0x5a) then
    return kNoop
  end

  if code == XK_Left or code == XK_Home then
    if not st then
      local units = units_of(ctx)
      if #units == 0 then return kNoop end
      st = { input = ctx.input, caret = #units + 1, units = units }
    end
    finish_insert(ctx, st)
    if code == XK_Home then
      st.caret = 1
      while st.caret < #st.units and st.units[st.caret].text == "" do st.caret = st.caret + 1 end
    else
      repeat st.caret = st.caret - 1 until st.caret <= 1 or st.units[st.caret].text ~= ""
      if st.caret < 1 then st.caret = 1 end
    end
    apply(ctx, st)
    return kAccepted
  end
  -- Down at the end of the input: the list for the last word, the one just
  -- typed, instead of a list that starts at the beginning of a long input.
  if code == XK_Down and not st and ctx:has_menu() then
    local units = units_of(ctx)
    local caret = last_word_start(ctx, units)
    if caret and caret > 1 then
      st = { input = ctx.input, caret = caret, units = units }
      open_list(ctx, st)
      ctx:set_property("asus_edit_auto", "1")
      return kAccepted
    end
    return kNoop  -- a single word: the ordinary list (asus_keys)
  end
  if not st then return kNoop end

  if code == XK_Right then
    finish_insert(ctx, st)
    repeat st.caret = st.caret + 1 until st.caret > #st.units or st.units[st.caret].text ~= ""
    if st.caret > #st.units then
      lock_to(ctx, st, #st.input)
      leave(ctx)
    else
      apply(ctx, st)
    end
    return kAccepted
  end
  if code == XK_Escape or code == XK_End then
    finish_insert(ctx, st)
    lock_to(ctx, st, #st.input)
    leave(ctx)
    return kAccepted
  end
  if code == XK_Down then
    open_list(ctx, st)
    return kAccepted
  end
  if code == XK_BackSpace then
    if st.ins then
      -- Delete the last inserted key.
      local at = st.ins.e
      st.input = st.input:sub(1, at - 1) .. st.input:sub(at + 1)
      st.ins.e = st.ins.e - 1
      shift_units(st, at, -1)
      if st.ins.e == st.ins.s then st.ins = nil end
    elseif st.caret > 1 then
      local u = table.remove(st.units, st.caret - 1)
      st.input = st.input:sub(1, u.s) .. st.input:sub(u.e + 1)
      shift_units(st, u.e, u.s - u.e)
      st.caret = st.caret - 1
    else
      return kAccepted
    end
    if st.input == "" then
      leave(ctx)
      ctx:clear()
      return kAccepted
    end
    lock_to(ctx, st, 0)  -- drop all selections: the input changes below
    ctx.input = st.input
    apply(ctx, st)
    return kAccepted
  end
  if is_key_char(code) then
    -- Insert at the cursor.
    local at = st.ins and st.ins.e or caret_offset(st)
    if at >= #st.input and not st.ins then
      lock_to(ctx, st, #st.input)
      leave(ctx)
      return kNoop  -- at the end: ordinary typing
    end
    local ch = string.char(code)
    shift_units(st, at, 1)
    st.input = st.input:sub(1, at) .. ch .. st.input:sub(at + 1)
    st.ins = st.ins and { s = st.ins.s, e = st.ins.e + 1 } or { s = at, e = at + 1 }
    lock_to(ctx, st, 0)
    ctx.input = st.input
    apply(ctx, st)
    return kAccepted
  end
  return kNoop  -- Enter commits, punctuation commits then types (librime)
end

-- Filter -----------------------------------------------------------------------
-- Text around the cursor and the reading after it, for the bubble.
local function caret_comment(st)
  local before, after = {}, {}
  for i = math.max(1, st.caret - 3), st.caret - 1 do before[#before + 1] = st.units[i].text end
  for i = st.caret, math.min(#st.units, st.caret + 2) do after[#after + 1] = st.units[i].text end
  local u = st.units[st.caret]
  local r = ""
  if st.ins then
    r = reading(st.input:sub(st.ins.s + 1, st.ins.e))
  elseif u then
    r = (u.kind == "zh") and reading(st.input:sub(u.s + 1, u.e)) or ""
  end
  return table.concat(before) .. "｜" .. table.concat(after) .. (r ~= "" and ("  " .. r) or "")
end

local function filter(input, env)
  local ctx = env.engine.context
  local st = load(ctx)
  local fix = ctx:get_property(FIX) or ""
  local fs, fe, ftext = fix:match("^(%d+)" .. FS .. "(%d+)" .. FS .. "(.*)$")
  fs, fe = tonumber(fs), tonumber(fe)
  if not st and not fs then
    for cand in input:iter() do yield(cand) end
    return
  end
  local first, buffer = nil, {}
  -- No candidate twice (the current text, the sentence and the phrase are
  -- often the same word).
  local seen = {}
  local function out(c)
    local key = c.start .. ":" .. c._end .. ":" .. c.text
    if not seen[key] then seen[key] = true; yield(c) end
  end
  for cand in input:iter() do
    if not first then
      first = cand
      -- This translation's segment.
      local a, b = cand.start, cand._end
      for _, s in ipairs(segments(ctx)) do
        if s.start == a and s._end > s.start then b = s._end end
      end
      local synthetic
      if st and a >= (st.ins and st.ins.s or caret_offset(st)) or (st and st.ins and a == st.ins.s) then
        -- Current text of the units in [a, b), with the inserted keys converted.
        local covered, parts, ok = a, {}, true
        if st.ins and st.ins.s == a then
          local conv
          for c in input:iter() do  -- remaining candidates of this segment
            buffer[#buffer + 1] = c
            if not conv and c._end == st.ins.e then conv = c.text end
            if #buffer > 150 then break end
          end
          if first._end == st.ins.e then conv = first.text end
          conv = conv or reading(st.input:sub(st.ins.s + 1, st.ins.e))
          ctx:set_property("asus_edit_conv", conv)
          parts[1] = conv
          covered = st.ins.e
        end
        for _, u in ipairs(st.units) do
          if u.s >= covered and u.e <= b then
            if u.s ~= covered then ok = false end
            parts[#parts + 1] = u.text
            covered = u.e
          end
        end
        if ok and covered == b then
          local comment = (b == #ctx.input) and caret_comment(st) or ""
          synthetic = Candidate("edit", a, b, table.concat(parts), comment)
          synthetic.quality = 2000
        end
      end
      if fs and fs == a then out(Candidate("fixed", fs, fe, ftext, "")) end
      if synthetic then out(synthetic) end
      -- The list at a number / English word: its own forms first.
      local unit_end = tonumber((ctx:get_property(OPEN_END) or ""):match("^(%d+)"))
      if unit_end and st and a == caret_offset(st) then
        local front, rest = {}, {}
        for _, c in ipairs({ cand, table.unpack(buffer) }) do
          if c._end == unit_end and (c.type == "number" or c.type == "english") then
            front[#front + 1] = c
          else
            rest[#rest + 1] = c
          end
        end
        for c in input:iter() do
          if c._end == unit_end and (c.type == "number" or c.type == "english") then
            front[#front + 1] = c
          else
            rest[#rest + 1] = c
          end
          if #rest > 60 then break end
        end
        for _, c in ipairs(front) do out(c) end
        cand, buffer = rest[1], { table.unpack(rest, 2) }
      end
      if cand then out(cand) end
      for _, c in ipairs(buffer) do out(c) end
    else
      out(cand)
    end
  end
end

-- The "fixed" candidate for select_fixed() also comes from a translator: the
-- filter only runs when something translates the span, and nothing does for
-- some (an English word typed in capitals, API).
local function translator(input, seg, env)
  local fix = env.engine.context:get_property(FIX) or ""
  local fs, fe, ftext = fix:match("^(%d+)" .. FS .. "(%d+)" .. FS .. "(.*)$")
  if fs and tonumber(fs) == seg.start then
    local c = Candidate("fixed", seg.start, tonumber(fe), ftext, "")
    c.quality = 3000
    yield(c)
  end
end

return { processor = processor, filter = filter, translator = translator }
