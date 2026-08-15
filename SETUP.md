# SETUP.md — 초기 설정 가이드

이 문서는 이 저장소를 처음 설정할 때 딱 한 번만 하면 되는 작업을 순서대로 안내합니다.
민감정보(서비스 계정 키 등)는 절대 코드에 하드코딩하지 않고, 전부 GitHub Secrets(또는 로컬
환경변수)로만 다룹니다.

## 0. 사전 준비

- GitHub 계정, 이 저장소에 대한 쓰기 권한
- Google 계정 (개인 gmail 계정이면 충분)
- 결과를 받을 구글 스프레드시트 1개 (미리 만들어두거나, 아래에서 새로 만들어도 됨)

## 1. 구글 클라우드에서 서비스 계정 만들기

1. https://console.cloud.google.com/ 접속 (개인 구글 계정으로 로그인)
2. 상단의 프로젝트 선택 드롭다운 → **새 프로젝트** 생성 (이름 예: `sepa-screener`)
3. 좌측 메뉴 → **API 및 서비스 > 라이브러리** 로 이동
4. `Google Sheets API` 검색 → **사용 설정(Enable)** 클릭
5. 좌측 메뉴 → **API 및 서비스 > 사용자 인증 정보(Credentials)** 로 이동
6. 상단 **+ 사용자 인증 정보 만들기 > 서비스 계정** 선택
7. 서비스 계정 이름 입력 (예: `sepa-screener-bot`) → 만들기 및 계속하기 → 역할은 생략해도 됨 →
   완료
8. 생성된 서비스 계정을 클릭 → **키(Keys)** 탭 → **키 추가 > 새 키 만들기** → 유형 **JSON** 선택
   → 만들기
   - JSON 키 파일이 자동으로 다운로드됩니다. **이 파일을 절대 저장소에 커밋하지 마세요.**
     (`.gitignore`에 이미 `*.json` 패턴으로 막아두었습니다.)
9. 다운로드된 JSON 파일을 열어보면 `"client_email": "sepa-screener-bot@....iam.gserviceaccount.com"`
   같은 필드가 있습니다. 이 이메일 주소를 다음 단계에서 사용합니다.

## 2. 구글 스프레드시트 공유

1. 결과를 받을 구글 스프레드시트를 엽니다 (없으면 새로 만드세요: sheets.new)
2. 우측 상단 **공유** 버튼 클릭
3. 1단계에서 확인한 서비스 계정 이메일 주소(`...iam.gserviceaccount.com`)를 추가하고
   권한을 **편집자(Editor)** 로 설정 후 공유
4. 스프레드시트 주소창의 URL에서 시트 ID를 복사해둡니다.
   `https://docs.google.com/spreadsheets/d/여기가_시트_ID/edit` 형태입니다.

## 3. GitHub Secrets 등록

저장소 페이지에서 **Settings > Secrets and variables > Actions > New repository secret** 으로
아래 2개를 등록합니다.

| Secret 이름 | 값 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 1단계에서 다운로드한 JSON 키 파일의 **전체 내용**을 그대로 복사/붙여넣기 (중괄호 `{...}` 포함 전체) |
| `GOOGLE_SHEET_ID` | 2단계에서 복사한 스프레드시트 ID |

이 두 값이 없으면 `screening.py`는 에러 없이 CSV 저장만 하고 정상 종료합니다(구글시트
업로드는 조용히 생략). 즉 Secrets 설정 전에도 GitHub Actions는 안전하게 돌아갑니다.

## 4. 로컬에서 테스트하고 싶다면

로컬 PowerShell에서 (커밋하지 말고 현재 세션에서만 유효):

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content -Raw "C:\경로\다운로드한-키.json"
$env:GOOGLE_SHEET_ID = "여기에_시트_ID"
python screening.py
```

## 5. GitHub Actions 동작 확인

1. 저장소의 **Actions** 탭 → `SEPA Trend Template Daily Screening` 워크플로우 선택
2. **Run workflow** 버튼으로 수동 실행 (workflow_dispatch)
3. 실행이 끝나면 로그에서 "구글시트 업로드 완료" 메시지를 확인하고, 실제 스프레드시트에
   오늘 날짜(`YYYY-MM-DD`) 이름의 새 탭이 생겼는지 확인
4. 별도로 CSV 결과는 해당 워크플로우 실행의 **Artifacts** 섹션에서도 내려받을 수 있습니다.

## 6. 자동 실행 스케줄

`.github/workflows/daily_screen.yml`에 평일(월~금) 20:00 KST(UTC 11:00) 자동 실행이 이미
설정되어 있습니다. 별도 조치 없이 저장소가 GitHub에 push되어 있으면 그대로 동작합니다.

> 참고: GitHub Actions의 `schedule` 트리거는 정확히 그 분에 실행되지 않고 몇 분 정도
> 지연될 수 있습니다 (GitHub 인프라 부하에 따름). 정확한 시각이 중요하면 참고하세요.

## 보안 체크리스트

- [ ] 서비스 계정 JSON 키 파일을 로컬 저장소 폴더 밖에 보관했는가 (또는 `.gitignore`로
      막혀있는 위치에)
- [ ] `git status`로 JSON/키 파일이 스테이징되지 않았는지 커밋 전 확인
- [ ] 서비스 계정에는 스프레드시트 편집 권한만 공유했는가 (그 외 구글 드라이브 전체 접근
      권한을 주지 않았는지)
- [ ] GitHub Secrets 값은 저장소 Settings 화면에서만 확인 가능하고, 워크플로우 로그에는
      출력되지 않음 (GitHub이 자동으로 마스킹)
