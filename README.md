# promo-collector

promo-studio용 사진 수집기. GitHub Actions(공개 저장소, 무료 무제한)에서 예약 실행됩니다.

## 하는 일
1. `config.yaml`의 카테고리·주제를 출처(Wikimedia 등)에서 검색
2. 출처별 하루 한도 안에서 다운로드 (Pixabay·Openverse는 소량만, 대량 금지 준수)
3. 1단계 검사: 중복 · 그림/사진 · 얼굴 · 큰 글자 · 주제 일치 (오픈소스, VM에서 실행)
4. 통과분만 본인 Google Drive에 업로드 + 관리표에 `자동 검사 통과`로 기록 *(다음 단계)*

사진은 저장소에 저장되지 않습니다. VM은 매 실행 후 삭제됩니다.
키·Drive 토큰은 저장소가 아니라 **GitHub Actions Secrets**에 넣습니다(공개되지 않음).

## 로컬에서 되는 것
```
pip install Pillow imagehash pyyaml requests pytest
python -m pytest tests/test_offline.py -q      # 설정·한도·중복·판정·Wikimedia 파싱
python -m collector.run --dry-run              # 어떤 주제를 어디서 몇 장 찾을지 계획만
```

## 검증 상태
- 로컬 시험 통과(13개): 설정 검증, 하루 한도·요청 간격, 중복(지문), 판정 규칙, Wikimedia 파싱, 계획
- **미검증(Actions 첫 실행 예정):** 실제 Wikimedia 다운로드, CLIP·얼굴·OCR 검사, Drive 업로드
  - 이유: 모델 다운로드·외부 접속이 개발 환경에서 막혀 있어, 열린 인터넷인 Actions에서 첫 검증

## Secrets (저장소 설정 → Secrets and variables → Actions)
- `GOOGLE_OAUTH` — socks398 계정의 Drive·Sheets 토큰 JSON. `scripts/make_token.py`로 한 번 생성
- `DRIVE_FOLDER_ID` — "Promo 자동 수집" 폴더 ID (폴더 URL의 끝부분)
- `SHEET_ID` — 관리표 스프레드시트 ID
- `PIXABAY_KEY` — Pixabay API 키 (소량 수집 켤 때, 선택)

토큰이 없으면 업로드를 건너뛰고 out/ 저장만 합니다(안전). 셋 다 넣으면 통과분을 Drive에 올리고
관리표에 `상태=사용 가능`, 메모에 `자동수집(collector)`로 기록합니다.

### Drive 토큰 만들기(한 번)
`scripts/make_token.py` 맨 위 주석의 단계를 따라 Google Cloud에서 OAuth 클라이언트를 만들고,
본인 컴퓨터에서 실행해 나온 JSON 한 줄을 `GOOGLE_OAUTH` 비밀값에 넣습니다.
