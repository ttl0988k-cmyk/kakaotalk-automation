# -*- coding: utf-8 -*-
"""자식 컨트롤에서 직접 텍스트 획득 실험 (클릭·클립보드 불필요 목표).

카톡 자식 구조(실측):
  EVA_VH_ListControl_Dblclk  = 대화 목록
  RICHEDIT50W                = 입력창
  _EVA_CustomScrollCtrl      = 스크롤바

A1) SendMessage(WM_GETTEXT) 로 대화 목록 읽기
A2) SendMessage(WM_GETTEXT) 로 입력창 읽기
A3) AttachThreadInput + SetFocus(list) 후 Ctrl+A/C
A4) PostMessage(list, WM_KEYDOWN) 로 Ctrl+A/C
A5) LVM_GETITEMCOUNT / LVM_GETITEMTEXT 시도
"""
import sys, os, time, ctypes
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kt
import win32gui, win32process, win32con, win32api

hwnd = 262798
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4

# 자식 찾기
kids = {}
def cb(h, acc):
    try:
        kids[win32gui.GetClassName(h)] = h
    except Exception:
        pass
    return True
win32gui.EnumChildWindows(hwnd, cb, None)
print("자식:", {k: v for k, v in kids.items()})
lst = kids.get("EVA_VH_ListControl_Dblclk")
rich = kids.get("RICHEDIT50W")

def gettext_send(h, bufsize=200000):
    """SendMessage WM_GETTEXT."""
    buf = ctypes.create_unicode_buffer(bufsize)
    n = ctypes.windll.user32.SendMessageW(h, WM_GETTEXT, bufsize, buf)
    return n, buf.value

print("\n" + "="*60 + "\nA1) 대화목록 WM_GETTEXT\n" + "="*60)
if lst:
    try:
        L = ctypes.windll.user32.SendMessageW(lst, WM_GETTEXTLENGTH, 0, 0)
        print(f"  WM_GETTEXTLENGTH = {L}")
        n, t = gettext_send(lst)
        print(f"  WM_GETTEXT 반환 n={n} len={len(t)}")
        if t:
            print("  head=", repr(t[:200]))
    except Exception as e:
        print("  오류:", e)

print("\n" + "="*60 + "\nA1b) 대화목록 LVM_GETITEMCOUNT\n" + "="*60)
if lst:
    try:
        c = ctypes.windll.user32.SendMessageW(lst, LVM_GETITEMCOUNT, 0, 0)
        print(f"  itemcount = {c}")
    except Exception as e:
        print("  오류:", e)

print("\n" + "="*60 + "\nA2) 입력창 WM_GETTEXT\n" + "="*60)
if rich:
    try:
        n, t = gettext_send(rich, 4096)
        print(f"  반환 n={n} len={len(t)} text={t!r}")
    except Exception as e:
        print("  오류:", e)

print("\n" + "="*60 + "\nA3) AttachThreadInput + SetFocus(list) → Ctrl+A/C\n" + "="*60)
if lst:
    backup = kt.clip_read()
    try:
        kt.focus(hwnd); time.sleep(0.3)
        ftid = ctypes.windll.kernel32.GetCurrentThreadId()
        ttid, _ = win32process.GetWindowThreadProcessId(hwnd)
        ok1 = ctypes.windll.user32.AttachThreadInput(ftid, ttid, True)
        ctypes.windll.user32.SetFocus(lst)
        ctypes.windll.user32.AttachThreadInput(ftid, ttid, False)
        time.sleep(0.3)
        import ctypes as C
        class G(ctypes.Structure):
            _fields_=[('cbSize',C.c_uint),('flags',C.c_uint),('hwndActive',C.c_void_p),('hwndFocus',C.c_void_p),('hwndCapture',C.c_void_p),('hwndMenuOwner',C.c_void_p),('hwndMoveSize',C.c_void_p),('hwndCaret',C.c_void_p),('rcCaret',C.c_long*4)]
        g=G(); g.cbSize=C.sizeof(g)
        ctypes.windll.user32.GetGUIThreadInfo(ttid, ctypes.byref(g))
        print(f"  attach={ok1} focus hwnd={g.hwndFocus} (목표={lst}) match={g.hwndFocus==lst}")
        kt.clip_write("__SENT__")
        kt.ctrl_key(0x41); time.sleep(0.1); kt.ctrl_key(0x43); time.sleep(0.5)
        c = kt.clip_read() or ""
        print(f"  복사 len={len(c)}  head={repr(c[:120])}")
    except Exception as e:
        print("  오류:", e)
    finally:
        kt.clip_write(backup or "")

print("\n" + "="*60 + "\nA4) PostMessage(list, WM_KEYDOWN) Ctrl+A/C\n" + "="*60)
if lst:
    backup = kt.clip_read()
    try:
        kt.focus(hwnd); time.sleep(0.25)
        kt.clip_write("__SENT__")
        # Ctrl down, A, A up, Ctrl up 을 리스트 컨트롤에 직접
        win32gui.PostMessage(lst, win32con.WM_KEYDOWN, win32con.VK_CONTROL, 0)
        win32gui.PostMessage(lst, win32con.WM_KEYDOWN, 0x41, 0)
        win32gui.PostMessage(lst, win32con.WM_KEYUP, 0x41, 0)
        win32gui.PostMessage(lst, win32con.WM_KEYUP, win32con.VK_CONTROL, 0)
        time.sleep(0.15)
        win32gui.PostMessage(lst, win32con.WM_KEYDOWN, win32con.VK_CONTROL, 0)
        win32gui.PostMessage(lst, win32con.WM_KEYDOWN, 0x43, 0)
        win32gui.PostMessage(lst, win32con.WM_KEYUP, 0x43, 0)
        win32gui.PostMessage(lst, win32con.WM_KEYUP, win32con.VK_CONTROL, 0)
        time.sleep(0.6)
        c = kt.clip_read() or ""
        print(f"  복사 len={len(c)}  head={repr(c[:120])}")
    except Exception as e:
        print("  오류:", e)
    finally:
        kt.clip_write(backup or "")
