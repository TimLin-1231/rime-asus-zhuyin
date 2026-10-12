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
-- After a pause the bubble offers a whole phrase instead, written by the
-- local model of the writing helper (assist/bridge.py) from the text typed
-- so far; Tab types it. This only happens while that program is running:
--   * the English text committed here since the caret was last moved is
--     kept in memory (env.typed, a few hundred characters; any key that may
--     move the caret - arrows, Enter, shortcuts - empties it);
--   * after a word and Space it is written to a named pipe, numbered;
--   * the helper answers by pressing F24: this processor takes that key,
--     reads the phrase from a second pipe and shows it, if nothing was
--     typed in between. (Lua has no timer; the key is the only way in.)
-- asus_english/continue: false turns it off.
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
local XK_F24 = 0xffd5         -- pressed by assist/bridge.py: an answer is ready
local TYPED_MAX = 600         -- characters of typed text kept for the model
local TYPED_STALE = 120       -- seconds without typing after which it is dropped
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

local settings = { auto = true }  -- asus_english/autocorrect, for correct_word()

local function init(env)
  local config = env.engine.schema.config
  local function flag(name)
    local v = config:get_bool("asus_english/" .. name)
    return (v == nil) or v
  end
  env.compose, env.auto, env.predict = flag("compose"), flag("autocorrect"), flag("predict")
  env.live = flag("continue")
  settings.auto = env.auto
  env.pipe = "\\\\.\\pipe\\" .. (config:get_string("asus_english/pipe") or "asus_zhuyin_assist")
  env.typed, env.typed_at, env.asked, env.waiting = "", 0, 0, nil
end

-- The text typed so far, for the model ---------------------------------------
local function forget(env)
  env.typed = ""
end

local function note(env, text)
  local now = os.time()
  if now - env.typed_at > TYPED_STALE then env.typed = "" end
  env.typed_at = now
  env.typed = (env.typed .. text):sub(-TYPED_MAX)
end

local function commit_text(env, text)
  env.engine:commit_text(text)
  note(env, text)
end

local function send(env, message)
  local f = io.open(env.pipe .. "_in", "wb")  -- fails at once without the helper
  if not f then return false end
  f:write(message)
  f:close()
  return true
end

-- Ask for a continuation of what was typed (at least two words of it).
local function ask(env, ctx)
  if not env.live or ctx:get_option(NOAUTO) or not env.typed:match("%a%s+%S*%a") then return end
  env.asked = env.asked % 1000000 + 1
  env.waiting = send(env, env.asked .. "\t" .. env.typed) and env.asked or nil
end

-- The user typed on: the answer to the last request is not wanted any more.
local function cancel(env)
  if env.waiting then
    env.waiting = nil
    send(env, "0")
  end
end

-- F24: the phrase for the request still waiting, or nil.
local function answer(env)
  local waiting = env.waiting
  env.waiting = nil
  if not waiting then return nil end
  local f = io.open(env.pipe .. "_out", "rb")
  if not f then return nil end
  local data = f:read("l") or ""  -- (one line: reading on into the closed pipe is an error)
  f:close()
  local number, phrase = data:match("^(%d+)\t(.+)$")
  return tonumber(number) == waiting and phrase or nil
end

-- The correction shown but not committed yet: the input as it was typed, or nil.
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

-- Chinese mode (asus_keys.lua): Space after the English word that ends the
-- input. The same correction, shown in place of that word with the space.
local function correct_word(ctx, word, tail)
  if not settings.auto or ctx:get_option(NOAUTO) or not word:match("^%a+$") then return false end
  local fixed = lang.autocorrect(word)
  if not fixed then return false end
  ctx:set_property(AUTO, ctx.input .. "\t" .. fixed .. tail)
  ctx:refresh_non_confirmed_composition()
  return true
end

-- A corrected word is waiting: BackSpace / Esc bring back what was typed
-- (true: the key is done with); any other key commits the correction and is
-- then handled as usual (false).
local function settle(ctx, key)
  local typed = corrected(ctx)
  if not typed then return false end
  ctx:set_property(AUTO, "")
  local code = key.keycode
  if key.modifier == 0 and (code == XK_BackSpace or code == XK_Escape) then
    lang.keep_word(typed:match("(%a+)%.?$"))
    ctx:refresh_non_confirmed_composition()
    return true
  end
  ctx:commit()
  return false
end

-- Chinese mode: a word picked from the list instead of the correction
-- offered before it (更正) is what the user meant - it is left alone from
-- then on. (At the editing cursor the list starts with the text as it is,
-- asus_edit.lua; the correction comes after that.)
local function note_pick(ctx, key, env)
  if key.modifier ~= 0 or not ctx:get_option(EXPANDED) then return end
  local seg = not ctx.composition:empty() and ctx.composition:back()
  if not seg or not seg.menu then return end
  local code, index = key.keycode, nil
  if code >= 0x31 and code <= 0x39 then
    local page_size = env.engine.schema.page_size
    index = seg.selected_index - seg.selected_index % page_size + (code - 0x31)
  elseif code == XK_space or code == XK_Return then
    index = seg.selected_index
  end
  local cand = index and index > 0 and seg:get_candidate_at(index)
  -- (No look at cand.type: at the editing cursor the candidates are wrapped.)
  local word = cand and cand.text:match("^(%a+)$")
  if not word or not lang.autocorrect(word, true) then return end
  for i = 0, math.min(index - 1, 2) do
    local before = seg:get_candidate_at(i)
    if before and before.comment == lang.FIX_MARK then
      -- Remembered when the text is committed: doing it now would rebuild
      -- the list (without the correction) under the pick being made.
      env.keep_later = word
      return
    end
  end
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
  ask(env, ctx)
end

local function next_words(ctx)
  local seg = not ctx.composition:empty() and ctx.composition:back()
  return seg and seg:has_tag(NEXT_TAG) and seg or nil
end

local processor = {}

function processor.init(env)
  init(env)
  -- Everything committed through the context in English mode is typed text
  -- (what engine:commit_text sends is noted where it is sent).
  env.commit_conn = env.engine.context.commit_notifier:connect(function(c)
    if active(c, env) then note(env, c:get_commit_text() or "") end
    if env.keep_later then  -- note_pick()
      lang.keep_word(env.keep_later)
      env.keep_later = nil
    end
  end)
end

function processor.fini(env)
  if env.commit_conn then env.commit_conn:disconnect() end
end

local function handle(key, env)
  local ctx = env.engine.context
  local code = key.keycode
  if code == XK_F24 then
    -- From assist/bridge.py, never for the program: taken in every mode.
    local phrase = not key:release() and active(ctx, env) and ctx.input == "" and answer(env)
    if phrase then
      local words, offer = { phrase }, next_words(ctx)
      for i = 0, NEXT_SHOW - 2 do  -- the single words offered so far stay behind it
        local cand = offer and offer:get_candidate_at(i)
        if cand and cand.comment ~= phrase:match("^%S+") then words[#words + 1] = cand.comment end
      end
      ctx:clear()
      require("asus_predict").show(ctx, words, NEXT_TAG)
    end
    return kAccepted
  end
  if key:release() or MODIFIERS[code] then return kNoop end
  if not active(ctx, env) then
    forget(env)  -- Chinese mode, or a program typed into directly
    if settle(ctx, key) then return kAccepted end
    note_pick(ctx, key, env)
    return kNoop
  end
  cancel(env)
  local plain = not key:ctrl() and not key:alt() and not key:super()
  if settle(ctx, key) then return kAccepted end

  -- Next words on offer: Tab types the first and a space; anything else
  -- dismisses them.
  local offer = next_words(ctx)
  if offer then
    local cand = offer:get_candidate_at(0)
    local word = cand and cand.comment or ""
    ctx:clear()
    if key.modifier == 0 and code == XK_Tab and word ~= "" then
      commit_text(env, word .. " ")
      local last = word:match("(%a[%a']*)$") or ""  -- (of a phrase; none after a full stop)
      ctx:set_property("asus_en_prev", last:lower())
      offer_next(ctx, env, last)
      return kAccepted
    end
    -- A character that is not composed is committed here rather than passed
    -- on: Weasel has a (empty) composition open for the bubble, and a key
    -- the program inserts itself while it closes can be lost.
    if plain and code >= 0x20 and code < 0x7f and not string.char(code):match("[%w'@._+#%-]") then
      commit_text(env, string.char(code))
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
        commit_text(env, " ")
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
    commit_text(env, " ")
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

-- Keys that go on to the program change the text there too: a character is
-- added to what was typed, BackSpace takes one off, and any key that may
-- move the caret or do something else (arrows, Enter, Tab, shortcuts)
-- means the text before the caret is no longer known.
function processor.func(key, env)
  local result = handle(key, env)
  local ctx = env.engine.context
  local code = key.keycode
  if result == kNoop and not key:release() and not MODIFIERS[code] and active(ctx, env)
      and not ctx:is_composing() then
    if key:ctrl() or key:alt() or key:super() then
      forget(env)
    elseif code >= 0x20 and code < 0x7f then
      note(env, string.char(code))
    elseif code == XK_BackSpace then
      env.typed = env.typed:sub(1, -2)
    else
      forget(env)
    end
  end
  return result
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

return { processor = processor, segmentor = segmentor, correct_word = correct_word }
