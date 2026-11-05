#!/usr/bin/env python3
"""
kt_mcp.py — 카카오톡 PC 조작 MCP 서버 (Windows)

에이전트가 CLI 문자열을 조립하지 않고 도구를 직접 호출할 수 있게 하는 얇은 래퍼.
실제 동작은 전부 kt.py 의 검증된 함수를 그대로 쓴다 (4중 오발송 가드 유지).

설계 원칙
---------
1. stdout 보호
   MCP stdio 에서 stdout 은 JSON-RPC 채널이다. kt.py 의 log() 가 print() 로
   stdout 에 쓰면 프로토콜이 즉시 깨진다. 그래서 import 직후 kt.log 를
   stderr 로 돌린다. 이 서버 자신도 stdout 에 아무것도 찍지 않는다.

2. 전송 멱등 (operation_id)
   네트워크·타임아웃·카톡 접힘으로 결과가 애매할 때 에이전트가 재전송하면
   같은 메시지가 두 번 간다. operation_id 를 원장(JSONL)에 기록해
   같은 ID 재호출은 실제 전송 없이 기존 결과만 돌려준다.
   outcome 이 sent/sent_unverified 면 절대 재전송하지 않는다.

3. 방 이름 대조
   kt.send_text 의 expect_title 가드를 그대로 통과시킨다.

실행
----
    python kt_mcp.py            # stdio MCP 서버

MCP 등록 예 (Claude Code / Codex / Hermes)
----
    command: python
    args:    ["<이 파일의 절대경로>"]
"""

import difflib
import json
import os
import sys
import time
import uuid
from datetime import datetime

# ── 1) stdout 보호: kt.py 를 import 하기 전에 아무것도 stdout 에 쓰지 않는다 ──
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import kt  # noqa: E402  (경로 주입 후 import)

# kt.py 의 log() 를 stderr 로 교체 — stdout 은 MCP 프로토콜 전용으로 비워 둔다
kt.log = lambda m="": print(m, file=sys.stderr, flush=True)

from mcp.server.fastmcp import FastMCP  # noqa: E402

# ── 2) 전송 원장 ──────────────────────────────────────────────────────────
# ★ CLI(kt.py send) 와 같은 파일을 공유한다. 한쪽에서 보낸 것을 다른 쪽이
#   알아야 중복 발송이 실제로 막힌다.
_OPS_FILE = kt.SEND_LEDGER


def _ops_read_all():
    """{operation_id: record} — 마지막 기록이 이긴다."""
    return kt.ledger_read()


def _ops_append(rec):
    return kt.ledger_append(rec)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _candidates(room, n=5):
    """요청한 방 이름과 비슷한 열린 방 제목을 제안한다.

    오타·부분 이름으로 엉뚱한 방을 조작하는 사고를 막기 위해,
    못 찾았을 때 '무엇이 열려 있는지'를 함께 돌려준다.
    """
    try:
        titles = [t for _, t in kt.list_rooms()]
    except Exception:
        return []
    scored = []
    for t in titles:
        s = _similar(room, t)
        scored.append((s, t))
    scored.sort(reverse=True)
    return [t for s, t in scored if s >= 0.3][:n]


def _similar(a, b):
    """정규화 후 유사도 (0.0~1.0). kt 내부 로직과 같은 감각으로 비교."""
    try:
        na = kt._norm(a or "")
        nb = kt._norm(b or "")
    except Exception:
        na, nb = (a or ""), (b or "")
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sm = difflib.SequenceMatcher(None, na, nb)
    return sm.ratio()


def _title_match(requested, actual):
    """요청 이름이 실제 창 제목과 '같은 방'으로 볼 수 있는지 판정한다.

    허용 기준 (하나라도 만족):
      1) 정규화 후 완전 일치
      2) 요청이 실제 제목의 부분 문자열  ('프로젝트' ⊂ '프로젝트방')
      3) 유사도 0.5 이상 (오타 허용)

    2) 를 허용하는 이유: 사용자가 짧게 부르는 것이 정상이고,
    후보가 유일하면 오발송 위험이 없다. 다만 kt_send 쪽은
    kt.send_text 의 expect_title 가드가 한 번 더 대조한다.
    """
    if not requested or not actual:
        return False
    try:
        nr = kt._norm(requested)
        na = kt._norm(actual)
    except Exception:
        nr, na = requested, actual
    if nr and na:
        if nr == na:
            return True
        if nr in na:
            return True
    return _similar(requested, actual) >= 0.5


def _resolve_room(room, auto_open=True):
    """방 이름 → (hwnd, title). 실패하면 사유와 후보를 담은 dict 를 돌려준다.

    반환: ((hwnd, title), None) 또는 (None, error_dict)
    """
    try:
        found = kt.get_room(room)
    except Exception as e:
        return None, {"error": "방 조회 실패: %s" % e,
                      "candidates": _candidates(room)}

    if found:
        return found, None

    # 자동 열기 — 서브프로세스라 예외·지연이 날 수 있으므로 반드시 감싼다
    if auto_open:
        try:
            kt.log("[resolve] '%s' 방이 닫혀 있어 엽니다." % room)
            kt.cmd_open(room)
            time.sleep(0.6)
            found = kt.get_room(room)
            if found:
                return found, None
        except Exception as e:
            return None, {
                "error": "방을 여는 중 실패: %s" % e,
                "candidates": _candidates(room),
                "note": "자동 열기를 포기했습니다. kt_rooms 로 정확한 이름을 확인하세요.",
            }

    return None, {
        "error": "방을 찾지 못했습니다: '%s'" % room,
        "candidates": _candidates(room),
        "note": "부분 이름은 자동 매칭되지 않을 수 있습니다. kt_rooms 의 제목을 그대로 쓰세요.",
    }


# ── 3) MCP 서버 ───────────────────────────────────────────────────────────
mcp = FastMCP(
    "kakaotalk-pc",
    instructions=(
        "카카오톡 PC(Windows) 조작 도구. 모든 카톡 작업은 kt_* 도구를 사용한다. "
        "읽기·전송 모두 대상 방을 반드시 명시한다(추측 금지). "
        "전송은 kt_send 로만 하며 operation_id 를 요청마다 한 번 정해 재사용한다. "
        "결과가 uncertain/outcome_unknown 이면 재전송하지 말고 같은 operation_id 로 "
        "kt_send_result 를 조회해 실제 전송 여부를 확인한다. "
        "이 서버는 백그라운드에서 스스로 아무것도 하지 않는다 — 호출받을 때만 동작한다."
    ),
)


def _ok(**kw):
    kw.setdefault("ok", True)
    return json.dumps(kw, ensure_ascii=False, indent=2)


def _err(msg, **kw):
    kw.setdefault("ok", False)
    kw["error"] = msg
    return json.dumps(kw, ensure_ascii=False, indent=2)


@mcp.tool()
def kt_status() -> str:
    """카카오톡 실행 상태와 열린 방 목록을 한 번에 확인한다.

    카톡이 꺼져 있는지, 어떤 방이 열려 있는지 먼저 파악할 때 쓴다.
    어떤 조작이든 시작 전에 이걸로 대상을 확인하는 것이 안전하다.
    """
    try:
        wins = kt.list_windows()
    except Exception as e:
        return _err("창 목록 조회 실패: %s" % e)

    if not wins:
        return _ok(running=False, rooms=[], note="카카오톡 창을 찾지 못했습니다.")

    mains = []
    rooms = []
    for h, title, w, hgt, pos, vis in wins:
        item = {
            "hwnd": h,
            "title": title,
            "size": [w, hgt],
            "pos": list(pos),
            "visible": bool(vis),
        }
        if title == kt.MAIN_TITLE:
            mains.append(item)
        else:
            rooms.append(item)

    return _ok(running=True, main_windows=mains, room_count=len(rooms), rooms=rooms)


@mcp.tool()
def kt_rooms() -> str:
    """현재 열려 있는 채팅방 제목 목록만 간단히 돌려준다.

    방 이름을 정확히 알아야 읽기·전송이 가능하므로, 이름 확인용으로 쓴다.
    """
    try:
        rooms = kt.list_rooms()
    except Exception as e:
        return _err("방 목록 조회 실패: %s" % e)
    return _ok(count=len(rooms),
               rooms=[{"hwnd": h, "title": t} for h, t in rooms])


@mcp.tool()
def kt_read(room: str, last: int = 15, since_hours: float = 0,
            max_chars: int = 2000) -> str:
    """지정한 방의 최근 메시지를 읽어 구조화된 목록으로 돌려준다.

    Args:
        room: 정확한 방 이름. kt_rooms 로 확인한 제목과 일치해야 한다.
        last: 최근 몇 건을 가져올지 (기본 15, 단체방은 25~30 권장).
        since_hours: 0 이면 무시. 0보다 크면 그 시간 이내 메시지만.
        max_chars: 메시지 본문 최대 글자수. 길게 잘리면 값을 올린다.

    방이 닫혀 있으면 자동으로 연다. 읽기는 클립보드 전사 방식이라
    화면에 렌더된 범위만 잡힌다 — 카톡이 긴 글을 '전체보기'로 접으면
    접힌 부분은 전사되지 않을 수 있다.
    """
    if not room or not room.strip():
        return _err("room 을 지정해야 합니다. kt_rooms 로 이름을 먼저 확인하세요.")
    room = room.strip()

    try:
        found, rerr = _resolve_room(room, auto_open=True)
        if rerr:
            return json.dumps(dict(ok=False, requested=room, **rerr),
                              ensure_ascii=False, indent=2)
        hwnd, title = found
        # 실제 창 제목이 요청한 방과 다르면 읽지 않는다 (오독 방지)
        if not _title_match(room, title):
            return _err("창 제목 불일치로 중단했습니다.",
                        expected=room, actual=title,
                        candidates=_candidates(room))

        target = max(1, min(int(last or 15), 200))
        res, msgs, steps = kt.read_collect(
            hwnd, title,
            since_h=(float(since_hours) if since_hours else None),
            target=target,
            verbose=False,
        )

        if not res or not res.get("ok"):
            return _err("전사 실패", detail=(res or {}).get("error"))

        picked = msgs[-target:] if len(msgs) > target else msgs
        out = []
        for m in picked:
            body = m.get("body") or ""
            if max_chars and len(body) > max_chars:
                body = body[:max_chars] + "…(잘림)"
            out.append({
                "date": m.get("date"),
                "time": m.get("time"),
                "sender": m.get("sender"),
                "body": body,
            })

        return _ok(room=title, requested=room, count=len(out),
                   scroll_steps=steps, messages=out)
    except Exception as e:
        return _err("읽기 중 예외: %s" % e)


@mcp.tool()
def kt_send(room: str, text: str, operation_id: str = "",
            dry_run: bool = False) -> str:
    """지정한 방에 메시지를 전송한다. operation_id 로 중복 전송을 막는다.

    Args:
        room: 정확한 방 이름 (필수).
        text: 보낼 본문.
        operation_id: 요청마다 한 번 정하는 고유 ID. 비우면 자동 생성된다.
            같은 ID 로 다시 호출하면 실제 전송 없이 기존 결과를 돌려준다.
        dry_run: True 면 실제로 보내지 않고 검증만 한다.

    반환 status 의미:
        sent            — 전송 후 최근 메시지에서 확인됨
        sent_unverified — 전송된 것으로 보이나 확인 실패
                          (카톡이 긴 글을 접은 경우 등). 재전송 금지.
        failed          — 전송되지 않음 (가드 차단·포커스 이탈 등)
        dry_run         — 검증만 수행
    """
    if not room or not room.strip():
        return _err("room 을 지정해야 합니다.")
    if text is None or str(text) == "":
        return _err("text 가 비어 있습니다.")

    op = (operation_id or "").strip() or ("auto-" + uuid.uuid4().hex[:16])

    # ── 멱등 가드: 이미 처리된 operation_id 면 재전송하지 않는다 ──
    prior = _ops_read_all().get(op)
    if prior:
        return json.dumps({
            "ok": True,
            "already_processed": True,
            "resent": False,
            "operation_id": op,
            "status": prior.get("status"),
            "room": prior.get("room"),
            "at": prior.get("at"),
            "note": "이 operation_id 는 이미 처리되었습니다. 재전송하지 않았습니다. "
                    "실제 도착 여부는 kt_read 로 확인하세요.",
        }, ensure_ascii=False, indent=2)

    room = room.strip()
    try:
        found, rerr = _resolve_room(room, auto_open=True)
        if rerr:
            rec = {"operation_id": op, "room": room, "status": "not_sent",
                   "code": -2, "error": rerr.get("error"),
                   "text_hash": kt.text_hash(room, str(text)),
                   "chars": len(str(text)), "ts": time.time(),
                   "at": _now(), "src": "mcp"}
            _ops_append(rec)
            return json.dumps(dict(ok=False, operation_id=op, **rerr),
                              ensure_ascii=False, indent=2)
        hwnd, title = found

        # 최근 같은 본문을 이미 보냈으면 중복으로 판단해 막는다
        if not dry_run:
            dup = kt.ledger_recent_dup(title, str(text), within=60)
            if dup:
                return json.dumps({
                    "ok": False,
                    "already_sent_recently": True,
                    "resent": False,
                    "operation_id": op,
                    "room": title,
                    "prior_at": dup.get("at"),
                    "prior_status": dup.get("status"),
                    "note": "60초 안에 같은 본문을 이미 보냈습니다. 중복 발송으로 판단해 "
                            "취소했습니다. 실제 도착은 kt_read 로 확인하세요.",
                }, ensure_ascii=False, indent=2)

        code = kt.send_text(
            hwnd, title, str(text),
            expect_title=room,
            dry_run=bool(dry_run),
        )
    except Exception as e:
        rec = {"operation_id": op, "room": room, "status": "unknown",
               "code": -1, "error": str(e), "at": _now()}
        _ops_append(rec)
        return _err("전송 중 예외 — 재전송하지 말고 kt_read 로 확인하세요.",
                    operation_id=op, status="unknown", detail=str(e))

    # 종료코드 → 상태 매핑 (kt.send_text 규약)
    if dry_run:
        status = "dry_run"
    elif code == 0:
        status = "sent"
    elif code == 6:
        # 오늘 실측: 카톡이 긴 글을 '전체보기'로 접으면 확인이 실패하지만 실제로는 전송됨
        status = "sent_unverified"
    else:
        status = "failed"

    rec = {"operation_id": op, "room": title,
           "text_hash": kt.text_hash(title, str(text)),
           "status": status, "code": code,
           "chars": len(str(text)), "ts": time.time(),
           "at": _now(), "src": "mcp"}
    _ops_append(rec)

    note = None
    if status == "sent_unverified":
        note = ("전송은 실행됐으나 확인이 실패했습니다. 카톡이 긴 글을 접은 경우가 "
                "대표적입니다. 재전송하지 말고 kt_read 로 실제 도착을 확인하세요.")
    elif status == "failed":
        note = "가드에 의해 차단됐거나 전송되지 않았습니다. 방 이름과 창 상태를 확인하세요."

    return json.dumps({
        "ok": status in ("sent", "sent_unverified", "dry_run"),
        "operation_id": op,
        "room": title,
        "status": status,
        "exit_code": code,
        "chars": len(str(text)),
        "note": note,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def kt_send_result(operation_id: str) -> str:
    """앞서 보낸 전송의 기록을 조회한다. 재전송 판단용.

    상태가 uncertain/unknown 이면 이걸로 확인하고,
    실제 도착 여부는 kt_read 로 최종 확인한다.
    """
    op = (operation_id or "").strip()
    if not op:
        return _err("operation_id 가 필요합니다.")
    rec = _ops_read_all().get(op)
    if not rec:
        return _err("이 operation_id 기록이 없습니다 — 전송이 시작되지 않았을 수 있습니다.",
                    operation_id=op)
    return _ok(operation_id=op, record=rec,
               advice=("status 가 sent/sent_unverified 면 재전송하지 마세요."
                       if rec.get("status") in ("sent", "sent_unverified")
                       else "failed/unknown 이면 kt_read 로 도착을 먼저 확인하세요."))


@mcp.tool()
def kt_open(room: str) -> str:
    """이름으로 채팅방을 열어 창을 띄운다.

    읽기·전송 전에 방이 닫혀 있을 때 쓴다. kt_read 는 자동으로 열지만,
    명시적으로 열어 두고 싶을 때 이 도구를 쓴다.
    """
    if not room or not room.strip():
        return _err("room 을 지정해야 합니다.")
    room = room.strip()
    try:
        already = kt.get_room(room)
        if already:
            return _ok(room=already[1], hwnd=already[0], already_open=True)
        found, rerr = _resolve_room(room, auto_open=True)
        if rerr:
            return json.dumps(dict(ok=False, requested=room, **rerr),
                              ensure_ascii=False, indent=2)
        return _ok(room=found[1], hwnd=found[0], already_open=False)
    except Exception as e:
        return _err("방 열기 예외: %s" % e)


@mcp.tool()
def kt_show() -> str:
    """숨겨져 있거나 최소화된 카카오톡 메인 창을 복원한다.

    창이 작업표시줄에만 있고 목록에 안 보일 때 쓴다.
    """
    try:
        kt.list_windows()
        kt.cmd_show()
        time.sleep(0.4)
        wins = kt.list_windows()
        return _ok(restored=True, windows=[
            {"hwnd": h, "title": t, "visible": bool(v)} for h, t, _, _, _, v in wins
        ])
    except Exception as e:
        return _err("창 복원 예외: %s" % e)


@mcp.tool()
def kt_alerts(limit: int = 20, include_read: bool = False) -> str:
    """워처가 감지한 알림 큐를 조회한다 (워처가 켜져 있을 때만 쌓인다).

    Args:
        limit: 최대 건수.
        include_read: True 면 이미 읽은 알림도 포함.
    """
    try:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            base_log = kt.log
            kt.log = lambda m="": buf.write(str(m) + "\n")
            try:
                kt.cmd_alerts(n=int(limit or 20), show_all=bool(include_read))
            finally:
                kt.log = base_log
        text = buf.getvalue()
        return _ok(raw=text.strip() or "(알림 없음)")
    except Exception as e:
        return _err("알림 조회 예외: %s" % e)


def main():
    print("[kt_mcp] 카카오톡 PC MCP 서버 시작 (stdio)", file=sys.stderr, flush=True)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
