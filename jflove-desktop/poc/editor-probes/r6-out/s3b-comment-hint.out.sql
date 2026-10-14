SELECT
  /*+ INDEX(t idx1) */
  a
FROM
  t
WHERE
  a = 1;