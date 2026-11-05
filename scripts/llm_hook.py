#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""llm_hook.py — kt_watch.py 용 응답 생성기 템플릿

규약: stdin 으로 JSON 한 덩어리를 받고, stdout 에 응답 한 줄(또는 여러 줄)을 쓴다.

  {"text": "트리거 뒤 명령문", "raw": "원문 전체", "room": "방이름",
   "sender": "보낸사람", "time": "오후 2:16", "recent": [ {"sender","time","body"} ]}

연결:
  python kt_watch.py --room "방이름" --yes --hook "python llm_hook.py"

아래 generate() 안만 바꾸면 어떤 모델이든 붙는다.
"""
import json
import os
import sys

SYSTEM = "너는 대표님 대표님의 비서 '라온'이다. 카카오톡으로 짧게 답한다. 2문장 이내, 존댓말."


def generate(text, req):
    """여기에 실제 모델 호출을 넣는다. 지금은 규칙 기반 폴백."""
    t = (text or "").strip()

    if not t:
        return f"부르셨습니까, 대표님. ({req.get('time', '')} 수신)"

    # --- 예시 A: OpenAI 호환 엔드포인트 ---------------------------------
    # base = os.environ.get("LLM_BASE_URL")   # 예: http://127.0.0.1:1234/v1
    # key  = os.environ.get("LLM_API_KEY", "none")
    # model = os.environ.get("LLM_MODEL", "local-model")
    # if base:
    #     import urllib.request
    #     body = json.dumps({
    #         "model": model,
    #         "messages": [
    #             {"role": "system", "content": SYSTEM},
    #             {"role": "user", "content": t},
    #         ],
    #         "max_tokens": 300,
    #     }).encode()
    #     r = urllib.request.Request(
    #         base.rstrip("/") + "/chat/completions", data=body,
    #         headers={"Content-Type": "application/json",
    #                  "Authorization": "Bearer " + key})
    #     with urllib.request.urlopen(r, timeout=50) as resp:
    #         data = json.loads(resp.read())
    #     return data["choices"][0]["message"]["content"].strip()

    # --- 예시 B: 로컬 CLI 호출 -----------------------------------------
    # import subprocess
    # out = subprocess.run(["llm", "-m", "모델명", t],
    #                      capture_output=True, text=True, timeout=50)
    # if out.returncode == 0 and out.stdout.strip():
    #     return out.stdout.strip()

    # --- 폴백: 규칙 ----------------------------------------------------
    if t in ("안녕", "하이"):
        return "안녕하십니까, 대표님. 대기 중입니다."
    return f"「{t[:60]}」 전달받았습니다."


def main():
    try:
        req = json.load(sys.stdin)
    except Exception as e:
        print(f"입력 파싱 실패: {e}", file=sys.stderr)
        return 2

    try:
        reply = generate(req.get("text", ""), req)
    except Exception as e:
        print(f"생성 실패: {e}", file=sys.stderr)
        return 3

    if not reply:
        return 4
    print(reply.strip()[:900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
