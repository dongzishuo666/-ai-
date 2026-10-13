-- 文件：backend/app/database/migrations/001_discussion_v2.sql
-- 用途：功能二 DDL 从 v1 升级到 v2（无生产数据时无需执行，删除 app.db 即可）

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- 1. discussions 新增 current_round
ALTER TABLE discussions ADD COLUMN current_round INTEGER NOT NULL DEFAULT 0;

-- 2. 删除旧 discussion_scores，新建 round_scores（宽表）
DROP TABLE IF EXISTS discussion_scores;
CREATE TABLE round_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    round           INTEGER NOT NULL,
    ai_id           TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    quality         REAL DEFAULT 0.0,
    relevance       REAL DEFAULT 0.0,
    contribution    REAL DEFAULT 0.0,
    compliance      REAL DEFAULT 0.0,
    weighted_total  REAL DEFAULT 0.0,
    judge_model     TEXT NOT NULL,
    scored_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(discussion_id, round, ai_id)
);
CREATE INDEX idx_rs_disc_round ON round_scores(discussion_id, round);
CREATE INDEX idx_rs_ai ON round_scores(ai_id);

-- 3. 重建 discussion_bans（事件流式）
DROP TABLE IF EXISTS discussion_bans;
CREATE TABLE discussion_bans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    ai_id           TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    action          TEXT NOT NULL CHECK(action IN ('banned', 'unbanned')),
    reason          TEXT NOT NULL,
    ban_type        TEXT CHECK(ban_type IN ('low_score', 'violation')),
    effective_round INTEGER NOT NULL,
    ban_rounds      INTEGER DEFAULT 1,
    is_active       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_db_discussion ON discussion_bans(discussion_id);
CREATE INDEX idx_db_active ON discussion_bans(discussion_id, is_active);

COMMIT;

PRAGMA foreign_keys = ON;
