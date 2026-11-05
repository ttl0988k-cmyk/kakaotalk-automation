---
name: kakaotalk-automation
description: 에이전트(Claude Code·Codex 등)가 지시받아 카카오톡 PC 앱을 win32 API로 직접 조작하는 스킬 — 방 찾기, 클립보드 전사로 읽기, 붙여넣기로 전송. 봇 시스템이 아니라 에이전트가 호출하는 도구다. CLI(kt.py)와 MCP 서버(kt_mcp.py, 도구 8종) 두 가지로 제공하며 MCP 쪽은 멱등 전송(operation_id)과 구조화된 메시지 반환을 지원한다. 여러 방이 열려 있어도 지정한 방에만 들어가도록 4중 오발송 가드(방이름 대조·포그라운드 검증·새 메시지 감지) 내장. 카카오톡 읽기·전송·방 이동 작업 시 사용. 실물 스크립트 동봉.
---

# 카카오톡 조작

`scripts/`의 CLI로 카톡을 읽고 보낸다. 작업 폴더는 스크립트가 있는 곳에서 실행하면 된다.

## 설치 (처음 한 번)

**사용자가 "설치해줘"라고 하면 아래 1~4를 실행하고 결과만 보고한다.**

**1) 저장소 받기**
```bash
git clone https://github.com/ttl0988k-cmyk/kakaotalk-automation.git
```

**2) 의존성**
```bash
pip install pywin32 psutil pillow
```

**3) 자기 스킬 디렉토리에 복사 — 에이전트별**
```bash
cp -r kakaotalk-automation ~/.claude/skills/          # Claude Code
cp -r kakaotalk-automation ~/.codex/skills/           # Codex
cp -r kakaotalk-automation ~/.gemini/skills/          # Gemini CLI
cp -r kakaotalk-automation ~/.hermes/profiles/<프로필>/skills/   # Hermes
```

**4) 확인**
```bash
cd <설치경로>/scripts && python kt.py windows
```
열린 카톡 방 목록이 나오면 성공이다.

> `~` = `C:\Users\<사용자>` (Windows)
> 설치해도 백그라운드에서 아무것도 시작되지 않는다. 호출받을 때만 동작한다.

## MCP 도구로 쓰기 (권장)

CLI 문자열 조립 대신 **구조화된 도구**로 호출한다. `scripts/kt_mcp.py` 가 stdio MCP 서버다.

```bash
python scripts/kt_mcp.py
```

MCP 설정에 등록:
```json
{ "mcpServers": { "kakaotalk-pc": {
    "command": "python",
    "args": ["<설치경로>/scripts/kt_mcp.py"] } } }
```

| 도구 | 인자 | 반환 |
|---|---|---|
| `kt_status` | — | 실행 여부, 열린 방 목록 |
| `kt_rooms` | — | 방 제목 목록 |
| `kt_read` | `room`, `last=15`, `since_hours=0`, `max_chars=2000` | `messages[]` (sender·time·body 분리) |
| `kt_send` | `room`, `text`, `operation_id`, `dry_run=False` | `status`, `exit_code` |
| `kt_send_result` | `operation_id` | 원장 기록 |
| `kt_open` | `room` | `hwnd`, `title` |
| `kt_show` | — | 복원된 창 목록 |
| `kt_alerts` | `limit=20`, `include_read=False` | 알림 큐 |

### ★ 전송은 반드시 이 순서로

```
1) operation_id 를 요청마다 하나 정한다 (예: reply-20261105-01)
2) kt_send(room, text, operation_id)
3) status 확인
   - sent            → 끝
   - sent_unverified → 재전송 금지. kt_read 로 도착 확인
   - failed          → 방 이름·창 상태 확인 후 재시도
4) 애매하면 같은 operation_id 로 kt_send_result 조회 (재전송 아님)
```

`sent_unverified` 는 실패가 아니다. 카톡이 긴 글을 **'전체보기'로 접으면**
화면 전사로는 끝부분이 안 읽혀 확인이 실패하는데, 실제로는 전송된 상태다.
실측(2026-11-05): 588자 메시지가 505자까지만 전사돼 확인 실패로 기록됐으나
실제로는 전문이 도착했다. **이때 재전송하면 같은 글이 두 번 간다.**

### 방 이름

- 부분 이름도 매칭된다 (`프로젝트` → `프로젝트방`)
- 못 찾으면 `candidates` 에 비슷한 방 이름이 함께 온다 — 그걸 보고 다시 지정한다
- 추측으로 아무 방이나 지정하지 않는다

## 명령

```bash
python kt.py windows                     # 열린 창 목록
python kt.py show                        # 숨은 메인 창 복원
python kt.py open "방이름"               # 방 열기 (부분 매칭)
python kt.py read                        # 열린 방 최신 30건
python kt.py read "방이름" -n 50         # 50건 (닫혀 있으면 자동으로 연다)
python kt.py read -n 20 --json           # 구조화 출력
python kt.py read --since 6              # 최근 6시간
python kt.py read -n 100 --deep 3        # PageUp 3회로 과거까지
python kt.py read --save                 # 원문을 _room/read_clipboard.txt에 저장
python kt.py send "내용" --to "방이름"          # dry-run
python kt.py send "내용" --to "방이름" --yes    # 실제 전송
```

`open`은 메인 창 OCR → 유사도 매칭으로 방을 찾는다. 실제 이름이 `AI를 영끌하는 사랑방 커뮤니티`여도 `"AI를 영끌하는 사랑방"`으로 찾힌다.

## 읽은 원문 다루기

```python
import kt
txt = open('_room/read_clipboard.txt', encoding='utf-8').read()
ms = kt.parse_transcript(txt)     # [{date, sender, time, body}, ...]
```

전사는 `[발신자] [오후 H:MM] 본문` 형식이고 256건까지 로드된다. 최신 구간만 필요하면 `ms[-40:]`.

## 스크립트에서 전송

```python
import kt
txt = open('notice.txt', encoding='utf-8').read().rstrip()
hwnd, title = kt.get_room('방이름')
kt.enforce(hwnd, kt.ROOM_STD_W, kt.ROOM_STD_H)
rc = kt.send_text(hwnd, title, txt, expect_title=title, dry_run=False)  # 0 = 성공
```

여러 줄은 `\n` 그대로 들어간다.

## 함정 (실측)

### ⛔ Escape 금지
앱 전체가 종료된다. 방 닫기는 `WM_CLOSE`:
```python
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
```

### ⛔ Ctrl+A는 입력창 전체선택이 아니다
대화 전체 선택으로 가서 글자가 안 지워진다. 정리는 `Backspace` 반복(200회).

### ⛔ 클릭 좌표
대화 영역 = 창 높이 **45%**, 입력창 = **85.5%**. 42%는 이모티콘 버튼을 누른다.
⚠️ **단, 좌표 클릭은 기본 경로에서 쓰지 않는다** (아래 '클릭 없는 읽기/쓰기' 참조).

### ★클릭 없는 읽기·쓰기 — 링크·이모지 오클릭 원천 차단 (2026-11-05 실측 확정)

**증상**: 대화 본문을 좌표 클릭하면 그 자리에 있는 링크가 눌려 브라우저가 열리거나,
이모지가 눌려 이모티콘 창이 뜬 채로 고정된다. 스크립트로 클릭하면 계속 그 창만 뜬다.

**원인**: 기존 `read_via_clipboard`가 `focus_chat(frac=0.45)`로 **대화 본문 중앙을 클릭**했다.
포커스만 주면 되는데 클릭을 써서 그 자리의 UI 요소가 눌렸다.

**해결**: 카카오톡은 자식 컨트롤을 HWND로 노출한다(실측).

| 자식 클래스 | 용도 |
|---|---|
| `EVA_VH_ListControl_Dblclk` | **대화 목록** |
| `RICHEDIT50W` | **입력창** |
| `_EVA_CustomScrollCtrl` | 스크롤바 |

좌표 클릭 대신 **`AttachThreadInput` + `SetFocus(자식핸들)`** 로 포커스만 옮긴다.
```python
ftid = win32api.GetCurrentThreadId()
ttid, _ = win32process.GetWindowThreadProcessId(hwnd)
att = ctypes.windll.user32.AttachThreadInput(ftid, ttid, True)
ctypes.windll.user32.SetFocus(child_hwnd)     # ★ 좌표 클릭 없음
ctypes.windll.user32.AttachThreadInput(ftid, ttid, False)
```
포커스가 실제로 그 컨트롤에 갔는지는 `GetGUIThreadInfo(ttid).hwndFocus` 로 검증한다.

- **읽기**: `focus_chat_noclick()` → Ctrl+A → Ctrl+C (커서 이동 0)
- **쓰기**: `focus_child_noclick(EVA_INPUT_CLASS)` → Ctrl+V → Enter
- **입력 검증**: `WM_GETTEXT` 로 입력창을 직접 읽는다. 비어 있으면 `"메시지 입력"` 플레이스홀더가 나오므로 빈 값으로 정규화.
- 폴백 순서: `SetFocus` → `Shift+Tab` → (allow_click=True일 때만) 좌표 클릭
- **`Shift+Tab`은 결정적 보조키**: 포커스가 **입력창(`RICHEDIT50W`)에 있을 때** `Ctrl+A/Ctrl+C`는
  전사가 실패한다(입력창 내용만 잡힘). `Shift+Tab` 1회로 대화영역으로 넘어가면 성공한다.
  `Tab`·`F6`·`Ctrl+Tab`은 대화영역으로 못 간다(실측 비교).
- **`AttachThreadInput`은 반드시 `False`로 해제**한다. 해제를 빠뜨리면 두 스레드 입력 큐가 묶여
  카톡이 키를 씹는다.

**이모지/링크 창이 이미 떠버렸을 때 (복구)**
`Escape`는 앱 종료라 절대 쓰지 않는다. 그 창은 `Alt+F4` 또는 창의 닫기 버튼으로 닫는다.
이모티콘 창은 `PostMessage(WM_CLOSE)`도 통한다 — 닫은 뒤 `kt.py windows`로 방 창이 살아있는지 확인.

**실측(2026-11-05)**:
| 항목 | 결과 |
|---|---|
| 클릭 없는 읽기 | 6,418자 / 131건 / **0.74초** / method=`setfocus-noclick` |
| 클릭 없는 쓰기(dry-run) | 입력 검증 OK / 정리 완료 / method=`focus_child_noclick` |
| **마우스 커서 이동** | **0px** (before == after) |
| 좌표 클릭 호출 | 기본 경로 **0회** (allow_click 폴백에만 존재) |

- `WM_GETTEXT`로 대화 목록을 읽는 것은 **불가**(0자). 리스트 컨트롤이 텍스트를 노출하지 않는다 → 클립보드 전사가 유일.
- `PostMessage(WM_KEYDOWN)` 로 키를 직접 보내는 것도 **불가**(포커스 없으면 무시).

**★"클릭 0회"를 주장이 아니라 계측으로 증명하는 법**
스크립트 안에서 로그를 "클릭 안 함"으로 찍는 것은 자기신고라 증거가 안 된다. 실행 전후 **실제 커서 좌표**를 대조한다.
```python
import win32api, subprocess, sys
b = win32api.GetCursorPos()
r = subprocess.run([sys.executable,'kt.py','read','A방','-n','2'],
                   capture_output=True, text=True, encoding='utf-8')
a = win32api.GetCursorPos()
print('커서이동:', '0px ✔' if b==a else f'{b} -> {a}')   # 0px 이어야 통과
```
같은 패턴을 전송·방 열기에도 적용한다. `0px`가 아니면 어딘가 좌표 클릭이 살아있다는 뜻이다.


### ⛔ 여러 방 열려 있으면 `--to` 필수
없이 보내면 엉뚱한 방에 간다. 작업 전 `kt.py windows`로 확인.

### ⛔⛔ 창이 여러 개면 "읽기"도 다른 방을 잡는다 — 내용 오귀속 (2026-11-05 실측)

**증상**: `A방` 을 감시하는데 알림 요약이 이렇게 찍혔다.
`카톡 트리거 [A방] B방: @라온아 ...` — **방은 A방인데 발신자가 다른 방 이름**.
즉 `Ctrl+A`/`Ctrl+C` 로 긁은 전사가 대상 방이 아니라 **직전 활성 방의 것**이었다.

**원인 후보(실측으로 좁힌 것)**:
- `SetFocus(자식핸들)` 성공 후에도 카톡 내부의 "마지막 활성 대화방" 상태가 남아, 복사가 그 방 기준으로 수행됨.
- 화면 캡처 도구의 `CaptureSelectionWnd` 오버레이가 3개 떠 있어 키 입력을 가로챌 수 있음.

**현재 가드 (사고는 안 남)**:
`smt_text()` 가 입력 직전 `GetForegroundWindow()==hwnd` 를 재검증 → 불일치면 **전송 중단(반환 8)**.
그래서 이번엔 오발송 0건이었지만, **읽은 내용이 틀린 채로 트리거로 기록됐다.**

**⭐ 오귀속을 잡는 검증 규칙**
읽은 뒤 `parse_transcript()` 결과의 **발신자·본문**이 대상 방 문맥과 맞는지 교차 확인한다.
방 이름만 로그에 찍는 것으로는 절대 못 잡는다 — 이번 사고가 정확히 그 구멍이었다.
- 알림 payload에 **`room`(대상) / `detected_sender`(전사에서 읽힌 발신자)** 를 **둘 다** 넣는다.
- 두 값이 대상 방과 무관해 보이면 **답장하지 말고 경고만 올린다.**
- 근본 해결 전까지는 **감시 중에는 해당 방 창 1개만 남기는 것**이 가장 안전하다.

**알림 검증 순서**
1. `_watch.log` 배너의 `방=` 이 의도한 방인지
2. `kt.py alerts -n 5` 로 새 알림 적재 확인
3. **요약의 발신자가 그 방 사람인지** 육안 대조 (← 이 단계를 빼면 오귀속을 통과시킨다)
4. 실제 답장 전 `--to` 로 방을 고정

### ⛔ OCR이 0줄이면 `win_ocr.ps1` 부재를 먼저 의심
`kt_open.py`의 `OCR_PS1 = HERE/win_ocr.ps1`. 이 파일이 없으면 `ocr_image()`가 조용히 `[]`를
돌려주고 `[3/7] OCR 라인 0개` → **모든 방 열기가 실패**한다(에러도 안 뜬다).
- 증상: `[FAIL] "X" 매칭 실패 (최고점 0.0 < 0.45)`
- 확인: `ls scripts/win_ocr.ps1`
- 구현: Windows.Media.Ocr(WinRT) + `AsTask` Await 패턴. 출력 JSON은 **BOM 없는 UTF-8**
  (`[System.IO.File]::WriteAllText(..., UTF8Encoding($false))`) — Python이 `encoding='utf-8'`로 읽는다.

### ⛔ OCR이 특정 방 이름만 계속 놓칠 때 (좌표 폴백)
'A방' 방은 2배 업스케일로도 안 잡혔다. OCR이 방 이름은 못 읽고 **미리보기만** 읽어
유사도 0.315로 탈락. 이때 OCR을 더 파지 말고 좌표로 직접 연다:
1. `_work/screen.png`에서 의심 구간을 크롭·2배 확대 → `vision_analyze`로 "위에서 몇 번째 행,
   크롭 안 세로 %"를 판독
2. 원본 y = 크롭시작y + (판독px / 2)
3. `scripts/_open_by_coord.py`의 `TEXT_X/Y`를 그 값으로 바꿔 실행
   (`cx = l + (x+OFFSET_X)*sx`, `cy = t + (y+OFFSET_Y)*sy`, OFFSET_X=90 · OFFSET_Y=-8)
- ⚠️ 스크롤 위치가 바뀌면 y도 바뀐다. 반드시 **방금 캡처한 screen.png** 기준으로 계산.

### ✅ 다중 방 오발송 차단 — 4중 가드 (실측 검증됨)
`send_text()`가 아래 순서로 막는다. 반환코드로 어디서 멈췄는지 알 수 있다.

| # | 가드 | 막는 사고 | 반환 |
|---|---|---|---|
| 1 | 방이 2개 이상인데 `--to` 없음 → 아예 거부 | 아무 방에나 발송 | 7 |
| 2 | 방 이름 유사도 < 0.5 → 조작 전 중단 | 다른 방에 입력 | 3 |
| 3 | 포그라운드 확보 실패 → **클릭하지 않음** | 좌표 클릭이 남의 창에 찍힘 | 8 |
| 4 | 입력 후 새 메시지 도착 감지 → Enter 취소 | 대화 밀림 속 오발송 | 5 |

- 가드 3이 핵심: `focus()`가 `GetForegroundWindow()==hwnd`를 **검증**하고, 실패하면
  `focus_chat()`이 `False`를 돌려줘 클릭을 건너뛴다. Enter 직전에도 한 번 더 확인한다.
- 검증 스크립트: `scripts/_test_multiroom_guard.py` (실제 입력·전송 없이 5개 항목 PASS 확인).

#### ★ 일반 원칙 — win32 좌표 클릭은 "최상위 창"으로 들어간다
`SetForegroundWindow()`는 **요청일 뿐 보장이 아니다**(Windows 포그라운드 락).
호출이 예외 없이 지나가도 실제 활성 창은 그대로일 수 있고, 그 상태의 `mouse_event` 좌표 클릭은
**엉뚱한 창에 찍힌다.** 그래서 좌표 기반 자동화는 반드시:

1. `SetForegroundWindow` **호출 후** `GetForegroundWindow() == hwnd`로 **검증**한다
2. 실패 시 ALT 탭(`keybd_event(0x12)`)으로 락 해제 후 **재시도 3회**
3. 그래도 실패면 **클릭하지 말고 중단**(성공 위장 금지) — 반환값을 호출부가 검사하게 만든다
4. 키 입력(`Enter`) 직전에도 한 번 더 확인한다 — 그 사이 포커스가 튈 수 있다

이 패턴은 카톡뿐 아니라 메신저·ERP·포토샵 등 좌표 클릭하는 모든 win32 자동화에 동일 적용된다.


### ⛔ 종료코드 6 = 실패가 아니다 — 긴 메시지 접기 오탐 (2026-11-05 실측 확정)

**500자 넘는 메시지를 보내면 반드시 이 일이 벌어진다.**

```
[send] 방 이름 대조 OK (유사도 1.0)
[send] 입력 검증 OK (588자)          ← 입력창에 전문 들어간 것 확인됨
[send] Enter 전송 실행
[send] ⚠ 전송 확인 실패 — 최근 메시지: [... '공유합']
→ exit 6
```

**전송은 성공했다.** 카톡이 긴 말풍선을 `전체보기`로 접어 보여주기 때문에,
전사(클립보드 복사)에도 접힌 지점까지만 잡혀 확인 로직이 매칭에 실패하는 것뿐이다.

실측 대조:

| 지표 | 값 | 의미 |
|---|---|---|
| 입력창 검증 | 588자 | 전문이 입력됐다 |
| 전사 원문 | 505자에서 `…` | 화면에 보이는 만큼만 복사됨 |
| 화면 판독 | `전체보기` 버튼 존재 | 카톡이 접은 상태 |
| 실제 끝 | `필요한 명령만…` | 원문은 `필요한 명령만 실행` |

**판정 규칙 — exit 6을 받으면 이 순서로만 확인하고 끝낸다.**

1. `입력 검증 OK (N자)` 가 찍혔는가 → 찍혔으면 **전송된 것**이다. 재전송 금지.
2. 화면 캡처 1장 → `vision_analyze` 에 **짧은 질문 3개**로 묻는다.
   - "마지막으로 보이는 글자가 뭔가요?"
   - "URL이 보이나요?"
   - "`전체보기` 가 있나요?"
   > 질문이 길고 장황하면 vision이 엉뚱한 추론을 늘어놓는다. 짧게 3개만.
3. `전체보기` 가 확인되면 **접힘 확정**. 재전송하지 않는다.

**끝부분(URL 등)이 접혀 안 보이면** 원문을 다시 보내지 말고, 그 부분만 짧게 후속 전송한다.
긴 글 + 링크는 이렇게 2통이 정상이다.

```
위 긴 글은 말풍선의 '전체보기'를 누르면 끝까지 펼쳐집니다.
바로 보실 분은 여기로 ↓
<URL>
```

### ⚠️ 접히는 길이는 고정이 아니다
503자(이전 실측) · 505자(2026-11-05) — **"500자 근처에서 접힌다"** 로만 기억한다.
특정 숫자를 기준으로 판정하지 말 것.

### ⚠️ 내 메시지도 전사에 발신자가 찍힌다
`[내닉네임] [오전 2:24] ...` 처럼 나온다. 화면 말풍선에만 안 보인다.

### ⚠️ 답장 확인은 1회만
재읽기·재스크린샷 반복 금지.

## 왜 클립보드인가

| 시도 | 결과 |
|---|---|
| UIA/MSAA | 41 nodes, 메시지 텍스트 **0건** |
| 클립보드 전사 | **0.35초, 정확도 100%** |
| Windows OCR | 0.74초, 오독 (좌표 탐색용으로만) |
| Tesseract `kor` | 6자만 → 실패 |

### 참조 벤치마크 — macOS `kax` (2026-11-05 대조)
`D:\daon\_refs\kax\kakaotalk-ax` — 약 2,230줄(Swift AX 브리지 960줄), MCP 도구 13개.
접근성 트리에서 **발신자·시각·첨부를 필드로 분리**해 읽고, 첨부 이미지까지 넘긴다.

- 실질 격차는 **읽기 정밀도**(우리는 평탄화 + 접힘 손실)와 **인터페이스**(저쪽 MCP 13도구 / 우리 CLI 8서브커맨드).
- 단 **맥 전용**이라 윈도우 대체재가 아니다. 포팅 대상이 아니라 설계 참고용.
- 저쪽 멱등 전송 패턴(요청별 ID 1회 생성 → 결과 불확실 시 재전송 금지, 조회만)은 위 exit 6 규칙과 같은 사상이니 그대로 유지한다.
- ⚠️ **"AX로 읽으면 낫지 않나"를 다시 시도하지 않는다.** 윈도우 UIA는 위 표에서 이미 반증됐다(메시지 텍스트 0건). 이 질문은 닫혔다.

## 부재중 자동응답

봇이 `@라온아` 트리거에 직접 답한다. 이게 정상 운용이다.

```bash
python kt_watch.py --room "방이름" --me "내닉네임" --interval 20 --yes
```

트리거 기본 `@라온아,@라온`. `--hook "명령"`으로 외부 생성기 연결(stdin JSON → stdout).
끌 때: `terminal(background=true)`는 bash 래퍼와 파이썬 본체가 따로 산다. 래퍼만 죽이면 계속 돈다.

```bash
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*kt_watch*' } | ForEach-Object { \$_.ProcessId }"
powershell -NoProfile -Command "Stop-Process -Id <PID> -Force"
```

검증은 `_watch.log` 줄 수가 늘지 않는지로.

### ⛔⛔ 워처는 반드시 1개만 — 중복 실행이 읽기 오염의 진짜 원인 (2026-11-05 실측)

`wmic` 로 프로세스를 조회하면 이 환경에서 `WinError 2` 로 실패한다. 실패를 "없음"으로
오판해 워처를 계속 새로 띄우면 **3개가 동시에 폴링하며 포커스를 서로 뺏는다.**
그 결과 A방을 감시하는데 B방 내용을 읽어 **발신자·방이 뒤섞인 트리거**가 만들어진다.

→ 중복 감지는 `run_watch.py`(PowerShell CIM 기반)를 쓰고, 기동 전 PID를 반드시 확인한다.

```bash
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*kt_watch*' } | ForEach-Object { $_.ProcessId }"
```

#### ★ 방 이름 정합 검증 + pause 신호 (2중 방어)
- `poll_once` 는 읽은 창 제목과 기대 방 이름의 유사도가 **0.5 미만이면 폴링 결과를 폐기**한다.
- `kt.py send` 는 시작 시 `_work/watch_pause` 를 만들고, 워처는 그 파일이 있는 동안 폴링을 건너뛴다.
  (전송과 폴링이 동시에 돌면 서로 포커스를 뺏어 읽기 오염·전송 실패 — 실측)
- `read_via_clipboard` 는 복사 직후 `GetForegroundWindow()` 를 재확인해, 도중에 창이 바뀌었으면 내용을 버린다(TOCTOU).

#### ⛔ 첫 폴링이 실패하면 baseline 을 넘기지 말 것
`--reset-state` 로 시작했는데 첫 폴링이 읽기 실패하면, baseline 이 설정되지 않은 채
다음 폴링에서 방의 과거 메시지 수백 건이 전부 '신규'로 판정된다. 그 결과 **지난 트리거가
무더기로 재배달**된다(실측: pending 폭주). `state["_baseline_pending"]` 으로
성공 폴링에서만 baseline 을 확정한다.

### (보조) `--notify-only` — 봇이 답하지 않고 큐에만 적재

기본은 봇 응답이다. 트리거를 모아두고 나중에 사람이 볼 때만 이 옵션을 쓴다.

```bash
python kt_watch.py --room "방이름" --notify-only --rounds 0 --interval 20 --me "내닉네임"
python kt.py alerts -n 20            # 미확인 알림 조회
python kt.py alerts --mark-read      # 확인 처리
python kt.py alerts --clear          # 큐 비우기
```

- 큐 파일: `scripts/_work/alerts.jsonl` (JSONL, 최대 500건 자동 트림)
- `--notify-only` 에서는 **쿨다운을 무시**한다 — 연속 트리거를 빠짐없이 잡아야 하므로.
  실측: 쿨다운 3초가 켜져 있으면 트리거 4건 연속 중 **1건만** 적재됐다.
- 선택: `--notify-webhook URL` 로 외부(슬랙·텔레그램)에도 POST.

### ★상시 워처를 세션과 분리해 띄우기 (2026-11-05 실측)

백그라운드로 띄운 워처는 **부모 세션이 정리되면 같이 죽는다.** 로그에 시작 배너만 남고 끝난 경우가 그 증상이다. 반드시 detached 로 띄운다.

**❌ 실패한 방법**
- `cmd //c start "" pythonw kt_watch.py ...` (Git Bash) → **150초 타임아웃, 프로세스 생성 0.** Git Bash에서 `start` 는 사실상 안 먹는다. 재시도 금지.
- `powershell -File start_watch.ps1` 에 **한글 방 이름을 인자로** 넣기 → PS1이 UTF-8인데 PowerShell이 시스템 코드페이지로 읽어 `"--room", "A방"` 의 따옴표가 깨져 ParserError. **한글 리터럴은 PowerShell/배치에 넘기지 말 것.**

**✅ 정답 — 파이썬 런처(`scripts/run_watch.py`)**
```python
import subprocess, os, sys
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PYW = sys.executable.replace("python.exe", "pythonw.exe")
DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [PYW, os.path.join(SCRIPTS, "kt_watch.py"),
     "--room", "방이름", "--notify-only", "--interval", "8", "--window", "30"],
    cwd=SCRIPTS, creationflags=DETACHED,
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
```
- 방 이름·경로 등 **한글은 전부 파이썬 안에** 둔다. 셸을 거치지 않으므로 코드페이지 문제가 원천 차단된다.
- 실행: `python run_watch.py --room "방이름" [--interval 20] [--me "내닉네임"] [--notify-only]`
  → `STARTED pid=13380` 이 나오면 성공. `--room` 생략 시 `KT_ROOM` 환경변수를 쓴다.
  (실제 방 이름을 코드에 하드코딩하지 않는다 — 공개 리포에 남기 때문)
- `run_watch.py` 는 기동 전에 **중복 기동 가드**(PowerShell CIM 으로 기존 PID 조회 → 교체)를 갖는다.
- 기동 5초 후 `p.poll() is None` 로 생존을 확인하고, 죽었으면 `_watch.log` 꼬리를 자동 출력한다 — 조용한 실패를 막는 장치.
- ⚠️ `container: 'pythonw'` 로 띄우면 **stdout이 없어** 로그가 `_watch.log` 로만 남는다. 터미널 출력을 기대하지 말 것.

### CLI 문법 함정 (자주 틀림)
| 하고 싶은 것 | 틀린 명령 | 맞는 명령 |
|---|---|---|
| 방 목록 | `kt.py list` | `kt.py windows` |
| 특정 방 읽기 | `kt.py read --to "방"` | `kt.py read "방" -n 8` (name은 위치 인자) |
| 전송 | `kt.py send --to "방" "본문"` | `kt.py send "본문" --to "방"` (본문이 위치 인자, `--to`는 방 대조용) |
| 긴 본문 읽기 | `kt.py read "방" -n 1` | `kt.py read "방" -n 1 --max-chars 3000` (기본 **200**자에서 잘림) |

> `send` 에는 `--to` 가 있고 `read` 에는 **없다.** 둘을 같은 규칙으로 착각하면 `unrecognized arguments: --to` 로 즉사한다.
> `read` 출력이 `…` 로 끝나면 `--max-chars` 를 올려 다시 읽는다(기본 200).

### ⛔ 한글 방 이름·본문을 쉘 인자로 넘기지 말 것 (2026-11-05 실측)

bash에서 한글을 `"$(cat file)"` 로 넘기면 Windows 코드페이지에서 깨질 수 있다.
**파이썬 안에서 `sys.argv` 를 구성하고 `runpy` 로 실행**하면 인코딩을 완전히 우회한다.

```bash
python - <<'PY'
import io, sys, runpy
sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # 출력 깨짐 방지

body = io.open(r'C:\path\msg.txt', encoding='utf-8').read().rstrip()   # 본문은 파일에서
sys.argv = ['kt.py', 'send', body, '--to', '방이름', '--yes']          # 한글은 파이썬 리터럴
runpy.run_path('kt.py', run_name='__main__')
PY
```

`kt.py read` 도 같은 방식으로 호출한다(방 이름을 파이썬 문자열로).

### ⛔ PowerShell 출력을 파이썬으로 파이프하면 UnicodeDecodeError

`win_ocr.ps1` 은 시스템 코드페이지(cp949)로 stdout에 쓴다. 파이프로 받으면 죽는다.

```bash
# ✗ UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc7
powershell -File win_ocr.ps1 ... | python -c "import json,sys; json.load(sys.stdin)"

# ✓ 파일로 받아 utf-8 로 읽기
powershell -ExecutionPolicy Bypass -File win_ocr.ps1 -Path "img.png" -OutJson "out.json"
python -c "import io,json; d=json.loads(io.open('out.json',encoding='utf-8').read())"
```

**`win_ocr.ps1` 은 인자가 2개 다 필수다** — `-Path`(이미지) 와 `-OutJson`(출력).
하나만 주면 `MissingMandatoryParameter` 로 즉사하고, 그 오류문이 cp949라 원인이 안 보인다.
`lines` 가 0개로 나오면 인자 문제가 아니라 **캡처 시점에 창이 가려진 것**이다.

### 방 창 캡처 (검증용 — 3단 고정)

```python
import win32gui, win32con, time
from PIL import ImageGrab
hwnd = 262798                                    # kt.py windows 로 확인
win32gui.ShowWindow(hwnd, win32con.SW_SHOW)      # ① 복원
win32gui.SetForegroundWindow(hwnd)               # ② 전면 (이 순서를 지켜야 좌표가 먹는다)
time.sleep(0.6)                                  # ③ 렌더 대기 — 없으면 OCR 0줄
l, t, r, b = win32gui.GetWindowRect(hwnd)
ImageGrab.grab(bbox=(l, t, r, b)).save('shot.png')
```

`kt_room.py` 는 `read|send|watch` 액션 전용이라 `--shot` 같은 옵션이 없다. 캡처는 위 코드로 직접 한다.


## 파일

| 파일 | 역할 |
|---|---|
| `scripts/kt.py` | 통합 CLI |
| `scripts/kt_room.py` | 창 탐색, OCR, 클립보드 전사 |
| `scripts/kt_open.py` | OCR 매칭으로 방 열기 |
| `scripts/win_ocr.ps1` | **Windows OCR 엔진 (필수)** — 없으면 방 열기 전부 실패 |
| `scripts/_open_by_coord.py` | OCR이 놓친 행을 좌표로 직접 열기 (TEXT_X/Y 수정해서 사용) |
| `scripts/_test_multiroom_guard.py` | 다중 방 오발송 가드 5항목 자가검증 (실제 전송 없음) |
| `scripts/_test_child_read.py` | 자식 컨트롤 `AttachThreadInput+SetFocus` 클릭 없는 읽기 검증 |
| `scripts/_test_clickless_read.py` | 클릭 없이 `Ctrl+A/Ctrl+C` 전사 가능한지 검증 |
| `scripts/_test_clickless_send.py` | 클릭 없는 쓰기(입력→`WM_GETTEXT` 검증→정리) 검증 |
| `scripts/_test_clickless_edge.py` | 입력창 포커스·새 방·스크롤 후 읽기 반례 검증 |
| `scripts/_test_focus_via_keys.py` | `Tab`/`Shift+Tab`/`F6`/`Ctrl+Tab` 포커스 이동 비교 (Shift+Tab 판별용) |
| `scripts/kt_show.py` | 숨은 창 복원 |
| `scripts/kt_watch.py` | 자동응답 감시 (기본) / `--notify-only` 알림 적재 |
| `scripts/run_watch.py` | 워처 단일 인스턴스 기동(중복 방지) — PowerShell CIM 으로 기존 PID 확인 후 교체 |
| `scripts/llm_hook.py` | 응답 생성기 템플릿 |
| `references/README_kt.md` | 상세 사용법 |

## 실측 검증 기록 (2026-11-05)

> ⛔ **기록은 처음부터 비식별 이름으로 적는다** — `A방`·`테스트방`·`내게쓴방`·`내닉네임`.
> 실제 방 이름·인물 실명·닉네임은 이 파일에 쓰지 않는다. 한 번 적으면 리포 이력에 남아
> force push 로도 안 지워진다(`skill-publish-github` 사후 처리 항목 참조).

3개 방(내게쓴방 / A방 / 테스트방)을 동시에 띄운 상태에서 `--to "내게쓴방"` 전송 실험:
- 결과: '내게쓴방' 방에만 `오전 9:58` 도착, A방·테스트방 **오염 0건**
- 로그: 방 유사도 `1.0` → 입력 검증 `37자` → Enter → 최근 3건 내 확인
- 사고 2건 동시 발견·수리: ①`win_ocr.ps1` 부재로 OCR 0줄 ②'A방' 행 OCR 누락 → 좌표 폴백

### 워처 중복 실행 → 방 오귀속 (2026-11-05 실측)

`wmic` 프로세스 조회가 `WinError 2` 로 실패하는 환경에서, 실패를 "없음"으로 오판해
워처를 3개까지 중복 기동했다. 3개가 동시에 폴링하며 포커스를 서로 뺏은 결과
**A방을 감시하는데 B방 메시지를 읽어 방 이름이 뒤섞인 트리거**가 만들어졌다.
- 수리: ①`run_watch.py` 가 PowerShell CIM 으로 기존 PID 확인 후 교체 ②`poll_once` 가
  읽은 창 제목 ↔ 기대 방 유사도 0.5 미만이면 결과 폐기 ③`kt.py send` 가
  `_work/watch_pause` 를 켜면 워처가 폴링 중단 ④복사 직후 `GetForegroundWindow()` 재확인
- 교훈: **포커스를 쓰는 자동화는 동시 실행 자체가 결함이다.** 단일 인스턴스를 강제할 것.

## 정합성 검증 (이 스킬을 수정한 뒤 필수)

이 스킬은 **실물 스크립트 동봉 + 2곳 미러** 구조라 문서만 고치면 조용히 어긋난다.
수정 후 아래 6개를 전부 통과해야 "완료"다.

1. **문서 파일표 ↔ 실물 대조** — 파일표의 모든 경로가 실제로 존재하는지 전수 확인(`누락 0건`). 스크립트를 새로 추가했으면 **표에도 같이 적는다 — 표가 곧 계약이다.**
2. **문법** — `python -m py_compile kt.py kt_watch.py run_watch.py kt_room.py kt_open.py kt_show.py llm_hook.py`
3. **미러 일치** — `SKILL.md`·`.gitignore`·`scripts/kt.py`·`scripts/kt_watch.py`·`scripts/run_watch.py`를 `~/.claude/skills/dev/kakaotalk-automation`과 `diff -q`. 불일치면 복사.
4. **옵션 잔재** — 제거한 옵션명이 **사용법·예제 구간**과 코드에 `0`건인지 `grep -c`로 확인. 예) 기능 철거 시 문서 사용법 `0` + `--help`의 해당 옵션 `0`. 코드만 지우고 문서에 남기면 다음에 없는 기능을 쓰려 든다.
5. **워처 상태** — ①단일 프로세스인지 PowerShell CIM으로 PID 확인 ②`grep "모드=" scripts/_watch.log | tail -1` 로 **실전송**인지 확인(DRY-RUN·notify-only로 남아 있지 않은지).
6. **frontmatter** — `name:`·`description:` 생존 확인. 여기가 깨지면 스킬 로딩 자체가 죽는다(가장 흔한 사망 원인).

⚠️ 틀린 설명("이 경로로는 주입 불가" 등)을 실험으로 반증했으면 **문서에서 지운다.** 남겨두면 다음에 같은 삽질을 반복한다.

## 배포

MIT 라이선스로 GitHub에 공개되어 있다(리포 URL은 소유자 계정 참조).
`scripts/_work/`(대화 캡처·OCR 산출물)는 `.gitignore` 로 제외 — **절대 커밋 금지**(개인 대화 포함).


