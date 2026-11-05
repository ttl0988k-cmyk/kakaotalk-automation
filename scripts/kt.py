#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""kt.py — 카카오톡 PC 자동화 통합 CLI (사이드카)

단계: 숨긴 메인창 열기 → 크기 667×878 → 방 찾아 열기 → 방 크기 490×736 → 읽기/쓰기

사용법:
    python kt.py show                 # 숨겨진 카카오톡 메인창 열기 + 667×878
    python kt.py open "내게쓴방"           # 방 열기 + 490×736
    python kt.py read                 # 현재 방 읽기 (기본: 클립보드 전사, 약 0.35초)
    python kt.py read --ocr           # Windows OCR 방식 (좌표 포함, 약 0.75초)
    python kt.py read --json          # JSON 출력
    python kt.py send "메시지" --dry-run   # 입력만 (전송 안 함)
    python kt.py send "메시지" --yes       # 실제 전송
    python kt.py size                 # 현재 창 크기 점검(필요 시 기준 크기로 강제)
    python kt.py windows              # 카카오톡 창 목록

읽기 방식 실측(490×736, 2026-11-05):
    클립보드 전사  0.349초  853자  100% (발신자·시간·본문 완전)
    Windows OCR    0.739초 1013자  오독 있음(좌표 제공)
    Tesseract kor  0.162초    6자   실패(작은 한글 폰트)

안전 규칙:
    - Escape 키 사용 금지 (방/앱이 닫힘)
    - 클립보드는 백업 후 복원
    - 전송은 --yes 없으면 dry-run
    - 원본 대화는 로컬 파일로만 저장

send 종료코드 (0=성공, 나머지=중단 지점):
    2  열린 방 창 없음
    3  방 이름 불일치 (유사도 < 0.5)  ← 조작 전 중단
    4  입력 내용 불일치 (붙여넣기 실패)
    5  그 사이 새 메시지 도착 → Enter 취소
    6  전송 후 재읽기로 내 메시지 미확인
    7  방이 2개 이상인데 --to 미지정 → 거부
    8  대상 창 포그라운드 확보 실패 → 클릭/전송 안 함
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

HERE = os.path.dirname(os.path.abspath(__file__))

# ★ 워처 일시정지 신호 파일.
#   워처와 전송이 동시에 돌면 서로 포커스를 뺏어 읽기 오염·전송 실패가 난다(실측).
#   전송이 시작되면 이 파일을 만들고, 워처는 이 파일이 있는 동안 폴링을 건너뛴다.
PAUSE_FILE = os.path.join(HERE, "_work", "watch_pause")

# ★ 전송 원장 — CLI 와 MCP 가 같은 파일을 공유한다.
#   같은 메시지를 두 번 보내는 사고를 막기 위한 기록.
#   드는 이유: 전송 후 확인이 실패하는 경우가 실제로 있다(카톡이 긴 글을
#   '전체보기'로 접으면 화면 전사로는 끝이 안 읽힌다). 그때 사람/에이전트가
#   다시 보내면 같은 글이 두 번 도착한다. 원장으로 그걸 차단한다.
SEND_LEDGER = os.path.join(HERE, "_work", "send_ops.jsonl")


def text_hash(room, text):
    """방 이름 + 본문으로 만든 짧은 식별자. 중복 판정용."""
    h = hashlib.sha1()
    h.update((room or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((text or "").encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def ledger_read():
    """{operation_id: record} — 같은 ID 가 여러 번 있으면 마지막이 이긴다."""
    out = {}
    if not os.path.exists(SEND_LEDGER):
        return out
    try:
        with open(SEND_LEDGER, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                op = rec.get("operation_id")
                if op:
                    out[op] = rec
    except Exception:
        pass
    return out


def ledger_get(operation_id):
    if not operation_id:
        return None
    return ledger_read().get(operation_id)


def ledger_append(rec):
    os.makedirs(os.path.dirname(SEND_LEDGER), exist_ok=True)
    try:
        with open(SEND_LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def ledger_recent_dup(room, text, within=60):
    """within 초 안에 같은 방·같은 본문을 '실제로 보낸' 기록이 있으면 그것을 돌려준다.

    failed 는 중복으로 보지 않는다 — 애초에 안 갔으니 다시 보내야 한다.
    """
    target = text_hash(room, text)
    now = time.time()
    hit = None
    for rec in ledger_read().values():
        if rec.get("text_hash") != target:
            continue
        if rec.get("status") not in ("sent", "sent_unverified"):
            continue
        try:
            age = now - float(rec.get("ts") or 0)
        except Exception:
            continue
        if 0 <= age <= within:
            if hit is None or (rec.get("ts") or 0) > (hit.get("ts") or 0):
                hit = rec
    return hit


def set_watch_pause(on: bool) -> None:
    """워처 폴링 일시정지 신호를 켜고 끈다. 실패해도 예외를 던지지 않는다."""
    try:
        if on:
            os.makedirs(os.path.dirname(PAUSE_FILE), exist_ok=True)
            with open(PAUSE_FILE, "w", encoding="utf-8") as f:
                f.write(str(int(time.time())))
        else:
            if os.path.exists(PAUSE_FILE):
                os.remove(PAUSE_FILE)
    except Exception:
        pass


def watch_paused() -> bool:
    """워처가 폴링을 멈춰야 하는 상태인지. 60초 넘은 신호는 무시(좀비 방지)."""
    try:
        if not os.path.exists(PAUSE_FILE):
            return False
        age = time.time() - os.path.getmtime(PAUSE_FILE)
        return age < 60
    except Exception:
        return False
sys.path.insert(0, HERE)

import kt_room  # noqa: E402
import kt_show  # noqa: E402

ROOM_STD_W, ROOM_STD_H = 490, 736
MAIN_STD_W, MAIN_STD_H = 667, 878
MAIN_TITLE = "카카오톡"


def log(m=""):
    print(m, flush=True)


# ---------------------------------------------------------------- 창 탐색
def find_main():
    """kt_show.find_main_window 는 ((hwnd, title, visible), wins) 를 준다."""
    try:
        win, _wins = kt_show.find_main_window()
    except Exception:
        win = None
    if win:
        return win[0]
    res = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == MAIN_TITLE and win32gui.IsWindowVisible(h):
            res.append(h)

    win32gui.EnumWindows(cb, None)
    return res[0] if res else 0


def list_windows():
    rows = []

    def cb(h, _):
        t = win32gui.GetWindowText(h)
        if not t:
            return
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            return
        if "EVA" not in cls and t != MAIN_TITLE:
            return
        try:
            l, tp, r, b = win32gui.GetWindowRect(h)
        except Exception:
            return
        rows.append((h, t, r - l, b - tp, (l, tp), win32gui.IsWindowVisible(h)))

    win32gui.EnumWindows(cb, None)
    return rows


def focus(hwnd, strict=True):
    """창을 앞으로 올리고 실제로 포그라운드가 됐는지 '검증'한다.

    좌표 클릭은 항상 최상위 창으로 들어가므로, 포그라운드 확보에 실패한 채
    클릭하면 남의 창·다른 방에 입력이 찍힌다. 그래서 성공 여부를 반환한다.
    (반환값 무시하는 기존 호출부는 그대로 동작 = 하위호환)
    """
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    except Exception:
        pass

    for _ in range(3):
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.15)
        if win32gui.GetForegroundWindow() == hwnd:
            time.sleep(0.1)
            return True
        # 포그라운드 락 해제용 ALT 탭(Windows 관례)
        try:
            win32api.keybd_event(0x12, 0, 0, 0)
            win32api.keybd_event(0x12, 0, 2, 0)
            time.sleep(0.05)
        except Exception:
            pass

    return win32gui.GetForegroundWindow() == hwnd


def enforce(hwnd, w, h):
    """창 크기를 기준 크기로 강제. 이미 맞으면 그대로."""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
    except Exception:
        return False
    if (r - l, b - t) == (w, h):
        return True
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetWindowPos(hwnd, None, l, t, w, h,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    time.sleep(0.25)
    return True


# ---------------------------------------------------------------- 클립보드
def clip_read():
    for _ in range(15):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.08)
    return None


def clip_write(txt):
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        if txt:
            win32clipboard.SetClipboardText(txt, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def click(x, y):
    win32api.SetCursorPos((x, y))
    time.sleep(0.04)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def ctrl_key(vk):
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


CLIP_DATE_RE = re.compile(r"^(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
CLIP_MSG_RE = re.compile(
    r"^\[([^\]\[]{1,40})\]\s*\[(오전|오후)\s*(\d{1,2})\s*:\s*(\d{2})\]\s*(.*)$"
)


def parse_transcript(txt):
    """'[발신자] [오후 H:MM] 본문' 형식의 클립보드 전사를 메시지 목록으로."""
    norm = txt.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    out = []
    cur_date = None
    cur = None
    for raw in norm.split("\n"):
        s = raw.strip()
        if not s:
            if cur is not None:
                cur["body"] += "\n"
            continue
        dm = CLIP_DATE_RE.match(s)
        if dm:
            cur_date = f"{dm.group(1)}년 {int(dm.group(2))}월 {int(dm.group(3))}일"
            if cur is not None:
                out.append(cur)
                cur = None
            continue
        mm = CLIP_MSG_RE.match(s)
        if mm:
            if cur is not None:
                out.append(cur)
            cur = {
                "date": cur_date,
                "sender": mm.group(1).strip(),
                "time": f"{mm.group(2)} {int(mm.group(3))}:{mm.group(4)}",
                "body": mm.group(5),
            }
            continue
        if cur is not None:
            cur["body"] = (cur["body"] + "\n" + s) if cur["body"].strip() else s
    if cur is not None:
        out.append(cur)
    for m in out:
        m["body"] = m["body"].strip()
    return out


def _copy_transcript_once():
    """Ctrl+A → Ctrl+C 로 클립보드에 전사 시도. 성공 시 텍스트, 실패 시 None."""
    clip_write("__SENTINEL__")
    ctrl_key(0x41)   # Ctrl+A
    time.sleep(0.05)
    ctrl_key(0x43)   # Ctrl+C
    for _ in range(25):
        time.sleep(0.04)
        cur = clip_read()
        if cur and cur != "__SENTINEL__":
            return cur
    return None


def _shift_tab():
    """Shift+Tab 1회 — 입력창 포커스에서 대화영역으로 이동(클릭 없이)."""
    win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
    win32api.keybd_event(0x09, 0, 0, 0)          # Tab down
    time.sleep(0.05)
    win32api.keybd_event(0x09, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.30)


# 카카오톡 자식 컨트롤 클래스 (실측)
EVA_LIST_CLASS = "EVA_VH_ListControl_Dblclk"   # 대화 목록
EVA_INPUT_CLASS = "RICHEDIT50W"                # 입력창


def find_child_by_class(hwnd, cls):
    """자식 창 중 지정 클래스의 첫 HWND 반환."""
    found = []

    def cb(h, acc):
        try:
            if win32gui.GetClassName(h) == cls:
                acc.append(h)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, found)
    except Exception:
        return None
    return found[0] if found else None


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
        ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", ctypes.c_long * 4),
    ]


def get_focus_hwnd(hwnd):
    """대상 창 스레드의 현재 포커스 HWND 조회."""
    try:
        tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        g = _GUITHREADINFO()
        g.cbSize = ctypes.sizeof(g)
        if not ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(g)):
            return None
        return int(g.hwndFocus) if g.hwndFocus else None
    except Exception:
        return None


def focus_child_noclick(hwnd, cls):
    """★클릭 없이 지정 자식 컨트롤에 포커스를 준다 (AttachThreadInput + SetFocus).

    좌표 클릭은 그 자리에 있는 링크·이모지를 눌러 브라우저/이모티콘 창을 띄운다.
    자식 컨트롤 클래스가 확인된 경우 이 방법으로 포커스만 옮긴다.

    반환: (핸들, 포커스성공여부)
    """
    try:
        focus(hwnd)          # 창을 앞으로 (클릭 아님)
        time.sleep(0.12)
        if win32gui.GetForegroundWindow() != hwnd:
            return None, False
        h = find_child_by_class(hwnd, cls)
        if not h:
            return None, False

        ftid = win32api.GetCurrentThreadId()
        ttid, _ = win32process.GetWindowThreadProcessId(hwnd)
        attached = False
        try:
            attached = bool(ctypes.windll.user32.AttachThreadInput(ftid, ttid, True))
            ctypes.windll.user32.SetFocus(h)
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(ftid, ttid, False)
        time.sleep(0.12)
        return h, (get_focus_hwnd(hwnd) == h)
    except Exception:
        return None, False


def focus_chat_noclick(hwnd):
    """★클릭 없이 대화 목록 컨트롤에 포커스를 준다.

    대화 본문을 클릭하면 그 자리의 링크·이모지가 눌려 브라우저/이모티콘 창이
    뜨는 사고가 난다. 그래서 좌표 클릭 대신
    AttachThreadInput → SetFocus(EVA_VH_ListControl_Dblclk) 로 포커스만 옮긴다.

    반환: 포커스가 실제로 대화 목록에 갔으면 True.
    """
    _, ok = focus_child_noclick(hwnd, EVA_LIST_CLASS)
    return ok


def read_input_box(hwnd):
    """입력창 텍스트 읽기 (WM_GETTEXT). 비어 있으면 플레이스홀더가 나온다."""
    try:
        rich = find_child_by_class(hwnd, EVA_INPUT_CLASS)
        if not rich:
            return None
        buf = ctypes.create_unicode_buffer(4096)
        n = ctypes.windll.user32.SendMessageW(rich, 0x000D, 4096, buf)
        return buf.value if n else ""
    except Exception:
        return None


def read_via_clipboard(hwnd, title=None, retry=2, allow_click=False):
    """[클릭 금지 우선] Ctrl+A → Ctrl+C → 클립보드 파싱.

    ★ 대화 본문을 클릭하면 그 자리에 있는 '링크·이모지'가 눌려
      브라우저/이모티콘 창이 뜨는 사고가 난다. 그래서 클릭을 쓰지 않는다.

    포커스 확보 3단계 (전부 클릭 없음):
      1) 창만 앞으로 올리고(SetForegroundWindow) 바로 복사 시도
      2) 실패하면 Shift+Tab 1회 — 입력창 → 대화영역 포커스 이동
      3) allow_click=True 일 때만 최후 폴백으로 클릭 (기본 비활성)
    """
    t0 = time.perf_counter()
    # ★ 백그라운드 상태로 Ctrl+A/Ctrl+C를 보내면 '다른 방' 내용을 복사한다(실측).
    #   창 3개가 떠 있을 때 복사가 포그라운드 창으로 가서 엉뚱한 방을 읽고,
    #   그 결과 전송 가드가 오탐으로 전송을 취소했다. 복사 전에 반드시 확인한다.
    if not focus(hwnd, strict=True):
        log(f"[read] ⚠ 대상 창 포그라운드 확보 실패 — 복사 중단 (room={title})")
        return {"ok": False, "sec": 0, "messages": [], "chars": 0,
                "method": None, "error": "not_foreground"}
    backup = clip_read()
    cursor = win32api.GetCursorPos()
    txt = None
    method = None
    try:
        # 1) ★클릭 없이 — 대화 목록 컨트롤에 직접 포커스 (AttachThreadInput+SetFocus)
        if focus_chat_noclick(hwnd):
            cand = _copy_transcript_once()
            if cand:
                txt, method = cand, "setfocus-noclick"

        # 2) Shift+Tab 폴백 (여전히 클릭 없음)
        if not txt:
            focus(hwnd)
            time.sleep(0.15)
            _shift_tab()
            cand = _copy_transcript_once()
            if cand:
                txt, method = cand, "shift-tab-noclick"

        # 3) 최후 폴백 — allow_click=True 일 때만 (기본 비활성)
        for attempt in range(retry if (not txt and allow_click) else 0):
            focus_chat(hwnd, frac=0.45 if attempt == 0 else 0.60)
            cand = _copy_transcript_once()
            if cand:
                txt, method = cand, "click-fallback"
                log("[read] ⚠ 폴백: 클릭 사용")
                break
    finally:
        # ★ 복사가 끝난 시점에 '아직 대상 창이 앞에 있는지' 재확인한다 (TOCTOU).
        #   워처·전송이 동시에 돌면 focus 직후 다른 창이 앞으로 나와, 엉뚱한 방의
        #   내용을 대상 방 것으로 오인한다(실측: B방 내용이 A방 것으로 기록).
        try:
            if txt and win32gui.GetForegroundWindow() != hwnd:
                log(f"[read] ⚠ 복사 중 포그라운드 변경 — 내용 폐기 (room={title})")
                txt = None
        except Exception:
            pass
        win32api.SetCursorPos(cursor)
        clip_write(backup or "")
    sec = time.perf_counter() - t0
    if not txt or txt == "__SENTINEL__":
        return {"ok": False, "sec": round(sec, 3), "messages": [], "chars": 0,
                "method": method}
    msgs = parse_transcript(txt)
    if not msgs:
        return {"ok": False, "sec": round(sec, 3), "messages": [], "chars": len(txt),
                "method": method, "raw": txt}
    return {
        "ok": True,
        "sec": round(sec, 3),
        "chars": len(txt),
        "messages": msgs,
        "raw": txt,
        "method": method,
    }


# ---------------------------------------------------------------- 명령
def cmd_show():
    hwnd = find_main()
    if not hwnd:
        log("[show] 카카오톡 메인창을 찾지 못했습니다.")
        return 2
    log(f"[show] hwnd={hwnd} '{win32gui.GetWindowText(hwnd)}'")
    kt_show.show(hwnd, MAIN_STD_W, MAIN_STD_H)
    enforce(hwnd, MAIN_STD_W, MAIN_STD_H)
    log(f"[show] OK {win32gui.GetWindowRect(hwnd)} (목표 {MAIN_STD_W}x{MAIN_STD_H})")
    return 0


def get_room(name=None):
    """kt_room.find_room_window 는 (hwnd, title, rect) 를 준다."""
    r = kt_room.find_room_window(name)
    if not r:
        return None
    try:
        h, title, _rect = r
    except Exception:
        return None
    if not h:
        return None
    return h, title


def cmd_open(name):
    # ★ stdin=DEVNULL 필수: MCP stdio 서버처럼 부모의 stdin 이 파이프로 열려 있으면
    #   자식이 그 파이프를 상속받아 읽으려다 영원히 블록된다.
    #   실측(2026-11-05): MCP 경유 호출 시 없는 방 요청이 180초 타임아웃까지 걸렸다.
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "kt_open.py"), name],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, stdin=subprocess.DEVNULL,
    )
    out = (r.stdout or "") + (r.stderr or "")
    log(out.strip())
    room = get_room(name)
    if room:
        hwnd, _t = room
        enforce(hwnd, ROOM_STD_W, ROOM_STD_H)
        scroll_bottom(hwnd)
        log(f"[open] 방 크기 강제 {ROOM_STD_W}x{ROOM_STD_H} -> {win32gui.GetWindowRect(hwnd)}")
        log("[open] 스크롤 맨 아래(최신)로 이동")
    return 0 if "OK" in out or room else 1


def _to_min(m):
    mo, hm = m["time"].split()
    hh, mm = map(int, hm.split(":"))
    if mo == "오후" and hh != 12:
        hh += 12
    if mo == "오전" and hh == 12:
        hh = 0
    return hh * 60 + mm


def filter_since(msgs, hours):
    """최근 N시간 이내 메시지만. 날짜 순번 × 1440 + 시각으로 절대분 계산."""
    import datetime
    dayidx = {}
    seq = []
    prev = None
    for m in msgs:
        d = m["date"] or (seq[-1][1]["date"] if seq else None)
        if d not in dayidx:
            dayidx[d] = len(dayidx) * 1440
        t0 = dayidx[d] + _to_min(m)
        if prev is not None and t0 < prev:
            dayidx[d] += 1440
            t0 = dayidx[d] + _to_min(m)
        prev = t0
        seq.append((t0, m))
    if not seq:
        return msgs
    cutoff = seq[-1][0] - hours * 60
    return [m for t0, m in seq if t0 >= cutoff]


def press_key(vk, times=1, delay=0.9):
    """키보드 키 반복 입력. 카카오톡 스크롤은 PageUp/PageDown 사용."""
    for _ in range(times):
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.04)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(delay)


VK_PAGEUP = 0x21
VK_PAGEDOWN = 0x22
VK_HOME = 0x24
VK_END = 0x23


def focus_chat(hwnd, frac=0.45, strict=True):
    """대화 본문을 클릭해 포커스를 준다. (입력창·이모티콘 버튼 회피)

    strict=True 면 포그라운드 확보 실패 시 클릭하지 않고 False 를 돌려준다.
    → 좌표 클릭이 엉뚱한 창/다른 방에 찍히는 사고를 원천 차단.
    """
    if not focus(hwnd) and strict:
        return False
    time.sleep(0.25)
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
    except Exception:
        return False
    click(l + (r - l) // 2, t + int((b - t) * frac))
    time.sleep(0.3)
    return True


def scroll_load(hwnd, times=1):
    """PageUp 키로 위로 스크롤해 이전 메시지를 더 로드한다.

    카카오톡은 화면에 로드된 범위만 Ctrl+A 복사에 포함되므로 필수.
    ★ 클릭 없이 — 포커스만 준 뒤 PageUp. 링크·이모지 오클릭 방지.
    """
    focus(hwnd)
    time.sleep(0.2)
    press_key(VK_PAGEUP, times, delay=1.1)


def scroll_bottom(hwnd):
    """End 키로 맨 아래(최신) 복귀. ★ 클릭 없이 — 포커스만 준다."""
    focus(hwnd)
    time.sleep(0.2)
    press_key(VK_END, 1, delay=0.5)
    # End 가 안 먹는 경우(포커스가 입력창)에만 Shift+Tab 후 재시도
    _shift_tab()
    press_key(VK_END, 1, delay=0.5)


def ensure_chat_focus_noclick(hwnd):
    """클릭 없이 대화영역 포커스 확보 (Shift+Tab 사용). 실패 시 False."""
    focus(hwnd)
    time.sleep(0.2)
    _shift_tab()
    return True


# PageUp 1회당 추가 로드되는 메시지 수 (실측: 10 → 40 → 70 → 100)
LOAD_PER_PAGEUP = 30
# 읽기 기본 목표 건수 = 스크롤 단위와 일치시켜야 놓치지 않는다
DEFAULT_BATCH = 30


def read_collect(hwnd, title, since_h=None, target=DEFAULT_BATCH,
                 max_scroll=10, verbose=True):
    """목표 건수를 확보할 때까지 PageUp으로 로드 범위를 자동 확장한다.

    스크롤 1회가 30건을 로드하는데 읽기가 그보다 적으면
    화면 밖 과거 메시지를 놓친다. 그래서 '조건 충족 건수'가
    목표에 닿을 때까지 한 페이지씩 더 올린다.
    """
    res = None
    sel = []
    prev_loaded = -1
    same_count = 0
    for step in range(max_scroll + 1):
        res = read_via_clipboard(hwnd, title)
        if not res["ok"]:
            time.sleep(0.5)
            res = read_via_clipboard(hwnd, title)
        msgs = res.get("messages") or []
        loaded = len(msgs)
        sel = filter_since(msgs, since_h) if since_h is not None else msgs
        if verbose:
            log(f"  [load] {step}회차: 로드 {loaded}건 → 조건충족 {len(sel)}건 (목표 {target})")
        if len(sel) >= target:
            return res, sel, step
        if loaded == prev_loaded:
            same_count += 1
            # 렌더 지연일 수 있으므로 2회 연속 동일할 때만 전체 로드로 판정
            if same_count >= 2:
                if verbose:
                    log("  [load] 2회 연속 증가 없음 — 전체 로드 완료")
                return res, sel, step
        else:
            same_count = 0
        prev_loaded = loaded
        scroll_load(hwnd, 1)
        time.sleep(0.5)
    if verbose:
        log(f"  [load] 스크롤 상한 {max_scroll}회 도달")
    return res, sel, max_scroll


def cmd_read(name=None, mode="clip", as_json=False, last=None, max_chars=200,
             save=False, deep=0, since_h=None, max_scroll=10):
    room = get_room(name)
    if not room and name:
        log(f"[read] '{name}' 방이 닫혀 있어 자동으로 엽니다.")
        cmd_open(name)
        room = get_room(name)
    if not room:
        log("[read] 열린 방 창이 없습니다. 먼저: python kt.py open \"방이름\"")
        return 2
    hwnd, title = room
    enforce(hwnd, ROOM_STD_W, ROOM_STD_H)

    # ★ 기준점 고정: 항상 맨 아래(최신)에서 시작해야 '위로 N건'이 성립한다
    scroll_bottom(hwnd)
    log("[read] 스크롤 맨 아래(최신)로 복귀")

    if deep:
        scroll_load(hwnd, deep)
        log(f"[read] 위로 스크롤 {deep}회 → 로드 범위 확대")

    if mode == "ocr":
        t0 = time.perf_counter()
        res = kt_room.read_room(hwnd, title, name, want_json=True)
        sec = time.perf_counter() - t0
        items = res.get("messages") or res.get("content") or []
        total = len(items)
        if last:
            items = items[-last:]
        log(f"[read] 방식=WindowsOCR {sec:.3f}s 방='{title}' 전체 {total}건 → {len(items)}건")
        if as_json:
            log(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            for m in items:
                t = (m.get("text") or "").strip()
                log(f"  {t[:max_chars]}")
        return 0

    res = read_via_clipboard(hwnd, title)
    if not res["ok"]:
        log("[read] 클립보드 전사 실패 — OCR로 폴백합니다.")
        return cmd_read(name, "ocr", as_json, last, max_chars, save)

    # 목표 건수 기본 30 (스크롤 1회 로드량과 일치 → 맥락 파악 충분)
    target = last if last else DEFAULT_BATCH
    steps = 0
    if target:
        res, msgs, steps = read_collect(hwnd, title, since_h, target, max_scroll)
        total = len(res.get("messages") or [])
        if len(msgs) > target:
            msgs = msgs[-target:]
        # 다음 읽기를 위해 최신(맨 아래)으로 복귀
        scroll_bottom(hwnd)
    else:
        msgs = res["messages"]
        total = len(msgs)
        if since_h is not None:
            msgs = filter_since(msgs, since_h)
        if last:
            msgs = msgs[-last:]

    log(f"[read] 방식=클립보드 {res['sec']}s / 로드 {total}건 → 출력 {len(msgs)}건 "
        f"/ 자동스크롤 {steps}회 / 방='{title}'")
    if as_json:
        log(json.dumps([dict(m) for m in msgs], ensure_ascii=False, indent=2))
    else:
        for m in msgs:
            head = f"[{m['date']}] {m['sender']} ({m['time']})" if m["date"] else f"{m['sender']} ({m['time']})"
            body = m["body"].replace("\n", " / ")
            if max_chars and len(body) > max_chars:
                body = body[:max_chars] + "…"
            log(f"{head}: {body}")

    if save:
        path = os.path.join(HERE, "_room", "read_clipboard.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(res["raw"])
        log(f"[read] 원본 저장: {path}")
    return 0


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


VK_RETURN = 0x0D
VK_DELETE = 0x2E


def clear_input(hwnd, n=200):
    """입력창 내용을 비운다. ★ 클릭 없이 — 입력창에 SetFocus 후 Backspace.

    ★ Ctrl+A 는 입력창이 아니라 '대화 전체선택'으로 갈 수 있어 쓰지 않는다.
    ★ Escape 는 방/앱을 닫으므로 절대 쓰지 않는다.
    """
    try:
        focus_child_noclick(hwnd, EVA_INPUT_CLASS)
        time.sleep(0.12)
        press_key(0x08, n, delay=0.008)   # VK_BACK × n
        time.sleep(0.2)
        return True
    except Exception:
        return False


def input_text(hwnd):
    """입력창에 실제로 들어있는 텍스트를 읽는다 (WM_GETTEXT — 클릭·클립보드 불필요)."""
    try:
        v = read_input_box(hwnd)
        if v is None:
            return ""
        # 비어 있을 때 나오는 플레이스홀더는 빈 값으로 취급
        return "" if v.strip() in ("메시지 입력", "메시지를 입력하세요", "") else v
    except Exception:
        return ""


def send_text(hwnd, title, text, expect_title=None, dry_run=True):
    """안전 가드가 붙은 전송.

    순서:
      1) 방 이름 대조 (기대와 다르면 중단)
      2) 기준 크기 + 맨 아래(최신) 고정
      3) 클립보드 백업
      4) 입력 전 마지막 메시지 스냅샷
      5) 입력창 클릭 → Ctrl+A → Ctrl+V
      6) 입력 내용 검증 (Ctrl+A/Ctrl+C로 실제 입력창 읽기)
      7) ★ 새 메시지 도착 가드 (스냅샷과 달라졌으면 전송 취소)
      8) Enter 전송
      9) 재읽기로 내 메시지 확인
     10) 클립보드 복원
    """
    # 1) 방 이름 대조
    if expect_title:
        from kt_open import similarity
        s = similarity(expect_title, title)
        if s < 0.5:
            log(f"[send] ✗ 중단: 방 불일치 '{title}' (기대='{expect_title}', 유사도 {s})")
            return 3
        log(f"[send] 방 이름 대조 OK: '{title}' (유사도 {s})")

    # 2) 기준 크기 + 최신 위치
    enforce(hwnd, ROOM_STD_W, ROOM_STD_H)
    scroll_bottom(hwnd)

    backup = clip_read()
    cursor = win32api.GetCursorPos()
    ok = False
    set_watch_pause(True)
    try:
        # 3) 입력 전 스냅샷
        before = read_via_clipboard(hwnd, title)
        before_msgs = before.get("messages") or []
        before_last = before_msgs[-1] if before_msgs else None
        if before_last:
            log(f"[send] 입력 전 마지막: {before_last['sender']} ({before_last['time']}) "
                f"{(before_last['body'] or '')[:40]}")

        # 4) ★입력창 포커스 — 클릭 없이 자식 컨트롤에 SetFocus
        #    (대화 본문/입력창을 좌표 클릭하면 그 자리의 링크·이모지가 눌려
        #     브라우저나 이모티콘 창이 뜬다 → 좌표 클릭을 쓰지 않는다)
        rich, ok = focus_child_noclick(hwnd, EVA_INPUT_CLASS)
        if not ok:
            log("[send] ✗ 중단: 입력창 포커스 확보 실패 — 입력하지 않음")
            return 8
        time.sleep(0.12)
        if win32gui.GetForegroundWindow() != hwnd:
            log("[send] ✗ 중단: 입력 직전 활성 창이 대상이 아님")
            return 8

        # 5) 붙여넣기 (한글 IME 우회)
        clip_write(text)
        time.sleep(0.15)
        ctrl_key(0x41)   # 기존 내용 전체 선택
        time.sleep(0.10)
        ctrl_key(0x56)   # Ctrl+V
        time.sleep(0.5)

        # 6) 입력 내용 검증 — WM_GETTEXT 로 입력창을 직접 읽는다(클릭·클립보드 불필요)
        typed = input_text(hwnd)
        if not typed or _norm(typed) != _norm(text):
            log(f"[send] ✗ 중단: 입력 내용 불일치 (기대={_norm(text)[:40]!r}, 실제={_norm(typed)[:40]!r})")
            return 4
        log(f"[send] 입력 검증 OK ({len(text)}자)")

        # 7) 새 메시지 도착 가드
        now = read_via_clipboard(hwnd, title)
        now_msgs = now.get("messages") or []
        now_last = now_msgs[-1] if now_msgs else None
        if before_last and now_last and (
            now_last["time"] != before_last["time"]
            or _norm(now_last["body"]) != _norm(before_last["body"])
        ):
            log(f"[send] ✗ 중단: 그 사이 새 메시지 도착 "
                f"({now_last['sender']} {now_last['time']} {(now_last['body'] or '')[:40]})")
            return 5

        if dry_run:
            log(f'[send] dry-run: "{text}" 입력 완료 (전송 안 함)')
            ok = True
            return 0

        # 8) 전송 — ★ Enter 직전 마지막 포커스 확인 (엉뚱한 창 전송 차단)
        if win32gui.GetForegroundWindow() != hwnd:
            log("[send] ✗ 중단: 전송 직전 활성 창이 대상이 아님")
            return 8
        press_key(VK_RETURN, 1, delay=0.0)
        time.sleep(1.4)
        log(f'[send] Enter 전송 실행')

        # 9) 전송 확인
        after = read_via_clipboard(hwnd, title)
        after_msgs = after.get("messages") or []
        tail = after_msgs[-3:]
        hit = any(_norm(text) in _norm(m["body"]) for m in tail)
        if hit:
            log(f"[send] ✓ 전송 확인됨 (최근 {len(tail)}건 안에 발견)")
            ok = True
            return 0
        log(f"[send] ⚠ 전송 확인 실패 — 최근 메시지: "
            f"{[ (m['time'], (m['body'] or '')[:25]) for m in tail ]}")
        return 6
    finally:
        # ★ 입력창에 글자가 남으면 다음 Enter에 오발송될 수 있다 → 반드시 비운다
        #   dry-run 은 전송하지 않았으므로 반드시 정리, 실패 시에도 정리
        if dry_run or not ok:
            clear_input(hwnd)
            log("[send] 입력창 정리 완료")
        set_watch_pause(False)
        win32api.SetCursorPos(cursor)
        clip_write(backup or "")


def list_rooms():
    """열린 모든 방 창 (메인창 제외)."""
    import psutil
    import win32process
    cands = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        title = win32gui.GetWindowText(h)
        if not title or title == MAIN_TITLE:
            return
        try:
            # ★ 채팅 창은 EVA_Window / EVA_Window_Dblclk 클래스만 해당한다.
            #   CaptureSelectionWnd(Afx:...) 같은 카톡 내부 도구 창을 방으로 오인하면
            #   존재하지 않는 방 목록이 노출되고 엉뚱한 창에 입력을 시도할 수 있다.
            #   실측(2026-11-05): 카톡 캡처 도구 창 9개가 방으로 잘못 잡혔다.
            if "EVA" not in win32gui.GetClassName(h):
                return
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid and "kakao" in psutil.Process(pid).name().lower():
                cands.append((h, title))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return cands


def cmd_send(text, dry_run=True, to=None, operation_id=None, force=False, dup_window=60):
    # 방이 여러 개 열려 있는데 대상 미지정 → 오발송 위험. 중단.
    opened = list_rooms()
    if not to and len(opened) > 1:
        log("[send] ✗ 중단: 방이 여러 개 열려 있습니다. --to 로 대상을 지정하세요.")
        for h, t in opened:
            log(f"        - '{t}' (hwnd={h})")
        return 7

    room = get_room(to)
    if not room:
        log("[send] 열린 방 창이 없습니다.")
        return 2
    hwnd, title = room

    # ── 멱등 가드 ────────────────────────────────────────────────────────
    # 목적: 전송 결과가 애매할 때(카톡이 긴 글을 접어 확인 실패 등) 사람이나
    #       에이전트가 같은 글을 다시 보내 중복 발송되는 사고를 막는다.
    # 1) operation_id 를 지정하면 그 ID 로 이미 처리된 적이 있는지 본다.
    # 2) 지정이 없어도 dup_window 초 안에 같은 방에 같은 본문을 보낸 기록이
    #    있으면 막는다. --force 로만 통과한다.
    op = (operation_id or "").strip()
    if op and not force:
        prior = ledger_get(op)
        if prior and prior.get("status") in ("sent", "sent_unverified"):
            log(f"[send] ✗ 중단: operation_id '{op}' 는 이미 처리됐습니다 "
                f"(status={prior.get('status')}, {prior.get('at')}).")
            log("        재전송하지 않았습니다. 도착 여부는 read 로 확인하세요.")
            log("        그래도 보내야 하면 --force 또는 새 operation_id 를 쓰세요.")
            return 9

    if not dry_run and not force:
        dup = ledger_recent_dup(title, text, within=dup_window)
        if dup:
            log(f"[send] ✗ 중단: {dup_window}초 안에 같은 본문을 이미 보냈습니다 "
                f"({dup.get('at')}, status={dup.get('status')}).")
            log("        중복 발송으로 판단해 취소했습니다. 그래도 보내려면 --force.")
            return 9

    log(f"[send] 대상 방='{title}' {'(dry-run)' if dry_run else '(실제 전송)'}")
    code = send_text(hwnd, title, text, expect_title=to, dry_run=dry_run)

    # ── 결과 기록 (dry_run 은 기록하지 않는다) ──
    if not dry_run:
        if code == 0:
            status = "sent"
        elif code == 6:
            # 전송은 됐으나 확인 실패 — 카톡이 긴 글을 '전체보기'로 접은 경우 등
            status = "sent_unverified"
        else:
            status = "failed"
        ledger_append({
            "operation_id": op or ("auto-" + secrets.token_hex(8)),
            "room": title,
            "text_hash": text_hash(title, text),
            "status": status,
            "code": code,
            "chars": len(text or ""),
            "ts": time.time(),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "src": "cli",
        })
        if status == "sent_unverified":
            log("[send] ⚠ 전송은 실행됐으나 확인이 실패했습니다(긴 글 접힘 등).")
            log("        재전송하지 말고 read 로 실제 도착을 확인하세요.")
    return code


def cmd_size():
    room = get_room()
    hwnd = room[0] if room else 0
    if hwnd:
        before = win32gui.GetWindowRect(hwnd)
        enforce(hwnd, ROOM_STD_W, ROOM_STD_H)
        log(f"[size] 방 '{room[1]}' {before} -> {win32gui.GetWindowRect(hwnd)}")
    main = find_main()
    if main:
        before = win32gui.GetWindowRect(main)
        enforce(main, MAIN_STD_W, MAIN_STD_H)
        log(f"[size] 메인 {before} -> {win32gui.GetWindowRect(main)}")
    if not hwnd and not main:
        log("[size] 카카오톡 창 없음")
        return 2
    return 0


def cmd_windows():
    for h, t, w, hh, pos, vis in list_windows():
        log(f"hwnd={h} '{t}' {w}x{hh} at {pos} visible={int(vis)}")
    return 0


def cmd_alerts(n=20, show_all=False, clear=False, mark_read=False):
    """라온에게 온 트리거 알림 조회 (_work/alerts.jsonl)."""
    p = os.path.join(HERE, "_work", "alerts.jsonl")
    if clear:
        if os.path.exists(p):
            os.remove(p)
            log(f"[alerts] 큐 비움: {p}")
            return 0
        log("[alerts] 큐 파일 없음")
        return 0
    if not os.path.exists(p):
        log("[alerts] 알림 없음 (파일 없음)")
        return 0
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    target = rows if show_all else [r for r in rows if not r.get("read")]
    if target:
        for r in target[-n:]:
            log(f"[{r.get('ts','')}] {r.get('room','')} / {r.get('sender','')} "
                f"({r.get('when','')})")
            log(f"    {r.get('text','')}")
        log(f"[alerts] {min(n,len(target))}건 표시 (미확인 {sum(1 for r in rows if not r.get('read'))}건 / 전체 {len(rows)}건)")
    else:
        log("[alerts] 새 알림 없음")

    # 읽음 처리 — 같은 알림이 매 주기 반복 보고되지 않도록
    if mark_read and target:
        try:
            for r in rows:
                r["read"] = True
            with open(p, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            log(f"[alerts] {len(target)}건 읽음 처리 완료")
        except Exception as e:
            log(f"[alerts] 읽음 처리 실패: {e}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="카카오톡 자동화 통합 CLI")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show")
    p_open = sub.add_parser("open")
    p_open.add_argument("name")
    p_read = sub.add_parser("read")
    p_read.add_argument("name", nargs="?")
    p_read.add_argument("--ocr", action="store_true")
    p_read.add_argument("--json", action="store_true")
    p_read.add_argument("-n", "--last", type=int, default=None,
                        help="최근 N개 메시지만 읽기 (권장: 1:1은 15, 단체는 25~30)")
    p_read.add_argument("--deep", type=int, default=0, metavar="N",
                        help="위로 스크롤 N회 해서 이전 메시지 더 로드")
    p_read.add_argument("--since", type=float, default=None, metavar="H",
                        help="최근 H시간 이내 메시지만 (기본 목표 30건까지 자동 스크롤)")
    p_read.add_argument("--max-scroll", type=int, default=10, metavar="N",
                        help="자동 스크롤 최대 횟수 (기본 10)")
    p_read.add_argument("--max-chars", type=int, default=200,
                        help="메시지 본문 최대 글자수 (기본 200)")
    p_read.add_argument("--save", action="store_true",
                        help="원본을 로컬 파일로 저장 (기본 저장 안 함)")
    p_send = sub.add_parser("send")
    p_send.add_argument("text")
    p_send.add_argument("--to", default=None, metavar="방이름",
                        help="전송 전 방 이름 대조 (지정 시)")
    p_send.add_argument("--yes", action="store_true")
    p_send.add_argument("--dry-run", action="store_true")
    p_send.add_argument("--op-id", default=None, metavar="ID",
                        help="작업 식별자. 같은 ID 재사용 시 중복 전송을 막는다 "
                             "(예: reply-20261105-01)")
    p_send.add_argument("--force", action="store_true",
                        help="멱등/중복 가드를 무시하고 강제 전송")
    p_send.add_argument("--dup-window", type=int, default=60, metavar="초",
                        help="같은 본문 중복 차단 시간 (기본 60초)")
    sub.add_parser("size")
    sub.add_parser("windows")
    p_alerts = sub.add_parser("alerts")
    p_alerts.add_argument("-n", type=int, default=20, help="표시할 최근 건수")
    p_alerts.add_argument("--all", action="store_true", help="읽은 것도 포함")
    p_alerts.add_argument("--clear", action="store_true", help="알림 큐 비우기")
    p_alerts.add_argument("--mark-read", action="store_true", help="확인한 알림을 읽음 처리")

    a = ap.parse_args()
    if a.cmd == "show":
        return cmd_show()
    if a.cmd == "open":
        return cmd_open(a.name)
    if a.cmd == "read":
        return cmd_read(a.name, "ocr" if a.ocr else "clip", a.json,
                        a.last, a.max_chars, a.save, a.deep, a.since, a.max_scroll)
    if a.cmd == "send":
        return cmd_send(a.text, dry_run=not a.yes, to=a.to,
                        operation_id=a.op_id, force=a.force,
                        dup_window=a.dup_window)
    if a.cmd == "size":
        return cmd_size()
    if a.cmd == "windows":
        return cmd_windows()
    if a.cmd == "alerts":
        return cmd_alerts(a.n, a.all, a.clear, a.mark_read)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
