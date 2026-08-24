SET NAMES utf8mb4;
CREATE DATABASE meta DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON meta.* TO 'didilili'@'%';

USE meta;

DROP TABLE IF EXISTS table_info;
CREATE TABLE table_info
(
    id          VARCHAR(64) PRIMARY KEY COMMENT '表编号',
    name        VARCHAR(128) COMMENT '表名称',
    role        VARCHAR(32) COMMENT '表类型(fact/dim)',
    description TEXT COMMENT '表描述'
);



DROP TABLE IF EXISTS column_info;
CREATE TABLE column_info
(
    id          VARCHAR(64) PRIMARY KEY COMMENT '列编号',
    name        VARCHAR(128) COMMENT '列名称',
    type        VARCHAR(64) COMMENT '数据类型',
    role        VARCHAR(32) COMMENT '列类型(primary_key,foreign_key,measure,dimension)',
    examples    JSON COMMENT '数据示例',
    description TEXT COMMENT '列描述',
    alias       JSON COMMENT '列别名',
    table_id    VARCHAR(64) COMMENT '所属表编号'
);

DROP TABLE IF EXISTS metric_info;
CREATE TABLE metric_info
(
    id               VARCHAR(64) PRIMARY KEY COMMENT '指标编码',
    name             VARCHAR(128) COMMENT '指标名称',
    description      TEXT COMMENT '指标描述',
    relevant_columns JSON COMMENT '关联的列',
    alias            JSON COMMENT '指标别名'
);


DROP TABLE IF EXISTS column_metric;
CREATE TABLE column_metric
(
    column_id VARCHAR(64) COMMENT '列编号',
    metric_id VARCHAR(64) COMMENT '指标编号',
    PRIMARY KEY (column_id, metric_id)
);

DROP TABLE IF EXISTS chat_session;
CREATE TABLE chat_session
(
    id         VARCHAR(64) PRIMARY KEY COMMENT '会话id(UUID字符串)',
    title      VARCHAR(255) COMMENT '会话标题(首条用户消息截断30字)',
    created_at BIGINT COMMENT '创建时间(epoch毫秒)',
    updated_at BIGINT COMMENT '更新时间(epoch毫秒)'
);

DROP TABLE IF EXISTS chat_message;
CREATE TABLE chat_message
(
    id             VARCHAR(64) PRIMARY KEY COMMENT '消息id(UUID字符串)',
    session_id     VARCHAR(64) COMMENT '所属会话id',
    role           VARCHAR(16) COMMENT 'user/assistant',
    content        TEXT COMMENT '文本内容(assistant为摘要文本)',
    steps          JSON COMMENT 'assistant执行步骤进度',
    `sql`          TEXT COMMENT '最终执行的SQL',
    result_summary JSON COMMENT '结果摘要(前10行样例)',
    error          TEXT COMMENT '错误信息',
    audit_log_id   BIGINT COMMENT '关联的写操作审计记录ID(有则消息左侧可回滚)',
    created_at     BIGINT COMMENT '创建时间(epoch毫秒)',
    INDEX idx_msg_session_created (session_id, created_at)
);

DROP TABLE IF EXISTS write_audit_log;
CREATE TABLE write_audit_log
(
    log_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '审计记录ID',
    session_id   VARCHAR(64) COMMENT '会话ID(可能为空，兼容无会话兜底场景)',
    seq          INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '同一会话内的操作序号(单调递增,用于逆序回滚)',
    op_type      VARCHAR(16) NOT NULL COMMENT '操作类型(INSERT/UPDATE/DELETE)',
    table_name   VARCHAR(128) NOT NULL COMMENT '受影响表',
    sql_text     TEXT NOT NULL COMMENT '实际执行的SQL',
    before_data  JSON NULL COMMENT '执行前的受影响行快照(回滚依据)',
    row_count    INT NOT NULL DEFAULT 0 COMMENT '影响行数',
    status       VARCHAR(16) NOT NULL DEFAULT 'committed' COMMENT 'committed=已执行 / rolled_back=已回滚',
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间',
    PRIMARY KEY (log_id),
    KEY idx_audit_session_seq (session_id, seq),
    KEY idx_audit_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='写操作审计日志(支撑Time-Travel回滚)';
