-- v1.10.0 升级 DDL：远程 SSH/SFTP 连接配置
-- 幂等；只建表与索引，不触碰任何业务数据。
-- 服务端只存档配置，不建立 SSH 连接、不做 TCP 转发。

CREATE TABLE IF NOT EXISTS ssh_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS ssh_connections (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    ssh_group_id         INTEGER NOT NULL DEFAULT 0,
    name                 TEXT    NOT NULL,
    host                 TEXT    NOT NULL,
    port                 INTEGER NOT NULL DEFAULT 22,
    username             TEXT    NOT NULL DEFAULT '',
    auth_type            TEXT    NOT NULL DEFAULT 'password',
    password             TEXT    NOT NULL DEFAULT '',
    private_key          TEXT    NOT NULL DEFAULT '',
    passphrase           TEXT    NOT NULL DEFAULT '',
    jump_connection_id   INTEGER NOT NULL DEFAULT 0,
    terminal_type        TEXT    NOT NULL DEFAULT 'xterm-256color',
    charset              TEXT    NOT NULL DEFAULT 'UTF-8',
    init_command         TEXT    NOT NULL DEFAULT '',
    host_key_fingerprint TEXT    NOT NULL DEFAULT '',
    last_connected_at    TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL,
    deleted_at           TEXT
);

CREATE INDEX IF NOT EXISTS ssh_groups_user_id_idx ON ssh_groups(user_id);
CREATE INDEX IF NOT EXISTS ssh_connections_user_id_idx ON ssh_connections(user_id);
CREATE INDEX IF NOT EXISTS ssh_connections_group_id_idx ON ssh_connections(ssh_group_id);
CREATE INDEX IF NOT EXISTS ssh_connections_jump_idx ON ssh_connections(jump_connection_id);
