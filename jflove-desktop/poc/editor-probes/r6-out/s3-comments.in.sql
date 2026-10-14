-- 顶部行注释
select a, -- 行内注释
       /* 块注释 */ b
from t /* 跨
   行块注释 */ where a = 1; -- 末尾行注释