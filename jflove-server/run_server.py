"""
JFLove 服务端容器化启动器（不修改业务源代码）

通过 JFLOVE_DB_PATH 环境变量覆盖 src.config.settings.DB_PATH，
在 import src.main 之前生效，从而避免 src.models.database 的
`from src.config.settings import DB_PATH` 拿到旧值。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 /app 成为根目录，能 import src.*
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 1) 先 import settings 模块，但不要 from-import 任何符号
import src.config.settings as _settings  # noqa: E402

# 2) 用环境变量覆盖 DB_PATH（此刻 src.models.database 还没被加载）
_env_db = os.environ.get("JFLOVE_DB_PATH")
if _env_db:
    Path(_env_db).parent.mkdir(parents=True, exist_ok=True)
    _settings.DB_PATH = _env_db
    print(f"[run_server] DB_PATH 已通过环境变量覆盖为: {_env_db}", flush=True)

# 3) 再 import 主应用 + 启动 uvicorn
import uvicorn  # noqa: E402
from src.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=_settings.HOST,
        port=_settings.PORT,
        log_level="info",
    )
