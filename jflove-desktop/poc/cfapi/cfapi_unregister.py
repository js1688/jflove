"""
解除 CfAPI 注册（用于删除被挂载的目录）

背景：v1.7.0 桌面端在启用挂载点时会 `CfRegisterSyncRoot`；如果应用异常退出
没有注销，该目录会一直被视为「云同步根」，导致资源管理器进不去、也删不掉。

用法（先退出 JFLove 应用，再执行）：

    python jflove-desktop/poc/cfapi/cfapi_unregister.py "D:\\JFLove\\照片库"

退出码：0 = 已解除注册（或本来就没注册）；1 = 解除失败（打印 HRESULT）。
解除后即可正常删除目录。
"""

from __future__ import annotations

import ctypes
import os
import sys


def _hr(hr: int) -> str:
    return f"0x{hr & 0xFFFFFFFF:08X}"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python jflove-desktop/poc/cfapi/cfapi_unregister.py <挂载目录>")
        return 2

    path = os.path.abspath(sys.argv[1])
    if not os.path.isdir(path):
        print(f"目录不存在: {path}")
        return 1

    cldapi = ctypes.WinDLL("cldapi", use_last_error=True)
    cldapi.CfUnregisterSyncRoot.argtypes = [ctypes.c_wchar_p]
    cldapi.CfUnregisterSyncRoot.restype = ctypes.c_long

    hr = cldapi.CfUnregisterSyncRoot(path)
    if hr >= 0:
        print(f"[OK] 已解除 CfAPI 注册: {path}")
        print("现在可以删除该目录了；如果仍被占用，重启资源管理器或重启系统后再删。")
        return 0

    print(f"[FAIL] CfUnregisterSyncRoot hr={_hr(hr)}")
    if (hr & 0xFFFFFFFF) == 0x8007017B:
        print("提示：仍有进程连接着该 sync root —— 请先完全退出 JFLove（含托盘）再试。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
