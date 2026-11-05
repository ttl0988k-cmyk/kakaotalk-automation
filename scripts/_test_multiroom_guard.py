# -*- coding: utf-8 -*-
"""다중 방 오발송 가드 실측 검증 (실제 카톡에 입력/전송 없음)."""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kt

def line(s):
    print("\n" + "=" * 60)
    print(s)
    print("=" * 60)

def main():
    rooms = kt.list_rooms()
    line("T0) 현재 열린 방 창 목록")
    for h, t in rooms:
        print(f"   hwnd={h} title='{t}'")
    print(f"   → 방 창 {len(rooms)}개")

    if not rooms:
        print("방 창 없음 — 검증 불가")
        return

    hwnd, title = rooms[0]
    print(f"\n대상: hwnd={hwnd} '{title}'")

    line("T1) 방 이름 → 창 정확 매칭 (get_room)")
    getattr(kt, "get_room")  # 존재 확인
    r = kt.get_room(title)
    print(f"   get_room('{title}') → {r}")
    print(f"   판정: {'PASS' if r and r[0] == hwnd else 'FAIL'}")

    line("T2) 엉뚱한 방 이름으로 전송 시도 → 조작 전 중단되어야 함")
    rc = kt.send_text(hwnd, title, "가드테스트", expect_title="전혀다른방이름XYZ", dry_run=True)
    print(f"   반환코드={rc} (3=방불일치 중단 기대)")
    print(f"   판정: {'PASS' if rc == 3 else 'FAIL'}")

    line("T3) focus(hwnd) 후 실제 포그라운드 창 일치 검사")
    import win32gui, time
    kt.focus(hwnd)
    time.sleep(0.3)
    fg = win32gui.GetForegroundWindow()
    fg_title = win32gui.GetWindowText(fg)
    print(f"   대상 hwnd={hwnd} / 실제 foreground hwnd={fg} ('{fg_title}')")
    print(f"   판정: {'PASS' if fg == hwnd else 'FAIL — 포그라운드 락 또는 타 창 활성'}")

    line("T4) 다중 방 열림 + --to 미지정 → 전송 자체 거부(return 7)")
    real = kt.list_rooms
    kt.list_rooms = lambda: [(111, "방A"), (222, "방B")]
    try:
        rc = kt.cmd_send("테스트", dry_run=True, to=None)
    finally:
        kt.list_rooms = real
    print(f"   반환코드={rc} (7=대상 미지정 중단 기대)")
    print(f"   판정: {'PASS' if rc == 7 else 'FAIL'}")

    line("T5) 다중 방 + 엉뚱한 --to 지정 → 다른 방으로 입력되지 않아야 함")
    kt.list_rooms = lambda: [(111, "방A"), (222, "방B")]
    try:
        rc = kt.cmd_send("테스트", dry_run=True, to="존재하지않는방QQQ")
    finally:
        kt.list_rooms = real
    print(f"   반환코드={rc} (2=열린 방 창 없음 기대)")
    print(f"   판정: {'PASS' if rc == 2 else 'FAIL'}")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
