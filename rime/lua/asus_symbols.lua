-- asus_symbols.lua
-- Quick punctuation, modelled after ASUS Smart Input.
--
--   * ` opens a symbol keyboard: the candidate window lists "symbol  key" for
--     the current category; pressing a key commits its symbol immediately.
--     Tab / Shift+Tab switch category, Left/Right move the highlight, Up/Down
--     change page, Space/Enter commit the highlighted symbol, Esc/BackSpace
--     close. Layouts live in the schema under asus_symbols/categories
--     ("<key><symbol>" items separated by spaces; item order = display order).
--   * Ctrl+punctuation commits full-width punctuation directly (Chinese mode
--     only). Ctrl+/ and Ctrl+[ ] are deliberately left to applications.
--   * Half-width punctuation after letters and digits (idea from BlendKey,
--     MIT), so mixed text types naturally: 10:30, file.txt, v2.3, I'll,
--     a@b.com, snake_case, 10,000, 50%, Hello, world. Chinese mode, no
--     Ctrl/Alt:
--     1. Empty composition, and the last text committed ends in A-Z a-z 0-9
--        (librime's commit history; a space, Chinese or punctuation after it
--        does not count, and BackSpace / Return passed to the application
--        empty the history): , . : ' _ @ # $ % ^ & * = + ~ | commit their
--        ASCII character at once. , and . are the Zhuyin keys ㄝ ㄡ: no
--        syllable is being typed there, and starting one with them right
--        after English or digits is rare. To type ㄝ/ㄡ there anyway:
--        BackSpace (the app deletes the , or .) and the key again. The keys
--        typed next may finish the word (asus_lang.lua: property asus_glue,
--        file. + txt, I' + ll) or the number (asus_num_ctx: 2026. + 5).
--     2. While composing, caret at the end, list closed: a key that does not
--        belong to the run of keys before it (asus_lang.in_run_at: , . ' + #
--        @ _ inside or after a word stay there, and asus_lang decides with
--        its plan: 我2026,我 / 我file.txt / 3.5, while 歐 偶 耶 still type)
--        first makes digit keys at the end a number (asus_lang.finalize:
--        我10:30, 我10，). Then ' $ % ^ & * = ~ | right after an English word
--        or a number commit it and their ASCII character (我file$, 我50%);
--        any other key goes on to librime's punctuator as before.
--     After Chinese nothing changes: < > [ ] { } \ " give ，。「」『』、“”.
--     These stay full-width after English too (OK，我 / 「OK」 /
--     Python、Java are how Chinese text is written); ( ) ? ! : are ASCII
--     everywhere (schema __patch). Ctrl+punctuation is always full-width,
--     the ` keyboard is unchanged, English mode is not affected.
--
-- processor must come first in engine/processors; translator handles the
-- segment tagged asus_symbol (recognizer pattern "^`$").

local kRejected, kAccepted, kNoop = 0, 1, 2

local XK_Tab, XK_ISO_Left_Tab = 0xff09, 0xfe20
local XK_Return, XK_KP_Enter = 0xff0d, 0xff8d
local XK_Escape, XK_BackSpace = 0xff1b, 0xff08
local XK_space, XK_grave = 0x20, 0x60

local PROP = "asus_symbol_category"
local TRIGGER = "`"

-- Ctrl+key -> full-width punctuation. Weasel clears Ctrl but keeps Shift when
-- translating keys, so Ctrl+Shift+; arrives as ':' with Shift|Control; the
-- unshifted spelling is accepted too.
local CTRL_PUNCT = {
  [","] = "，", ["."] = "。", ["'"] = "、", [";"] = "；",
  [":"] = "：", ["?"] = "？", ["!"] = "！",
  ["{"] = "『", ["}"] = "』", ["("] = "（", [")"] = "）",
}
local SHIFTED = { [";"] = ":", ["/"] = "?", ["1"] = "!", ["["] = "{", ["]"] = "}",
                  ["9"] = "(", ["0"] = ")" }

-- Load categories from the schema config: { {name=, items={{key=, sym=}}, map={} } }
local function load_categories(config)
  local cats = {}
  local n = config:get_list_size("asus_symbols/categories") or 0
  for i = 0, n - 1 do
    local base = "asus_symbols/categories/@" .. i
    local name = config:get_string(base .. "/name") or ("#" .. (i + 1))
    local layout = config:get_string(base .. "/layout") or ""
    local cat = { name = name, items = {}, map = {} }
    for item in layout:gmatch("%S+") do
      local key, sym = item:sub(1, 1), item:sub(2)
      if sym ~= "" then
        cat.items[#cat.items + 1] = { key = key, sym = sym }
        cat.map[key] = sym
      end
    end
    cats[#cats + 1] = cat
  end
  return cats
end

local function current_index(ctx, count)
  local i = tonumber(ctx:get_property(PROP)) or 1
  if i < 1 or i > count then i = 1 end
  return i
end

local function key_label(key)
  if key:match("%l") then return key:upper() end
  if key:match("%u") then return "⇧" .. key end
  if key == " " then return "Space" end
  return key
end

-- Processor ----------------------------------------------------------------
local processor = {}

function processor.init(env)
  env.cats = load_categories(env.engine.schema.config)
end

local function in_symbol_mode(ctx)
  return ctx.input == TRIGGER
end

local function commit(env, text)
  local ctx = env.engine.context
  ctx:clear()
  env.engine:commit_text(text)
end

-- Half-width punctuation after letters and digits ----------------------------
-- Keys typed as their ASCII character right after a committed letter or
-- digit (rule 1 in the header). , . are ㄝ ㄡ otherwise; : is ASCII anyway
-- but goes through here so that 10 + : + 30 knows the 30 is a number.
local AFTER_COMMIT = {}
for c in (",.:'_@#$%^&*=+~|"):gmatch(".") do AFTER_COMMIT[c] = true end
-- Keys typed as ASCII right after an English word or a number in the
-- composition (rule 2). , . ' + # @ _ are not here: asus_lang.lua keeps them
-- in the English word or number (file.txt, I'm, C++, a@b.com, 10,000).
local IN_COMPOSITION = {}
for c in ("'$%^&*=~|"):gmatch(".") do IN_COMPOSITION[c] = true end

-- Returns kAccepted when the key was handled, kNoop when the next processors
-- must see it (after the composition was prepared), or nil.
local function half_width(env, ctx, ch)
  local lang = require("asus_lang")
  if not ctx:is_composing() then
    if not AFTER_COMMIT[ch] then return nil end
    local last = lang.last_commit(ctx)
    if not last:match("[A-Za-z0-9]$") then return nil end
    env.engine:commit_text(ch)
    ctx:set_property("asus_num_ctx", (last:match("%d$") and ch:match("[.,:]")) and "1" or "0")
    -- The keys typed next may finish the word: file. + txt, I' + ll.
    ctx:set_property("asus_glue", ch:match("[.'@_]") and (last:match("[%w._@'%-]*$"):sub(-40) .. ch) or "")
    return kAccepted
  end
  local input = ctx.input
  if in_symbol_mode(ctx) or input == "" or ctx.caret_pos ~= #input
      or ctx:get_option("_asus_expanded") or (ctx:get_property("asus_edit") or "") ~= "" then
    return nil
  end
  if lang.in_run_at(input .. ch, #input + 1) then return nil end  -- a key of the word / syllable
  -- The composition is about to be committed by the punctuator (or the key
  -- below): digit keys at its end are a number now (10:30, 我10，).
  lang.finalize(ctx, true)
  local seg = ctx.composition:back()
  if IN_COMPOSITION[ch] and seg and ctx.input:match("[A-Za-z0-9]$")
      and (seg:has_tag("asus_en") or seg:has_tag("asus_num")) then
    ctx:commit()
    env.engine:commit_text(ch)
    return kAccepted
  end
  return kNoop
end

function processor.func(key, env)
  local ctx = env.engine.context
  if key:release() or ctx:get_option("ascii_mode") then
    return kNoop
  end
  local code = key.keycode

  -- Ctrl+punctuation ---------------------------------------------------------
  if key:ctrl() and not key:alt() and code >= 0x20 and code < 0x7f then
    local ch = string.char(code)
    if key:shift() and SHIFTED[ch] then ch = SHIFTED[ch] end
    local punct = CTRL_PUNCT[ch]
    if punct then
      if ctx:is_composing() and not in_symbol_mode(ctx) then
        require("asus_lang").finalize(ctx)  -- digit keys at the end: a number now
        ctx:commit()
      else
        ctx:clear()
      end
      env.engine:commit_text(punct)
      return kAccepted
    end
    return kNoop
  end
  if key:ctrl() or key:alt() then
    return kNoop
  end

  -- Half-width punctuation after letters and digits (rules in the header) --
  if code > 0x20 and code < 0x7f and code ~= XK_grave and string.char(code):match("%p") then
    local done = half_width(env, ctx, string.char(code))
    if done ~= nil then return done end
  end

  -- Open the symbol keyboard ---------------------------------------------
  if not ctx:is_composing() then
    if code == XK_grave and not key:shift() and #env.cats > 0 then
      ctx:set_property(PROP, "1")
      ctx:set_option("_linear", false)  -- vertical list: Up/Down, Left/Right = page
      ctx:push_input(TRIGGER)
      return kAccepted
    end
    return kNoop
  end
  if not in_symbol_mode(ctx) then
    return kNoop
  end

  -- Inside the symbol keyboard ---------------------------------------------
  if code == XK_Tab or code == XK_ISO_Left_Tab then
    local i = current_index(ctx, #env.cats)
    local step = (key:shift() or code == XK_ISO_Left_Tab) and -1 or 1
    i = (i - 1 + step) % #env.cats + 1
    ctx:set_property(PROP, tostring(i))
    ctx:refresh_non_confirmed_composition()
    return kAccepted
  end
  if code == XK_Escape or code == XK_BackSpace then
    ctx:clear()
    return kAccepted
  end
  if code == XK_space or code == XK_Return or code == XK_KP_Enter then
    local cand = ctx:get_selected_candidate()
    if cand then commit(env, cand.text) else ctx:clear() end
    return kAccepted
  end
  if code > 0x20 and code < 0x7f then
    local cat = env.cats[current_index(ctx, #env.cats)]
    local sym = cat.map[string.char(code)]
    if sym then commit(env, sym) end
    return kAccepted  -- unmapped printable keys are swallowed
  end
  if key.modifier == 0 and (code == 0xff51 or code == 0xff53) then  -- Left / Right
    require("asus_keys").turn_page(ctx, env, code == 0xff53 and 1 or -1)
    return kAccepted
  end
  return kNoop  -- other navigation keys go to the selector
end

-- Translator ---------------------------------------------------------------
local translator = {}

function translator.init(env)
  env.cats = load_categories(env.engine.schema.config)
end

function translator.func(input, seg, env)
  if not seg:has_tag("asus_symbol") or #env.cats == 0 then return end
  local ctx = env.engine.context
  local i = current_index(ctx, #env.cats)
  local names = {}
  for j, c in ipairs(env.cats) do
    names[#names + 1] = (j == i) and ("【" .. c.name .. "】") or c.name
  end
  -- The translator's seg is read-only in the bundled librime-lua; the prompt
  -- is set on the composition's segment instead (cosmetic, so never fatal).
  pcall(function()
    ctx.composition:back().prompt = table.concat(names, " ") .. "  Tab 切換"
  end)
  -- The prompt is hidden when Weasel previews inline, so the first candidate
  -- also carries the category bar in its comment.
  local tabs = table.concat(names, " ")
  for n, item in ipairs(env.cats[i].items) do
    local comment = key_label(item.key)
    if n == 1 then comment = comment .. "  " .. tabs .. " ⇥" end
    local cand = Candidate("asus_symbol", seg.start, seg._end, item.sym, comment)
    cand.quality = 1000
    yield(cand)
  end
end

return { processor = processor, translator = translator }
