-- asus_predict.lua
-- Next-word prediction and autocomplete, iPhone / ASUS Smart Input style.
--
--   * After a commit, the likely next words are shown in the bubble (as gray
--     comments with empty candidate text, so nothing appears inline in the
--     document). Tab or Right accepts the highlighted one, Up/Down move the
--     highlight. Any other key dismisses the prediction and is processed
--     normally (Enter / BackSpace / punctuation go to the app).
--   * While composing (>= 2 characters converted), asus_compact.lua shows
--     "⇥ continuation"; Tab commits conversion + continuation (autocomplete).
--
-- Data: opencc/asus_next.json (prefix -> continuations, best first), built by
-- tools/build_lang_data.py from McBopomofo phrases + personal words. Only the
-- last few committed characters are kept, in memory, never written to disk.
--
-- processor  lua_processor@*asus_predict*processor  (first processor)
-- The option "prediction" (switch) turns it off.

local kRejected, kAccepted, kNoop = 0, 1, 2
local XK_Tab, XK_Right, XK_Up, XK_Down = 0xff09, 0xff53, 0xff52, 0xff54
local XK_Shift_L, XK_Shift_R, XK_Control_L, XK_Control_R = 0xffe1, 0xffe2, 0xffe3, 0xffe4

local TAG = "prediction"
local MAX_PREFIX = 4   -- characters of committed text used as the key
local SHOW = 3         -- predictions offered

local next_cc

-- Loading the OpenCC text dict takes ~140 ms; done at session start (init)
-- instead of on the first keystroke that needs it, which was a visible hitch.
local function load_next()
  if next_cc == nil then
    local ok, cc = pcall(Opencc, "asus_next.json")
    next_cc = ok and cc or false
  end
end

local function lookup(key)
  load_next()
  if not next_cc then return nil end
  local forms = next_cc:convert_word(key)
  if forms and #forms > 0 then return forms end
  return nil
end

-- Last n UTF-8 characters of s.
local function tail_chars(s, n)
  local starts = {}
  for p in utf8.codes(s) do starts[#starts + 1] = p end
  if #starts <= n then return s end
  return s:sub(starts[#starts - n + 1])
end

-- Continuations for text: the longest suffix (<= MAX_PREFIX chars) that is a
-- known prefix wins; shorter suffixes fill up the list.
local function predict(text, limit)
  local out, seen = {}, {}
  local cps = {}
  for _, cp in utf8.codes(text) do cps[#cps + 1] = cp end
  for len = math.min(MAX_PREFIX, #cps), 1, -1 do
    local key = utf8.char(table.unpack(cps, #cps - len + 1, #cps))
    for _, w in ipairs(lookup(key) or {}) do
      if not seen[w] then
        seen[w] = true
        out[#out + 1] = w
        if #out >= limit then return out end
      end
    end
  end
  return out
end

local function is_cjk_text(s)
  local any = false
  for _, cp in utf8.codes(s) do
    if cp < 0x3400 or cp > 0x9fff then return false end
    any = true
  end
  return any
end

local function prediction_segment(ctx)
  if not ctx.composition then return nil end
  local comp = ctx.composition
  if comp:empty() then return nil end
  local seg = comp:back()
  if seg:has_tag(TAG) then return seg end
  return nil
end

local function show(ctx, words)
  local seg = Segment(0, 0)
  seg.tags = Set({ TAG })
  local menu = Menu()
  menu:add_translation(Translation(function()
    for _, w in ipairs(words) do
      -- Empty text: the inline preview stays empty; the word is the comment.
      yield(Candidate(TAG, 0, 0, "", w))
    end
  end))
  seg.menu = menu
  ctx.composition:push_back(seg)
end

local processor = {}

function processor.init(env)
  load_next()
  local ctx = env.engine.context
  env.history = ""
  env.pending = false
  env.commit_conn = ctx.commit_notifier:connect(function(c)
    local text = c:get_commit_text() or ""
    if is_cjk_text(text) then
      env.history = tail_chars(env.history .. text, MAX_PREFIX)
      env.pending = true
    else
      env.history, env.pending = "", false
    end
  end)
  env.update_conn = ctx.update_notifier:connect(function(c)
    if not env.pending or c.input ~= "" or not c.composition:empty() then return end
    env.pending = false
    if c:get_option("ascii_mode") or not c:get_option("prediction") then return end
    local words = predict(env.history, SHOW)
    if #words > 0 then show(c, words) end
  end)
end

-- Commit `text` through the engine, then predict after it (used for accepted
-- predictions and autocomplete continuations, which bypass the context).
local function commit_and_predict(env, text)
  local ctx = env.engine.context
  env.engine:commit_text(text)
  env.history = tail_chars(env.history .. text, MAX_PREFIX)
  if ctx:get_option("prediction") then
    local words = predict(env.history, SHOW)
    if #words > 0 then show(ctx, words) end
  end
end

function processor.fini(env)
  if env.commit_conn then env.commit_conn:disconnect() end
  if env.update_conn then env.update_conn:disconnect() end
end

-- Autocomplete: best continuation of a conversion of >= 2 characters.
local function complete(text)
  if not is_cjk_text(text) or utf8.len(text) < 2 then return nil end
  return predict(text, 1)[1]
end

function processor.func(key, env)
  local ctx = env.engine.context
  if key:release() then return kNoop end
  local code = key.keycode
  local seg = prediction_segment(ctx)

  if not seg then
    -- Autocomplete while composing: Tab = conversion + continuation.
    if code == XK_Tab and key.modifier == 0 and ctx:is_composing()
        and not ctx:get_option("ascii_mode") and ctx.input:sub(1, 1) ~= "`" then
      local last = ctx.composition:back()
      local text = ctx:get_commit_text()
      local cont = not (last and last:has_tag("asus_en")) and complete(text)
      if cont then
        env.pending = false
        ctx:commit()          -- learns the conversion; history gets `text`
        env.pending = false   -- (commit_notifier set it again)
        ctx:clear()
        commit_and_predict(env, cont)
        return kAccepted
      end
    end
    return kNoop
  end

  if code == XK_Shift_L or code == XK_Shift_R or code == XK_Control_L or code == XK_Control_R then
    return kNoop  -- modifiers alone keep the prediction (double-Shift still works)
  end
  local count = seg.menu:prepare(SHOW)
  if key.modifier == 0 and (code == XK_Tab or code == XK_Right) then
    local cand = seg:get_candidate_at(seg.selected_index)
    local word = cand and cand.comment or ""
    ctx:clear()
    if word ~= "" then commit_and_predict(env, word) end  -- chains predictions
    return kAccepted
  end
  if key.modifier == 0 and (code == XK_Down or code == XK_Up) and count > 1 then
    local step = (code == XK_Down) and 1 or -1
    seg.selected_index = (seg.selected_index + step) % count
    return kAccepted
  end
  -- Anything else: drop the prediction, then let the key be processed as if
  -- nothing were showing.
  ctx:clear()
  env.history = ""
  return kNoop
end

return { processor = processor, complete = complete, predict = predict }
