# -*- coding: utf-8 -*-
"""OCR이 놓친 행을 좌표로 직접 열기 (A방)."""
import sys, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kt_open as K

TARGET = "A방"
# 원본 캡처(_work/screen.png, 667x878) 기준 텍스트 앵커
TEXT_X, TEXT_Y = 146, 493

def main():
    hwnd = K.find_kakao_main()
    if not hwnd:
        print("메인창 없음"); return 2
    l, t, r, b = K.win32gui.GetWindowRect(hwnd)
    print(f"메인창 hwnd={hwnd} rect=({l},{t},{r},{b})")

    from PIL import Image
    w_img, h_img = Image.open(os.path.join(K.WORK, 'screen.png')).size
    sx = (r - l) / w_img
    sy = (b - t) / h_img

    cx = int(l + (TEXT_X + K.OFFSET_X) * sx)
    cy = int(t + (TEXT_Y + K.OFFSET_Y) * sy)
    print(f"클릭 좌표=({cx},{cy}) scale=({sx:.3f},{sy:.3f})")

    K.focus_window(hwnd)
    time.sleep(0.4)
    before = {h for h, _, _ in K.kakao_windows()}

    K.click_at(cx, cy, 2)
    print("더블클릭 완료 — 새 창 대기")

    found = None
    for i in range(12):
        time.sleep(0.6)
        for h, ti, rc in K.kakao_windows():
            if h not in before and ti:
                found = (h, ti, rc)
                break
        if found:
            break
        print(f"  {i+1}회차 대기...")

    if not found:
        print("✗ 새 창 안 뜸")
        return 1

    h, ti, rc = found
    print(f"✓ 새 창: '{ti}' hwnd={h} rect={rc}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
