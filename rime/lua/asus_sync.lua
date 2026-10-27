-- asus_sync.lua
-- Multi-device sync of what asus_learn.lua learned (asus_learn.userdb),
-- through a folder that a cloud drive mirrors (Google Drive), on Windows and
-- macOS. Driven by asus_learn.lua; nothing else calls it.
--
-- Configured in the user folder's installation.yaml, with Rime's own keys:
--   sync_dir: <folder>         without it nothing happens at all: no file is
--                              read from or written to any sync folder and
--                              no tombstone is recorded
--   installation_id: <id>      this device's folder name (letters, digits, - _ .)
--
-- Files:
--   <sync_dir>/daohui/<id>/asus_learn.txt   this device's snapshot:
--       #daohui-asus-learn v1<TAB><installation_id><TAB><unix time>
--       key<TAB>value                         (keys contain tabs: the value is
--       ...                                    after the last one), as stored
--       #end<TAB><number of key lines>        (a file without it is incomplete
--                                              - still being synced - and skipped)
--     Written whole (temporary file + rename), only when the lines changed.
--     The folder <sync_dir>/daohui/<id>/ is made by the settings app; on
--     macOS this module makes it too (mkdir -p, only below a sync_dir that
--     exists), on Windows it cannot without a console window flashing up,
--     so it waits for it.
--   <user dir>/daohui_sync_request           written by the settings app to ask
--       for a sync: first line anything that changes per request (a time),
--       then one line per other device's installation_id (the folders under
--       <sync_dir>/daohui/; Lua cannot list a folder). Devices imported once
--       are remembered, so later requests may list fewer.
--
-- When:
--   * at IME start (the first session of the process): import, then export
--   * when daohui_sync_request has changed (looked at, at most every
--     asus_sync/check_seconds, default 3, on a key press): installation.yaml
--     is read again, then import and export
--   * export alone when something was learned and the last export is
--     asus_sync/export_minutes (default 30) old, on a key press
--   Lua only runs on key presses and new sessions: a request written while
--   nobody types is handled at the next key (or at the next IME start).
--
-- Import (per other device, skipped while its snapshot's first line is the
-- one imported last): idempotent and order-independent.
--   * weighted values ("name w t|..."; keys c x e w n m): per key and name
--     the entry with the larger weight at a common time wins (asus_learn's
--     decay: w * 0.5^(age / half life)), kept as written by its device, with
--     that device's time stamp (so importing a copy of the same data, or an
--     A -> B -> A round trip, changes nothing); entries decayed below the
--     prune level are dropped; e/m keep 1 entry, n keeps 8, as asus_learn does
--   * "r" (pair learned) fills in what is missing; "b" (never predict), "q",
--     "d" and anything newer: the local value wins, missing ones are filled
--   * tombstones ("~\t<key>" = "name t|..."; name "*" = the whole key),
--     recorded when the user forgets something (Shift+BackSpace,
--     Shift/Ctrl+Delete): an entry stamped at or before the tombstone's time
--     is dropped, here and in every import; an entry stamped later was
--     learned again since and stays. An "r" value has no stamp: it stays
--     only when its "c" entry was learned again after the tombstone.
--     Tombstones are imported too (so a third device honours them) and are
--     dropped after TOMB_DAYS.
-- Not exported: librime's metadata ("\1/..."), this module's bookkeeping
-- ("\2sync\t<device>" = first line imported last), keys of librime's own
-- sync ("w \t..." = "c=0 d=0 t=1": RimeSyncUserData backs up the two-field
-- keys of every *.userdb and merges them back under the first field plus a
-- space, which asus_learn never reads), and weighted keys with nothing left.

local clock = require("asus_clock")

local M = {}

local MAGIC = "#daohui-asus-learn v1"
local HEADER = "^#daohui%-asus%-learn v1\t([^\t]*)\t%d+$"
local SNAPSHOT = "asus_learn.txt"
local REQUEST = "daohui_sync_request"
local TOMB_DAYS = 180
local MARK = "\2sync\t"
local MAPS = { c = true, x = true, e = true, w = true, n = true, m = true }
local ID = "^[%w][%w%-_.]*$"

local learn         -- from asus_learn.lua: {db=, half_life=, prune=, keep=, reload=}
local cfg           -- {dir=, root=, id=, own=} while sync_dir is set
local request_path
local token         -- daohui_sync_request content handled last
local check_every, export_every = 3, 30 * 60
local last_check, last_export = 0, 0
local dirty = false
local warned = false

local function warn(msg)
  if log and log.warning then log.warning("asus_sync.lua: " .. msg) end
end

local function join(a, b)
  if a:sub(-1) == "/" or a:sub(-1) == "\\" then return a .. b end
  return a .. "/" .. b
end

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

-- installation.yaml ------------------------------------------------------------
-- A YAML scalar as librime (yaml-cpp) or a person writes it: "double" (with
-- escapes), 'single' or plain; nil for null / empty.
local ESC = { ["\\"] = "\\", ['"'] = '"', ["/"] = "/", t = "\t", n = "\n", r = "\r", [" "] = " ", ["0"] = "\0" }
local function scalar(s)
  local q = s:sub(1, 1)
  local out, i = {}, 2
  if q == '"' then
    while i <= #s do
      local c = s:sub(i, i)
      if c == '"' then return table.concat(out) end
      if c == "\\" then
        local n = s:sub(i + 1, i + 1)
        local len = ({ x = 2, u = 4, U = 8 })[n]
        if len then
          local cp = tonumber(s:sub(i + 2, i + 1 + len), 16)
          if not cp then return nil end
          out[#out + 1] = n == "x" and string.char(cp) or utf8.char(cp)
          i = i + 2 + len
        else
          out[#out + 1] = ESC[n] or n
          i = i + 2
        end
      else
        out[#out + 1] = c
        i = i + 1
      end
    end
    return nil  -- (unterminated)
  elseif q == "'" then
    while i <= #s do
      local c = s:sub(i, i)
      if c == "'" then
        if s:sub(i + 1, i + 1) ~= "'" then return table.concat(out) end
        i = i + 1
      end
      out[#out + 1] = c
      i = i + 1
    end
    return nil
  end
  s = s:gsub("%s+#.*$", ""):gsub("%s+$", "")
  if s == "" or s == "~" or s:lower() == "null" then return nil end
  return s
end

local function installation()
  local out = {}
  for line in (read_file(join(rime_api.get_user_data_dir(), "installation.yaml")) or ""):gmatch("[^\r\n]+") do
    local k, v = line:match("^([%w_]+):%s*(.-)%s*$")
    if k == "sync_dir" or k == "installation_id" then out[k] = scalar(v) end
  end
  return out
end

local function configure()
  local inst = installation()
  local dir, id = inst.sync_dir, inst.installation_id
  if not dir or not id or not id:match(ID) then
    cfg = nil
    return
  end
  dir = dir:gsub("[/\\]+$", "")
  local root = join(dir, "daohui")
  cfg = { dir = dir, root = root, id = id, own = join(join(root, id), SNAPSHOT) }
end

-- Values -----------------------------------------------------------------------
-- Weighted entries of a value, as written: name -> {w=, t=, s=item text}.
local function items(v)
  local out = {}
  for item in (v or ""):gmatch("[^|]+") do
    local name, w, t = item:match("^(.-) ([%d%.e%-]+) (%d+)$")
    w, t = tonumber(w), tonumber(t)
    if name and w and t and w > 0 then out[name] = { w = w, t = t, s = item } end
  end
  return out
end

-- Does entry a outweigh b at any common time? (log2 of the weight at time 0.)
-- Ties: the later stamp, then the text, so the winner never depends on
-- which side is local.
local function beats(a, b)
  local hl = learn.half_life()
  local sa, sb = math.log(a.w, 2) + a.t / hl, math.log(b.w, 2) + b.t / hl
  if sa ~= sb then return sa > sb end
  if a.t ~= b.t then return a.t > b.t end
  return a.s > b.s
end

local function live(e, now)
  return e.w * 0.5 ^ (math.max(0, now - e.t) / learn.half_life()) >= learn.prune
end

local function compose(map, keep, now)
  local names = {}
  for name, e in pairs(map) do
    if live(e, now) then names[#names + 1] = name end
  end
  table.sort(names, function(a, b) return beats(map[a], map[b]) end)
  local parts = {}
  for i, name in ipairs(names) do
    if keep and i > keep then break end
    parts[#parts + 1] = map[name].s
  end
  return table.concat(parts, "|")
end

-- Tombstones: name -> time ("*": the whole key).
local function tombs(v)
  local out = {}
  for item in (v or ""):gmatch("[^|]+") do
    local name, t = item:match("^(.-) (%d+)$")
    if name then out[name] = math.max(out[name] or 0, tonumber(t)) end
  end
  return out
end

local function tomb_text(map, now)
  local names = {}
  for name, t in pairs(map) do
    if now - t <= TOMB_DAYS * 86400 then names[#names + 1] = name end
  end
  table.sort(names)
  local parts = {}
  for i, name in ipairs(names) do parts[i] = name .. " " .. map[name] end
  return table.concat(parts, "|")
end

local function buried(tm, name, t)
  if not tm then return false end
  local when = math.max(tm["*"] or 0, tm[name] or 0)
  return when > 0 and t <= when
end

-- "r\t<keys>\t<chosen>" = rejected: was the pair learned again after `when`?
-- (get: key -> value, of the side the r value comes from)
local function relearned(get, rkey, rejected, when)
  local k, c = rkey:match("^r\t(.*)\t([^\t]*)$")
  local e = k and items(get("c\t" .. k .. "\t" .. rejected))[c]
  return e ~= nil and e.t > when
end

-- Import -----------------------------------------------------------------------
-- Drop what tombstone map tm buries from the local value of key.
local function bury(db, key, tm, now)
  local v = db:fetch(key)
  if not v then return false end
  local kind = key:match("^([^\t]*)\t")
  if MAPS[kind] then
    local map, hit = items(v), false
    for name, e in pairs(map) do
      if buried(tm, name, e.t) then map[name], hit = nil, true end
    end
    if not hit then return false end
    local s = compose(map, learn.keep[kind], now)
    if s == "" then db:erase(key) else db:update(key, s) end
    return true
  elseif kind == "r" and tm["*"] and not relearned(function(k) return db:fetch(k) end, key, v, tm["*"]) then
    db:erase(key)
    return true
  end
  return false
end

-- Merge one other device's snapshot; true when local data changed.
local function import(peer, now)
  local db = learn.db()
  local text = read_file(join(join(cfg.root, peer), SNAPSHOT))
  if not text then return false end
  local header = text:match("^([^\n]*)\n")
  header = header and (header:gsub("\r$", ""))
  local id = header and header:match(HEADER)
  if not id or id == cfg.id or db:fetch(MARK .. peer) == header then return false end
  local rows, tomb_rows, n, complete = {}, {}, 0, false
  for line in text:gmatch("([^\n]*)\n") do
    line = line:gsub("\r$", "")
    local count = line:match("^#end\t(%d+)$")
    if count then
      complete = tonumber(count) == n
      break
    end
    if line:sub(1, 1) ~= "#" then
      local k, v = line:match("^(.*)\t([^\t]*)$")
      if k and k:find("\t") then
        n = n + 1
        if k:sub(1, 2) == "~\t" then tomb_rows[k:sub(3)] = v else rows[k] = v end
      end
    end
  end
  if not complete then return false end  -- (still being written or synced)
  local changed = false
  -- Tombstones first: kept here too (for later imports and other devices).
  local tm_of = {}
  for key, v in pairs(tomb_rows) do
    local tk = "~\t" .. key
    local old = db:fetch(tk)
    local map = tombs(old)
    for name, t in pairs(tombs(v)) do map[name] = math.max(map[name] or 0, t) end
    local s = tomb_text(map, now)
    if s ~= (old or "") then
      if s == "" then db:erase(tk) else db:update(tk, s) end
      changed = true
    end
    tm_of[key] = tombs(s)
    changed = bury(db, key, tm_of[key], now) or changed
  end
  local function tomb_map(key)
    if tm_of[key] == nil then
      local v = db:fetch("~\t" .. key)
      tm_of[key] = v and tombs(v) or false
    end
    return tm_of[key] or nil
  end
  for key, v in pairs(rows) do
    local kind = key:match("^([^\t]*)\t")
    if kind and kind ~= "" and kind:byte(1) > 0x20 and kind:sub(-1) ~= " " then
      local old = db:fetch(key)
      local new
      if MAPS[kind] then
        local tm = tomb_map(key)
        local map, hit = items(old), false
        for name, e in pairs(items(v)) do
          if not buried(tm, name, e.t) and live(e, now) and (not map[name] or beats(e, map[name])) then
            map[name], hit = e, true
          end
        end
        if hit then new = compose(map, learn.keep[kind], now) end
      elseif not old then
        local tm = kind == "r" and tomb_map(key)
        if not (tm and tm["*"]) or relearned(function(k) return rows[k] end, key, v, tm["*"]) then
          new = v
        end
      end
      if new and new ~= "" and new ~= old then
        db:update(key, new)
        changed = true
      end
    end
  end
  db:update(MARK .. peer, header)
  return changed
end

-- Export -----------------------------------------------------------------------
-- <sync_dir>/daohui/<id>/, below a sync_dir that exists (never a cloud
-- folder that is not mounted). Not on Windows: a console window would flash.
local function make_dir()
  local dir = join(cfg.root, cfg.id)
  local probe = not clock.windows and io.open(cfg.dir, "r")  -- (a folder opens on macOS)
  if not probe then
    if not warned then warn("folder " .. dir .. " is missing; the settings app makes it") end
    warned = true
    return false
  end
  probe:close()
  os.execute("mkdir -p '" .. dir:gsub("'", "'\\''") .. "'")
  return true
end

local function export(now)
  local db = learn.db()
  local lines, expired = {}, {}
  local q = db:query("")
  if not q then return false end
  for k, v in q:iter() do
    local kind = k:match("^([^\t]*)\t")
    if kind and kind ~= "" and kind:byte(1) > 0x20 and kind:sub(-1) ~= " " and not k:find("[\r\n]")
        and not v:find("[\r\n]") then
      if kind == "~" then
        if tomb_text(tombs(v), now) == "" then expired[#expired + 1] = k
        else lines[#lines + 1] = k .. "\t" .. v end
      elseif MAPS[kind] then
        for _, e in pairs(items(v)) do
          if live(e, now) then
            lines[#lines + 1] = k .. "\t" .. v
            break
          end
        end
      else
        lines[#lines + 1] = k .. "\t" .. v
      end
    end
  end
  q = nil
  collectgarbage()  -- release the LevelDB iterator before writing
  for _, k in ipairs(expired) do db:erase(k) end
  last_export, dirty = now, false
  local body = table.concat(lines, "\n") .. (#lines > 0 and "\n" or "") .. "#end\t" .. #lines .. "\n"
  local old = read_file(cfg.own)
  if old and old:match("^[^\n]*\n(.*)$") == body then return false end
  local tmp = cfg.own .. ".tmp"
  local f = io.open(tmp, "wb")
  if not f and make_dir() then f = io.open(tmp, "wb") end
  if not f then return false end
  f:write(MAGIC, "\t", cfg.id, "\t", tostring(now), "\n", body)
  f:close()
  if not os.rename(tmp, cfg.own) then  -- (Windows: no rename over a file)
    os.remove(cfg.own)
    if not os.rename(tmp, cfg.own) then
      os.remove(tmp)
      return false
    end
  end
  return true
end

-- Import from every other device, then export.
local function run(now, request)
  local db = learn.db()
  local peers, seen = {}, { [cfg.id] = true }
  local function add(p)
    if p and p:match(ID) and not seen[p] then
      seen[p] = true
      peers[#peers + 1] = p
    end
  end
  local first = true
  for line in (request or ""):gmatch("[^\r\n]+") do
    if not first then add(line:match("^%s*(.-)%s*$")) end
    first = false
  end
  local q = db:query(MARK)
  if q then
    for k in q:iter() do
      if k:sub(1, #MARK) ~= MARK then break end
      add(k:sub(#MARK + 1))
    end
  end
  q = nil
  collectgarbage()
  local changed = false
  for _, p in ipairs(peers) do
    local ok, hit = pcall(import, p, now)
    if not ok then warn("import from " .. p .. ": " .. tostring(hit)) end
    changed = (ok and hit) or changed
  end
  if changed then learn.reload() end
  local ok, err = pcall(export, now)
  if not ok then warn("export: " .. tostring(err)) end
end

-- API for asus_learn.lua -------------------------------------------------------
-- At the first session of the process, with the database open.
function M.start(api, config)
  learn = api
  check_every = tonumber(config:get_string("asus_sync/check_seconds") or "") or check_every
  local minutes = tonumber(config:get_string("asus_sync/export_minutes") or "")
  if minutes then export_every = minutes * 60 end
  request_path = join(rime_api.get_user_data_dir(), REQUEST)
  token = read_file(request_path)
  configure()
  local now = os.time()
  last_check = now
  if cfg then run(now, token) end
end

-- On every key press (cheap: a clock read; a small file read every few seconds).
function M.tick()
  if not learn then return end
  local now = os.time()
  if now - last_check < check_every then return end
  last_check = now
  local request = read_file(request_path)
  if request and request ~= token then
    token = request
    configure()
    if cfg then run(now, request) end
  elseif cfg and dirty and now - last_export >= export_every then
    local ok, err = pcall(export, now)
    if not ok then warn("export: " .. tostring(err)) end
  end
end

-- Something was written to the database.
function M.changed()
  dirty = true
end

-- The user forgot entry `name` of `key` (nil: the whole key).
function M.forgot(key, name)
  if not cfg or not learn then return end
  local db = learn.db()
  local tk = "~\t" .. key
  local map = tombs(db:fetch(tk))
  local now = os.time()
  map[name or "*"] = now
  db:update(tk, tomb_text(map, now))
  dirty = true
end

return M
