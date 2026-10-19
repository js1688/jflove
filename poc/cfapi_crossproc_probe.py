"""CfAPI 跨进程枚举探针 v3（诊断用，非交付代码）

真实场景是「资源管理器（另一个进程）枚举挂载目录，同步进程负责应答回调」。
同进程枚举会被平台特殊对待，因此本探针把枚举放到子进程里执行：

    python poc/cfapi_enum_probe.py            # 当前实现（含 FETCH_PLACEHOLDERS）
    python poc/cfapi_enum_probe.py --legacy   # 只注册 FETCH_DATA（模拟旧版行为）
    python poc/cfapi_enum_probe.py --dir D:\\testa   # 针对已存在的挂载根

判据：子进程枚举耗时 < 2s = 正常；≈60s 且报 0x800701AA = 复现用户截图错误。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jflove-desktop"))

from src.utils import mount_cfapi as mc  # noqa: E402

_calls: list[str] = []


def _wrap(name: str):
    orig = getattr(mc.WinCfapiMount, name)

    def patched(self, info_ptr, params_ptr):
        _calls.append(name)
        print(f"[probe] callback {name}", flush=True)
        return orig(self, info_ptr, params_ptr)

    return patched


for _n in ("_on_fetch_placeholders", "_on_fetch_data", "_on_delete", "_on_rename",
           "_on_dehydrate"):
    setattr(mc.WinCfapiMount, _n, _wrap(_n))


def enum_in_child(path: str, timeout: int = 80) -> None:
    code = (
        "import os,sys,time\n"
        "t=time.time()\n"
        "try:\n"
        f"    items=os.listdir(r'{path}')\n"
        "    print('CHILD_OK %.2fs %r' % (time.time()-t, items))\n"
        "except OSError as e:\n"
        "    print('CHILD_FAIL %.2fs winerror=%s %s' % (time.time()-t, getattr(e,'winerror',None), e))\n"
    )
    started = time.time()
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=timeout)
        print(f"[probe] child = {out.stdout.strip()} {out.stderr.strip()}",
              flush=True)
    except subprocess.TimeoutExpired:
        print(f"[probe] CHILD_TIMEOUT after {time.time() - started:.1f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="")
    ap.add_argument("--legacy", action="store_true")
    args = ap.parse_args()

    own_dir = not args.dir
    root = args.dir or tempfile.mkdtemp(prefix="cfapi_xproc_", dir="D:\\")
    os.makedirs(root, exist_ok=True)
    print(f"[probe] root = {root} legacy={args.legacy}", flush=True)

    mount = mc.WinCfapiMount("probe-" + os.path.basename(root), root,
                             fetch=lambda rel: b"abcd")
    if args.legacy:
        # 模拟旧版：只注册 FETCH_DATA（没有 FETCH_PLACEHOLDERS 应答）
        mount._registrations = (mc._CF_CALLBACK_REGISTRATION * 2)(
            mc._CF_CALLBACK_REGISTRATION(
                mc.CF_CALLBACK_TYPE_FETCH_DATA, mount._cb_fetch,
            ),
            mc._CF_CALLBACK_REGISTRATION(mc.CF_CALLBACK_TYPE_NONE,
                                         mc._CF_CALLBACK()),
        )

    if not mount.start():
        print("[probe] START_FAILED", flush=True)
        return 2

    print(f"[probe] root attrs = 0x{os.stat(root).st_file_attributes:08X}", flush=True)
    if own_dir:
        for i in range(2):
            print(f"[probe] add_file p{i}.bin -> "
                  f"{mount.add_file(f'p{i}.bin', 4, time.time() - 60)}", flush=True)
        os.makedirs(os.path.join(root, "sub"), exist_ok=True)
        with open(os.path.join(root, "sub", "c.txt"), "wb") as f:
            f.write(b"abcd")

    print("[probe] idle 5s ...", flush=True)
    time.sleep(5)

    enum_in_child(root)
    enum_in_child(os.path.join(root, "sub"))

    print(f"[probe] callbacks = {_calls}", flush=True)
    mount.stop()
    if own_dir:
        try:
            shutil.rmtree(root)
        except OSError as e:
            print(f"[probe] cleanup warning: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
