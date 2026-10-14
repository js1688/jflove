# R-6 实测原始输出（xml-formatter 3.7.0 / sql-formatter 15.9.0 / Node v24.18.0）

每个用例的完整输入输出原文见 `r6-out/<用例名>.in.*` 与 `.out.*`。

| 用例 | 组 | 标题 | 抛错 | 输入 | 输出 |
|---|---|---|---|---|---|
| x1-comment | XML | 含注释 <!-- ... -->：格式化后是否保留 | — | `<root><!--keep-me--><a>1</a><!-- second comment --><b>2</b></root>` | `<root>\n    <!--keep-me-->\n    <a>\n        1\n    </a>\n    <!-- second comment -->\n    <b>\n        2\n    </b>\n</root>` |
| x2-cdata | XML | CDATA：是否保留 + CDATA 内空白是否被改动 | — | `<root><a><![CDATA[  x < y\n   	 spaced  ]]></a></root>` | `<root>\n    <a>\n        <![CDATA[  x < y\n   	 spaced  ]]>\n    </a>\n</root>` |
| x2b-ac33-sample | XML | AC-33 原文样例：<root><!--c--><a><![CDATA[ x < y ]]></a></root> | — | `<root><!--c--><a><![CDATA[ x < y ]]></a></root>` | `<root>\n    <!--c-->\n    <a>\n        <![CDATA[ x < y ]]>\n    </a>\n</root>` |
| x3-selfclosing | XML | 自闭合标签 <br/> 与 <img src="x" /> 是否变成 <br></br> | — | `<r><br/><img src="x" /><hr /></r>` | `<r>\n    <br/>\n    <img src="x"/>\n    <hr/>\n</r>` |
| x3b-empty-tag | XML | 空元素 <a></a> 是否被改成自闭合 | — | `<r><a></a><b> </b><c/></r>` | `<r>\n    <a></a>\n    <b>\n    </b>\n    <c/>\n</r>` |
| x4-single-quote | XML | 单引号属性 attr='v' 是否被改写 | — | `<r a='1' b='two words'><c d='3'/></r>` | `<r a="1" b="two words">\n    <c d="3"/>\n</r>` |
| x4b-unquoted-attr | XML | 无引号属性值 b=2（非法 XML）是否报错 / 被改写 | — | `<r a=2 b=x><c/></r>` | `<r a="2" b="x">\n    <c/>\n</r>` |
| x4c-attr-entity | XML | 属性含实体 &amp; 与内嵌引号 | — | `<r a="x&amp;y" b='it"quote'/>` | `<r a="x&amp;y" b="it&quot;quote"/>` |
| x5-text-whitespace | XML | 文本节点内空白 <a>hello   world</a> 是否被压缩 | — | `<r><a>hello   world</a><b>  lead and trail  </b></r>` | `<r>\n    <a>\n        hello   world\n    </a>\n    <b>\n        lead and trail\n    </b>\n</r>` |
| x5b-xml-space | XML | xml:space="preserve" 是否被尊重（文档宣称支持） | — | `<r><p xml:space="preserve">This is <b>some</b> content.</p></r>` | `<r>\n    <p xml:space="preserve">This is <b>some</b> content.</p>\n</r>` |
| x5c-collapse-content | XML | collapseContent:true 对文本节点空白的影响 | — | `<r><a>hello   world</a><b>  lead  </b></r>` | `<r>\n  <a>hello   world</a>\n  <b>  lead  </b>\n</r>` |
| x6-minify-basic | XML | 压缩模式 xmlFormat.minify()：元素间空白 | — | `<r>\n  <a>1</a>\n  <b>2</b>\n</r>` | `<r><a>1</a><b>2</b></r>` |
| x6b-minify-space-textnode | XML | 压缩：<a> </a>（有意义的一个空格文本节点）是否被吃成 <a></a> | — | `<r>\n  <a> </a>\n  <b>x</b>\n</r>` | `<r><a></a><b>x</b></r>` |
| x6c-minify-textnode-inner | XML | 压缩：文本节点内部空白 "hello   world"（非元素间）是否被改 | — | `<r>\n  <a>hello   world</a>\n</r>` | `<r><a>hello   world</a></r>` |
| x6d-minify-collapse | XML | 压缩 + collapseContent:true（README 示例看到 "some<b>content" 空格消失） | — | `<r><p>This is <b>some</b> content.</p></r>` | `<r><p>This is <b>some</b> content.</p></r>` |
| x6e-minify-ac33 | XML | AC-33 要求：压缩后仍可被标准 XML 解析器解析，且文本节点内容不变 | — | `<root><!--c--><a><![CDATA[ x < y ]]></a><b>hello   world</b></root>` | `<root><!--c--><a><![CDATA[ x < y ]]></a><b>hello   world</b></root>` |
| x7-invalid-missing-close | XML | 非法 XML（缺少闭合标签）是否抛异常 | — | `<root><a>1</root>` | `<root>\n    <a>\n        1\n    </a>\n</root>` |
| x7b-invalid-throwonfailure-false | XML | 非法 XML + throwOnFailure:false → 是否静默返回原文 | — | `<root><a>1</root>` | `<root>\n    <a>\n        1\n    </a>\n</root>` |
| x7c-invalid-strict | XML | 非法 XML + strictMode:true | Error | `<root><a>1</root>` | (无) |
| x7d-plain-text | XML | 完全不是 XML 的纯文本（应报错） | Error | `hello world\nnot xml at all` | (无) |
| x8-declaration | XML | 声明 <?xml version="1.0" encoding="UTF-8"?> 是否保留 | — | `<?xml version="1.0" encoding="UTF-8"?>\n<r><a>1</a></r>` | `<?xml version="1.0" encoding="UTF-8"?>\n<r>\n    <a>\n        1\n    </a>\n</r>` |
| x8b-bom | XML | BOM（\uFEFF）是否保留、是否重复 | — | `﻿<?xml version="1.0" encoding="UTF-8"?><r><a>1</a></r>` | `<?xml version="1.0" encoding="UTF-8"?>\n<r>\n    <a>\n        1\n    </a>\n</r>` |
| x8c-no-decl | XML | 无声明时是否被加上声明 | — | `<r><a>1</a></r>` | `<r>\n    <a>\n        1\n    </a>\n</r>` |
| x9-recommended | XML | 推荐参数 {indentation:"  ", lineSeparator:"\n", attributeQuotes:"double"} 下的格式化 | — | `<?xml version="1.0" encoding="UTF-8"?>\n<root><!--c--><a k='v'><![CDATA[ x < y ]]></a><br/><b>hello   world</b></root>` | `<?xml version="1.0" encoding="UTF-8"?>\n<root>\n  <!--c-->\n  <a k="v">\n    <![CDATA[ x < y ]]>\n  </a>\n  <br/>\n  <b>\n    hello   world\n  </b>\n</root>` |
| x10-default-linesep | XML | 默认 lineSeparator 是否为 \r\n（跨平台一致性） | — | `<r><a>1</a><b>2</b></r>` | `<r>\n    <a>\n        1\n    </a>\n    <b>\n        2\n    </b>\n</r>` |
| s2a-keywordCase-upper | SQL | keywordCase:'upper' | — | `select id, name from users where age > 18 and city = 'Beijing' order by id;` | `SELECT\n  id,\n  name\nFROM\n  users\nWHERE\n  age > 18\n  AND city = 'Beijing'\nORDER BY\n  id;` |
| s2b-keywordCase-lower | SQL | keywordCase:'lower' | — | `SELECT ID FROM USERS WHERE X = 1;` | `select\n  ID\nfrom\n  USERS\nwhere\n  X = 1;` |
| s2c-keywordCase-preserve | SQL | keywordCase:'preserve' | — | `select id, name from users where age > 18 and city = 'Beijing' order by id;` | `select\n  id,\n  name\nfrom\n  users\nwhere\n  age > 18\n  and city = 'Beijing'\norder by\n  id;` |
| s2d-case-scope | SQL | keywordCase 是否误改标识符 / 字符串字面量 / 函数名 | — | `select Foo, 'Select Bar' as Lit, count(*) from MyTable where name = 'upper CASE';` | `SELECT\n  Foo,\n  'Select Bar' AS Lit,\n  count(*)\nFROM\n  MyTable\nWHERE\n  name = 'upper CASE';` |
| s2e-identifierCase | SQL | identifierCase / dataTypeCase / functionCase 的独立效果 | — | `select Foo, count(*) from MyTable where x = 'y';` | `SELECT\n  foo,\n  count(*)\nFROM\n  mytable\nWHERE\n  x = 'y';` |
| s3-comments | SQL | 行注释 / 块注释 / 行内注释是否保留与位置漂移 | — | `-- 顶部行注释\nselect a, -- 行内注释\n       /* 块注释 */ b\nfrom t /* 跨\n   行块注释 */ where a = 1; -- 末尾行注释` | `-- 顶部行注释\nSELECT\n  a, -- 行内注释\n  /* 块注释 */\n  b\nFROM\n  t\n  /* 跨\n  行块注释 */\nWHERE\n  a = 1;\n\n-- 末尾行注释` |
| s3b-comment-hint | SQL | 优化器提示 /*+ ... */ 在 select 之后的位置 | — | `select /*+ INDEX(t idx1) */ a from t where a = 1;` | `SELECT\n  /*+ INDEX(t idx1) */\n  a\nFROM\n  t\nWHERE\n  a = 1;` |
| s3c-comment-only | SQL | 仅含注释的文本（无语句）→ 是否报错 | — | `-- just a comment\n/* and another */` | `-- just a comment\n/* and another */` |
| s4a-tabWidth-2 | SQL | tabWidth:2 | — | `select id, name from users where age > 18 and city = 'Beijing' order by id;` | `select\n  id,\n  name\nfrom\n  users\nwhere\n  age > 18\n  and city = 'Beijing'\norder by\n  id;` |
| s4b-tabWidth-8 | SQL | tabWidth:8 | — | `select id, name from users where age > 18 and city = 'Beijing' order by id;` | `select\n        id,\n        name\nfrom\n        users\nwhere\n        age > 18\n        and city = 'Beijing'\norder by\n        id;` |
| s4c-useTabs | SQL | useTabs:true 的实际字符（应是 \t） | — | `select id, name from users where age > 18 and city = 'Beijing' order by id;` | `select\n	id,\n	name\nfrom\n	users\nwhere\n	age > 18\n	and city = 'Beijing'\norder by\n	id;` |
| s4d-indentStyle | SQL | indentStyle:'tabularLeft' 与 'standard' 的差异 | — | `select a, b, c from t where a = 1 and b = 2;` | `select    a,\n          b,\n          c\nfrom      t\nwhere     a = 1\nand       b = 2;` |
| s4e-linesBetweenQueries | SQL | linesBetweenQueries 对多语句间距的影响 | — | `select 1; select 2;` | `select\n  1;\n\n\n\nselect\n  2;` |
| s5a-string-semicolon | SQL | 字符串里的分号 SELECT 'a;b' AS c FROM t | — | `SELECT 'a;b' AS c FROM t;` | `SELECT\n  'a;b' AS c\nFROM\n  t;` |
| s5b-string-keyword | SQL | 字符串里的关键字与注释形文本是否被误解析 | — | `SELECT 'select -- not a comment /* neither */ ; from' AS c FROM t;` | `SELECT\n  'select -- not a comment /* neither */ ; from' AS c\nFROM\n  t;` |
| s5c-quoted-identifier | SQL | 双引号标识符 / 反引号标识符里的关键字 | — | `SELECT "from" FROM `where` WHERE x = 'and';` | `SELECT\n  "from"\nFROM\n  `where`\nWHERE\n  x = 'and';` |
| s5d-dollar-quote | SQL | PostgreSQL 美元引用 $$ ... $$ 里的分号与关键字 | — | `SELECT $$a;b select$$ AS c FROM t;` | `SELECT\n  $$a;b select$$ AS c\nFROM\n  t;` |
| s5e-params | SQL | 参数占位符 :name / @p / ? 是否被改写 | Error | `select * from t where a = :name and b = @p and c = ?;` | (无) |
| s6a-multi-statement | SQL | 多语句（; 分隔）能否处理 | — | `select 1;\nselect 2;\ndelete from t where a=1;` | `select\n  1;\n\nselect\n  2;\n\ndelete from t\nwhere\n  a = 1;` |
| s6b-stored-proc-pg | SQL | PostgreSQL 存储过程（含 $$ 体与内部分号） | — | `-- 存储过程示例（PostgreSQL）\nCREATE OR REPLACE FUNCTION add_one(x int) RETURNS int AS $$\nBEGIN\n  RETURN x + 1;\nEND;\n$$ LANGUAGE plpgsql;\nINSERT INTO t (a) VALUES (1);\nUPDATE t SET a = a + 1;` | `-- 存储过程示例（PostgreSQL）\nCREATE OR REPLACE FUNCTION add_one (x int) RETURNS int AS $$\nBEGIN\n  RETURN x + 1;\nEND;\n$$ LANGUAGE plpgsql;\n\nINSERT INTO\n  t (a)\nVALUES\n  (1);\n\nUPDATE t\nSET\n  a = a + 1;` |
| s6c-stored-proc-mysql | SQL | MySQL 存储过程（DELIMITER 与 BEGIN...END） | — | `DELIMITER //\nCREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\nEND //\nDELIMITER ;` | `DELIMITER / /\nCREATE PROCEDURE p () BEGIN\nSELECT\n  1;\n\nEND / / DELIMITER;` |
| s6d-transactsql | SQL | T-SQL 的 GO 批处理分隔符 | — | `SELECT 1\nGO\nSELECT 2\nGO` | `SELECT\n  1\nGO\nSELECT\n  2\nGO` |
| s6e-missing-semicolon | SQL | 缺少结尾分号的单条语句 | — | `select a from t where b = 1` | `select\n  a\nfrom\n  t\nwhere\n  b = 1` |
| s7a-invalid-truncated | SQL | 非法 SQL：截断的语句 select from where | — | `select from where;` | `select\nfrom\nwhere;` |
| s7b-invalid-unbalanced | SQL | 非法 SQL：未闭合括号 / 引号 | Error | `select (a from t where b = 'unclosed;` | (无) |
| s7c-not-sql | SQL | 完全不是 SQL 的纯文本（XML 片段） | — | `<root><a>1</a></root>` | `< root > < a > 1 < / a > < / root >` |
| s7d-plain-words | SQL | 纯文字（中文散文）→ 是否抛错还是原样返回 | Error | `这是一段中文，不是 SQL 语句。` | (无) |
| s7e-bad-option | SQL | 非法语言取值 / 非法 keywordCase → 是否明确抛错 | Error | `select 1;` | (无) |
| s7f-bad-keywordcase | SQL | 非法 keywordCase 取值 | TypeError | `select 1;` | (无) |
| s1-dialects | SQL | 方言枚举与 20 个方言的实际可用性（共 20 个） | — | `select a, b from t where a = 1 order by b limit 10;` | `{\n  "languages": [\n    "bigquery",\n    "clickhouse",\n    "db2",\n    "db2i",\n    "duckdb",\n    "hive",\n    "mariadb",\n    "mysql",\n    "n1ql",\n    "plsql",\n    "postgresql",\n    "redshift",\n    "singlestored` |

## SQL 方言枚举（来自 node_modules/sql-formatter/dist/cjs/languages/）

```
bigquery, clickhouse, db2, db2i, duckdb, hive, mariadb, mysql, n1ql, plsql, postgresql, redshift, singlestoredb, snowflake, spark, sql, sqlite, tidb, transactsql, trino
```

| 方言 | 复杂语句是否成功 | 失败原因 |
|---|---|---|
| bigquery | ✅ | — |
| clickhouse | ✅ | — |
| db2 | ✅ | — |
| db2i | ✅ | — |
| duckdb | ✅ | — |
| hive | ✅ | — |
| mariadb | ✅ | — |
| mysql | ✅ | — |
| n1ql | ✅ | — |
| plsql | ✅ | — |
| postgresql | ✅ | — |
| redshift | ✅ | — |
| singlestoredb | ✅ | — |
| snowflake | ✅ | — |
| spark | ✅ | — |
| sql | ✅ | — |
| sqlite | ✅ | — |
| tidb | ✅ | — |
| transactsql | ✅ | — |
| trino | ✅ | — |