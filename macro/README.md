# macro/ — 매크로 지표 일일 브리핑 모듈

SEPA 스크리너 레포에 `macro/` 폴더째로 넣으면 되는 독립 모듈.
매일 15개 매크로 지표를 수집 → `rules.yaml` 규칙으로 해석 → 국면(리스크온/중립/리스크오프) 판정 → Claude 3문장 요약 → `docs/macro.json` 저장 → 대시보드 카드에 표시.

```
macro/
├── run_macro.py          진입점 (수집 → 해석 → 저장)
├── fetch_macro.py        지표 수집 + 변화율/백분위 계산   ← 지표 추가는 여기 INDICATORS
├── interpret.py          규칙 엔진 + 국면 판정 + Claude 요약
├── rules.yaml            ★ 해석 규칙·임계값·문장·점수 (코드 수정 없이 편집)
├── sheets.py             구글시트 저장 (선택)
├── requirements.txt
├── data/macro_history.csv   시계열 (자동 생성)
├── docs/
│   ├── macro.json        결과 (자동 생성, 대시보드가 읽음)
│   ├── macro-card.js     카드 렌더러
│   └── macro.html        단독 페이지 (미리보기용)
└── .github/workflows/macro.yml   매일 06:30 KST 자동 실행
```

## 1. 로컬에서 먼저 돌려보기

```bash
cd macro
pip install -r requirements.txt
python run_macro.py --mock stress --no-claude   # 네트워크 없이 규칙 테스트 (리스크오프 시나리오)
python run_macro.py --mock calm  --no-claude    # 조용한 시나리오
python run_macro.py --no-claude                 # 실데이터 (FRED·yfinance·pykrx)
ANTHROPIC_API_KEY=sk-ant-... python run_macro.py   # 실데이터 + Claude 요약
```

결과 확인: `docs/macro.html` 을 로컬 서버로 열기 (`python -m http.server -d docs 8000` → http://localhost:8000/macro.html).
`file://` 로 직접 열면 fetch 가 막히니 서버로 열어야 함.

## 2. 기존 레포에 붙이기

1. `macro/` 폴더를 레포 루트에 복사.
2. `.github/workflows/macro.yml` 을 레포의 `.github/workflows/` 로 이동.
3. 기존 GitHub Pages 대시보드(`docs/index.html` 등)에 두 줄 추가:
   ```html
   <div id="macro-card" data-src="macro/docs/macro.json"></div>
   <script src="macro/docs/macro-card.js"></script>
   ```
   `data-src` 는 index.html 기준 상대경로. Pages 가 `docs/` 만 배포한다면 워크플로의
   `MACRO_JSON_PATH` 주석을 풀어 `docs/macro.json` 으로 바로 쓰게 하고, `macro-card.js` 도 `docs/` 로 복사.
4. GitHub → Settings → Secrets and variables → Actions:
   | 이름 | 필수 | 설명 |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | 권장 | 없으면 규칙 문장만 출력, 3문장 요약 생략 |
   | `ECOS_API_KEY` | 선택 | 한국은행 ECOS (국고채 3년). https://ecos.bok.or.kr 에서 무료 발급 |
   | `GSHEET_ID` + `GOOGLE_SERVICE_ACCOUNT_JSON` | 선택 | 기존 SEPA 시트에 `macro_daily`, `macro_signals` 탭 자동 생성 |
   | (Variables) `CLAUDE_MODEL` | 선택 | 기본값 `claude-sonnet-4-5`. 다른 모델 쓰려면 지정 |
5. Actions 탭 → macro-daily → Run workflow 로 수동 1회 실행해서 확인.

## 3. SEPA 스크리너와 연결

`macro-card.js` 는 로드 후 `window.MACRO_REGIME` ("risk_on" | "neutral" | "risk_off") 과
`window.MACRO_DATA` 를 노출함. 스크리너 테이블 렌더 시:

```js
if (window.MACRO_REGIME === "risk_off") row.classList.add("hold");  // "관망" 배지
```

파이썬 쪽에서 쓰려면 `docs/macro.json` 의 `regime` 필드를 읽으면 됨.

## 4. 규칙 수정

`rules.yaml` 만 편집. 예: VIX 주의 기준을 22로 올리려면

```yaml
condition: "22 <= VIX.value < 30"
```

새 규칙은 리스트에 항목 추가. 사용 가능한 속성 목록은 파일 상단 주석 참조.
잘못된 표현식은 실행 로그에 경고로 뜨고 해당 규칙만 건너뜀(파이프라인은 멈추지 않음).

## 5. 지표 추가

`fetch_macro.py` 의 `INDICATORS` 에 한 줄 추가 + `GROUPS` 에 배치. yfinance 티커면 `src="yf"`, FRED 시리즈면 `src="fred"`.

## 소스·시점 참고

- FRED(미 금리·HY 스프레드·WTI)는 미 동부 기준 익일 오전 갱신 → 06:30 KST 실행 시 "전전일" 값일 수 있음. 지표 타일에 기준일이 표시됨.
- pykrx(외국인 수급)는 KRX 서버 상태에 따라 간헐적으로 실패함 → 실패 시 해당 지표만 빠지고 나머지는 정상.
- 규칙 판정과 요약은 참고용이며 매수·매도 신호가 아님.
