# -*- coding: utf-8 -*-
"""클릭 없이 '포커스만' 대화영역으로 옮기는 방법 탐색.

입력창에 포커스가 있을 때, 클릭 대신 키보드로 대화영역 포커스를 얻을 수 있나?
  T1) Tab 1회 → Ctrl+A/C
  T2) Shift+Tab 1회 → Ctrl+A/C
  T3) Tab 2회
  T4) F6 / Ctrl+Tab 등
각 단계마다 전사 길이로 판정(>500자면 대화 잡힘).
"""
import sys, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kt

VK_TAB = 0x09
VK_SHIFT = 0x10
VK_F6 = 0x75

def probe(tag):
    kt.clip_write("__SENT__")
    kt.ctrl_key(0x41); time.sleep(0.12)
    kt.ctrl_key(0x43); time.sleep(0.4)
    c = kt.clip_read() or ""
    ok = len(c) > 500
    print(f"    {tag:22s} len={len(c):5d}  {'✔ 대화' if ok else '✘ 아님'}")
    return ok, c

def tap_tab(shift=False):
    if shift:
        kt.win32api.keybd_event(VK_SHIFT, 0, 0, 0)
    kt.win32api.keybd_event(VK_TAB, 0, 0, 0)
    time.sleep(0.05)
    kt.win32api.keybd_event(VK_TAB, 0, kt.win32con.KEYEVENTF_KEYUP, 0)
    if shift:
        kt.win32api.keybd_event(VK_SHIFT, 0, kt.win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.35)

def main():
    room = kt.get_room("A방")
    if not room: print("방 없음"); return 2
    hwnd, title = room
    l, t, r, b = kt.win32gui.GetWindowRect(hwnd)
    backup = kt.clip_read()
    print(f"대상 hwnd={hwnd} '{title}'")

    try:
        # 기준선: 입력창 포커스 강제
        kt.focus(hwnd); time.sleep(0.3)
        kt.click(l + (r-l)//2, t + int((b-t)*0.855))
        time.sleep(0.4)
        probe("BASELINE_입력창")

        # T1: Tab 1회
        tap_tab()
        probe("T1_Tab1")

        # T2: Tab 2회
        tap_tab()
        probe("T2_Tab2")

        # T3: Tab 3회
        tap_tab()
        probe("T3_Tab3")

        # 입력창으로 돌아가 Shift+Tab
        kt.focus(hwnd); time.sleep(0.25)
        kt.click(l + (r-l)//2, t + int((b-t)*0.855)); time.sleep(0.35)
        tap_tab(shift=True)
        probe("T4_ShiftTab1")

        # F6
        kt.press_key(VK_F6, 1, delay=0.4)
        probe("T5_F6")

        # T6: 클릭 없이 마우스휠만 (WM_MOUSEWHEEL) 후
        kt.focus(hwnd); time.sleep(0.25)
        kt.win32api.mouse_event(0x0800, 0, 0, 120, 0)  # MOUSEEVENTF_WHEEL up
        time.sleep(0.5)
        probe("T6_휠스크롤만")

    finally:
        if backup: kt.clip_write(backup)
    return 0

if __name__ == "__main__":
    sys.exit(main())
