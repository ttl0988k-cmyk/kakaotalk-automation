#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kt_show.py - 숨겨진 카카오톡 창 열기 (자동화 1단계)

사용법:
    python kt_show.py              # 숨겨진 메인 창 열기 + 667x878 통일
    python kt_show.py --check      # 상태만 확인 (열지 않음)
    python kt_show.py --size 700 900   # 크기 직접 지정

동작:
    1) KakaoTalk 프로세스의 모든 최상위 창 열거
    2) 제목이 '카카오톡'인 메인 창 찾기 (숨겨져 있어도 찾음)
    3) ShowWindow(SW_RESTORE) 로 복원
    4) SetForegroundWindow 로 전면
    5) 크기를 667x878 로 통일
    6) 결과 검증 후 출력
"""

import argparse
import sys
import time

import psutil
import win32con
import win32gui
import win32process

MAIN_TITLE = '카카오톡'
STD_W, STD_H = 667, 878


def log(m):
    print(m, flush=True)


def kakao_top_windows():
    """카카오톡 프로세스의 모든 최상위 창 (hwnd, title, visible, class, rect)."""
    rows = []

    def cb(h, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            if not pid:
                return
            if 'kakao' not in psutil.Process(pid).name().lower():
                return
            rows.append((
                h,
                win32gui.GetWindowText(h),
                bool(win32gui.IsWindowVisible(h)),
                win32gui.GetClassName(h),
                win32gui.GetWindowRect(h),
            ))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return rows


def find_main_window():
    """제목이 '카카오톡'인 창. 보이는 것을 우선."""
    wins = kakao_top_windows()
    titled = [w for w in wins if w[1] == MAIN_TITLE]
    if not titled:
        return None, wins
    visible = [w for w in titled if w[2]]
    return (visible[0] if visible else titled[0]), wins


def show(hwnd, want_w=None, want_h=None):
    """창 복원 + 전면 + 크기 통일."""
    want_w = want_w or STD_W
    want_h = want_h or STD_H

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    before = (r - l, b - t)
    was_visible = bool(win32gui.IsWindowVisible(hwnd))

    # 1) 복원
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.35)

    # 2) 전면
    fg = False
    for _ in range(3):
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() == hwnd:
                fg = True
                break
        except Exception:
            pass
        time.sleep(0.25)

    # 3) 크기 통일
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    cur = (r - l, b - t)
    if cur != (want_w, want_h):
        win32gui.SetWindowPos(hwnd, 0, l, t, want_w, want_h,
                              win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        time.sleep(0.45)
        l, t, r, b = win32gui.GetWindowRect(hwnd)

    return {
        'hwnd': hwnd,
        'was_visible': was_visible,
        'before_size': before,
        'after_size': (r - l, b - t),
        'pos': (l, t),
        'foreground': fg,
        'visible': bool(win32gui.IsWindowVisible(hwnd)),
    }


def main():
    ap = argparse.ArgumentParser(description='숨겨진 카카오톡 창 열기')
    ap.add_argument('--check', action='store_true', help='상태만 확인')
    ap.add_argument('--size', nargs=2, type=int, metavar=('W', 'H'),
                    help='창 크기 지정 (기본 667 878)')
    args = ap.parse_args()

    found, all_wins = find_main_window()
    if not found:
        log('[FAIL] 카카오톡 창을 찾을 수 없음')
        log(f'       KakaoTalk 창 {len(all_wins)}개 열거됨')
        for h, ti, vis, cls, rect in all_wins:
            if ti:
                log(f'         hwnd={h} vis={int(vis)} title={ti!r}')
        return 2

    hwnd, title, visible, cls, rect = found
    log(f'[1/4] 메인 창 발견 hwnd={hwnd} title={title!r} class={cls}')
    log(f'[1/4] 현재 상태 visible={int(visible)} 크기={rect[2]-rect[0]}x{rect[3]-rect[1]}')

    if args.check:
        log('[check] 열지 않고 종료')
        return 0

    want_w, want_h = (args.size if args.size else (STD_W, STD_H))
    res = show(hwnd, want_w, want_h)

    log(f'[2/4] 복원: visible={int(res["was_visible"])} -> {int(res["visible"])}')
    log(f'[3/4] 전면: {"OK" if res["foreground"] else "FAIL"}')
    log(f'[4/4] 크기: {res["before_size"][0]}x{res["before_size"][1]} -> '
        f'{res["after_size"][0]}x{res["after_size"][1]}  at {res["pos"]}')

    ok = res['visible'] and res['after_size'] == (want_w, want_h)
    log('[DONE] ' + ('OK' if ok else 'PARTIAL'))
    return 0 if ok else 3


if __name__ == '__main__':
    sys.exit(main())
