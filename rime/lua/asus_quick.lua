-- asus_quick.lua
-- Dates and number forms in the candidate list (after BlendKey's QuickInput,
-- github.com/0rigind1865-bit/BlendKey, MIT).
--
--   * Dates: in an open list (Down), the word 今天 / 明天 / 昨天 / 後天 /
--     前天 is followed by that day as 2026-10-25, 2026/10/25, 2026年10月25日,
--     民國115年10月25日 and 10月25日（六）; 現在 by 14:05, 下午2:05 and
--     2026-10-25 14:05. Picked like any other row (1-9, Space, Enter).
--   * Numbers: Tab on a number at the end of the input (keys all digits,
--     typed on the top row) opens the list with it written as 1234, 1,234,
--     壹仟貳佰參拾肆, 新臺幣壹仟貳佰參拾肆元整, 一千二百三十四, 一二三四
--     and １２３４ (a decimal: 3.14, 參點壹肆, 三點一四, ...).
--
-- Nothing is added while the list is closed, so typing is not slowed down.
-- Candidates have the type "quick"; asus_learn.lua does not learn picks of
-- them. The schema option asus_quick/now ("2026-10-20 14:05") fixes the
-- clock for tests.
--
--   processor  lua_processor@*asus_quick*processor  (before asus_predict: Tab)
--   filter     lua_filter@*asus_quick*filter        (after asus_lang*digits)

local kAccepted, kNoop = 1, 2
local XK_Tab = 0xff09
local EXPANDED = "_asus_expanded"
local NUM = "asus_quick_num"  -- "<input>\t<start>" of the number Tab was pressed on

-- Numbers -------------------------------------------------------------------
local UPPER = { digits = { "零", "壹", "貳", "參", "肆", "伍", "陸", "柒", "捌", "玖" },
                units = { "", "拾", "佰", "仟" } }
local LOWER = { digits = { "零", "一", "二", "三", "四", "五", "六", "七", "八", "九" },
                units = { "", "十", "百", "千" } }
local GROUPS = { "", "萬", "億", "兆" }
local ONE_BY_ONE = { "〇", "一", "二", "三", "四", "五", "六", "七", "八", "九" }
local FULL = { ["0"] = "０", ["1"] = "１", ["2"] = "２", ["3"] = "３", ["4"] = "４",
  ["5"] = "５", ["6"] = "６", ["7"] = "７", ["8"] = "８", ["9"] = "９", ["."] = "．" }
local MAX_DIGITS = 16  -- up to 兆

local function digit(set, ch) return set.digits[tonumber(ch) + 1] end

-- The integer `int` (digits, no leading zeros) in Chinese numerals: groups
-- of four with 萬 / 億 / 兆, one 零 for any run of zeros between non-zero
-- digits (20304 貳萬零參佰零肆, 100001000 壹億零壹仟), none at the end of a
-- group (10001000 壹仟萬壹仟).
local function numerals(int, set)
  if int == "" then return set.digits[1] end
  local out, zero = {}, false
  local n = #int
  local ngroups = math.ceil(n / 4)
  for g = ngroups, 1, -1 do
    local hi = n - (g - 1) * 4  -- last digit of this group
    local group = int:sub(math.max(1, hi - 3), hi)
    group = string.rep("0", 4 - #group) .. group
    if group == "0000" then
      zero = #out > 0
    else
      for p = 1, 4 do
        local d = group:sub(p, p)
        if d == "0" then
          if #out > 0 and p < 4 and group:sub(p + 1):match("[1-9]") then zero = true end
        else
          if zero then out[#out + 1] = set.digits[1] end
          zero = false
          out[#out + 1] = digit(set, d) .. set.units[5 - p]
        end
      end
      out[#out + 1] = GROUPS[g]
    end
  end
  return table.concat(out)
end

local function one_by_one(s, set)
  return (s:gsub("%d", function(d) return set[tonumber(d) + 1] end))
end

local function with_commas(int)
  local out = int:reverse():gsub("(%d%d%d)", "%1,"):reverse()
  return (out:gsub("^,", ""))
end

-- All the ways to write the number typed as `keys`, the keys first.
local function number_forms(keys)
  local int, frac = keys:match("^(%d+)%.?(%d*)$")
  if not int then return {} end
  local stripped = int:gsub("^0+", "")
  if #stripped > MAX_DIGITS then return {} end
  local decimal = keys:find(".", 1, true) ~= nil
  local function chinese(set)
    local s = numerals(stripped, set)
    if set == LOWER then s = s:gsub("^一十", "十") end
    if decimal then
      s = s .. "點" .. frac:gsub("%d", function(d) return digit(set, d) end)
    end
    return s
  end
  local forms = {
    { keys, "數字" },
    { with_commas(int) .. (decimal and "." .. frac or ""), "千分位" },
    { chinese(UPPER), "大寫" },
  }
  if not decimal and stripped ~= "" then
    forms[#forms + 1] = { "新臺幣" .. chinese(UPPER) .. "元整", "金額" }
  end
  forms[#forms + 1] = { chinese(LOWER), "國字" }
  forms[#forms + 1] = { one_by_one(int, ONE_BY_ONE) .. (decimal and "點" .. one_by_one(frac, ONE_BY_ONE) or ""),
                        "逐字" }
  forms[#forms + 1] = { (keys:gsub(".", FULL)), "全形" }
  local out, seen = {}, {}
  for _, f in ipairs(forms) do
    if not seen[f[1]] then
      seen[f[1]] = true
      out[#out + 1] = f
    end
  end
  return out
end

-- Dates ---------------------------------------------------------------------
local DAYS = { ["今天"] = 0, ["明天"] = 1, ["昨天"] = -1, ["後天"] = 2, ["前天"] = -2 }
local WEEKDAY = { "日", "一", "二", "三", "四", "五", "六" }

local function now_time(env)
  local fixed = env.now
  if fixed then
    local y, m, d, h, mi = fixed:match("^(%d+)-(%d+)-(%d+)%s+(%d+):(%d+)$")
    if y then
      return os.time({ year = tonumber(y), month = tonumber(m), day = tonumber(d),
                       hour = tonumber(h), min = tonumber(mi) })
    end
  end
  return os.time()
end

local function date_forms(env, word)
  local t = os.date("*t", now_time(env))
  if word == "現在" then
    local h12 = t.hour % 12 == 0 and 12 or t.hour % 12
    return {
      { string.format("%02d:%02d", t.hour, t.min), "時間" },
      { string.format("%s%d:%02d", t.hour < 12 and "上午" or "下午", h12, t.min), "時間" },
      { string.format("%04d-%02d-%02d %02d:%02d", t.year, t.month, t.day, t.hour, t.min), "時間" },
    }
  end
  -- Noon, so that moving the day never trips over a change of clock.
  local d = os.date("*t", os.time({ year = t.year, month = t.month, day = t.day + DAYS[word], hour = 12 }))
  return {
    { string.format("%04d-%02d-%02d", d.year, d.month, d.day), "日期" },
    { string.format("%04d/%02d/%02d", d.year, d.month, d.day), "日期" },
    { string.format("%d年%d月%d日", d.year, d.month, d.day), "日期" },
    { string.format("民國%d年%d月%d日", d.year - 1911, d.month, d.day), "日期" },
    { string.format("%d月%d日（%s）", d.month, d.day, WEEKDAY[d.wday]), "日期" },
  }
end

-- Processor -----------------------------------------------------------------
local processor = {}

function processor.init(env)
  local ctx = env.engine.context
  env.commit_conn = ctx.commit_notifier:connect(function(c) c:set_property(NUM, "") end)
end

function processor.fini(env)
  if env.commit_conn then env.commit_conn:disconnect() end
end

function processor.func(key, env)
  local ctx = env.engine.context
  if key:release() or key.keycode ~= XK_Tab or key.modifier ~= 0 then return kNoop end
  if not ctx:is_composing() or ctx:get_option("ascii_mode") or ctx:get_option(EXPANDED)
      or ctx.input:sub(1, 1) == "`" or ctx.caret_pos ~= #ctx.input then
    return kNoop
  end
  require("asus_lang").finalize(ctx)  -- digit keys at the end: a number now
  local seg = ctx.composition:back()
  if not seg or seg._end ~= #ctx.input then return kNoop end
  local keys = ctx.input:sub(seg.start + 1, seg._end)
  if not (keys:match("^%d+%.?%d*$") and keys:match("%d$")) or #number_forms(keys) == 0 then
    return kNoop
  end
  ctx:set_property(NUM, ctx.input .. "\t" .. seg.start)
  require("asus_keys").expand(ctx)
  return kAccepted
end

-- Filter --------------------------------------------------------------------
local filter = {}

function filter.init(env)
  local now = env.engine.schema.config:get_string("asus_quick/now")
  env.now = (now and now ~= "") and now or nil
end

function filter.func(input, env)
  local ctx = env.engine.context
  if not ctx:get_option(EXPANDED) then
    for cand in input:iter() do yield(cand) end
    return
  end
  local num_start = nil
  local mark = ctx:get_property(NUM)
  if mark ~= "" then
    local at_input, at = mark:match("^(.*)\t(%d+)$")
    if at_input == ctx.input then num_start = tonumber(at) end
  end
  local seen, dated = {}, {}
  local first = true
  for cand in input:iter() do
    if first and num_start and cand.start == num_start and cand._end == #ctx.input then
      local keys = ctx.input:sub(cand.start + 1, cand._end)
      for _, f in ipairs(number_forms(keys)) do
        seen[f[1]] = true
        yield(Candidate("quick", cand.start, cand._end, f[1], f[2]))
      end
    end
    first = false
    if not seen[cand.text] then
      seen[cand.text] = true
      yield(cand)
    end
    local span = cand.start .. ":" .. cand._end
    if DAYS[cand.text] or cand.text == "現在" then
      if not dated[span] then
        dated[span] = true
        for _, f in ipairs(date_forms(env, cand.text)) do
          if not seen[f[1]] then
            seen[f[1]] = true
            yield(Candidate("quick", cand.start, cand._end, f[1], f[2]))
          end
        end
      end
    end
  end
end

return { processor = processor, filter = filter, number_forms = number_forms }
