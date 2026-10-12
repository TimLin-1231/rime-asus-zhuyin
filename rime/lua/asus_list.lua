-- asus_list.lua
-- The text after the highlighted candidate of an open list.
--
-- librime previews the selected candidate followed by the rest of the input
-- as typed: with the highlight on a candidate shorter than its segment (再往
-- for ㄗㄞˋ ㄨㄤˇ ㄌㄨˋ ㄕㄤˋ ㄇㄞˇ) the inline text read 我想再往xj4g;4a93. No
-- setting changes that; only a candidate that reaches the end of the input
-- has nothing after it. So the highlighted candidate is replaced, for
-- display only, by one that spans the rest of the input and whose text ends
-- with what the rest reads now (再往路上買):
--
--   highlight(ctx, i)  moves the highlight to row i; when that candidate is
--                      shorter than the input, the property asus_list_hl
--                      names it and the menu is built again
--   wrap(...)          asus_compact.lua (the last filter, where the rows have
--                      their final numbers) swaps that one candidate
--   before_key(...)    asus_shift.lua, the first processor: any key but the
--                      ones that move the highlight puts the real candidate
--                      back first, so picking, learning and forgetting never
--                      see the stand-in
--
-- Helper module: not a component of the schema.

local M = {}

local HL = "asus_list_hl"  -- "<row>\t<segment start>\t<input>", or ""
local XK_Up, XK_Down, XK_Page_Up, XK_Page_Down = 0xff52, 0xff54, 0xff55, 0xff56
local MOVES = { [XK_Up] = true, [XK_Down] = true, [XK_Page_Up] = true, [XK_Page_Down] = true }
local MODIFIERS = { [0xffe1] = true, [0xffe2] = true, [0xffe3] = true, [0xffe4] = true,
                    [0xffe9] = true, [0xffea] = true, [0xffe5] = true }

local function chars(s)
  local out = {}
  for _, cp in utf8.codes(s) do out[#out + 1] = utf8.char(cp) end
  return out
end

-- Row and segment start the property names, if it is about this input.
local function current(ctx)
  local row, start, input = (ctx:get_property(HL) or ""):match("^(%d+)\t(%d+)\t(.*)$")
  if not row or input ~= ctx.input then return nil end
  return tonumber(row), tonumber(start)
end
M.current = current

function M.clear(ctx)
  if (ctx:get_property(HL) or "") ~= "" then ctx:set_property(HL, "") end
end

-- Move the highlight of the open list to row `index` (0-based); false when
-- there is no such row.
function M.highlight(ctx, index)
  local seg = ctx.composition:back()
  if not seg or not seg.menu or index < 0 or seg.menu:prepare(index + 1) <= index then return false end
  local had = current(ctx) ~= nil
  local cand = seg:get_candidate_at(index)
  local short = cand ~= nil and cand._end < #ctx.input
  if not had and not short then
    seg.selected_index = index
    return true
  end
  ctx:set_property(HL, short and (index .. "\t" .. seg.start .. "\t" .. ctx.input) or "")
  ctx:refresh_non_confirmed_composition()
  seg = ctx.composition:back()
  if seg and seg.menu and seg.menu:prepare(index + 1) > index then seg.selected_index = index end
  return true
end

-- The real candidates again, the highlight where it was.
function M.restore(ctx)
  local raw = ctx:get_property(HL) or ""
  if raw == "" then return end
  local index = current(ctx)
  ctx:set_property(HL, "")
  if not index or not ctx:is_composing() then return end
  ctx:refresh_non_confirmed_composition()
  local seg = ctx.composition:back()
  if seg and seg.menu and seg.menu:prepare(index + 1) > index then seg.selected_index = index end
end

-- First thing for every key pressed (asus_shift.lua).
function M.before_key(key, ctx)
  if (ctx:get_property(HL) or "") == "" then return end
  local code = key.keycode
  if MODIFIERS[code] then return end
  if MOVES[code] and key.modifier == 0 and current(ctx) then return end  -- highlight() follows
  M.restore(ctx)
end

-- What the input after cand reads now: the units of the editing cursor
-- (asus_edit.lua), or else the characters of the list's first candidate, one
-- per syllable. nil when that cannot be told.
local function tail(ctx, first, cand)
  local text = require("asus_edit").text_after(ctx, cand._end)
  if text then return text end
  if not first or first._end ~= #ctx.input or first.start ~= cand.start then return nil end
  local syl, starts = require("asus_learn").syllables(ctx.input:sub(first.start + 1, first._end))
  local cs = chars(first.text)
  if not syl or #syl ~= #cs then return nil end
  for i = 1, #syl do
    if first.start + starts[i] == cand._end then return table.concat(cs, "", i) end
  end
  return nil
end

-- For asus_compact.lua: cand is row `row` of the list whose first candidate
-- is `first`. The stand-in for it when it is the row named by highlight(),
-- else cand itself.
function M.wrap(ctx, row, first, cand)
  local index, start = current(ctx)
  if index ~= row or start ~= cand.start or cand._end >= #ctx.input then return cand end
  local rest = tail(ctx, first, cand)
  if not rest or rest == "" then return cand end
  -- (The comment says which part of the row is the candidate itself.)
  local comment = cand.text .. (cand.comment ~= "" and (" " .. cand.comment) or "")
  local c = Candidate(cand.type, cand.start, #ctx.input, cand.text .. rest, comment)
  c.quality = cand.quality
  return c
end

return M
