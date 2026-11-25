-- M2 持久记忆库 schema — 见《00_协作规程与接口契约.txt》PART B1
-- Phase1 只用到 episodes / facts；principles / affective_state 为后续阶段预留，
-- 先建表以保证 schema 前向兼容，避免日后破坏性迁移。

CREATE TABLE IF NOT EXISTS episodes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  role       TEXT NOT NULL,          -- user | ai | system
  content    TEXT NOT NULL,
  session_id TEXT
);

CREATE TABLE IF NOT EXISTS facts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  subject           TEXT NOT NULL,
  predicate         TEXT NOT NULL,
  object            TEXT NOT NULL,
  ts                TEXT NOT NULL,
  confidence        REAL NOT NULL DEFAULT 0.5,
  source_episode_id INTEGER
);

CREATE TABLE IF NOT EXISTS principles (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  text            TEXT NOT NULL,
  created_ts      TEXT NOT NULL,
  revised_ts      TEXT,
  revision_reason TEXT,
  active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS affective_state (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  mood       TEXT,
  valence    REAL,
  arousal    REAL,
  updated_ts TEXT,
  note       TEXT
);

-- Phase2 新增：通用键值元数据表（非破坏性，CREATE IF NOT EXISTS）。
-- 现用于存反思引擎的增量水位线 reflect_upto_ep_id，避免重复蒸馏已处理的 episode。
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- Phase3 新增：M6 主动推送触发器（非破坏性）。
-- justify_reason NOT NULL 且业务层强制非空：落实“主动要有正当理由”，防贴心变骚扰。
CREATE TABLE IF NOT EXISTS triggers (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  type            TEXT NOT NULL,        -- manual | date | condition
  spec            TEXT,                 -- date: ISO datetime; condition: 如 "daily"
  message_template TEXT NOT NULL,
  last_fired_ts   TEXT,
  justify_reason  TEXT NOT NULL,
  active          INTEGER NOT NULL DEFAULT 1
);

-- Phase3 新增：主动推送发件箱（系统“关掉也在”产出的消息先落这里，待送达/查看）。
CREATE TABLE IF NOT EXISTS outbox (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  trigger_id INTEGER,
  channel    TEXT NOT NULL DEFAULT "cli",  -- 未来: cli | web_push | email ...
  message    TEXT NOT NULL,
  is_read    INTEGER NOT NULL DEFAULT 0
);

-- Phase5 新增：事实改口留痕。upsert_fact 覆盖旧值时先把旧值写这里，
-- 这样“你以前说我做的是什么”答得上来——记忆体不只是存当前值，还要记得它曾经怎么想。
-- object = 当时以为是事实的值；replaced_ts = 被改掉的时刻。
CREATE TABLE IF NOT EXISTS fact_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fact_id           INTEGER NOT NULL,
  subject           TEXT NOT NULL,
  predicate         TEXT NOT NULL,
  object            TEXT NOT NULL,
  ts                TEXT NOT NULL,          -- 该旧值当初写入的时间
  replaced_ts       TEXT NOT NULL,          -- 被新值改掉的时刻
  source_episode_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_fact_history_sp ON fact_history(subject, predicate);

-- Phase5.6 新增：原则改口留痕（与 fact_history 对称）。
-- 为什么不让自我模型自己记账：原则的写入口有【好几个】——M4 反思、CLI :retire、
-- 界面"作废这条"、self_model.revise_principle。账记在调用方 = 只要有人绕过它，
-- 账就少一笔（评审时已实测复现）。所以账记在唯一的写入口 M2 里，谁来写都留痕。
-- old_text=NULL 表示这条是新增；op=retire 时 new_text=NULL。
CREATE TABLE IF NOT EXISTS principle_history (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  principle_id INTEGER NOT NULL,
  op           TEXT NOT NULL,            -- add | revise | retire
  old_text     TEXT,
  new_text     TEXT,
  reason       TEXT NOT NULL,
  ts           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_principle_history_pid ON principle_history(principle_id, ts);

-- B0 世界模型 v0：可核对的实体/关系/事件/信念/矛盾。
-- 同库，不另开第二套事实库；只存结构化账，不存“模型对世界的自由解释”。
CREATE TABLE IF NOT EXISTS world_entity (
  id             TEXT PRIMARY KEY,
  type           TEXT NOT NULL,
  name           TEXT NOT NULL,
  aliases        TEXT NOT NULL DEFAULT '[]',
  sensitivity    TEXT NOT NULL DEFAULT 'normal',
  created_at     TEXT NOT NULL,
  source_episode INTEGER,
  status         TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS world_relation (
  id             TEXT PRIMARY KEY,
  subject_id     TEXT NOT NULL,
  predicate      TEXT NOT NULL,
  object_id      TEXT,
  object_value   TEXT,
  valid_from     TEXT,
  valid_to       TEXT,
  confidence     REAL NOT NULL DEFAULT 0.5,
  source_episode INTEGER,
  observed_at    TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'active',
  retire_reason  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS world_event (
  id             TEXT PRIMARY KEY,
  at             TEXT NOT NULL,
  kind           TEXT NOT NULL,
  summary        TEXT NOT NULL,
  participants   TEXT NOT NULL DEFAULT '[]',
  place_id       TEXT,
  source_episode INTEGER,
  confidence     REAL NOT NULL DEFAULT 0.5,
  sensitivity    TEXT NOT NULL DEFAULT 'normal',
  status         TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS world_belief (
  id             TEXT PRIMARY KEY,
  holder         TEXT NOT NULL,
  subject_id     TEXT,
  predicate      TEXT NOT NULL,
  value          TEXT,
  confidence     REAL NOT NULL DEFAULT 0.5,
  source         TEXT,
  updated_at     TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS world_contradiction (
  id           TEXT PRIMARY KEY,
  a_kind       TEXT NOT NULL,
  a_id         TEXT NOT NULL,
  b_kind       TEXT NOT NULL,
  b_id         TEXT NOT NULL,
  detected_at  TEXT NOT NULL,
  resolution   TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_world_relation_sp ON world_relation(subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_world_event_at ON world_event(at);
CREATE INDEX IF NOT EXISTS idx_world_belief_holder ON world_belief(holder);
-- 真正的记忆系统 v1：档案柜 + 记事本（2026-11-08）
CREATE TABLE IF NOT EXISTS memory_item (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL DEFAULT 'note',
  subject       TEXT NOT NULL DEFAULT '',
  predicate     TEXT NOT NULL DEFAULT '',
  text          TEXT NOT NULL,
  summary       TEXT NOT NULL DEFAULT '',
  source        TEXT NOT NULL DEFAULT '',
  source_id     TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  valid_from    TEXT NOT NULL DEFAULT '',
  valid_to      TEXT NOT NULL DEFAULT '',
  confidence    REAL NOT NULL DEFAULT 0.6,
  importance    REAL NOT NULL DEFAULT 0.5,
  tags          TEXT NOT NULL DEFAULT '',
  entities      TEXT NOT NULL DEFAULT '',
  hash          TEXT NOT NULL DEFAULT '',
  superseded_by INTEGER,
  status        TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_memory_item_subject ON memory_item(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_memory_item_status ON memory_item(status);
CREATE INDEX IF NOT EXISTS idx_memory_item_created ON memory_item(created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_item_fts USING fts5(
  text, summary, tags
);

CREATE TABLE IF NOT EXISTS memory_notebook (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL UNIQUE,
  updated_at TEXT NOT NULL,
  data       TEXT NOT NULL DEFAULT '{}'
);
