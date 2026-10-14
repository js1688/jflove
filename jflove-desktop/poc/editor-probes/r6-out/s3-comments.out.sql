-- 顶部行注释
SELECT
  a, -- 行内注释
  /* 块注释 */
  b
FROM
  t
  /* 跨
  行块注释 */
WHERE
  a = 1;

-- 末尾行注释