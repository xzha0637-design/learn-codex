"""内核沙箱（s05）：macOS Seatbelt 包住 shell 命令。

生产级：策略从 `(deny default)` 起步，读放开、写仅限可写根、**通篇无 network-* 允许 → 默认禁网**。
非 macOS 退回无沙箱执行（教学占位；真 Codex 在 Linux 用 Landlock+seccomp）。
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

IS_MACOS = platform.system() == "Darwin"
SEATBELT = "/usr/bin/sandbox-exec"


def build_seatbelt_policy(n_writable_roots: int) -> str:
    lines = [
        "(version 1)",
        "(deny default)",                          # 默认全拒
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow file-read*)",                       # 读放开
        '(allow file-write-data (literal "/dev/null"))',
    ]
    for i in range(n_writable_roots):              # 写仅限可写根
        lines.append(f'(allow file-write* (subpath (param "WRITABLE_ROOT_{i}")))')
    return "\n".join(lines)                          # 注意：通篇无 network-* → 默认禁网


def run_sandboxed(command: str, writable_roots: list[str], cwd: Path) -> str:
    roots = [str(Path(r).resolve()) for r in writable_roots]
    if not IS_MACOS:
        return _raw_run(["/bin/sh", "-c", command], cwd) + "  [non-macOS: 无沙箱]"
    args = [SEATBELT, "-p", build_seatbelt_policy(len(roots))]
    for i, root in enumerate(roots):
        args += ["-D", f"WRITABLE_ROOT_{i}={root}"]
    args += ["--", "/bin/sh", "-c", command]
    return _raw_run(args, cwd)


def _raw_run(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out if out else "(no output)")
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"
