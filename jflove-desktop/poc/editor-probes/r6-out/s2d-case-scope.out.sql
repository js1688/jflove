SELECT
  Foo,
  'Select Bar' AS Lit,
  count(*)
FROM
  MyTable
WHERE
  name = 'upper CASE';