-- asus_english.lua
-- English mode (ascii_mode) with suggestions, like a phone keyboard.
--
-- Letters, digits and ' - . @ _ + # are composed instead of going straight
-- to the application; the bubble shows the typed word and up to three
-- guesses (completions, spelling fixes, contractions, remembered email
-- addresses; asus_lang.lua translates the segment tagged asus_ascii). The
-- text is committed exactly as typed unless a guess is taken:
--   Space        commit + space (expanded list: the highlighted guess)
--   Tab          the first guess (⇥); none: commit, and the Tab goes on
--   Down         list the guesses (1-9, Space, Esc as in Chinese mode)
--   BackSpace    delete the last typed character
--   Enter, punctuation, arrows, Ctrl/Alt shortcuts and any other key:
--                commit what was typed, then the key goes to the program
-- Programs listed with asus_en_direct: true under app_options in
-- weasel.custom.yaml (terminals by default) keep the plain direct output,
-- as does asus_english/compose: false in the schema patch.
-- Only email addresses are learned from English mode (asus_learn.lua).
--
--   processor  lua_processor@*asus_english*processor  (after asus_learn)
--   segmentor  lua_segmentor@*asus_english*segmentor  (before ascii_segmentor)

local kAccepted, kNoop = 1, 2
local XK_space, XK_Tab, XK_Return, XK_BackSpace = 0x20, 0xff09, 0xff0d, 0xff08
local XK_Escape, XK_Down = 0xff1b, 0xff54
local EXPANDED = "_asus_expanded"  -- asus_keys.lua
local DIRECT = "asus_en_direct"

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
  local v = env.engine.schema.config:get_bool("asus_english/compose")
  env.compose = (v == nil) or v
end

local processor = { init = init }

function processor.func(key, env)
  local ctx = env.engine.context
  if key:release() or not active(ctx, env) then return kNoop end
  local code = key.keycode
  if MODIFIERS[code] then return kNoop end
  local plain = not key:ctrl() and not key:alt() and not key:super()
  local expanded = ctx:get_option(EXPANDED)

  if expanded then
    -- The list owns digits, Space, arrows and Esc (asus_keys.lua).
    if key.modifier == 0 and ((code >= 0x31 and code <= 0x39) or code == XK_space
                              or code == XK_Escape or LIST_KEYS[code]) then
      if code == XK_space then
        ctx:commit()  -- the highlighted guess
        env.engine:commit_text(" ")
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
    ctx:commit()
    env.engine:commit_text(" ")
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
    local word = seg and require("asus_lang").first_guess(ctx.input, ctx)
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
