-- ============================================================
-- 文件：backend/app/database/init.sql
-- 版本：v3（DDL 执行细节终审最终版）
-- 依据：施工蓝图 V1.2 + DDL 细节终审裁定 + DDL 执行细节终审裁定
-- 结构：Section 1 PRAGMA / 2 全局配置 / 3 功能一 / 4 功能二 / 5 功能三 / 6 公共
-- ============================================================

-- ============================================================
-- Section 1: PRAGMA 设置
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA cache_size = -4000;

-- ============================================================
-- Section 2: 全局配置表（Static Data）
-- ============================================================

CREATE TABLE IF NOT EXISTS ai_model_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ai_id           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK(channel IN ('relay', 'official')),
    model_name      TEXT NOT NULL,
    max_tokens      INTEGER NOT NULL DEFAULT 4096,
    timeout_seconds INTEGER NOT NULL DEFAULT 45,
    is_active       INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT DEFAULT 'unknown',
    last_latency_ms INTEGER,
    last_check_at   TEXT,
    config_extra    TEXT
);

-- 8 条模型定义（真相源为 settings.AI_MODELS，启动时由 model_registry 同步覆盖）
INSERT OR IGNORE INTO ai_model_registry
    (ai_id, display_name, channel, model_name, max_tokens, timeout_seconds)
VALUES
    ('gemini',   'Gemini',       'relay',    'gemini-2.0-flash',   4096, 45),
    ('openai',   'OpenAI GPT',   'relay',    'gpt-4o',             4096, 45),
    ('claude',   'Claude',       'relay',    'claude-3.5-sonnet',  4096, 45),
    ('grok',     'Grok',         'relay',    'grok-2',             4096, 45),
    ('deepseek', 'DeepSeek',     'official', 'deepseek-chat',      4096, 45),
    ('zhipu',    '智谱清言',      'official', 'glm-4-flash',        4096, 45),
    ('wenxin',   '文心一言',      'official', 'ernie-4.0-8k',       4096, 45),
    ('kimi',     'Kimi',         'official', 'moonshot-v1-8k',     4096, 45);

-- ============================================================
-- Section 3: 功能一 · 文档智能 Prompt 生成
-- ============================================================

CREATE TABLE IF NOT EXISTS doc_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    doc_type        TEXT NOT NULL CHECK(doc_type IN ('pdf', 'docx', 'txt', 'md')),
    file_size       INTEGER NOT NULL,
    file_path       TEXT NOT NULL,
    char_count      INTEGER DEFAULT 0,
    raw_text        TEXT,
    summary         TEXT,
    user_need       TEXT,
    final_prompt    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'done', 'failed', 'saved')),
    error_message   TEXT,
    total_duration_ms INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_doc_tasks_status ON doc_tasks(status);
CREATE INDEX IF NOT EXISTS idx_doc_tasks_created ON doc_tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS doc_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER NOT NULL REFERENCES doc_tasks(id) ON DELETE CASCADE,
    step_number     INTEGER NOT NULL CHECK(step_number IN (1, 2, 3)),
    role            TEXT NOT NULL CHECK(role IN ('planner', 'optimizer', 'finalizer')),
    model           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    input_prompt    TEXT,
    output_content  TEXT,
    output_preview  TEXT,
    duration_ms     INTEGER,
    error_message   TEXT,
    started_at      TEXT,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_doc_steps_task ON doc_steps(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_steps_unique ON doc_steps(task_id, step_number);

-- ============================================================
-- Section 4: 功能二 · 多 AI 讨论 + 裁判监管（核心变更区）
-- ============================================================

-- 4.1 discussions（讨论主表）
CREATE TABLE IF NOT EXISTS discussions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    topic                TEXT NOT NULL,
    mode                 TEXT NOT NULL DEFAULT 'free' CHECK(mode IN ('free', 'structured')),
    status               TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created', 'running', 'paused', 'done', 'terminated', 'failed')),
    -- pause_timeout：暂停超时自动终止（区别于 user_manual 用户手动终止）
    end_reason           TEXT CHECK(end_reason IN ('consensus', 'diminishing_returns', 'max_rounds', 'user_manual', 'pause_timeout', 'error')),
    total_rounds         INTEGER NOT NULL DEFAULT 3 CHECK(total_rounds BETWEEN 1 AND 5),
    current_round        INTEGER NOT NULL DEFAULT 0,
    rounds_completed     INTEGER NOT NULL DEFAULT 0,
    max_duration_seconds INTEGER DEFAULT 300,
    total_duration_ms    INTEGER,
    judge_summary        TEXT,
    action_plan          TEXT,
    error_message        TEXT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    started_at           TEXT,
    paused_at            TEXT,
    resumed_at           TEXT,
    completed_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_discussions_status ON discussions(status);
CREATE INDEX IF NOT EXISTS idx_discussions_created ON discussions(created_at DESC);

-- 4.2 discussion_participants（讨论参与者表）
-- 数据来源：create_discussion 时动态插入 11 条（8 discussant + 3 judge）
-- 不使用 init.sql 静态插入（受外键约束限制，且每场讨论参与者独立）
CREATE TABLE IF NOT EXISTS discussion_participants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    ai_id           TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK(channel IN ('relay', 'official')),
    role            TEXT NOT NULL DEFAULT 'discussant' CHECK(role IN ('discussant', 'judge')),
    judge_type      TEXT CHECK(judge_type IN ('compliance', 'order', 'evaluation')),
    is_banned       INTEGER NOT NULL DEFAULT 0,
    ban_start_round INTEGER,
    ban_end_round   INTEGER,
    total_score     REAL DEFAULT 0.0,
    final_rank      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_dp_discussion ON discussion_participants(discussion_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dp_unique
    ON discussion_participants(discussion_id, ai_id, role);

-- 4.3 discussion_messages（讨论消息表）
CREATE TABLE IF NOT EXISTS discussion_messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id       INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    round               INTEGER NOT NULL,
    message_order       INTEGER NOT NULL,
    ai_id               TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    content             TEXT NOT NULL,
    word_count          INTEGER DEFAULT 0,
    compliance_status   TEXT NOT NULL DEFAULT 'passed' CHECK(compliance_status IN ('passed', 'warning', 'violation', 'banned')),
    compliance_reason   TEXT,
    violation_type      TEXT,
    weighted_total      REAL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_dm_discussion ON discussion_messages(discussion_id);
CREATE INDEX IF NOT EXISTS idx_dm_round ON discussion_messages(discussion_id, round);
CREATE INDEX IF NOT EXISTS idx_dm_ai ON discussion_messages(discussion_id, ai_id);

-- 4.4 round_scores（每轮评分表 · 宽表）
CREATE TABLE IF NOT EXISTS round_scores (
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

CREATE INDEX IF NOT EXISTS idx_rs_disc_round ON round_scores(discussion_id, round);
CREATE INDEX IF NOT EXISTS idx_rs_ai ON round_scores(ai_id);

-- 4.5 discussion_bans（禁言记录表 · 事件流式 + is_active）
CREATE TABLE IF NOT EXISTS discussion_bans (
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
    -- seat_id: 禁言作用在哪个席位（discussion_participants.id）。
    -- 同一模型占多个席位时，解禁必须按席位匹配，否则会误清另一席的禁言；
    -- 老记录为 NULL，表示「按模型整体」的旧行为。
    seat_id         INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_db_discussion ON discussion_bans(discussion_id);
CREATE INDEX IF NOT EXISTS idx_db_active ON discussion_bans(discussion_id, is_active);

-- 4.6 discussion_judge_logs（裁判操作日志表）
CREATE TABLE IF NOT EXISTS discussion_judge_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    judge_type      TEXT NOT NULL CHECK(judge_type IN ('compliance', 'order', 'evaluation')),
    judge_model     TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    target_ai_id    TEXT,
    detail          TEXT,
    round           INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_djl_discussion ON discussion_judge_logs(discussion_id);

-- 4.7 discussion_round_summaries（轮次总结表）
CREATE TABLE IF NOT EXISTS discussion_round_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id   INTEGER NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    round           INTEGER NOT NULL,
    messages_count  INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    summary_text    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_drs_unique
    ON discussion_round_summaries(discussion_id, round);

-- ============================================================
-- Section 5: 功能三 · AI 三级文章生成
-- ============================================================

CREATE TABLE IF NOT EXISTS articles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    topic               TEXT NOT NULL,
    genre               TEXT NOT NULL DEFAULT 'general' CHECK(genre IN ('tech_blog', 'business', 'academic', 'essay', 'news', 'tutorial', 'general')),
    target_word_count   INTEGER DEFAULT 1500,
    style               TEXT,
    audience            TEXT,
    additional_requirements TEXT,
    status              TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created', 'outlining', 'outline_ready', 'writing', 'revising', 'done', 'failed')),
    final_word_count    INTEGER,
    total_duration_ms   INTEGER,
    error_message       TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at DESC);

CREATE TABLE IF NOT EXISTS article_stages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    stage           TEXT NOT NULL CHECK(stage IN ('outline', 'draft', 'revision')),
    role            TEXT NOT NULL CHECK(role IN ('outliner', 'writer', 'reviewer')),
    model           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'done', 'failed')),
    input_content   TEXT,
    output_content  TEXT,
    word_count      INTEGER DEFAULT 0,
    revision_notes  TEXT,
    duration_ms     INTEGER,
    error_message   TEXT,
    started_at      TEXT,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_as_article ON article_stages(article_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_as_unique ON article_stages(article_id, stage);

-- ============================================================
-- Section 6: 公共审计表
-- ============================================================

CREATE TABLE IF NOT EXISTS ai_call_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ai_id           TEXT NOT NULL,
    model           TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK(channel IN ('relay', 'official')),
    task_type       TEXT NOT NULL CHECK(task_type IN ('doc_process', 'discussion', 'article_gen', 'judge', 'system_check')),
    task_id         INTEGER,
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    status          TEXT NOT NULL CHECK(status IN ('success', 'failed', 'timeout', 'skipped')),
    error_message   TEXT,
    request_payload TEXT,
    response_preview TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_acl_ai ON ai_call_logs(ai_id);
CREATE INDEX IF NOT EXISTS idx_acl_task ON ai_call_logs(task_type, task_id);
CREATE INDEX IF NOT EXISTS idx_acl_created ON ai_call_logs(created_at DESC);

-- 6.2 service_runs（服务运行日记 · 累计运行时长）
-- 每次启动一段：boot_at → shutdown_at，累计相加才是平台真实服役时长
-- （/health 的 uptime_seconds 只反映当前进程，重启即归零）
CREATE TABLE IF NOT EXISTS service_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    boot_at           TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    shutdown_at       TEXT,
    -- app: 应用自己记的；journal: 由 systemd 日志回填的历史段
    source            TEXT NOT NULL DEFAULT 'app',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_sr_boot ON service_runs(boot_at);
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
    ai_id TEXT,                               -- 会话级模型：非空=该模型直连（不套模式提示词）
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

-- ---------- 8. 记忆库（裁判总结 / 讨论总结的持久化与检索） ----------
-- memory_type: judge（裁判总结）/ discussion（讨论者13部分总结）/ frame / manual
-- sections_json: 裁判总结四节结构化字段（最终结论/各方贡献评估/争议点裁决/后续建议）
-- 说明：init.sql 只在首次建库执行；已存在的库由 memory_repo.ensure_table_sync() 补建
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type   TEXT NOT NULL CHECK(memory_type IN ('judge', 'discussion', 'frame', 'manual')),
    topic         TEXT,
    content       TEXT NOT NULL,
    sections_json TEXT,
    source_type   TEXT,
    source_id     INTEGER,
    judge_model   TEXT,
    ai_id         TEXT,
    display_name  TEXT,
    char_count    INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_type    ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mem_source  ON memories(source_type, source_id);

-- ---------- 9. 统一评分记录（最终积分榜 / 质量检测分） ----------
-- feature: discussion（讨论终局评价裁判打分）/ article（文章质检）/ doc（文档质检）
-- task_id: discussion_id / article_id / doc task_id
-- 三个功能的最终分落同一张表，才能跨任务聚合出「平时分」，供用户讨论前选人参考
-- 说明：init.sql 只在首次建库执行；已存在的库由 score_repo.ensure_tables_sync() 补建
CREATE TABLE IF NOT EXISTS ai_score_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    feature      TEXT NOT NULL,
    task_id      INTEGER NOT NULL,
    ai_id        TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT '',      -- discussant / outliner / writer / reviewer ...
    score        REAL NOT NULL DEFAULT 0,       -- 0-100 最终分
    grade        TEXT NOT NULL DEFAULT '',      -- S / A / B / C
    comment      TEXT NOT NULL DEFAULT '',      -- 一句话评价
    scored_by    TEXT NOT NULL DEFAULT '',      -- 打分者 ai_id
    overall      REAL NOT NULL DEFAULT 0,       -- 成品总评分（质检员给出；讨论为 0）
    summary      TEXT NOT NULL DEFAULT '',      -- 质检总结（同任务各条冗余存同一份）
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_aisr_task ON ai_score_records(feature, task_id);
CREATE INDEX IF NOT EXISTS idx_aisr_ai   ON ai_score_records(ai_id);
