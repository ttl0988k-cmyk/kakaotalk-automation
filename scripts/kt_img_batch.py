#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kt_img_batch.py — 카카오톡 이미지 일괄 전송 (단순·견고 버전)

실측으로 확인된 사실:
  · 카톡 PC 는 클립보드 이미지를 붙여넣으면 '클립보드 이미지 전송' 확인 팝업을 띄운다
  · 그 팝업의 '전송' 버튼은 PostMessage 클릭으로 누를 수 있다 (마우스 커서 0px)
  · Ctrl+V 는 대상 창이 포그라운드일 때만 먹는다. 실패하면 팝업이 안 뜬다
  · 카톡의 CaptureSelectionWnd 오버레이는 포커스/클릭을 가로챈다 -> 시작 시 정리

그래서 장마다  [오버레이 정리 -> 포커스 시도 -> Ctrl+V -> 팝업 대기 -> 팝업 클릭]
을 최대 N회 재시도한다. 팝업이 뜨면 그 팝업에는 방금 넣은 이미지가 들어 있다.

사용:
    python kt_img_batch.py --room "방이름" --dir <폴더> [--skip 3] [--retries 8]
"""
import argparse
import ctypes
import io
import os
import struct
import sys
import time
from ctypes import wintypes

import win32api
import win32clipboard
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab

# kt.py 탐색 — 스킬 내부 동봉본 우선(자기 위치 기준), 미러 경로는 폴백.
_HERE = os.path.dirname(os.path.abspath(__file__))
KT = next((p for p in (
    _HERE,
    os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "raon",
                 "skills", "dev", "kakaotalk-automation", "scripts"),
    os.path.join(os.path.expanduser("~"), ".claude", "skills", "dev",
                 "kakaotalk-automation", "scripts"),
) if os.path.exists(os.path.join(p, "kt.py"))), _HERE)
sys.path.insert(0, KT)
import kt  # noqa: E402


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------- 카톡 창 유틸
def kill_overlays():
    """CaptureSelectionWnd 오버레이 제거 — 이것이 포커스를 가로챈다."""
    caps = []

    def cb(h, _):
        try:
            if win32gui.GetClassName(h).startswith("Afx:") and \
               win32gui.GetWindowText(h) == "CaptureSelectionWnd":
                caps.append(h)
        except Exception:
            pass
    win32gui.EnumWindows(cb, None)
    for h in caps:
        try:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
    if caps:
        time.sleep(0.4)
    return len(caps)


def children(hwnd):
    out = []

    def cb(ch, _):
        out.append(ch)
    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return out


def find_popup(hwnd):
    """'클립보드 이미지 전송' 팝업을 찾는다.

    ★중요(실측 2026-11-05): 이 팝업은 방 창의 '자식(WS_CHILD)'이 아니라
      방 창이 소유한(owned) 최상위 창이다. GetParent()는 방 창을 반환하지만
      EnumChildWindows()에는 절대 나타나지 않는다.
      -> EnumWindows() + GW_OWNER 로 찾아야 한다. 이걸 놓쳐서
         '팝업도 없음'으로 오판하고 전송이 계속 실패했다.
    """
    found = []

    def cb(ch, _):
        try:
            if win32gui.GetClassName(ch) != "EVA_Window_Dblclk":
                return
            if win32gui.GetWindowText(ch):
                return
            owner = win32gui.GetWindow(ch, win32con.GW_OWNER)
            if owner != hwnd and win32gui.GetParent(ch) != hwnd:
                return
            if ch == hwnd:
                return
            l, t, r, b = win32gui.GetWindowRect(ch)
            if r - l >= 120 and b - t >= 120:
                found.append(ch)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return found[0] if found else None


def close_popup(pop):
    for _ in range(6):
        if not win32gui.IsWindow(pop):
            return True
        try:
            win32gui.PostMessage(pop, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
        time.sleep(0.3)
    return not win32gui.IsWindow(pop)


def grab_focus(hwnd):
    """포그라운드를 여러 방법으로 시도. 성공 여부 반환."""
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    for _ in range(4):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            time.sleep(0.12)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
        try:
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.25)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        # ALT 를 누른 채 유지하며 재시도 (포그라운드 락 우회 관례)
        try:
            win32api.keybd_event(0x12, 0, 0, 0)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.2)
            win32api.keybd_event(0x12, 0, 2, 0)
        except Exception:
            pass
        time.sleep(0.2)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return win32gui.GetForegroundWindow() == hwnd


def set_clip_image(path):
    im = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "BMP")
    dib = buf.getvalue()[14:]
    for _ in range(6):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            time.sleep(0.12)
    else:
        raise RuntimeError("클립보드 열기 실패")
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()
    return im.size


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")
VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv")


def set_clip_files(paths):
    """CF_HDROP — 파일 '경로'만 클립보드에 적재한다.

    영상·대용량은 비트맵(CF_DIB)으로 못 넣으므로 이 경로를 쓴다.
    카톡이 붙여넣기 시 파일 첨부로 인식하는지가 관건이며,
    인식하면 이미지와 동일하게 '전송' 확인 팝업이 뜬다.
    """
    if isinstance(paths, str):
        paths = [paths]
    paths = [os.path.abspath(p) for p in paths]
    # DROPFILES: pFiles(4) pt.x(4) pt.y(4) fNC(4) fWide(4) = 20바이트
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    body = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    data = header + body

    GMEM_MOVEABLE = 0x0002
    CF_HDROP = 15
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32

    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        raise RuntimeError("GlobalAlloc 실패")

    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    p = k32.GlobalLock(h)
    if not p:
        raise RuntimeError("GlobalLock 실패")
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock(h)

    # pywin32 의 SetClipboardData 는 64비트 HGLOBAL 을 int 로 강제 변환해
    # OverflowError 를 낸다 → user32 를 ctypes 로 직접 호출한다.
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.CloseClipboard.restype = wintypes.BOOL

    for _ in range(6):
        if u32.OpenClipboard(None):
            break
        time.sleep(0.12)
    else:
        raise RuntimeError("클립보드 열기 실패")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(CF_HDROP, h):
            raise RuntimeError("SetClipboardData(CF_HDROP) 실패")
    finally:
        u32.CloseClipboard()
    return len(paths)


def set_clip_media(path):
    """확장자로 이미지/영상 경로를 자동 분기."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return ("file", set_clip_files(path))
    return ("image", set_clip_image(path))



def click_send(pop):
    """팝업 하단의 '전송' 버튼을 픽셀 스캔으로 찾아 PostMessage 클릭."""
    l, t, r, b = win32gui.GetWindowRect(pop)
    img = ImageGrab.grab(bbox=(l, t, r, b)).convert("RGB")
    W, H = img.size
    px = img.load()
    pts = []
    for y in range(int(H * 0.78), H):
        for x in range(W):
            cr, cg, cbb = px[x, y]
            if abs(cr - cg) < 12 and abs(cg - cbb) < 12 and cr > 232:
                continue
            pts.append((x, y))
    if not pts:
        return False, "버튼 픽셀 없음"
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    sx = (min(xs) + max(xs)) // 2
    sy = (min(ys) + max(ys)) // 2
    ox, oy = win32gui.ClientToScreen(pop, (0, 0))
    cx, cy = sx + l - ox, sy + t - oy
    lp = (cy << 16) | (cx % 65536)
    win32gui.PostMessage(pop, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
    time.sleep(0.08)
    win32gui.PostMessage(pop, win32con.WM_LBUTTONUP, 0, lp)
    time.sleep(1.3)
    return (not win32gui.IsWindow(pop)), "click(%d,%d)" % (sx + l, sy + t)


# ------------------------------------------------------------- 파일 첨부 경로
#
# ★실측(2026-11-05): 카톡 PC 는 CF_HDROP 붙여넣기를 '파일 첨부'로 받지 않는다.
#   포커스(RICHEDIT50W SetFocus)·Preferred DropEffect(COPY) 를 모두 붙여도
#   입력창이 비어 있고 팝업도 뜨지 않았다. CF_DIB(이미지)만 통한다.
#   → 영상·문서는 클립보드를 우회해 **파일 첨부 아이콘 → '열기' 다이얼로그**
#     경로로 보낸다. 이 경로는 실측 성공했다(mp4 651KB, 전송 팝업 '1개 전송').

def _find_file_icon(hwnd):
    """입력창 아래 툴바의 '파일 모양' 첨부 아이콘 화면좌표를 찾는다.

    툴바는 owner-drawn 이라 자식 창으로 안 잡힌다 → 픽셀로 찾는다.
    좌측부터 [+][이모티콘][파일] 순서이므로 **마지막 군집**이 파일 아이콘이다.
    """
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    img = ImageGrab.grab(bbox=(l, t, r, b)).convert("RGB")
    W, H = img.size
    px = img.load()
    y0, y1 = H - 38, H - 14
    cols = {}
    for x in range(16, min(200, W)):
        d = 0
        for y in range(y0, y1):
            if sum(px[x, y]) < 640:
                d += 1
        if d >= 2:
            cols[x] = d
    xs = sorted(cols)
    if not xs:
        return None
    groups = []
    cur = [xs[0]]
    for a, b2 in zip(xs, xs[1:]):
        if b2 - a <= 6:
            cur.append(b2)
        else:
            groups.append(cur)
            cur = [b2]
    groups.append(cur)
    groups = [g for g in groups if len(g) >= 5]
    if len(groups) < 2:
        return None
    g = groups[-1]                       # 가장 오른쪽 = 파일 아이콘
    return (l + (g[0] + g[-1]) // 2, t + (y0 + y1) // 2)


def _find_open_dialog(parent, before):
    """방 창이 소유한 '열기' 파일 다이얼로그를 찾는다."""
    found = []

    def cb(h, a):
        try:
            if h in before:
                return True
            if win32gui.GetClassName(h) == "#32770" and win32gui.GetParent(h) == parent:
                a.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, found)
    return found[0] if found else None


def _set_dialog_path(dlg, path):
    """'파일 이름(&N)' 입력란에 경로를 넣고 '열기' 를 누른다."""
    combo = win32gui.FindWindowEx(dlg, 0, "ComboBoxEx32", None)
    ed = None
    if combo:
        inner = win32gui.FindWindowEx(combo, 0, "ComboBox", None)
        if inner:
            ed = win32gui.FindWindowEx(inner, 0, "Edit", None)
    if not ed:
        ed = win32gui.FindWindowEx(dlg, 0, "Edit", None)
    if not ed:
        return False

    win32gui.SendMessage(ed, win32con.WM_SETTEXT, 0, path)
    time.sleep(0.25)

    # '열기(&O)' 버튼 — 텍스트로 찾는다
    btn = None
    btns = []

    def cb(h, a):
        try:
            if win32gui.GetClassName(h) == "Button":
                tx = win32gui.GetWindowText(h)
                if tx.startswith("열기"):
                    a.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(dlg, cb, btns)
    if btns:
        btn = btns[0]
    if btn:
        win32gui.SendMessage(btn, win32con.BM_CLICK, 0, 0)
    else:
        kt.press_key(0x0D)               # VK_RETURN 폴백
    time.sleep(1.2)
    return True


def _click_send_ratio(pop):
    """팝업의 'N개 전송' 버튼을 비율 좌표로 클릭한다.

    이미지 팝업과 달리 파일 팝업은 버튼이 팝업 높이의 약 78% 지점에 있다
    (실측: 300x185 팝업에서 중심 y=144). 기존 click_send 의 '하단 22%'
    스캔은 이 경계에 걸려 빗나갔다.
    """
    l, t, r, b = win32gui.GetWindowRect(pop)
    W, H = r - l, b - t
    for fy in (0.78, 0.72, 0.85):
        cx, cy = W // 2, int(H * fy)
        lp = (cy << 16) | (cx % 65536)
        win32gui.PostMessage(pop, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        time.sleep(0.09)
        win32gui.PostMessage(pop, win32con.WM_LBUTTONUP, 0, lp)
        time.sleep(1.3)
        if not win32gui.IsWindow(pop):
            return True
    return False


def send_file(hwnd, path, retries=3):
    """영상·문서를 '파일 첨부' 다이얼로그 경로로 전송한다."""
    name = os.path.basename(path)
    if not os.path.exists(path):
        log("    ✗ 파일 없음: %s" % path)
        return False
    for attempt in range(1, retries + 1):
        p = find_popup(hwnd)
        if p is not None:
            close_popup(p)
        kill_overlays()
        if not grab_focus(hwnd):
            log("    (%d) 포커스 실패" % attempt)
            time.sleep(0.5)
            continue

        before = set()

        def collect(h, a):
            a.add(h)
            return True

        win32gui.EnumWindows(collect, before)

        ic = _find_file_icon(hwnd)
        if not ic:
            log("    (%d) 파일 첨부 아이콘 못 찾음" % attempt)
            time.sleep(0.5)
            continue
        kt.click(ic[0], ic[1])

        deadline = time.time() + 3.5
        dlg = None
        while time.time() < deadline:
            dlg = _find_open_dialog(hwnd, before)
            if dlg:
                break
            time.sleep(0.2)
        if not dlg:
            log("    (%d) '열기' 다이얼로그 안 뜸" % attempt)
            time.sleep(0.6)
            continue

        _set_dialog_path(dlg, os.path.abspath(path))

        deadline = time.time() + 12.0
        pop = None
        while time.time() < deadline:
            pop = find_popup(hwnd)
            if pop is not None:
                break
            time.sleep(0.25)
        if pop is None:
            log("    (%d) 전송 팝업 안 뜸" % attempt)
            time.sleep(0.6)
            continue

        time.sleep(0.6)
        if _click_send_ratio(pop):
            log("    ✓ %s 전송 (파일첨부, %.0fKB)" % (name, os.path.getsize(path) / 1024.0))
            return True
        log("    (%d) 'N개 전송' 클릭 실패" % attempt)
        time.sleep(0.5)

    log("    ✗ %s 실패 — %d회 재시도 소진" % (name, retries))
    return False


# ---------------------------------------------------------------- 전송
def send_image(hwnd, path, retries=8):
    name = os.path.basename(path)
    for attempt in range(1, retries + 1):
        # 1) 남은 팝업 정리
        p = find_popup(hwnd)
        if p is not None:
            # 남은 팝업은 이전 이미지일 수 있으므로 닫는다
            if not close_popup(p):
                log("    (%d) 팝업이 닫히지 않음 — 클릭 시도" % attempt)
                ok, msg = click_send(p)
                log("    (%d) 잔여팝업 클릭 -> %s" % (attempt, msg))
                continue

        kill_overlays()

        # 2) 포커스 — 창만 앞으로 하면 Ctrl+V 가 입력창에 안 들어간다.
        #    RICHEDIT50W 자식에 AttachThreadInput+SetFocus 로 직접 포커스를 준다.
        if not grab_focus(hwnd):
            log("    (%d) 포커스 실패" % attempt)
            time.sleep(0.5)
            continue
        kt.focus_child_noclick(hwnd, "RICHEDIT50W")
        time.sleep(0.15)

        # 3) 클립보드 + Ctrl+V
        try:
            kind, sz = set_clip_media(path)
        except Exception as e:
            log("    (%d) 클립보드 실패: %s" % (attempt, e))
            time.sleep(0.4)
            continue
        kt.ctrl_key(0x56)

        # 4) 팝업 대기 (최대 6초)
        deadline = time.time() + 6.0
        pop = None
        while time.time() < deadline:
            pop = find_popup(hwnd)
            if pop is not None:
                break
            time.sleep(0.2)

        if pop is None:
            log("    (%d) 팝업 안 뜸 — Ctrl+V 유실, 재시도" % attempt)
            time.sleep(0.5)
            continue

        time.sleep(0.4)  # 미리보기 렌더 대기
        ok, msg = click_send(pop)
        if ok:
            log("    ✓ %s 전송 (%s, %s)" % (name, msg, "%.0fx%.0f" % sz))
            return True
        log("    (%d) 전송버튼 클릭 실패: %s" % (attempt, msg))
        time.sleep(0.4)

    log("    ✗ %s 실패 — %d회 재시도 소진" % (name, retries))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--skip", type=int, default=0, help="이미 보낸 앞쪽 장수")
    ap.add_argument("--retries", type=int, default=8)
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="목록만 출력하고 전송하지 않는다 (사고 방지용)")
    args = ap.parse_args()

    files = sorted(
        os.path.join(args.dir, f) for f in os.listdir(args.dir)
        if f.lower().endswith(IMAGE_EXT + VIDEO_EXT)
        and not f.startswith("contact_sheet")
    )
    if args.skip:
        files = files[args.skip:]
    if not files:
        raise SystemExit("[batch] 보낼 파일 없음")

    if args.dry_run:
        log("[batch] DRY-RUN — 전송하지 않습니다. 대상 %d개:" % len(files))
        for i, f in enumerate(files, 1):
            ext = os.path.splitext(f)[1].lower()
            kind = "파일첨부" if ext in VIDEO_EXT else "클립보드"
            log("  %2d. [%s] %s (%.0fKB)"
                % (i, kind, os.path.basename(f), os.path.getsize(f) / 1024.0))
        return 0

    hwnd, title = kt.get_room(args.room)
    log("[batch] 대상: %s (hwnd=%s) / %d장" % (title, hwnd, len(files)))
    kill_overlays()

    done, fail = [], []
    for i, f in enumerate(files, 1):
        log("[batch] (%d/%d) %s" % (i, len(files), os.path.basename(f)))
        ext = os.path.splitext(f)[1].lower()
        if ext in VIDEO_EXT:
            ok = send_file(hwnd, f, retries=min(args.retries, 3))
        else:
            ok = send_image(hwnd, f, retries=args.retries)
        if ok:
            done.append(f)
        else:
            fail.append(f)
        time.sleep(args.gap)

    log("[batch] 완료 %d / 실패 %d" % (len(done), len(fail)))
    for f in fail:
        log("   - 실패: %s" % os.path.basename(f))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
