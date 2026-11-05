# kakaotalk-automation

> ## ⚠️ 이것은 **에이전트 스킬**입니다 — 봇 시스템이 아닙니다
>
> **Claude Code · Codex · Gemini CLI · Cursor 등 AI 에이전트가 로드해서 쓰는 스킬**입니다.
> 상시 떠서 혼자 답하는 **챗봇 플랫폼이 아닙니다.**
>
> ```
> 사용자 지시  →  에이전트가 SKILL.md를 읽고  →  scripts/kt.py 를 실행  →  카톡 전송/응답
> ```
>
> **에이전트가 판단해서 도구를 호출하는 구조**입니다. 데몬·서버·계정 연동 봇이 아닙니다.
> 설치 후 자동으로 무언가를 하지 않습니다. **호출해야만** 동작합니다.
>
> `scripts/kt_watch.py` 에 **자동응답(봇) 모드가 부수 기능으로 들어 있지만**,
> 그것은 "에이전트 없이도 돌릴 수 있는 편의 옵션"일 뿐 **이 저장소의 정체성이 아닙니다.**
> 핵심은 **에이전트가 지시를 받아 카카오톡 메시지를 읽고·보내는 능력**을 부여하는 것입니다.

Windows **카카오톡 PC 앱**을 win32 API로 직접 조작하는 **에이전트 스킬 + CLI 툴킷**입니다.
UI 자동화 라이브러리(UIA/MSAA) 없이 **창 메시지 + 클립보드 전사**로 읽고, **붙여넣기**로 보냅니다.

여러 채팅방 창이 동시에 떠 있어도 **지정한 방에만** 글이 들어가도록 4중 오발송 가드를 내장했습니다.

---

## 이게 뭔가 / 이게 뭔가 아닌가

| ✅ 이것은 | ❌ 이것은 아님 |
|---|---|
| AI 에이전트가 로드하는 **스킬** (`SKILL.md` + 실행 스크립트) | 카카오톡 봇 서비스·플랫폼 |
| 에이전트가 **지시받았을 때** 카톡을 읽고 보내는 **도구** | 24시간 상시 대기하는 자동응답 서버 |
| 로컬 CLI (`kt.py read/send/open/windows`) | 카카오톡 계정 API 연동 봇 (공식 API 아님) |
| 창 핸들 기반 **데스크톱 UI 자동화** | 모바일·웹 카카오톡 지원 도구 |
| 사용자가 **직접 호출**해서 쓰는 유틸 | 설치하면 알아서 동작하는 자동화 |

### 에이전트가 이 스킬을 쓰는 실제 흐름

```
① 사용자: "○○방에 '회의 3시'라고 보내줘"
② 에이전트: SKILL.md 로드 → kt.py 사용법 확인
③ 에이전트: kt.py windows   (열린 방 확인)
④ 에이전트: kt.py send "회의 3시" --to "○○방" --yes
⑤ 결과 확인 후 사용자에게 보고
```

에이전트가 **상황을 판단해 명령을 조합**합니다. 정해진 시나리오만 반복하는 봇이 아닙니다.

### 부수 기능: 자동응답 모드 (`kt_watch.py`)

에이전트 없이 단독으로 돌리고 싶을 때만 쓰는 **선택 옵션**입니다.

```bash
python scripts/kt_watch.py --room "방이름" --me "내닉네임" --interval 20 --yes
```

- 트리거(`@봇이름`)가 오면 **내장 응답기**가 정해진 몇 가지에만 답합니다.
- 진짜 대화가 필요하면 `--hook "명령"`으로 LLM을 붙입니다.
- ⚠️ 단독 실행은 PC 포커스를 주기적으로 가져갑니다. **평소에는 에이전트 호출 방식을 권장합니다.**

---

## 왜 만들었나

카카오톡 PC 앱은 메시지 텍스트를 UIA/MSAA로 노출하지 않습니다(실측: 41 nodes, 본문 **0건**).
그래서 이 툴은:

| 읽기 | 쓰기 |
|---|---|
| 창 포커스(SetFocus) → `Ctrl+A` → `Ctrl+C` → 클립보드 파싱 | 클립보드에 넣고 `Ctrl+V` (한글 IME 우회) |

### ★좌표 클릭을 쓰지 않습니다

대화 본문을 좌표로 클릭하면 **그 자리에 있는 링크가 눌려 브라우저가 열리거나,
이모지가 눌려 이모티콘 창이 뜬 채 고정**됩니다. 스크립트로 조작할 때 치명적입니다.

그래서 카카오톡이 노출하는 **자식 컨트롤 핸들에 직접 포커스**를 줍니다.

| 자식 클래스 | 용도 |
|---|---|
| `EVA_VH_ListControl_Dblclk` | 대화 목록 |
| `RICHEDIT50W` | 입력창 |

```python
ftid = win32api.GetCurrentThreadId()
ttid, _ = win32process.GetWindowThreadProcessId(hwnd)
ctypes.windll.user32.AttachThreadInput(ftid, ttid, True)
ctypes.windll.user32.SetFocus(child_hwnd)      # 좌표 클릭 없음
ctypes.windll.user32.AttachThreadInput(ftid, ttid, False)
```

- 읽기·쓰기 모두 **마우스 커서가 0px도 움직이지 않습니다** (실측)
- 입력 검증도 `WM_GETTEXT`로 입력창을 직접 읽으므로 클릭·클립보드가 불필요합니다
- 폴백: `SetFocus` → `Shift+Tab` → (옵션 활성 시에만) 좌표 클릭

부수 효과로 **클립보드 백업/복원**, **포커스 검증** 같은 안전 장치가 필수가 됩니다.

---

## 설치 (에이전트 스킬로)

**"설치"는 프로그램 실행이 아니라, 에이전트의 스킬 디렉토리에 이 폴더를 넣는 것입니다.**
설치해도 백그라운드에서 아무것도 시작되지 않습니다. 호출받을 때만 동작합니다.

설치 방법은 **두 가지**입니다. 편한 쪽을 쓰세요.

---

### 방법 1 — 터미널에서 직접 설치

```bash
# 1) 저장소 받기
git clone https://github.com/ttl0988k-cmyk/kakaotalk-automation.git
cd kakaotalk-automation
```

```bash
# 2) 의존성 설치
pip install pywin32 psutil pillow
```

```bash
# 3) 스킬 디렉토리에 복사 — 에이전트별 경로

# Claude Code
mkdir -p ~/.claude/skills
cp -r . ~/.claude/skills/kakaotalk-automation

# Codex / AGENTS.md 기반
mkdir -p ~/.codex/skills
cp -r . ~/.codex/skills/kakaotalk-automation

# Gemini CLI
mkdir -p ~/.gemini/skills
cp -r . ~/.gemini/skills/kakaotalk-automation

# Hermes 프로필
mkdir -p ~/.hermes/profiles/<프로필>/skills
cp -r . ~/.hermes/profiles/<프로필>/skills/kakaotalk-automation

# Cursor · 프로젝트 단위
mkdir -p .agent/skills
cp -r . .agent/skills/kakaotalk-automation
```

> `~` 는 Windows 기준 `C:\Users\<사용자>` 입니다.
> Git Bash에서는 `~/.claude/skills` 그대로 쓰면 됩니다.

```bash
# 4) 설치 확인
cd ~/.claude/skills/kakaotalk-automation/scripts
python kt.py windows
```

열려 있는 카톡 방 목록이 나오면 설치 성공입니다.

---

### 방법 2 — 에이전트에게 말로 시키기 (가장 쉬움)

터미널을 직접 칠 필요 없이, **에이전트에게 그냥 이렇게 말하면 됩니다.**

```
이 저장소를 네 스킬로 설치해줘.
https://github.com/ttl0988k-cmyk/kakaotalk-automation

설치 확인까지 해줘.
```

에이전트가 이렇게 처리합니다.

| 단계 | 에이전트가 하는 일 |
|---|---|
| 1 | `git clone` 으로 저장소 받기 |
| 2 | 자기 스킬 디렉토리 경로 확인 (`~/.claude/skills` 등) |
| 3 | 폴더 복사 + `pip install pywin32 psutil pillow` |
| 4 | `python kt.py windows` 로 동작 확인 |
| 5 | 결과 보고 |

**에이전트별로 이렇게 말해도 알아듣습니다:**

```
# Claude Code / Codex
"kakaotalk-automation 스킬 설치하고 kt.py windows 로 확인해줘"

# 이미 클론해 둔 폴더가 있다면
"이 폴더를 네 스킬 디렉토리에 넣고 SKILL.md 읽어서 쓸 수 있게 해줘"

# 설치 + 바로 사용까지 한 번에
"카톡 자동화 스킬 설치하고, 열린 방 목록 보여줘"
```

> 💡 **핵심**: 이 스킬은 `SKILL.md` 한 장이 사용설명서 역할을 합니다.
> 에이전트가 `SKILL.md`를 읽을 수 있는 위치에 폴더만 있으면 나머지는 알아서 합니다.
> Codex처럼 `AGENTS.md` 기반 에이전트는 프롬프트 첫 줄에
> `이 폴더의 SKILL.md를 먼저 읽고 그 절차대로 실행한다` 를 붙이면 확실합니다.

---

### 방법 3 — 수동 다운로드 (터미널 없이)

1. 저장소 페이지에서 `Code` → `Download ZIP`
2. 압축 풀기
3. 나온 폴더를 스킬 디렉토리로 이동
   - Claude Code → `C:\Users\<사용자>\.claude\skills\kakaotalk-automation`
   - Hermes → `C:\Users\<사용자>\.hermes\profiles\<프로필>\skills\kakaotalk-automation`
4. 명령 프롬프트에서 `pip install pywin32 psutil pillow`

---

### 첫 확인

```bash
cd kakaotalk-automation/scripts
pip install pywin32 psutil pillow
python kt.py windows      # 열린 카톡 방이 보이면 정상
```

---

## 요구 사항

- Windows 10/11 + 카카오톡 PC 앱 설치·로그인
- Python 3.10+
- `pywin32`, `psutil`, `Pillow`
- `agent-desktop` (창 캡처·마우스/키 입력) — `npx agent-desktop@latest`
- Windows OCR (선택, `kt.py open` 의 방 이름 매칭용 / `Windows.Media.Ocr`)

```bash
pip install pywin32 psutil pillow
```

---

## 명령

```bash
python kt.py windows                     # 열린 카카오톡 창 목록
python kt.py show                        # 숨은 메인 창 복원
python kt.py open "방이름"               # 방 열기 (부분 매칭)
python kt.py read                        # 열린 방 최신 30건
python kt.py read "방이름" -n 50         # 50건 (닫혀 있으면 자동으로 연다)
python kt.py read -n 20 --json           # 구조화 출력
python kt.py read --since 6              # 최근 6시간
python kt.py read -n 100 --deep 3        # PageUp 3회로 과거까지
python kt.py send "내용" --to "방이름"    # dry-run (입력만, 전송 안 함)
python kt.py send "내용" --to "방이름" --yes   # 실제 전송
```

`send` 는 `--yes` 없이는 **절대 전송하지 않습니다.**

---

## MCP 서버로 쓰기 (에이전트가 도구로 직접 호출)

CLI 문자열을 조립하지 않고, 에이전트가 **구조화된 도구**로 카톡을 다루는 방식입니다.
`scripts/kt_mcp.py` 가 표준 MCP(stdio) 서버로 뜹니다.

```bash
python scripts/kt_mcp.py            # stdio MCP 서버
```

### 등록

**Claude Code / Codex / Hermes** — MCP 설정에 아래를 추가합니다.

```json
{
  "mcpServers": {
    "kakaotalk-pc": {
      "command": "python",
      "args": ["C:\\경로\\kakaotalk-automation\\scripts\\kt_mcp.py"]
    }
  }
}
```

### 제공 도구 8종

| 도구 | 하는 일 |
|---|---|
| `kt_status` | 실행 여부 + 열린 방 목록 (작업 전 확인용) |
| `kt_rooms` | 열린 방 제목만 |
| `kt_read` | 방 읽기 → **구조화된 메시지 목록** (발신자·시각·본문 분리) |
| `kt_send` | 전송 (`operation_id` 로 중복 차단) |
| `kt_send_result` | 앞서 보낸 전송의 상태 조회 |
| `kt_open` | 방 열기 |
| `kt_show` | 숨은 메인 창 복원 |
| `kt_alerts` | 워처 알림 큐 조회 |

### CLI 대비 나은 점

- **문자열 파싱 없음** — `kt_read` 는 발신자·시각·본문을 각각 필드로 준다
- **멱등 전송** — 결과가 애매할 때 재전송하면 중복 발송된다. `operation_id` 를
  원장에 기록해 같은 ID 재호출은 **실제로 보내지 않고** 기존 결과만 돌려준다
- **후보 제안** — 방을 못 찾으면 열려 있는 방 중 비슷한 이름을 함께 돌려준다

### 전송 상태값

`kt_send` 는 결과를 상태로 구분합니다. **이 구분이 재전송 사고를 막습니다.**

| status | 의미 | 에이전트가 할 일 |
|---|---|---|
| `sent` | 전송 후 확인됨 | 끝 |
| `sent_unverified` | 전송된 것으로 보이나 확인 실패<br>(카톡이 긴 글을 접은 경우 등) | **재전송 금지.** `kt_read` 로 확인 |
| `failed` | 전송 안 됨 (가드 차단·포커스 이탈) | 방 이름·창 상태 확인 |
| `dry_run` | 검증만 수행 | — |

```python
# 에이전트가 쓰는 흐름
kt_status()                                  # 카톡 켜졌나, 무슨 방 있나
kt_read(room="프로젝트방", last=20)   # 읽기
kt_send(room="프로젝트방",
        text="회신 본문",
        operation_id="reply-20261105-01")     # 전송 (ID 재사용)
```

> `operation_id` 는 **요청마다 새로** 만들되, 같은 요청의 재시도에는 **같은 ID** 를 씁니다.
> 그래야 중복 발송이 막힙니다.

---

## 다중 방 오발송 차단 — 4중 가드

`send_text()` 가 아래 순서로 막고, 종료코드로 어디서 멈췄는지 알려줍니다.

| # | 가드 | 막는 사고 | 종료코드 |
|---|---|---|---|
| 1 | 방이 2개 이상인데 `--to` 미지정 → 거부 | 아무 방에나 발송 | 7 |
| 2 | 방 이름 유사도 < 0.5 → 조작 전 중단 | 다른 방에 입력 | 3 |
| 3 | 포그라운드 확보 실패 → **클릭 자체를 안 함** | 좌표 클릭이 남의 창에 찍힘 | 8 |
| 4 | 입력 후 새 메시지 도착 감지 → Enter 취소 | 대화 밀리는 중 오발송 | 5 |

3번이 핵심입니다. Windows 포그라운드 락에 걸리면 `SetForegroundWindow` 가 **조용히 실패**하는데,
좌표 클릭은 **항상 최상위 창**으로 들어갑니다. 그래서 `focus()` 가 실제 활성 창을 검증하고,
실패하면 클릭을 건너뜁니다. Enter 직전에도 한 번 더 확인합니다.

기타 종료코드: `2` 열린 방 없음 · `4` 입력 내용 불일치 · `6` 전송 후 미확인 · `0` 성공

---

## 실측 검증 (2026-11-05)

3개 방을 동시에 띄운 상태에서 한 방에만 전송:

```
창 3개 동시 오픈  → 대상 방에만 도착 (오전 9:58)
다른 2개 방       → 오염 0건
로그: 방 유사도 1.0 → 입력 검증 37자 → Enter → 최근 3건 내 확인
```

가드 단위 테스트: `scripts/_test_multiroom_guard.py` — 5개 항목 PASS (실제 입력·전송 없이 검증)

작업 중 발견·수리한 사고 2건:
1. `win_ocr.ps1` 부재 → OCR이 예외 없이 `[]` 반환 → **모든 방 열기 실패**. 스크립트 신규 작성.
2. 특정 방 이름을 OCR이 반복 누락(미리보기만 읽어 유사도 0.315) → 좌표 폴백(`_open_by_coord.py`).

---

## 구조

| 파일 | 역할 |
|---|---|
| `SKILL.md` | **에이전트 스킬 정의** — 에이전트가 읽고 따라 실행하는 절차 + 실측 함정 모음 |
| `scripts/kt.py` | **메인 CLI** (read / send / open / windows) — 에이전트가 실제로 호출하는 것 |
| `scripts/kt_mcp.py` | **MCP 서버** — 같은 기능을 도구 8종으로 노출. 멱등 전송 포함 |
| `scripts/kt_room.py` | 창 탐색, OCR, 클립보드 전사 |
| `scripts/kt_open.py` | OCR 유사도 매칭으로 방 열기 |
| `scripts/win_ocr.ps1` | Windows OCR (WinRT) 래퍼 |
| `scripts/kt_show.py` | 숨은 메인 창 복원 |
| `scripts/kt_watch.py` | ⚠️ **부수 기능** — 자동응답(봇) 감시 루프. 에이전트 없이 단독 실행할 때만 |
| `scripts/run_watch.py` | ⚠️ **부수 기능** — 워처 단일 인스턴스 기동 (중복 실행 방지) |
| `scripts/llm_hook.py` | 응답 생성기 템플릿 (hook 연결용) |
| `references/README_kt.md` | 상세 사용법 |

---

## ⛔ 알려진 함정

- **`Escape` 키 금지** — 모달 닫기가 아니라 **앱 전체 종료**. 방 닫기는 `WM_CLOSE`.
- **`Ctrl+F` 금지** — 검색이 아니라 '친구 추가' 모달이 열립니다. 방 찾기는 OCR+클릭으로.
- **`Ctrl+A`는 입력창 전체선택이 아님** — 대화 전체 선택으로 갑니다. 입력창 정리는 `Backspace` 반복.
- **긴 메시지는 전사에서 503자까지만** 읽힘 (전송은 정상, 화면 축약 때문). 재전송 금지.
- 클릭 좌표: 대화 영역 = 창 높이 **45%**, 입력창 = **85.5%** (42%는 이모티콘 버튼).

---

## ⚠️ 주의

개인 대화 내용을 다루는 도구입니다.

- `scripts/_work/`(캡처·OCR 산출물)는 `.gitignore`로 제외되어 있습니다. **직접 커밋하지 마세요.**
- 이 저장소의 예시 방 이름·계정명은 모두 일반명(`내게쓴방` / `A방` / `테스트 커뮤니티` / `내닉네임`)으로 치환되어 있습니다.
- 카카오톡 약관 및 관계 법령의 적용을 받습니다. 본인 계정·본인 책임 하에 사용하세요.

---

## License

[MIT](LICENSE) © 2026 ttl0988k-cmyk

