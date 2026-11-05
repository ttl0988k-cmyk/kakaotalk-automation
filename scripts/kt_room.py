#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kt_room.py - 카카오톡 채팅방 읽기 / 쓰기

사용법:
    python kt_room.py read                    # 현재 열린 방 읽기
    python kt_room.py read --json             # JSON으로 출력
    python kt_room.py send "보낼 메시지"       # 메시지 전송
    python kt_room.py send "메시지" --dry-run  # 입력만 하고 전송 안 함
    python kt_room.py watch                   # 새 메시지 감시 (폴링)

읽기 원리:
    - agent-desktop 으로 방 창 캡처 (창 크기 그대로)
    - Windows OCR 로 라인별 텍스트 + 좌표
    - y좌표 순 정렬 → 날짜 구분선/시간/본문 파싱
    - 말풍선 색(노랑 #FEE500)으로 내 메시지 판별

쓰기 원리:
    - 입력창 좌표 클릭
    - 클립보드에 텍스트 넣고 Ctrl+V (한글 IME 우회)
    - Enter
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

import win32con
import win32gui

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_PS1 = os.path.join(HERE, 'win_ocr.ps1')
WORK = os.path.join(HERE, '_room')
MAIN_TITLE = '카카오톡'

# ---- 기준 창 크기 (스크립트가 이 크기로 강제 맞춘다) ----
# 다른 사람/도구가 창 크기를 바꿔도 스크립트가 여기로 되돌린다.
ROOM_STD_W, ROOM_STD_H = 490, 736      # 채팅방 창
MAIN_STD_W, MAIN_STD_H = 667, 878      # 메인 창 (kt_open.py 와 동일)

# 말풍선 색 (카카오 노랑)
KAKAO_YELLOW = (254, 229, 0)


def log(m):
    print(m, flush=True)


# ------------------------------------------------------------------ 창
def enforce_size(hwnd, title):
    """
    창 크기를 기준 크기로 강제 통일한다.
    - 메인 창('카카오톡') -> MAIN_STD_*
    - 채팅방 창 -> ROOM_STD_*
    이미 기준 크기면 아무것도 하지 않는다.
    반환: (w, h, changed)
    """
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    cur_w, cur_h = r - l, b - t

    if title == MAIN_TITLE:
        tw, th = MAIN_STD_W, MAIN_STD_H
    else:
        tw, th = ROOM_STD_W, ROOM_STD_H

    if (cur_w, cur_h) == (tw, th):
        return cur_w, cur_h, False

    win32gui.SetWindowPos(
        hwnd, 0, l, t, tw, th,
        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    )
    time.sleep(0.5)
    l2, t2, r2, b2 = win32gui.GetWindowRect(hwnd)
    log(f'[size] 창 크기 통일: {cur_w}x{cur_h} -> {r2-l2}x{b2-t2}')
    return r2 - l2, b2 - t2, True


def find_room_window(room_name=None):
    """열린 방 창 찾기. 메인 창(카카오톡) 제외."""
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
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid and 'kakao' in psutil.Process(pid).name().lower():
                cands.append((h, title, win32gui.GetWindowRect(h)))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)

    if room_name:
        from kt_open import similarity
        best, bs = None, 0.0
        for h, ti, rc in cands:
            s = similarity(room_name, ti)
            if s > bs:
                best, bs = (h, ti, rc), s
        if best and bs > 0.5:
            return best
        return None
    return cands[0] if cands else None


def focus(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.25)
    for _ in range(3):
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return win32gui.GetForegroundWindow() == hwnd


def agent_wid(title):
    try:
        raw = subprocess.run(
            ['npx', '-y', 'agent-desktop@latest', 'list-windows'],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=60, shell=True
        ).stdout
        for w in json.loads(raw)['data']:
            if w.get('title') == title:
                return w['id']
    except Exception:
        pass
    return None


def capture(hwnd, title, path):
    wid = agent_wid(title)
    if wid:
        subprocess.run(
            ['npx', '-y', 'agent-desktop@latest', 'screenshot',
             '--window-id', wid, path],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=90, shell=True
        )
        if os.path.exists(path):
            return True
    try:
        from PIL import ImageGrab
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        ImageGrab.grab(bbox=(l, t, r, b)).save(path)
        return os.path.exists(path)
    except Exception:
        return False


def ocr(path, jpath):
    subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-File', OCR_PS1, '-Path', path, '-OutJson', jpath],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=120
    )
    if not os.path.exists(jpath):
        return []
    with open(jpath, encoding='utf-8') as f:
        return json.load(f).get('content', [])


# ------------------------------------------------------------------ 말풍선 색 판정
def bubble_side(img_path, y_top, y_bottom, box_x0, box_x1):
    """
    말풍선 영역의 색으로 내 메시지 여부 판정.
    노란색 비율이 높으면 내 메시지(오른쪽 정렬).
    반환: ('me'|'other'|'unknown', yellow_ratio, right_aligned)
    """
    try:
        from PIL import Image
        im = Image.open(img_path).convert('RGB')
        W, _ = im.size
        y0 = max(0, y_top)
        y1 = min(im.height, y_bottom)
        x0 = max(0, box_x0)
        x1 = min(W, box_x1)
        if x1 <= x0 or y1 <= y0:
            return 'unknown', 0.0, False
        crop = im.crop((x0, y0, x1, y1))
        px = list(crop.getdata())
        if not px:
            return 'unknown', 0.0, False
        yellow = 0
        for r, g, b in px:
            if r > 200 and g > 180 and b < 120:
                yellow += 1
        ratio = yellow / len(px)
        # 우측 정렬 여부: 오른쪽 절반에 텍스트가 몰려있는지
        right_aligned = (x1 > W * 0.55) and (x0 > W * 0.25)
        side = 'me' if ratio > 0.15 else ('other' if ratio < 0.03 else 'unknown')
        return side, round(ratio, 3), right_aligned
    except Exception:
        return 'unknown', 0.0, False


# ------------------------------------------------------------------ 읽기
# OCR이 "2026년 1 1 4일" 처럼 숫자를 띄우는 경우까지 허용
DATE_RE = re.compile(r'(20\d{2})\s*년\s*(1?\d)\s*(1?\d)\s*(\d{1,2})\s*일')
DATE_LOOSE = re.compile(r'년.*월.*일|년.*일\s*(월|화|수|목|금|토|일)요일')
TIME_RE = re.compile(r'(오전|오후)\s*(\d{1,2})\s*[:：]\s*(\d{2})')
# OCR이 "= 450", "450" 처럼 읽는 시간 표기
TIME_LOOSE = re.compile(r'^[=\-\s]*(오전|오후)?\s*(\d{1,2})\s*[:：]?\s*(\d{2})[^0-9]*$')
UI_NOISE = ('전체보기', '전치보기', '메시지 입력', 'ptL시지')


def classify_line(txt, y, H):
    """라인 분류: date / time / body / noise"""
    s = (txt or '').strip()
    if not s:
        return 'noise'

    # UI 잡음
    for n in UI_NOISE:
        if n in s:
            return 'noise'

    # 날짜 (요일 포함이면 확정)
    if DATE_RE.search(s):
        return 'date'
    if re.search(r'(20\d{2})', s) and re.search(r'(월|일)', s) and re.search(r'요일', s):
        return 'date'
    if DATE_LOOSE.search(s):
        return 'date'

    # 시간 (짧은 라인만)
    if len(s) <= 14:
        if TIME_RE.search(s):
            return 'time'
        if TIME_LOOSE.match(s) and re.search(r'\d{2}', s):
            return 'time'

    # 상단 헤더 / 하단 입력창
    # 입력창은 하단 고정. 실측: 736px 창에서 입력창 텍스트 y≈630
    if y < H * 0.10 or y > H * 0.845:
        return 'noise'

    # 한글 없는 짧은 기호
    if len(s) <= 2 and not re.search(r'[가-힣]', s):
        return 'noise'

    return 'body'


def read_room(hwnd, title, room_name=None, want_json=False):
    os.makedirs(WORK, exist_ok=True)
    png = os.path.join(WORK, 'room.png')
    jpath = os.path.join(WORK, 'room.json')
    for p in (png, jpath):
        if os.path.exists(p):
            os.remove(p)

    focus(hwnd)
    enforce_size(hwnd, title)          # 창 크기를 기준 크기로 통일
    if not capture(hwnd, title, png):
        return {'ok': False, 'error': 'capture_failed'}

    from PIL import Image
    W, H = Image.open(png).size

    items = ocr(png, jpath)
    if not items:
        return {'ok': False, 'error': 'ocr_failed'}

    items = sorted(items, key=lambda it: (it.get('y', 0), it.get('x', 0)))

    dates, times, bodies = [], [], []
    for it in items:
        y = it.get('y', 0)
        txt = (it.get('text') or '').strip()
        kind = classify_line(txt, y, H)
        if kind == 'date':
            dates.append({'y': y, 'text': txt})
        elif kind == 'time':
            times.append({'y': y, 'text': txt})
        elif kind == 'body':
            bodies.append(it)

    # 말풍선 그룹핑: 직전 라인 bottom 과의 간격으로 판단 (고정 임계)
    # 실측: 줄 간격 15~17px, 말풍선 간 간격 22~41px
    GAP_SAME_BUBBLE = 9
    groups = []
    prev_bottom = None
    for it in bodies:
        if groups and prev_bottom is not None and (it['y'] - prev_bottom) <= GAP_SAME_BUBBLE:
            g = groups[-1]
            g['lines'].append(it['text'])
            g['x0'] = min(g['x0'], it['x'])
            g['x1'] = max(g['x1'], it['right'])
            g['y1'] = max(g['y1'], it['bottom'])
        else:
            groups.append({
                'lines': [it['text']],
                'y0': it['y'], 'y1': it['bottom'],
                'x0': it['x'], 'x1': it['right'],
            })
        prev_bottom = it['bottom']

    msgs = []
    for g in groups:
        text = ' '.join(g['lines'])
        side, ratio, right = bubble_side(png, g['y0'] - 3, g['y1'] + 3,
                                         g['x0'] - 10, g['x1'] + 10)
        # 노란색 판정 실패 시 정렬로 보정
        if side == 'unknown':
            side = 'me' if right else 'other'
        msgs.append({
            'y': g['y0'],
            'x': g['x0'],
            'side': side,
            'yellow_ratio': ratio,
            'text': text,
        })

    result = {
        'ok': True,
        'room': title,
        'window_size': [W, H],
        'dates': dates,
        'times': times,
        'messages': msgs,
        'count': len(msgs),
    }

    if want_json:
        log(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        log(f'[방] {title}  ({W}x{H})')
        for d in dates:
            log(f'  ── {d["text"]} ──')
        for t in times:
            log(f'  [{t["text"]}]')
        log('')
        for m in msgs:
            mark = ' 나 ' if m['side'] == 'me' else ('상대' if m['side'] == 'other' else ' ?  ')
            log(f'  ({mark}) {m["text"][:90]}')
        log('')
        log(f'메시지 {len(msgs)}건 / 날짜 {len(dates)} / 시간 {len(times)}')
        jout = os.path.join(WORK, 'read_result.json')
        with open(jout, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log(f'저장: {jout}')

    return result


# ------------------------------------------------------------------ 쓰기
def set_clipboard(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def ad(cmd_args, timeout=60):
    return subprocess.run(
        ['npx', '-y', 'agent-desktop@latest'] + cmd_args,
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=timeout, shell=True
    )


def send_message(hwnd, title, text, dry_run=False):
    """입력창 클릭 → 클립보드 붙여넣기 → Enter"""
    focus(hwnd)
    enforce_size(hwnd, title)          # 창 크기를 기준 크기로 통일
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    W, H = r - l, b - t
    time.sleep(0.4)

    # 입력창 위치: 하단부 중앙
    ix = l + int(W * 0.50)
    iy = t + int(H * 0.855)
    log(f'[send] 입력창 클릭 ({ix},{iy})')

    # 입력창 클릭 (포커스)
    ad(['mouse-click', '--headed', '--xy', f'{ix},{iy}'])
    time.sleep(0.5)

    # 클립보드로 붙여넣기 (한글 IME 우회)
    set_clipboard(text)
    time.sleep(0.3)
    ad(['press', '--headed', 'ctrl+a'])
    time.sleep(0.15)
    ad(['press', '--headed', 'ctrl+v'])
    time.sleep(0.6)

    if dry_run:
        log(f'[send] dry-run: "{text}" 입력만 완료 (전송 안 함)')
        return True

    ad(['press', '--headed', 'return'])
    time.sleep(1.2)
    log(f'[send] 전송 완료: "{text}"')
    return True


# ------------------------------------------------------------------ watch
def watch(hwnd, title, interval=3, max_rounds=200):
    """새 메시지 감시."""
    log(f'[watch] 시작 (주기 {interval}초)')
    prev_sig = None
    for i in range(max_rounds):
        res = read_room(hwnd, title)
        if not res.get('ok'):
            time.sleep(interval)
            continue
        sig = tuple(m['text'] for m in res['messages'])
        if prev_sig is not None and sig != prev_sig:
            new = [m for m in res['messages'] if m['text'] not in prev_sig]
            for m in new:
                log(f'[NEW] {m["side"]}: {m["text"][:80]}')
        prev_sig = sig
        time.sleep(interval)
    return True


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description='카카오톡 방 읽기/쓰기')
    ap.add_argument('action', choices=['read', 'send', 'watch'],
                    help='read=읽기, send=보내기, watch=감시')
    ap.add_argument('text', nargs='?', help='send일 때 보낼 메시지')
    ap.add_argument('--room', help='방 이름 (없으면 열린 방 첫 번째)')
    ap.add_argument('--json', action='store_true', help='JSON 출력')
    ap.add_argument('--dry-run', action='store_true', help='입력만, 전송 안 함')
    ap.add_argument('--interval', type=int, default=3, help='watch 주기(초)')
    args = ap.parse_args()

    found = find_room_window(args.room)
    if not found:
        log('[FAIL] 열린 채팅방 창이 없습니다. kt_open.py 로 먼저 여세요.')
        return 2
    hwnd, title, rc = found
    log(f'[창] "{title}" {rc[2]-rc[0]}x{rc[3]-rc[1]} hwnd={hwnd}')

    if args.action == 'read':
        r = read_room(hwnd, title, args.room, args.json)
        return 0 if r.get('ok') else 3

    if args.action == 'send':
        if not args.text:
            log('[FAIL] 보낼 메시지를 지정하세요')
            return 2
        send_message(hwnd, title, args.text, args.dry_run)
        time.sleep(0.8)
        read_room(hwnd, title)
        return 0

    if args.action == 'watch':
        watch(hwnd, title, args.interval)
        return 0

    return 0


if __name__ == '__main__':
    sys.exit(main())
