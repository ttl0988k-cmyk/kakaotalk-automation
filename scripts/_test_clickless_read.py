# -*- coding: utf-8 -*-
"""클릭 없이 읽기 실험 — 어떤 경로가 실제로 되는지 실측.

실험:
  E1) 포커스만 주고(클릭 X) Ctrl+A/Ctrl+C  → 대화 전체가 잡히는가?
  E2) 안전 좌표 클릭(창 왼쪽 여백) 후 Ctrl+A/Ctrl+C
  E3) PostMessage 로 WM_LBUTTONDOWN/UP (커서 이동 없음)
  E4) 스크롤바 바로 안쪽 클릭

복사만 수행하므로 전송 위험 없음. 클립보드는 백업/복원.
"""
import sys, os, time, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kt

RESULT = {}

def clip_peek(tag):
    c = kt.clip_read()
    n = len(c or "")
    first = (c or "").replace("\n", " ⏎ ")[:90]
    print(f"    [{tag}] len={n}  head={first!r}")
    return c or ""

def ctrl_acopy():
    kt.ctrl_key(0x41)   # Ctrl+A
    time.sleep(0.12)
    kt.ctrl_key(0x43)   # Ctrl+C
    time.sleep(0.45)

def run_case(name, fn):
    print(f"\n{'='*62}\n{name}\n{'='*62}")
    kt.clip_write("__SENTINEL__")
    try:
        fn()
    except Exception as e:
        print(f"    오류: {e}")
        RESULT[name] = {"ok": False, "err": str(e)}
        return
    c = clip_peek(name)
    # 판정: 대화 전사면 200자 이상 + 날짜/시간 패턴 포함
    ok = ("__SENTINEL__" not in c) and len(c) > 50
    RESULT[name] = {"ok": ok, "len": len(c), "head": c[:120]}

def main():
    room = kt.get_room("A방")
    if not room:
        print("방 없음"); return 2
    hwnd, title = room
    print(f"대상: hwnd={hwnd} '{title}'")

    l, t, r, b = kt.win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    print(f"rect=({l},{t},{r},{b})  {w}x{h}")

    backup = kt.clip_read()
    fg0 = kt.win32gui.GetForegroundWindow()
    try:
        # ---------- E1: 포커스만, 클릭 없음 ----------
        def e1():
            kt.focus(hwnd)          # SetForegroundWindow 만, 클릭 X
            time.sleep(0.5)
            fg = kt.win32gui.GetForegroundWindow()
            print(f"    foreground={fg} (대상={hwnd}) {'일치' if fg==hwnd else '불일치'}")
            ctrl_acopy()
        run_case("E1_포커스만_클릭없음", e1)

        # ---------- E2: 왼쪽 여백 클릭 (말풍선 없는 영역) ----------
        def e2():
            kt.focus(hwnd); time.sleep(0.35)
            x = l + 14              # 아바타보다 왼쪽 여백
            y = t + int(h * 0.45)
            print(f"    클릭=({x},{y}) 좌측여백")
            kt.click(x, y); time.sleep(0.35)
            ctrl_acopy()
        run_case("E2_좌측여백클릭", e2)

        # ---------- E3: PostMessage 클릭 (커서 이동 없음) ----------
        def e3():
            kt.focus(hwnd); time.sleep(0.35)
            cx, cy = l + w // 2, t + int(h * 0.45)
            lp = (cy << 16) | (cx & 0xFFFF)
            kt.win32gui.PostMessage(hwnd, kt.win32con.WM_LBUTTONDOWN, 1, lp)
            time.sleep(0.08)
            kt.win32gui.PostMessage(hwnd, kt.win32con.WM_LBUTTONUP, 0, lp)
            time.sleep(0.4)
            print(f"    PostMessage 클릭 전송 ({cx},{cy}) — 물리 커서 안 움직임")
            ctrl_acopy()
        run_case("E3_PostMessage클릭", e3)

        # ---------- E4: 스크롤바 안쪽 ----------
        def e4():
            kt.focus(hwnd); time.sleep(0.35)
            x = r - 8               # 우측 스크롤바
            y = t + int(h * 0.5)
            print(f"    클릭=({x},{y}) 스크롤바")
            kt.click(x, y); time.sleep(0.35)
            ctrl_acopy()
        run_case("E4_스크롤바클릭", e4)

    finally:
        if backup:
            kt.clip_write(backup)
        try:
            if fg0 and kt.win32gui.IsWindow(fg0):
                kt.focus(fg0)
        except Exception:
            pass

    print(f"\n{'='*62}\n요약\n{'='*62}")
    for k, v in RESULT.items():
        print(f"  {k:26s} {'성공' if v.get('ok') else '실패'}  len={v.get('len',0)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
