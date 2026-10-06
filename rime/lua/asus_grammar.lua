-- asus_grammar.lua
-- Context rules for characters that share one reading and that a language
-- model alone gets wrong (evaluated on tests/eval/particles*.txt):
--   的 / 得 / 地   all typed ㄉㄜ˙ (MOE standard neutral reading)
--   在 / 再        both ㄗㄞˋ
-- Rules only rewrite a character whose syllable was actually typed as ㄉㄜ˙ /
-- ㄗㄞˋ, so 地 typed ㄉㄧˋ (土地) is never touched. They apply to the best
-- candidate of a pure-Chinese, fully toned input (one syllable per character).
--
-- Must come before asus_compact in engine/filters.

local zy = require("asus_zhuyin_data")

local DE, ZAI = "2k7", "y94"  -- Dachen keys of ㄉㄜ˙ and ㄗㄞˋ

-- Helpers --------------------------------------------------------------------
local function set(s)
  local t = {}
  for w in s:gmatch("%S+") do t[w] = true end
  return t
end

-- 得: verb/adjective + 得 + degree, comparison, result (跑得很快, 做得不錯).
local DE2_NEXT = set([[很 太 更 不 比 好 真 這麼 那麼 怎麼 多 非常 相當 有點 越來越
  特別 還 挺 蠻 滿 夠 正 及 過 到 清楚 一塌糊塗]])
-- ...but not after a possessor (我的很多朋友, 誰的比較好) or where X的 is a
-- word of its own (真的很好, 是的, 有的不錯, 好的).
local POSSESSOR = set([[我 你 妳 他 她 它 我們 你們 他們 她們 大家 誰 自己 別人 人家
  今天 昨天 明天 這 那 真 是 有 別 好 對 似 目]])

-- 地: adverbial adjective + 地 + verb (慢慢地走, 認真地讀).
-- Reduplicated forms are listed explicitly: a generic AA rule would also hit
-- 媽媽的書 / 哥哥的手機.
local DI_PREV = set([[認真 仔細 有效 開心 耐心 小心 熱烈 安靜 不停 快速 努力 用心 輕鬆
  順利 大聲 小聲 逐漸 漸漸 不斷 積極 主動 迅速 緩緩 默默 靜靜 輕輕 慢慢 好好 偷偷
  悄悄 狠狠 深深 緊緊 高興 溫柔 冷靜 清楚 詳細 徹底 充分 盡量 盡快 很快 用力 拼命
  勉強 隨便 公開 獨立 自由 簡單 明確 準確 正確 確實 持續 反覆 一再 不知不覺
  靜悄悄 慢吞吞 急忙 匆匆 匆忙 輕易 順手 隨手 默默無聞 大大 稍稍 微微 暗暗 痛快]])
-- Character starts that signal a noun (認真的人, 開心的事) keep 的.
local NOUN_NEXT = set([[人 事 樣 東 時 地 話 感 心 一 個 部 方 結 成 態 表 原 理 問 地方]])

-- 再: again / then (等一下我再說, 如果再不, 再想一想).
-- (Not 等: 我在等公車 and 請再等一下 are both common.)
local ZAI4_NEXT = set([[一次 一下 一點 說 見 來 試 不 也 度 次 加 聯絡 便宜 檢查 打給 給]])
-- Immediately before 再. (Not 明天 / 下次: 明天在台北 is common.)
local ZAI4_PREV = set([[等一下 晚點 晚一點 之後 然後 改天 如果 了 一下 不要 不能 不會 別
  要是 只要 就 可以 想]])
-- Sequence / later-time markers within the 6 characters before 再:
-- 先…再, V完…再, 等一下我再…
local ZAI4_WINDOW = set("先 完 等一下 晚點 待會")
-- 在: location / progressive (在家, 正在, 還在, 一直在).
local ZAI_PREV = set([[正 還 一直 都 也 現 存 實 所 好 自]])
local ZAI_NEXT = set([[家 這裡 那裡 哪 哪裡 公司 學校 桌 網路 圖書館 廚房 包包 床 外面 裡面 上面
  路上 台灣 中 上]])

-- UTF-8 string -> array of characters.
local function chars(s)
  local out = {}
  for _, cp in utf8.codes(s) do out[#out + 1] = utf8.char(cp) end
  return out
end

-- Does any word in `words` match the characters ending at i (left) / starting
-- at i (right)?
local function left_has(cs, i, words)
  for len = 1, 4 do
    if i - len + 1 < 1 then break end
    if words[table.concat(cs, "", i - len + 1, i)] then return true end
  end
  return false
end

local function right_has(cs, i, words)
  for len = 1, 4 do
    if i + len - 1 > #cs then break end
    if words[table.concat(cs, "", i, i + len - 1)] then return true end
  end
  return false
end

-- Does a word of `words` (1-3 characters) lie within the n characters ending at i?
local function window_has(cs, i, n, words)
  local lo = math.max(1, i - n + 1)
  for j = lo, i do
    for len = 1, 3 do
      if j + len - 1 > i then break end
      if words[table.concat(cs, "", j, j + len - 1)] then return true end
    end
  end
  return false
end

-- Split fully toned Zhuyin keys into syllables; nil if not possible.
local function syllables(input)
  local out, pos = {}, 1
  while pos <= #input do
    local found
    for len = math.min(5, #input - pos + 1), 1, -1 do
      local s = input:sub(pos, pos + len - 1)
      if zy.toned[s] then found = s; break end
    end
    if not found then return nil end
    out[#out + 1] = found
    pos = pos + #found
  end
  return out
end

-- Apply the rules; returns the corrected text or nil if unchanged.
local function correct(text, keys)
  local cs = chars(text)
  if #cs ~= #keys then return nil end
  local changed = false
  for i, ch in ipairs(cs) do
    local k = keys[i]
    if k == DE and (ch == "的" or ch == "得" or ch == "地") and i > 1 then
      local want
      if right_has(cs, i + 1, DE2_NEXT) and not left_has(cs, i - 1, POSSESSOR) then
        want = "得"
      elseif left_has(cs, i - 1, DI_PREV) and i < #cs and not right_has(cs, i + 1, NOUN_NEXT) then
        want = "地"
      end
      if want and want ~= ch then cs[i] = want; changed = true end
    elseif k == ZAI and (ch == "在" or ch == "再") then
      local want
      if left_has(cs, i - 1, ZAI_PREV) or right_has(cs, i + 1, ZAI_NEXT) then
        want = "在"
      elseif right_has(cs, i + 1, ZAI4_NEXT) or left_has(cs, i - 1, ZAI4_PREV)
          or window_has(cs, i - 1, 6, ZAI4_WINDOW)
          or (i + 3 <= #cs and cs[i + 1] == cs[i + 3] and cs[i + 2] == "一") then
        want = "再"  -- incl. 再想一想 (V一V)
      end
      if want and want ~= ch then cs[i] = want; changed = true end
    end
  end
  return changed and table.concat(cs) or nil
end

local function func(input, env)
  local ctx = env.engine.context
  local keys = syllables(ctx.input)
  local first = true
  for cand in input:iter() do
    if first and keys then
      first = false
      local fixed = correct(cand.text, keys)
      if fixed then
        yield(ShadowCandidate(cand, cand.type, fixed, cand.comment))
      else
        yield(cand)
      end
    else
      first = false
      yield(cand)
    end
  end
end

return { func = func, correct = correct }
