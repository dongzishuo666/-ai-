-- ============================================================
-- 文件：backend/scripts/migrate_v4.sql
-- 说明：扩展功能迁移（弹劾/信用分/认知框架/即时对话）
-- 依据：多ai讨论板块prompt与工作流程.txt 模块A/B/C/E/F
-- 用法：sqlite3 app.db < migrate_v4.sql（或首次建库自动包含于 init.sql v4）
-- ============================================================

-- ---------- 1. 弹劾记录（模块B/F） ----------
CREATE TABLE IF NOT EXISTS discussion_impeachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id INTEGER NOT NULL,
    round INTEGER NOT NULL DEFAULT 1,
    initiator_ai_id TEXT NOT NULL,
    target_judge_role TEXT NOT NULL,          -- compliance / order / evaluation
    target_ai_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    votes_json TEXT NOT NULL DEFAULT '{}',    -- {ai_id: agree/disagree}
    agree_count INTEGER NOT NULL DEFAULT 0,
    disagree_count INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT 'pending',   -- pending / passed / rejected
    replacement_ai_id TEXT,                   -- 通过后的新裁判
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (discussion_id) REFERENCES discussions(id) ON DELETE CASCADE
);

-- ---------- 2. 用户信用分（模块F） ----------
CREATE TABLE IF NOT EXISTS user_credit (
    user_id TEXT PRIMARY KEY DEFAULT 'default',
    credit_score REAL NOT NULL DEFAULT 100.0,
    restrictions_json TEXT NOT NULL DEFAULT '[]',  -- 限制功能列表
    total_thumbs_down INTEGER NOT NULL DEFAULT 0,  -- 累计点踩
    total_malicious_reports INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------- 3. 用户行为日志（模块F圆桌会议证据） ----------
CREATE TABLE IF NOT EXISTS user_behavior_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    action TEXT NOT NULL,                     -- thumbs_down / malicious_report / danger_request / appeal
    detail TEXT NOT NULL DEFAULT '',
    credit_delta REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------- 4. 认知框架记忆（模块E） ----------
CREATE TABLE IF NOT EXISTS cognitive_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_discussion_id INTEGER,
    topic TEXT NOT NULL DEFAULT '',
    frame_text TEXT NOT NULL,                 -- 框架正文（结构化认知框架）
    status TEXT NOT NULL DEFAULT 'active',    -- active / retired / tension（未解决张力）
    generation INTEGER NOT NULL DEFAULT 1,    -- 框架演化代数
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------- 5. 即时对话会话（模块A） ----------
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新对话',
    mode TEXT NOT NULL DEFAULT 'quick',       -- quick / serial / parallel
    supervisor_enabled INTEGER NOT NULL DEFAULT 1,
    memory_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,                       -- user / ai / supervisor / system
    ai_id TEXT,                               -- role=ai 时有效
    content TEXT NOT NULL DEFAULT '',
    is_anomaly INTEGER NOT NULL DEFAULT 0,    -- 后置哨兵判定
    anomaly_reason TEXT,
    rewrite_of INTEGER,                       -- 异常纠正重写时指向原消息id
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

-- ---------- 6. 讨论表扩展列（模块B：风格模式/共识检测/认知连续性） ----------
-- style_mode: planning / affective / roleplay / custom（区别于发言顺序 mode）
ALTER TABLE discussions ADD COLUMN style_mode TEXT NOT NULL DEFAULT 'free';
ALTER TABLE discussions ADD COLUMN until_consensus INTEGER NOT NULL DEFAULT 0;
ALTER TABLE discussions ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE discussions ADD COLUMN consensus_reached INTEGER NOT NULL DEFAULT 0;

-- ---------- 7. 索引 ----------
CREATE INDEX IF NOT EXISTS idx_impeachments_discussion ON discussion_impeachments(discussion_id);
CREATE INDEX IF NOT EXISTS idx_behavior_logs_user ON user_behavior_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_frames_status ON cognitive_frames(status);
