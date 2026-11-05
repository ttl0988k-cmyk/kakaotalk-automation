#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""kt_watch.py — 카카오톡 트리거 워처 (@라온아)

새 메시지를 주기적으로 폴링해서 트리거로 시작하는 메시지에만 자동 응답한다.
agent-desktop 본체와 분리된 사이드카. kt.py 의 검증된 읽기/쓰기 경로를 그대로 재사용한다.

사용 예
  # 1) 반응 대상만 확인 (전송 안 함)
  python kt_watch.py --room "내게쓴방" --once --dry-run

  # 2) 실제 응답 (내장 응답기)
  python kt_watch.py --room "내게쓴방" --yes

  # 3) LLM 등 외부 생성기 연결 (stdin JSON -> stdout 첫 줄)
  python kt_watch.py --room "내게쓴방" --yes --hook "python my_llm.py"

  # 4) 기존 메시지에도 반응 (기본은 과거 메시지 무시)
  python kt_watch.py --room "내게쓴방" --yes --reply-existing

안전장치
  · 기본이 dry-run 이다. --yes 를 줘야 실제로 전송한다.
  · sender == --me (또는 방 이름) 인 내 메시지는 무시한다. (자기 루프 차단)
  · 보낸 응답 원문을 즉시 seen 에 등록한다. (재감지 차단)
  · 같은 발신자에게 --cooldown 초 내 재응답하지 않는다.
  · 세션당 응답 --max-replies 를 넘으면 자동 정지.
  · Escape 키를 쓰지 않는다. (앱 종료 위험)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import kt  # noqa: E402  (검증된 읽기/쓰기 경로 재사용)

STATE_PATH = os.path.join(HERE, "_watch_state.json")
LOG_PATH = os.path.join(HERE, "_watch.log")
ALERT_PATH = os.path.join(HERE, "_work", "alerts.jsonl")   # 라온에게 전달할 알림 큐
ALERT_MAX = 500                                          # 큐 최대 보관 건수

DEFAULT_TRIGGERS = ["@라온아", "@라온"]
SEEN_MAX = 400          # 상태 파일에 보관할 fingerprint 개수
POLL_WINDOW = 12        # 매 폴링마다 읽을 최신 메시지 수


# ------------------------------------------------------------- 알림 큐
def append_alert(room, sender, text, when="", meta=None):
    """트리거를 '봇 응답' 대신 알림 큐에 적재한다 (라온이 나중에 조회).

    JSONL append-only. 조회는 `kt.py alerts` 로 한다.
    """
    try:
        os.makedirs(os.path.dirname(ALERT_PATH), exist_ok=True)
        rec = {
            "ts": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "room": room,
            "sender": sender,
            "when": when,
            "text": norm(text),
            "read": False,
        }
        if meta:
            rec.update(meta)
        with open(ALERT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim_alerts()
        log(f"[ALERT] {sender} : {norm(text)[:80]}")
        return True
    except Exception as e:
        log(f"[ALERT] 적재 실패: {e}")
        return False


def _trim_alerts():
    """큐가 너무 길면 오래된 것부터 버린다."""
    try:
        if not os.path.exists(ALERT_PATH):
            return
        with open(ALERT_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= ALERT_MAX:
            return
        with open(ALERT_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines[-ALERT_MAX:])
    except Exception:
        pass


def post_webhook(url, alert, timeout=5):
    """선택: 외부 웹훅(DAON 서버·슬랙·텔레그램 등)으로도 알림 전송."""
    if not url:
        return False
    try:
        import urllib.request
        data = json.dumps(alert, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception as e:
        log(f"[webhook] 실패: {e}")
        return False



# ----------------------------------------------------------------- 로그
def log(msg=""):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ----------------------------------------------------------------- 유틸
def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def fingerprint(m):
    """메시지 고유 지문. 같은 분에 같은 내용이 와도 date/time/sender/body 로 구분."""
    raw = "|".join([
        str(m.get("date", "")),
        str(m.get("time", "")),
        str(m.get("sender", "")),
        norm(m.get("body", ""))[:160],
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"seen": [], "replies": 0}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("seen", [])
        st.setdefault("replies", 0)
        return st
    except Exception:
        return {"seen": [], "replies": 0}


def save_state(st):
    st["seen"] = st["seen"][-SEEN_MAX:]
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        log(f"[warn] 상태 저장 실패: {e}")


# ----------------------------------------------------------------- 트리거
def match_trigger(body, triggers):
    """본문이 트리거로 '시작'할 때만 매칭. 반환=트리거 뒤 명령문(없으면 ''), 미매칭=None."""
    b = (body or "").strip()
    low = b.lower()
    for t in triggers:
        tl = t.lower()
        if low.startswith(tl):
            rest = b[len(t):]
            # '@라온아..' 처럼 붙은 구두점 제거
            return rest.lstrip(" \t.,:;!?-—~·…>)]}\"'")
    return None


# ----------------------------------------------------------------- 응답기
def builtin_reply(payload, ctx):
    p = (payload or "").strip()
    low = p.lower()

    if not p or low in ("안녕", "하이", "반가", "hello", "hi"):
        return f"부르셨습니까, 대표님. 「{ctx['room']}」 대기 중입니다. ({ctx['now']})"

    if p.startswith("시간") or p in ("몇시", "몇시야"):
        return f"지금 {ctx['now']} 입니다."

    if p.startswith("상태"):
        s = ctx["stats"]
        return (f"가동 {s['uptime']}. 폴링 {s['polls']}회 / 감지 {s['seen_msgs']}건 / "
                f"응답 {s['replies']}건 / 방 「{ctx['room']}」")

    if p.startswith("도움") or low in ("help", "명령", "메뉴"):
        return ("사용 가능: 안녕 · 시간 · 상태 · 도움말 · 최근 · 읽기 · 원문 "
                "(LLM 연결 시 자유 대화)")

    if p.startswith("최근") or p.startswith("읽기"):
        msgs = ctx.get("recent") or []
        if not msgs:
            return "최근 메시지를 읽지 못했습니다."
        tail = msgs[-3:]
        lines = [f"{m.get('time','')} {m.get('sender','')}: {norm(m.get('body',''))[:40]}" for m in tail]
        return "최근 3건\n" + "\n".join(lines)

    if p.startswith("원문"):
        return f"받은 원문: {ctx.get('raw','')[:180]}"

    # 기본: 확인 응답 (LLM 미연결 상태)
    return f"「{p[:60]}」 접수했습니다. (내장 응답기 — 자유 대화는 --hook 으로 연결)"


def hook_reply(hook_cmd, payload, ctx, timeout=60):
    """외부 생성기 호출. stdin=JSON, stdout 첫 줄(또는 전체)을 응답으로 사용."""
    req = {
        "text": payload,
        "raw": ctx.get("raw", ""),
        "room": ctx["room"],
        "sender": ctx["sender"],
        "time": ctx["time"],
        "recent": ctx.get("recent") or [],
    }
    try:
        r = subprocess.run(
            hook_cmd, shell=True,
            input=json.dumps(req, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log(f"[hook] 타임아웃 {timeout}s")
        return None
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    err = (r.stderr or b"").decode("utf-8", "replace").strip()
    if r.returncode != 0:
        log(f"[hook] 실패 rc={r.returncode} err={err[:200]}")
        return None
    if not out:
        log("[hook] 빈 응답")
        return None
    # 여러 줄 허용하되 과도한 길이는 절단
    return out[:900]


# ----------------------------------------------------------------- 방 확보
def ensure_room(name):
    """방 창을 확보하고 (hwnd, title) 반환. 없으면 열기 시도."""
    room = kt.get_room(name)
    if room:
        return room
    log(f"[room] 「{name}」 닫혀 있어 엽니다")
    try:
        kt.cmd_open(name)
    except Exception as e:
        log(f"[room] 열기 예외: {e}")
    for _ in range(12):
        time.sleep(0.4)
        room = kt.get_room(name)
        if room:
            return room
    return None


# ----------------------------------------------------------------- 폴링 1회
def poll_once(room_name, args, state, stats, first_run, seen_set, last_reply_at):
    # ★ 전송(kt.py send) 중에는 폴링을 건너뛴다.
    #   워처와 전송이 동시에 포커스를 다투면 엉뚱한 방을 읽어 오염된다(실측).
    try:
        if kt.watch_paused():
            log("[poll] 전송 중 — 폴링 건너뜀")
            state["_baseline_pending"] = bool(first_run)
            return 0
    except Exception:
        pass

    room = ensure_room(room_name)
    if not room:
        log(f"[poll] 방 창 확보 실패: {name_safe(room_name)}")
        return 0

    hwnd, title = room
    stats["polls"] += 1

    # ★★ 방 이름 정합 검증 (최우선 방어선) ★★
    # 창이 여러 개 떠 있으면 Ctrl+A/Ctrl+C 가 포그라운드 창으로 가서
    # '다른 방' 내용을 읽어온다(실측). 그 결과 B방 메시지가 A방
    # 트리거로 둔갑해 라온에게 잘못 배달됐다. 읽은 창 제목이 기대 방과
    # 충분히 비슷하지 않으면 이번 폴링 결과를 통째로 폐기한다.
    try:
        from kt_open import similarity
        s = similarity(room_name, title)
        if s < 0.5:
            log(f"[poll] ⚠ 방 불일치 폐기: 기대='{room_name}' 실제='{title}' (유사도 {s:.2f})")
            state["_baseline_pending"] = True
            return 0
    except Exception as _e:
        log(f"[poll] ⚠ 방 이름 검증 불가({_e}) — 그래도 계속")

    kt.enforce(hwnd, kt.ROOM_STD_W, kt.ROOM_STD_H)
    try:
        kt.scroll_bottom(hwnd)
    except Exception:
        pass

    res, sel, _step = kt.read_collect(
        hwnd, title, since_h=None, target=args.window, max_scroll=0, verbose=False
    )
    msgs = (res or {}).get("messages") or []
    if not msgs:
        log("[poll] 메시지 0건 (읽기 실패 또는 빈 방)")
        # ★ 읽기 실패 시 baseline 을 '완료'로 넘기면 안 된다.
        #   넘기면 다음 폴링에서 방의 과거 메시지 191건이 전부 '신규'로 판정돼
        #   지난 트리거가 무더기로 재배달된다(실측 — pending 5건 폭주).
        state["_baseline_pending"] = True
        return 0
    state["_baseline_pending"] = False

    replied = 0
    me = (args.me or "").strip()
    my_names = set()
    if not args.allow_self:
        # --allow-self 면 내 닉네임/방이름 필터를 모두 해제한다(테스트용)
        if me:
            my_names.add(me)
        my_names |= {title.strip(), room_name.strip()}
    my_names -= {""}


    for m in msgs:
        fp = fingerprint(m)
        if fp in seen_set:
            continue
        seen_set.add(fp)
        state["seen"].append(fp)
        stats["seen_msgs"] += 1

        sender = (m.get("sender") or "").strip()
        body = m.get("body") or ""

        # 첫 실행 baseline: 과거 메시지는 기록만 하고 반응하지 않는다
        if first_run and not args.reply_existing:
            continue

        if sender in my_names:
            continue

        payload = match_trigger(body, args.triggers)
        if payload is None:
            continue

        stats["matched"] += 1
        log(f"[TRIGGER] {sender} @ {m.get('time','')} : {norm(body)[:90]}")

        stats["uptime"] = f"{int(time.time() - stats['start'])}s"
        ctx = {
            "room": title,
            "sender": sender,
            "time": m.get("time", ""),
            "now": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "recent": msgs,
            "raw": body,
            "stats": dict(stats, replies=stats["replies"] + (0 if args.dry_run else 1)),
        }

        # 쿨다운 (★ 알림 전용 모드에서는 건너뛴다 — 트리거를 빠짐없이 잡아야 함)
        gap = time.time() - last_reply_at.get(sender, 0)
        if args.cooldown and gap < args.cooldown and not getattr(args, "notify_only", False):
            log(f"[skip] 쿨다운 {gap:.0f}s < {args.cooldown}s ({sender})")
            continue

        # ★ 알림 전용 모드 — 봇이 답하지 않고 라온에게 알림만 넘긴다
        if getattr(args, "notify_only", False):
            rec = append_alert(title, sender, body, m.get("time", ""),
                               meta={"trigger": payload})
            if args.notify_webhook:
                post_webhook(args.notify_webhook, {
                    "type": "kakao_trigger", "room": title,
                    "sender": sender, "time": m.get("time", ""),
                    "text": norm(body),
                })
            state["seen"].append(fp)
            seen_set.add(fp)
            stats["replies"] += 1
            last_reply_at[sender] = time.time()
            replied += 1
            log("[notify] 알림 적재 완료 (답장 안 함)")
            continue

        # 응답 생성
        reply = None
        if args.hook:
            reply = hook_reply(args.hook, payload, ctx, timeout=args.hook_timeout)
        if not reply:
            reply = builtin_reply(payload, ctx)

        # 폭주 방지
        if args.max_replies and state["replies"] >= args.max_replies:
            log(f"[stop] 최대 응답 {args.max_replies}건 도달")
            return -1

        if args.dry_run:
            log(f"[dry-run] 응답 예정 → {reply[:120]}")
            stats["replies"] += 1
            last_reply_at[sender] = time.time()
            replied += 1
            continue

        # 실제 전송
        rc = kt.send_text(hwnd, title, reply, expect_title=title, dry_run=False)
        if rc == 0:
            state["replies"] += 1
            stats["replies"] += 1
            last_reply_at[sender] = time.time()
            replied += 1
            # 보낸 응답을 seen 에 등록해 루프 차단
            fake = {"date": "", "time": "", "sender": title, "body": reply}
            fp2 = fingerprint(fake)
            seen_set.add(fp2)
            state["seen"].append(fp2)
            log(f"[SEND] {reply[:100]}")
        else:
            log(f"[FAIL] 전송 실패 rc={rc}")

    if replied or state["seen"]:
        save_state(state)
    return replied


def name_safe(s):
    try:
        return s
    except Exception:
        return "<room>"


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="카카오톡 트리거 워처 (@라온아)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--room", required=True, help="감시할 채팅방 이름")
    ap.add_argument("--triggers", default=",".join(DEFAULT_TRIGGERS),
                    help="트리거 목록(콤마 구분). 기본: " + ",".join(DEFAULT_TRIGGERS))
    ap.add_argument("--me", default="", help="내 닉네임(자기 메시지 무시용)")
    ap.add_argument("--allow-self", action="store_true",
                    help="내가 보낸 메시지에도 반응(나와의 채팅 테스트용)")
    ap.add_argument("--interval", type=float, default=5.0, help="폴링 주기(초). 기본 5")
    ap.add_argument("--window", type=int, default=POLL_WINDOW, help="폴링당 읽을 최신 건수")
    ap.add_argument("--yes", action="store_true", help="실제 전송 허용(없으면 dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="명시적 dry-run(전송 안 함)")
    ap.add_argument("--once", action="store_true", help="1회만 폴링하고 종료")
    ap.add_argument("--rounds", type=int, default=0, help="폴링 횟수 제한(0=무한)")
    ap.add_argument("--reply-existing", action="store_true", help="기존 메시지에도 반응")
    ap.add_argument("--hook", default="", help="외부 응답 생성기 명령 (stdin JSON → stdout)")
    ap.add_argument("--hook-timeout", type=int, default=60, help="hook 타임아웃(초)")
    ap.add_argument("--notify-only", action="store_true",
                    help="★답장하지 않고 알림만 적재 (나중에 kt.py alerts 로 조회)")
    ap.add_argument("--notify-webhook", default="",
                    help="알림을 POST 할 웹훅 URL (선택). 예: DAON 서버 엔드포인트")
    ap.add_argument("--cooldown", type=float, default=3.0, help="같은 발신자 재응답 최소 간격(초)")
    ap.add_argument("--max-replies", type=int, default=50, help="세션 최대 응답 수(0=무제한)")
    ap.add_argument("--reset-state", action="store_true", help="감지 기록 초기화 후 시작")
    args = ap.parse_args()

    args.triggers = [t.strip() for t in args.triggers.split(",") if t.strip()]
    if not args.triggers:
        args.triggers = list(DEFAULT_TRIGGERS)
    args.dry_run = not args.yes or args.dry_run

    if args.reset_state and os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        log("[state] 초기화")

    state = load_state()
    seen_set = set(state["seen"])
    first_run = len(seen_set) == 0

    room = ensure_room(args.room)
    if not room:
        log(f"[FAIL] 방을 열 수 없습니다: {args.room}")
        return 2

    stats = {"polls": 0, "seen_msgs": 0, "matched": 0, "replies": 0,
             "start": time.time(), "uptime": "0s"}
    last_reply_at = {}

    log("=" * 62)
    log(f"kt_watch 시작 | 방=「{args.room}」 | 트리거={args.triggers}")
    log(f"모드={'DRY-RUN(전송 안 함)' if args.dry_run else '실전송'} | 주기={args.interval}s | "
        f"기존메시지반응={'예' if args.reply_existing else '아니오'}")
    log(f"hook={args.hook or '(내장 응답기)'} | 최대응답={args.max_replies}")
    log("=" * 62)

    rnd = 0
    try:
        while True:
            rnd += 1
            stats["uptime"] = f"{int(time.time() - stats['start'])}s"
            n = poll_once(args.room, args, state, stats, first_run, seen_set, last_reply_at)
            # ★ baseline 은 '성공 폴링'에서만 확정한다. 읽기 실패로 끝난 폴링에서
            #   False 로 넘기면 방의 과거 메시지가 전부 신규로 판정돼 지난 트리거가
            #   무더기로 재배달된다(실측 사고: pending 5건 폭주).
            first_run = bool(state.get("_baseline_pending", False))
            if n == -1:
                break
            if args.once:
                log(f"[once] 완료 — 응답 {n}건")
                break
            if args.rounds and rnd >= args.rounds:
                log(f"[rounds] {rnd}회 완료")
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("[exit] 사용자 중단")
    finally:
        save_state(state)
        log(f"[summary] 폴링 {stats['polls']}회 / 감지 {stats['seen_msgs']}건 / "
            f"트리거 {stats['matched']}건 / 응답 {stats['replies']}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
