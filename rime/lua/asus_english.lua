-- asus_english.lua
-- English mode (ascii_mode) with suggestions, like a phone keyboard.
--
-- Letters, digits and ' - . @ _ + # are composed instead of going straight
-- to the application; the bubble shows the typed word and up to three
-- guesses (completions, spelling fixes, contractions, remembered email
-- addresses; asus_lang.lua translates the segment tagged asus_ascii). The
-- text is committed as typed unless a guess is taken or Space corrects it:
--   Space        commit + space (expanded list: the highlighted guess). A
--                misspelt word is corrected (asus_lang.autocorrect); the
--                corrected word stays underlined until the next key, and
--                BackSpace or Esc right away brings back what was typed
--                (and that word is left alone from then on). , ; : ! ? ) "
--                after a misspelt word correct it the same way.
--   Tab          the first guess (⇥); none: commit, and the Tab goes on
--   Down         list the guesses (1-9, Space, Esc as in Chinese mode)
--   BackSpace    delete the last typed character
--   Enter, punctuation, arrows, Ctrl/Alt shortcuts and any other key:
--                commit what was typed, then the key goes to the program
-- After Space the words that usually come next are offered (gray, as after
-- Chinese text; data: opencc/asus_en_follow): Tab types the first one and a
-- space, any other key dismisses them.
-- Programs listed with asus_en_direct: true under app_options in
-- weasel.custom.yaml (terminals by default) keep the plain direct output,
-- as does asus_english/compose: false in the schema patch. asus_en_noauto:
-- true (code editors by default) keeps the guesses but neither corrects nor
-- offers next words; asus_english/autocorrect: false and
-- asus_english/predict: false turn the two off everywhere.
-- Only email addresses are learned from English mode (asus_learn.lua).
--
--   processor  lua_processor@*asus_english*processor  (after asus_learn)
--   segmentor  lua_segmentor@*asus_english*segmentor  (before ascii_segmentor)

local kAccepted, kNoop = 1, 2
local XK_space, XK_Tab, XK_Return, XK_BackSpace = 0x20, 0xff09, 0xff0d, 0xff08
local XK_Escape, XK_Down = 0xff1b, 0xff54
local EXPANDED = "_asus_expanded"  -- asus_keys.lua
local DIRECT = "asus_en_direct"
local NOAUTO = "asus_en_noauto"
local NEXT_TAG = "asus_en_next"  -- the bubble of next words
local NEXT_SHOW = 3
local lang = require("asus_lang")
local AUTO = lang.AUTO_PROP
-- Keys that end a word and correct it, besides Space.
local ENDERS = { [0x2c] = true, [0x3b] = true, [0x3a] = true, [0x21] = true, [0x3f] = true,
                 [0x29] = true, [0x22] = true }

-- Keys that browse the expanded list (asus_keys.lua / librime).
local LIST_KEYS = {
  [0xff50] = true, [0xff51] = true, [0xff52] = true, [0xff53] = true, [0xff54] = true,
  [0xff55] = true, [0xff56] = true, [0xff57] = true,
}
local MODIFIERS = {
  [0xffe1] = true, [0xffe2] = true, [0xffe3] = true, [0xffe4] = true, [0xffe5] = true,
  [0xffe7] = true, [0xffe8] = true, [0xffe9] = true, [0xffea] = true, [0xffeb] = true,
  [0xffec] = true,
}

local function active(ctx, env)
  return env.compose and ctx:get_option("ascii_mode") and not ctx:get_option(DIRECT)
end

local function collapse(ctx)
  ctx:set_option(EXPANDED, false)
  ctx:set_option("_linear", false)
  ctx:set_option("_auto_commit", false)
end

local function init(env)
  local config = env.engine.schema.config
  local function flag(name)
    local v = config:get_bool("asus_english/" .. name)
    return (v == nil) or v
  end
  env.compose, env.auto, env.predict = flag("compose"), flag("autocorrect"), flag("predict")
end

-- The correction shown but not committed yet: what was typed, or nil.
local function corrected(ctx)
  local v = ctx:get_property(AUTO)
  if v == "" then return nil end
  local typed = v:match("^(.-)\t")
  if typed and typed == ctx.input and ctx:is_composing() then return typed end
  ctx:set_property(AUTO, "")  -- left over (focus moved, composition cleared)
  return nil
end

-- Space (tail " ") or an ENDERS key after a misspelt word: show the word
-- meant, with the tail, in its place. False when there is nothing to correct.
local function correct(ctx, env, tail)
  if not env.auto or ctx:get_option(NOAUTO) then return false end
  local word, stop = ctx.input:match("^(%a+)(%.?)$")
  local fixed = word and lang.autocorrect(word)
  if not fixed then return false end
  ctx:set_property(AUTO, ctx.input .. "\t" .. fixed .. stop .. tail)
  ctx:refresh_non_confirmed_composition()
  return true
end

-- The words that usually follow word, offered after it was committed with a
-- space.
local function offer_next(ctx, env, word)
  if not env.predict or ctx:get_option(NOAUTO) or not ctx:get_option("prediction")
      or not word:match("^%a[%a']*$") then
    return
  end
  local words = lang.followers_of(word, NEXT_SHOW)
  if #words > 0 then require("asus_predict").show(ctx, words, NEXT_TAG) end
end

local function next_words(ctx)
  local seg = not ctx.composition:empty() and ctx.composition:back()
  return seg and seg:has_tag(NEXT_TAG) and seg or nil
end

local processor = { init = init }

function processor.func(key, env)
  local ctx = env.engine.context
  if key:release() or not active(ctx, env) then return kNoop end
  local code = key.keycode
  if MODIFIERS[code] then return kNoop end
  local plain = not key:ctrl() and not key:alt() and not key:super()

  -- A corrected word is waiting: BackSpace / Esc bring back what was typed,
  -- any other key commits the correction and is then handled as usual.
  local typed = corrected(ctx)
  if typed then
    ctx:set_property(AUTO, "")
    if key.modifier == 0 and (code == XK_BackSpace or code == XK_Escape) then
      lang.keep_word(typed:match("^%a+"))
      ctx:refresh_non_confirmed_composition()
      return kAccepted
    end
    ctx:commit()
  end

  -- Next words on offer: Tab types the first and a space; anything else
  -- dismisses them.
  local offer = next_words(ctx)
  if offer then
    local cand = offer:get_candidate_at(0)
    local word = cand and cand.comment or ""
    ctx:clear()
    if key.modifier == 0 and code == XK_Tab and word ~= "" then
      env.engine:commit_text(word .. " ")
      ctx:set_property("asus_en_prev", word:lower())
      offer_next(ctx, env, word)
      return kAccepted
    end
    -- A character that is not composed is committed here rather than passed
    -- on: Weasel has a (empty) composition open for the bubble, and a key
    -- the program inserts itself while it closes can be lost.
    if plain and code >= 0x20 and code < 0x7f and not string.char(code):match("[%w'@._+#%-]") then
      env.engine:commit_text(string.char(code))
      return kAccepted
    end
  end
  local expanded = ctx:get_option(EXPANDED)

  if expanded then
    -- The list owns digits, Space, arrows and Esc (asus_keys.lua).
    if key.modifier == 0 and ((code >= 0x31 and code <= 0x39) or code == XK_space
                              or code == XK_Escape or LIST_KEYS[code]) then
      if code == XK_space then
        local cand = ctx:get_selected_candidate()
        ctx:commit()  -- the highlighted guess
        env.engine:commit_text(" ")
        offer_next(ctx, env, cand and cand.text or "")
        return kAccepted
      end
      return kNoop
    end
    if code == XK_Return and plain then
      ctx:commit()
      return kAccepted  -- Enter picked from the list; it is not passed on
    end
    collapse(ctx)
  end

  if plain and code > 0x20 and code < 0x7f and string.char(code):match("[%w'@._+#%-]") then
    ctx:push_input(string.char(code))
    return kAccepted
  end
  if not ctx:is_composing() then return kNoop end

  if plain and code == XK_space and key.modifier == 0 then
    if correct(ctx, env, " ") then return kAccepted end
    local word = ctx.input
    ctx:commit()
    env.engine:commit_text(" ")
    offer_next(ctx, env, word)
    return kAccepted
  end
  if plain and ENDERS[code] and correct(ctx, env, string.char(code)) then
    return kAccepted
  end
  if plain and code == XK_BackSpace then
    ctx:pop_input(1)
    return kAccepted
  end
  if plain and code == XK_Down and key.modifier == 0 and ctx:has_menu() then
    return kNoop  -- asus_keys.lua expands the list
  end
  if plain and code == XK_Tab and key.modifier == 0 then
    local seg = ctx.composition:back()
    local word = seg and lang.first_guess(ctx.input, ctx)
    if word then
      for i = 0, 8 do
        local c = seg:get_candidate_at(i)
        if not c then break end
        if c.text == word then
          seg.selected_index = i
          ctx:commit()
          return kAccepted
        end
      end
    end
    ctx:commit()
    return kNoop  -- no guess: the Tab goes on to the program
  end
  -- Enter, punctuation, arrows, shortcuts...: commit, then the key goes on.
  ctx:commit()
  return kNoop
end

-- One English segment over the whole input, without the Chinese reading.
local segmentor = { init = init }

function segmentor.func(segmentation, env)
  local ctx = env.engine.context
  if not active(ctx, env) then return true end
  local input = segmentation.input
  if type(input) == "function" then input = segmentation:input() end
  local start = segmentation:get_current_start_position()
  if start >= #input then return true end
  local seg = Segment(start, #input)
  seg.tags = Set({ "asus_en", "asus_ascii" })
  segmentation:add_segment(seg)
  return false
end

return { processor = processor, segmentor = segmentor }
