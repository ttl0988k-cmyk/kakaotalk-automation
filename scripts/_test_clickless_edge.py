# -*- coding: utf-8 -*-
"""클릭 없는 읽기의 '반례' 검증.

E5) 입력창에 포커스가 있을 때 클릭 없이 Ctrl+A/Ctrl+C → 대화가 잡히나?
E6) 다른 방을 새로 열고 [클릭 없이] 바로 읽기 → 잡히나?
E7) 클릭 없이 + PageUp 스크롤 후에도 유지되나?
"""
import sys, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kt

def peek(tag):
    c = kt.clip_read() or ""
    print(f"    [{tag}] len={len(c)}  head={c.replace(chr(10),' ⏎ ')[:80]!r}")
    return c

def copy_only():
    kt.ctrl_key(0x41); time.sleep(0.12)
    kt.ctrl_key(0x43); time.sleep(0.45)

def main():
    room = kt.get_room("A방")
    if not room:
        print("방 없음"); return 2
    hwnd, title = room
    print(f"대상 hwnd={hwnd} '{title}'")
    l, t, r, b = kt.win32gui.GetWindowRect(hwnd)
    backup = kt.clip_read()

    try:
        # ---- E5: 입력창에 일부러 포커스 ----
        print("\n" + "="*60 + "\nE5) 입력창 포커스 상태에서 클릭없이 읽기\n" + "="*60)
        kt.focus(hwnd); time.sleep(0.3)
        kt.click(l + (r-l)//2, t + int((b-t)*0.855))   # 입력창 클릭
        time.sleep(0.4)
        kt.clip_write("__SENT__")
        copy_only()
        c5 = peek("E5_입력창포커스")
        # 입력창이면 대개 짧거나(빈 문자열) 방이름 수준
        print(f"    판정: {'대화 잡힘(OK)' if len(c5)>500 else '대화 아님 — 입력창이 선택됨'}")

        # ---- 대화영역으로 복귀 (기존 방식) ----
        print("\n" + "="*60 + "\nE5-b) 대화영역 클릭 후 읽기 (기존 방식)\n" + "="*60)
        kt.click(l + (r-l)//2, t + int((b-t)*0.45))
        time.sleep(0.4)
        kt.clip_write("__SENT__")
        copy_only()
        c5b = peek("E5b_대화영역클릭")
        print(f"    판정: {'대화 잡힘(OK)' if len(c5b)>500 else '대화 아님'}")

        # ---- E7: 클릭 없이 PageUp 스크롤 후 ----
        print("\n" + "="*60 + "\nE7) 클릭없이 PageUp 후 읽기\n" + "="*60)
        kt.focus(hwnd); time.sleep(0.3)
        kt.press_key(kt.VK_PRIOR, 1, delay=0.1)   # PageUp
        time.sleep(0.5)
        kt.clip_write("__SENT__")
        copy_only()
        c7 = peek("E7_PageUp후")
        print(f"    판정: {'대화 잡힘(OK)' if len(c7)>500 else '대화 아님'}")

    finally:
        if backup: kt.clip_write(backup)
    return 0

if __name__ == "__main__":
    sys.exit(main())
