# -*- coding: utf-8 -*-
"""쓰기(send)도 클릭 없이 가능한지 실험 — 전송은 하지 않음.

S1) focus_chat_noclick → SetFocus(RICHEDIT50W) → Ctrl+V → 입력창 내용 확인 → 정리
커서 이동 여부도 측정.
"""
import sys, os, time, ctypes
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kt
import win32api, win32gui, win32process, win32con

TEXT = "클릭없는입력테스트123"

def setfocus_child(hwnd, cls):
    """대화목록/입력창 등 자식 컨트롤에 클릭 없이 포커스."""
    h = kt.find_child_by_class(hwnd, cls)
    if not h:
        return None, False
    ftid = win32api.GetCurrentThreadId()
    ttid, _ = win32process.GetWindowThreadProcessId(hwnd)
    att = False
    try:
        att = bool(ctypes.windll.user32.AttachThreadInput(ftid, ttid, True))
        ctypes.windll.user32.SetFocus(h)
    finally:
        if att:
            ctypes.windll.user32.AttachThreadInput(ftid, ttid, False)
    time.sleep(0.15)
    return h, kt.get_focus_hwnd(hwnd) == h

def clear_input(hwnd):
    rich = kt.find_child_by_class(hwnd, kt.EVA_INPUT_CLASS)
    if not rich:
        return
    # 입력창에 포커스 준 뒤 Backspace 반복
    setfocus_child(hwnd, kt.EVA_INPUT_CLASS)
    for _ in range(200):
        win32api.keybd_event(win32con.VK_BACK, 0, 0, 0)
        win32api.keybd_event(win32con.VK_BACK, 0, win32con.KEYEVENTF_KEYUP, 0)

def main():
    room = kt.get_room("테스트방")
    if not room:
        print("방 없음"); return 2
    hwnd, title = room
    print(f"대상: '{title}' hwnd={hwnd}")
    before = win32api.GetCursorPos()
    backup = kt.clip_read()
    try:
        # 1) 창 앞으로
        kt.focus(hwnd); time.sleep(0.3)
        print("입력 전 입력창 내용:", repr(kt.read_input_box(hwnd)))

        # 2) 클릭 없이 입력창 포커스
        rich, ok = setfocus_child(hwnd, kt.EVA_INPUT_CLASS)
        print(f"입력창 핸들={rich} 포커스={ok}")
        if not ok:
            print("✘ 입력창 포커스 실패"); return 1

        # 3) 붙여넣기
        kt.clip_write(TEXT)
        time.sleep(0.15)
        kt.ctrl_key(0x41); time.sleep(0.1)   # 기존 내용 선택
        kt.ctrl_key(0x56); time.sleep(0.5)   # 붙여넣기

        got = kt.read_input_box(hwnd)
        print("입력 후 입력창 내용:", repr(got))
        match = (got or "").strip() == TEXT
        print("판정:", "✔ 클릭 없이 입력 성공" if match else "✘ 불일치")

        # 4) 정리 (전송 안 함)
        clear_input(hwnd)
        time.sleep(0.3)
        print("정리 후 내용:", repr(kt.read_input_box(hwnd)))
    finally:
        kt.clip_write(backup or "")
        after = win32api.GetCursorPos()
        print("커서 이동:", "없음 ✔" if before == after else f"이동됨 ✘ {before}->{after}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
