-- GNA Agri-Intelligence Platform — Database Schema

CREATE TABLE IF NOT EXISTS farmers (
    farmer_id           TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    phone               TEXT UNIQUE NOT NULL,
    zone                TEXT,
    season_number       INTEGER DEFAULT 1,
    crop                TEXT,
    planting_date       DATE,
    total_hectares      REAL,
    has_inoculant       INTEGER DEFAULT 0,
    has_fertilizer      INTEGER DEFAULT 0,
    risk_score          REAL DEFAULT 0.0,
    risk_tier           TEXT DEFAULT 'Low',
    field_agent_id      TEXT,
    last_nudge_sent     DATETIME,
    yield_estimate_kg   REAL,
    nudge_responses     TEXT DEFAULT '{"total":0,"done":0,"help":0,"skip":0}',
    consecutive_help    INTEGER DEFAULT 0,
    total_in_kind_repay REAL DEFAULT 0,
    days_to_plant       INTEGER DEFAULT 0,
    preferred_language  TEXT DEFAULT 'english',
    camp_name           TEXT,
    district_name       TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id   TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('farmer', 'agent')),
    message     TEXT NOT NULL,
    topic       TEXT,
    language    TEXT,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (farmer_id) REFERENCES farmers(farmer_id)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id   TEXT NOT NULL,
    score       REAL NOT NULL,
    trigger     TEXT,
    alerted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (farmer_id) REFERENCES farmers(farmer_id)
);

CREATE TABLE IF NOT EXISTS field_agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT UNIQUE NOT NULL,
    zone        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nudge_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id   TEXT NOT NULL,
    phase       TEXT,
    topic       TEXT,
    message     TEXT,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_farmers_phone ON farmers(phone);
CREATE INDEX IF NOT EXISTS idx_farmers_risk ON farmers(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_farmers_risk_tier ON farmers(risk_tier);
CREATE INDEX IF NOT EXISTS idx_conversations_farmer ON conversations(farmer_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_topic ON conversations(farmer_id, topic, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_farmer ON risk_events(farmer_id, alerted_at DESC);
