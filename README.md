# ASTRA 기획 변경 영향 추적

기획 변경 이슈가 Jira에 올라오면 AI가 영향받는 작업 후보를 찾아 코멘트로 올리고,
PM이 확정한 작업에만 링크·라벨·담당자 알림을 다는 자동화입니다.

```
data/backlog.csv          가상 프로젝트 ASTRA 작업 60개 (Jira CSV 가져오기용)
data/spec_changes.json    기획 변경 6건과 건별 정답 영향 작업
src/prompt.py             영향 분석 프롬프트
src/impact_analyzer.py    analyze / apply (Jira 연동), offline (Jira 없이 검증)
src/evaluate.py           정답 대비 정밀도·재현율 계산
.github/workflows/impact.yml   Jira 자동화 규칙이 호출하는 GitHub Actions
tools/build_backlog.py    backlog.csv 생성 스크립트
```

## 1. Jira 없이 먼저 검증하기

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # Claude API 키
python src/impact_analyzer.py offline --change C1 --dry-run   # 프롬프트만 확인
python src/impact_analyzer.py offline --change all            # results/C1~C6.json 생성
python src/evaluate.py                                        # results/summary.md 생성
```

AI는 `AI_PROVIDER`로 고릅니다.

| AI_PROVIDER | 필요한 키 | 모델 변수 (기본값) | 비용 |
| --- | --- | --- | --- |
| `claude` (기본) | `ANTHROPIC_API_KEY` (console.anthropic.com) | `CLAUDE_MODEL` (`claude-sonnet-5-5`) | 콘솔 선불 크레딧 |
| `gemini` | `GEMINI_API_KEY` (aistudio.google.com) | `GEMINI_MODEL` (`gemini-3.6-flash`) | 무료 등급 (호출 한도 있음) |

```bash
export AI_PROVIDER=gemini GEMINI_API_KEY=...
python src/impact_analyzer.py offline --change C1
```

`evaluate.py`는 AI 결과와 함께 "같은 기획서 ID를 참조하는 작업을 모두 영향으로 보는" 기준선도 계산합니다.
모델을 바꾸려면 `CLAUDE_MODEL` 환경 변수를 설정합니다.

## 2. Jira 프로젝트 만들기

1. Atlassian 무료 사이트를 만들고 회사 관리형 스크럼 프로젝트를 만듭니다. 키는 `ASTRA`.
2. 이슈 타입 `기획 변경`을 추가합니다. 작업은 기본 타입 `작업`을 씁니다.
3. 커스텀 필드를 만듭니다.

| 필드 | 형식 | 쓰는 이슈 |
| --- | --- | --- |
| 기획서 ID | 단일 선택 (SYS-01 ~ ECO-01, -) | 작업, 기획 변경 |
| 직군 | 단일 선택 | 작업 |
| 변경 전 | 단락 | 기획 변경 |
| 변경 후 | 단락 | 기획 변경 |
| 확정 영향 작업 | 한 줄 텍스트 | 기획 변경 |
| 추가 공수(일) | 숫자 | 기획 변경 (PM 입력, 대시보드 집계용) |

필드 ID(`customfield_10xxx`)는 `https://<사이트>.atlassian.net/rest/api/3/field`에서 확인합니다.

4. `기획 변경` 워크플로: `접수 → AI 분석 중 → PM 검토 → 전파 승인 → 전파 완료`, 그리고 `PM 검토 → 반려`.
5. `data/backlog.csv`를 시스템 → 외부 시스템 가져오기 → CSV로 올립니다.
   `Discipline`은 직군, `Spec ID`는 기획서 ID, `Ref ID`는 레이블로 매핑합니다.

### exquisitejoon.atlassian.net의 ASTRA 값

| 변수 | 값 |
| --- | --- |
| `JIRA_BASE_URL` | `https://exquisitejoon.atlassian.net` |
| `JIRA_PROJECT` | `ASTRA` |
| `TASK_ISSUE_TYPE_ID` | `10010` (작업, 화면 이름 Task) |
| `FIELD_SPEC_ID` | `customfield_10044` (기획서 ID) |
| `FIELD_DISCIPLINE` | `customfield_10045` (직군) |
| `FIELD_BEFORE` | `customfield_10049` (변경 전) |
| `FIELD_AFTER` | `customfield_10048` (변경 후) |
| `FIELD_CONFIRMED` | `customfield_10047` (확정 영향 작업) |
| 추가 공수(일) | `customfield_10046` |

작업 키는 `ASTRA-1`~`ASTRA-60`이 `T01`~`T60`과 같은 순서이고, 각 작업에 Ref ID가 레이블로 붙어 있습니다.

## 3. GitHub 연결

리포 Settings → Secrets and variables → Actions에 등록합니다.

- Secrets: `JIRA_EMAIL`, `JIRA_API_TOKEN`, 그리고 `GEMINI_API_KEY` 또는 `ANTHROPIC_API_KEY`
- Variables: `AI_PROVIDER`(gemini면 등록, 비우면 claude), `JIRA_BASE_URL`, `JIRA_PROJECT`, `FIELD_SPEC_ID`, `FIELD_DISCIPLINE`, `FIELD_BEFORE`, `FIELD_AFTER`, `FIELD_CONFIRMED`

Jira API 토큰은 https://id.atlassian.com/manage-profile/security/api-tokens 에서 만듭니다.

## 4. Jira 자동화 규칙 2개

두 규칙 모두 동작은 "웹 요청 보내기"입니다.

- URL: `https://api.github.com/repos/<owner>/<repo>/dispatches` (POST)
- 헤더: `Authorization: Bearer <GitHub 토큰>`, `Accept: application/vnd.github+json`
- GitHub 토큰은 이 리포 하나에만 Contents 쓰기 권한을 준 fine-grained 토큰으로 만듭니다.

| 규칙 | 트리거 | 본문 |
| --- | --- | --- |
| 분석 요청 | `기획 변경` 이슈 생성 → 상태를 `AI 분석 중`으로 전환 | `{"event_type":"analyze","client_payload":{"key":"{{issue.key}}"}}` |
| 전파 실행 | `기획 변경` 이슈가 `전파 승인`으로 전환됨 | `{"event_type":"apply","client_payload":{"key":"{{issue.key}}"}}` |

무료 플랜은 자동화 규칙 실행 횟수에 월 한도가 있으니 설정 화면에서 확인하세요.
