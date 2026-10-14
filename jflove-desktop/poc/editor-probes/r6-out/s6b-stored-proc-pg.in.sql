-- 存储过程示例（PostgreSQL）
CREATE OR REPLACE FUNCTION add_one(x int) RETURNS int AS $$
BEGIN
  RETURN x + 1;
END;
$$ LANGUAGE plpgsql;
INSERT INTO t (a) VALUES (1);
UPDATE t SET a = a + 1;