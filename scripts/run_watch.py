# -*- coding: utf-8 -*-
"""kt_watch 를 세션과 분리(detached)시켜 기동한다.

PowerShell/cmd 인자 전달 시 한글 방 이름이 시스템 코드페이지에서 깨지는 문제를
피하려고, 방 이름을 파이썬 안에서 조립한다.
"""
import os
import subprocess
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PYW = PY.replace("python.exe", "pythonw.exe") if PY.endswith("python.exe") else PY


def running():
    """이미 kt_watch 가 돌고 있으면 그 PID 목록을 돌려준다.

    ★ wmic 는 이 환경에서 WinError 2 로 실패한다(실측). 실패하면 '없음'으로
      오판해 워처를 계속 새로 띄웠고, 그 결과 3개가 동시에 8초마다 폴링하며
      포커스를 서로 뺏어 읽기 오염이 발생했다. PowerShell CIM 으로 조회한다.
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*kt_watch.py*' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            stderr=subprocess.DEVNULL, timeout=25,
        ).decode("utf-8", "replace")
    except Exception:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip().isdigit()]


def main():
    # 방 이름은 인자 또는 환경변수로 받는다 — 실제 방 이름을 코드에 하드코딩하지 않는다.
    #   python run_watch.py --room "방이름" [--interval 20]
    import argparse as _ap
    ap = _ap.ArgumentParser(description="kt_watch 단일 인스턴스 기동")
    ap.add_argument("--room", default=os.environ.get("KT_ROOM", ""), help="감시할 방 이름")
    ap.add_argument("--interval", default="20", help="폴링 주기(초)")
    ap.add_argument("--me", default=os.environ.get("KT_ME", ""), help="내 닉네임")
    ap.add_argument("--notify-only", action="store_true", help="답장 없이 알림만 적재")
    a = ap.parse_args()
    if not a.room:
        print("ERROR: --room 또는 KT_ROOM 환경변수로 방 이름을 지정하세요")
        return 2

    pids = running()
    if pids:
        print(f"STOPPING old pid={','.join(pids)}")
        for p in pids:
            subprocess.run(["taskkill", "/PID", p, "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)

    args = [
        PYW, os.path.join(SCRIPTS, "kt_watch.py"),
        "--room", a.room,
        "--interval", a.interval,
        "--window", "30",
        "--reset-state",
    ]
    if a.me:
        args += ["--me", a.me]
    if a.notify_only:
        args += ["--notify-only"]
    else:
        args += ["--yes"]  # 봇이 실제로 답한다

    DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(
        args,
        cwd=SCRIPTS,
        creationflags=DETACHED,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(5)
    if p.poll() is None:
        print(f"STARTED pid={p.pid}")
        return 0

    print(f"DIED pid={p.pid} rc={p.returncode}")
    log = os.path.join(SCRIPTS, "_watch.log")
    if os.path.exists(log):
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            print("--- log tail ---")
            print("".join(f.readlines()[-15:]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
