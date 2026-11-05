#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kt_open.py - 카카오톡 채팅방 자동 열기 (OCR + 좌표 클릭)

사용법:
    python kt_open.py "테스트 커뮤니티"
    python kt_open.py "내게쓴방" --dry-run
    python kt_open.py --list          # 현재 화면의 방 목록만 OCR로 출력

동작 순서:
    1) 메인 창('카카오톡') 찾아 전면 + 원점 확보
    2) agent-desktop screenshot 으로 창 크기 그대로 캡처
    3) Windows OCR 로 라인별 텍스트 + 창내부 좌표 추출
    4) 퍼지 매칭으로 대상 행 특정 (OCR 오독 허용)
    5) 창내부 좌표 -> 화면 절대 좌표 변환
    6) 더블클릭
    7) 새 창 뜨는지 검증
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
WORK = os.path.join(HERE, '_work')

KAKAO_MAIN_TITLE = '카카오톡'
# 클릭 보정 오프셋 (실측 확정)
STD_W, STD_H = 667, 878          # 기준 창 크기 (스크립트가 강제 통일)
OFFSET_X = 90   # OCR 텍스트 시작점 -> 행 전체(아바타 포함) 중심
OFFSET_Y = -8   # OCR 텍스트 상단 -> 행 상단


# ---------------------------------------------------------------- 유틸
def log(msg):
    print(msg, flush=True)


def find_kakao_main():
    """제목이 '카카오톡'인 보이는 창의 hwnd 반환."""
    hits = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == KAKAO_MAIN_TITLE:
            hits.append(h)

    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else None


def focus_window(hwnd):
    """창을 전면으로. SetForegroundWindow 실패 시 재시도."""
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.25)
    for _ in range(3):
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.35)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return win32gui.GetForegroundWindow() == hwnd


def kakao_windows():
    """카카오톡 프로세스의 모든 최상위 창 목록 [(hwnd, title, rect)]."""
    import psutil
    import win32process
    out = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        title = win32gui.GetWindowText(h)
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid and 'kakao' in psutil.Process(pid).name().lower():
                out.append((h, title, win32gui.GetWindowRect(h)))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return out


# ---------------------------------------------------------------- 캡처 + OCR
def capture(hwnd, path):
    """agent-desktop 으로 창 캡처. 창 크기는 건드리지 않음."""
    # agent-desktop 창 ID 찾기
    wid = None
    try:
        raw = subprocess.run(
            ['npx', '-y', 'agent-desktop@latest', 'list-windows'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, shell=True
        ).stdout
        data = json.loads(raw)
        for w in data['data']:
            if w.get('title') == KAKAO_MAIN_TITLE:
                wid = w['id']
                break
    except Exception as e:
        log(f'[warn] list-windows 실패: {e}')

    if wid:
        r = subprocess.run(
            ['npx', '-y', 'agent-desktop@latest', 'screenshot',
             '--window-id', wid, path],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90, shell=True
        )
        if os.path.exists(path):
            return True
        log(f'[warn] screenshot 응답: {r.stdout[-200:]}')

    # 폴백: GDI 직접 캡처
    try:
        from PIL import ImageGrab
        l, t, r_, b = win32gui.GetWindowRect(hwnd)
        ImageGrab.grab(bbox=(l, t, r_, b)).save(path)
        log('[info] GDI 폴백 캡처 사용')
        return os.path.exists(path)
    except Exception as e:
        log(f'[err] 캡처 실패: {e}')
        return False


def ocr_image(img_path, json_path):
    """Windows OCR. 라인별 text/x/y/right/bottom 반환."""
    ps = [
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', OCR_PS1,
        '-Path', img_path,
        '-OutJson', json_path,
    ]
    subprocess.run(ps, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if not os.path.exists(json_path):
        return []
    with open(json_path, encoding='utf-8') as f:
        d = json.load(f)
    return d.get('content', [])


# ---------------------------------------------------------------- 매칭
CHOSEONG = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
JUNGSEONG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
JONGSEONG = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ',
             'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ',
             'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']


def normalize(s):
    """공백·특수문자 제거 후 소문자."""
    return re.sub(r'[\s\.\,\-\_\|\[\]\(\)\{\}\*\\/～~]+', '', s).lower()


def to_jamo(s):
    """한글 음절을 자모열로 분해 (OCR 오독 대응)."""
    out = []
    for ch in s:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            idx = code - 0xAC00
            cho = idx // 588
            jung = (idx % 588) // 28
            jong = idx % 28
            out.append(CHOSEONG[cho])
            out.append(JUNGSEONG[jung])
            if JONGSEONG[jong]:
                out.append(JONGSEONG[jong])
        else:
            out.append(ch)
    return out


def edit_distance(a, b):
    """레벤슈타인 거리."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def similarity(query, line):
    """0.0~1.0 유사도. 자모 단위 편집거리 기반."""
    q = ' '.join(to_jamo(normalize(query)))
    q = q.replace(' ', '')
    ln = ' '.join(to_jamo(normalize(line)))
    ln = ln.replace(' ', '')
    if not q or not ln:
        return 0.0

    # 완전 포함 시 만점
    if q in ln:
        return 1.0

    # 슬라이딩 윈도우로 최적 구간 비교 (라인에 미리보기 텍스트가 붙어있는 경우 대응)
    best = 0.0
    qlen = len(q)
    if len(ln) <= qlen:
        d = edit_distance(q, ln)
        best = 1.0 - d / max(qlen, len(ln))
    else:
        for start in range(0, len(ln) - qlen + 1):
            seg = ln[start:start + qlen]
            d = edit_distance(q, seg)
            s = 1.0 - d / qlen
            if s > best:
                best = s
                if best == 1.0:
                    break
    return max(0.0, best)


def match_room(items, query):
    """
    자모 유사도 매칭. 반환: (best_item, score)
    제목 줄은 짧고, 미리보기 줄은 길다 -> 길이 페널티 적용
    """
    qn = normalize(query)
    if not qn:
        return None, 0.0

    best, best_score = None, 0.0
    for it in items:
        text = it.get('text', '')
        if len(text.strip()) < 1:
            continue
        sim = similarity(query, text)

        # 길이 페널티: 질의보다 과하게 긴 라인은 미리보기일 확률이 높음
        extra = max(0, len(normalize(text)) - len(qn) * 2)
        penalty = min(0.25, extra * 0.012)
        sim -= penalty

        if sim > best_score:
            best, best_score = it, sim

    return best, round(best_score, 3)


def title_row_hits(items):
    """행 제목 후보(미리보기 줄이 아닌 것) 필터링용 휴리스틱."""
    out = []
    for it in items:
        t = it.get('text', '')
        if len(t.strip()) < 2:
            continue
        out.append(it)
    return out


# ---------------------------------------------------------------- 클릭
def click_at(x, y, count=2):
    subprocess.run(
        ['npx', '-y', 'agent-desktop@latest', 'mouse-click',
         '--headed', '--xy', f'{x},{y}', '--count', str(count)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, shell=True
    )


# ---------------------------------------------------------------- 메인
def main():
    ap = argparse.ArgumentParser(description='카카오톡 채팅방 자동 열기')
    ap.add_argument('room', nargs='?', help='방 이름 (부분일치 허용)')
    ap.add_argument('--list', action='store_true', help='현재 화면 방 목록만 출력')
    ap.add_argument('--dry-run', action='store_true', help='클릭하지 않고 좌표만 출력')
    ap.add_argument('--count', type=int, default=2, help='클릭 횟수 (기본 2=더블클릭)')
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)

    # 1) 창 찾기
    hwnd = find_kakao_main()
    if not hwnd:
        log('[FAIL] 카카오톡 메인 창을 찾을 수 없음 (제목="카카오톡")')
        return 2
    before = {h for h, _, _ in kakao_windows()}
    log(f'[1/7] 창 발견 hwnd={hwnd}')

    # 창 크기를 스크립트 기준(667x878)으로 강제 통일
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    if (r - l, b - t) != (STD_W, STD_H):
        win32gui.SetWindowPos(hwnd, 0, l, t, STD_W, STD_H,
                              win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        time.sleep(0.5)
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        log(f'[1/7] 창 크기 통일 -> {r-l}x{b-t}')
    log(f'[1/7] 창 원점=({l},{t}) 크기={r-l}x{b-t}')

    if not focus_window(hwnd):
        log('[warn] 전면 전환 실패 — 클릭이 안 먹을 수 있음')

    # 2) 캡처
    png = os.path.join(WORK, 'screen.png')
    if os.path.exists(png):
        os.remove(png)
    if not capture(hwnd, png):
        log('[FAIL] 캡처 실패')
        return 3
    from PIL import Image
    w_img, h_img = Image.open(png).size
    log(f'[2/7] 캡처 완료 {w_img}x{h_img}')

    # 3) OCR
    jpath = os.path.join(WORK, 'screen.json')
    items = ocr_image(png, jpath)
    log(f'[3/7] OCR 라인 {len(items)}개')

    # --list 모드
    if args.list:
        log('\n--- OCR 라인 목록 ---')
        for it in sorted(items, key=lambda x: x.get('y', 0)):
            log(f"  y={it.get('y', 0):>4} x={it.get('x', 0):>4} | {it.get('text', '')[:70]}")
        return 0

    if not args.room:
        log('[FAIL] 방 이름을 지정하세요. (--list 로 목록 확인)')
        return 2

    # 4) 매칭
    target, score = match_room(items, args.room)
    if not target or score < 0.45:
        log(f'[FAIL] "{args.room}" 매칭 실패 (최고점 {score} < 0.45)')
        log('       --list 로 화면 텍스트를 확인하세요')
        return 4
    log(f'[4/7] 매칭 score={score} | "{target.get("text", "")[:60]}"')

    # 5) 좌표 변환
    #    스케일 보정: 캡처 크기 != 창 크기일 때 비율 적용
    scale_x = (r - l) / w_img if w_img else 1.0
    scale_y = (b - t) / h_img if h_img else 1.0
    cx = int(l + (target.get('x', 0) + OFFSET_X) * scale_x)
    cy = int(t + (target.get('y', 0) + OFFSET_Y) * scale_y)
    log(f'[5/7] 화면 좌표=({cx},{cy})  scale=({scale_x:.3f},{scale_y:.3f})')

    if args.dry_run:
        log('[dry-run] 클릭 생략')
        return 0

    # 6) 클릭
    click_at(cx, cy, args.count)
    log(f'[6/7] 클릭 x{args.count} 완료')

    # 7) 검증 (새 창이 늦게 뜰 수 있으므로 폴링)
    new = []
    for attempt in range(10):
        time.sleep(0.6)
        after = kakao_windows()
        new = [(h, ti, rc) for h, ti, rc in after if h not in before and ti]
        if new:
            break

    if new:
        for h, ti, rc in new:
            log(f'[7/7] OK 새 창: "{ti}" {rc[2]-rc[0]}x{rc[3]-rc[1]} hwnd={h}')
        return 0

    log('[7/7] FAIL 새 창이 열리지 않음 (좌표 오프셋 조정 필요할 수 있음)')
    return 5


if __name__ == '__main__':
    sys.exit(main())
