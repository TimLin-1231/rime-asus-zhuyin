-- asus_email.lua
-- Email addresses: type the first few characters of an address you have
-- used before and the whole address is offered.
--
-- Addresses are remembered by asus_learn.lua (every address committed, plus
-- the ones listed in rime/emails.txt, one per line). While composing, when
-- the keys typed so far (at least MIN_PREFIX of them) are the start of a
-- remembered address, the bubble shows it as "⇥address" (asus_compact.lua)
-- and Tab types it, whatever the keys would otherwise convert to (s108 is
-- Zhuyin too). An address typed in full (name@host.com) is one English word
-- (asus_lang.lua), whose guesses are the remembered addresses starting so.
--
--   processor  lua_processor@*asus_email*processor  (before asus_predict)

local learn = require("asus_learn")

local kAccepted, kNoop = 1, 2
local XK_Tab = 0xff09
local EXPANDED = "_asus_expanded"
local MIN_PREFIX = 3

-- Remembered addresses starting with the typed keys (best first).
local function match(input, limit)
  if #input < MIN_PREFIX or not input:match("^[%w._%%+%-@]+$") then return {} end
  return learn.emails_for(input, limit or 1)
end

local processor = {}

function processor.init(env)
  local n = tonumber(env.engine.schema.config:get_string("asus_email/min_prefix") or "")
  if n and n >= 1 then MIN_PREFIX = n end
end

function processor.func(key, env)
  if key:release() or key.keycode ~= XK_Tab or key.modifier ~= 0 then return kNoop end
  local ctx = env.engine.context
  if not ctx:is_composing() or ctx:get_option("ascii_mode") or ctx:get_option(EXPANDED) then
    return kNoop
  end
  local mail = match(ctx.input)[1]
  if not mail then return kNoop end
  ctx:clear()
  env.engine:commit_text(mail)
  learn.learn_email(mail, 1)
  return kAccepted
end

return { processor = processor, match = match }
