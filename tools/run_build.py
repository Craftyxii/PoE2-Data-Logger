"""Run the Windows batch build and forward its exit status plus bounded CI failure annotations.

The subprocess starts at module load; this wrapper does not build on Linux."""

from collections import deque
import os
import subprocess


tail = deque(maxlen=80)
process = subprocess.Popen(["cmd", "/c", "Build.bat"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace")
for line in process.stdout:
    print(line, end="", flush=True)
    tail.append(line)
code = process.wait()
if code and os.environ.get("GITHUB_ACTIONS") == "true":
    detail = "".join(tail)[-12000:]
    for offset in range(0, len(detail), 3000):
        part = detail[offset:offset + 3000]
        part = part.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Windows build {offset // 3000 + 1}::{part}")
raise SystemExit(code)
