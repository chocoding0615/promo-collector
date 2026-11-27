"""Google Drive 업로드 + 관리표(Google Sheets) 기록.

인증: 환경변수 GOOGLE_OAUTH 에 JSON {client_id, client_secret, refresh_token}.
socks398 계정으로 한 번 동의해 만든 refresh_token을 GitHub 비밀값에 넣습니다(scripts/make_token.py).
토큰이 없으면 업로드를 건너뛰고 out/ 저장만 합니다(로컬·토큰 전 테스트용).

관리표 상태는 '사용 가능'으로 넣습니다. Promo가 '사용 가능'/'정상' 행만 쓰기 때문에, '자동 검사 통과'
같은 새 상태로 넣으면 Promo가 안 씁니다. 대신 메모에 '자동수집(collector)'을 적어 기존 행과 구분합니다.
"""
from __future__ import annotations
import datetime
import json
import os
from typing import Any

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",      # collector가 올린 파일만 접근
    "https://www.googleapis.com/auth/spreadsheets",    # 관리표 기록
]
# 관리표 A~N: 파일ID, 파일명, 카테고리, 세부구분, 장소·주제, 태그, 출처사이트, 원본URL, 작가, 라이선스, 상태, 최근사용계정, 최근사용일, 메모
CATALOG_TAB = "이미지 관리"
# 관리표의 기존 카테고리 이름(한글)에 맞춤. Promo가 한글 카테고리로 찾기 때문
CATEGORY_KO = {"travel": "여행", "cafe": "카페·음식·공간", "food": "카페·음식·공간", "animal": "반려동물"}
SOURCE_NAME = {"wikimedia": "Wikimedia Commons", "pixabay": "Pixabay", "openverse": "Openverse"}


def creds_available() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH"))


def _parse_oauth(raw: str) -> dict:
    """붙여넣기 실수(앞뒤 공백·따옴표·안내 문구)를 걸러 {...} 부분만 읽음. 값은 절대 출력하지 않음."""
    s = (raw or "").strip()
    if "{" not in s and '"client_id"' in s:
        # 중괄호만 빠지고 복사된 경우: 감싸서 읽음
        s = "{" + s.strip().strip(",").rstrip("}") + "}"
    s = s.strip("'")
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        has = [k for k in ("client_id", "client_secret", "refresh_token") if k in s]
        raise ValueError(f"GOOGLE_OAUTH 모양이 JSON이 아님(길이 {len(s)}, 중괄호 없음, 들어있는 항목 이름: "
                         f"{has or '없음'}) — make_token.py가 출력한 {{ 부터 }} 까지 한 줄 전체를 넣어 주세요")
    cfg = json.loads(s[a:b + 1])
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not cfg.get(k)]
    if missing:
        raise ValueError(f"GOOGLE_OAUTH에 빠진 항목: {', '.join(missing)}")
    return cfg


def _credentials():
    from google.oauth2.credentials import Credentials
    cfg = _parse_oauth(os.environ["GOOGLE_OAUTH"])
    return Credentials(
        token=None,
        refresh_token=cfg["refresh_token"],
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def build_row(meta: dict[str, Any], file_id: str, account_note: str = "자동수집(collector)") -> list[str]:
    """메타데이터 → 관리표 한 줄(A~N). 순수 함수라 로컬에서 시험 가능."""
    today = datetime.date.today().isoformat()
    tags = meta.get("subject", "")
    return [
        file_id,                                   # A 파일 ID
        meta.get("title", ""),                     # B 파일명
        CATEGORY_KO.get(meta.get("category", ""), meta.get("category", "")),  # C 카테고리(한글)
        "자동 수집",                                # D 세부 구분
        meta.get("subject", ""),                   # E 장소·주제
        tags,                                      # F 태그
        SOURCE_NAME.get(meta.get("source", ""), meta.get("source", "")),      # G 출처 사이트
        meta.get("page_url", ""),                  # H 원본 URL
        meta.get("author", ""),                    # I 작가
        meta.get("license", ""),                   # J 라이선스
        "사용 가능",                                # K 상태(Promo가 쓰는 값)
        "",                                        # L 최근 사용 계정
        "",                                        # M 최근 사용일
        f"{account_note} · phash:{meta.get('phash','')} · 등록:{today}",  # N 메모
    ]


class Uploader:
    def __init__(self, folder_id: str, sheet_id: str):
        self.folder_id = folder_id
        self.sheet_id = sheet_id
        self._drive = None
        self._sheets = None

    def _services(self):
        if self._drive is None:
            from googleapiclient.discovery import build
            c = _credentials()
            self._drive = build("drive", "v3", credentials=c, cache_discovery=False)
            self._sheets = build("sheets", "v4", credentials=c, cache_discovery=False)
        return self._drive, self._sheets

    def upload(self, data: bytes, name: str, mime: str = "image/jpeg") -> str:
        from googleapiclient.http import MediaInMemoryUpload
        drive, _ = self._services()
        media = MediaInMemoryUpload(data, mimetype=mime, resumable=False)
        f = drive.files().create(
            body={"name": name, "parents": [self.folder_id]},
            media_body=media, fields="id",
        ).execute()
        return f["id"]

    def known_phashes(self) -> list[str]:
        _, sheets = self._services()
        memos = sheets.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"'{CATALOG_TAB}'!N1:N",
        ).execute().get("values", [])
        return phashes_from_memos(memos)

    def append_rows(self, rows: list[list[str]]) -> None:
        """표 맨 아래 다음 줄부터 정확한 위치에 씀(행 삽입 없음).
        예전 append(A1, INSERT_ROWS)는 맨 위에 줄을 끼워 넣어 표 전체를 밀었음 → 사용 금지."""
        if not rows:
            return
        _, sheets = self._services()
        col = sheets.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"'{CATALOG_TAB}'!A1:A",
        ).execute().get("values", [])
        start = next_free_row(col)
        end = start + len(rows) - 1
        sheets.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=f"'{CATALOG_TAB}'!A{start}:N{end}",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()


def phashes_from_memos(memos: list[list[str]]) -> list[str]:
    """관리표 메모 칸에서 'phash:xxxx'를 모음(이미 올린 사진 → 다음 실행 중복 방지)."""
    import re
    out = []
    for r in memos:
        if r:
            out += re.findall(r"phash:([0-9a-f]{16})", str(r[0]))
    return out


def next_free_row(col_a: list[list[str]]) -> int:
    """A열 값(위에서부터) → 마지막으로 값이 있는 줄 다음 줄 번호(1부터). 머리줄('파일 ID')이 없으면 거부."""
    if not any(r and "파일 ID" in str(r[0]) for r in col_a):
        raise ValueError("관리표에서 '파일 ID' 머리줄을 찾지 못해 기록을 멈춤(표 위치를 확인해 주세요)")
    last = 0
    for i, r in enumerate(col_a, start=1):
        if r and str(r[0]).strip():
            last = i
    return last + 1


# ── 탈락 샘플 ──────────────────────────────────────────────
REJECT_TAB = "탈락 샘플"
REJECT_FOLDER = "탈락 샘플"
REJECT_HEADER = ["기록 시각(UTC)", "파일 ID", "보기", "카테고리", "주제", "출처", "원본 URL",
                 "탈락 이유", "점수", "판정(오판/정상)", "메모"]


def build_reject_row(meta: dict[str, Any], file_id: str, reason: str, scores: str, at_iso: str) -> list[str]:
    """탈락 샘플 한 줄(A~K). 판정(J)·메모(K)는 안티그래피티가 채움."""
    return [
        at_iso,
        file_id,
        f"https://drive.google.com/file/d/{file_id}/view" if file_id else "",
        CATEGORY_KO.get(meta.get("category", ""), meta.get("category", "")),
        meta.get("subject", ""),
        SOURCE_NAME.get(meta.get("source", ""), meta.get("source", "")),
        meta.get("page_url", ""),
        reason,
        scores,
        "",
        "",
    ]


def parse_judgments(values: list[list[str]]) -> list[dict]:
    """탈락 샘플 탭 A2:J → [{'at','reason','verdict'}]. 빈 줄은 건너뜀."""
    out = []
    for r in values:
        r = list(r) + [""] * (10 - len(r))
        if not str(r[0]).strip():
            continue
        out.append({"at": str(r[0]).strip(), "reason": str(r[7]).strip(), "verdict": str(r[9]).strip()})
    return out


class RejectStore:
    """탈락 샘플을 Drive '탈락 샘플' 폴더와 관리표 '탈락 샘플' 탭에 기록. 기존 탭·행은 건드리지 않음."""

    def __init__(self, uploader: "Uploader"):
        self.up = uploader
        self._folder = None
        self._tab_ready = False

    def folder_id(self) -> str:
        if self._folder:
            return self._folder
        drive, _ = self.up._services()
        q = (f"name = '{REJECT_FOLDER}' and mimeType = 'application/vnd.google-apps.folder' "
             f"and '{self.up.folder_id}' in parents and trashed = false")
        found = drive.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
        if found:
            self._folder = found[0]["id"]
        else:
            self._folder = drive.files().create(
                body={"name": REJECT_FOLDER, "mimeType": "application/vnd.google-apps.folder",
                      "parents": [self.up.folder_id]}, fields="id").execute()["id"]
        return self._folder

    def ensure_tab(self) -> None:
        if self._tab_ready:
            return
        _, sheets = self.up._services()
        meta = sheets.spreadsheets().get(spreadsheetId=self.up.sheet_id,
                                         fields="sheets.properties.title").execute()
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if REJECT_TAB not in titles:
            sheets.spreadsheets().batchUpdate(spreadsheetId=self.up.sheet_id, body={
                "requests": [{"addSheet": {"properties": {"title": REJECT_TAB,
                                                          "gridProperties": {"frozenRowCount": 1}}}}]
            }).execute()
            sheets.spreadsheets().values().update(
                spreadsheetId=self.up.sheet_id, range=f"'{REJECT_TAB}'!A1:K1",
                valueInputOption="RAW", body={"values": [REJECT_HEADER]}).execute()
        self._tab_ready = True

    def judgments(self) -> list[dict]:
        self.ensure_tab()
        _, sheets = self.up._services()
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=self.up.sheet_id, range=f"'{REJECT_TAB}'!A2:J").execute().get("values", [])
        return parse_judgments(vals)

    def upload(self, data: bytes, name: str) -> str:
        from googleapiclient.http import MediaInMemoryUpload
        drive, _ = self.up._services()
        media = MediaInMemoryUpload(data, mimetype="image/jpeg", resumable=False)
        return drive.files().create(body={"name": name, "parents": [self.folder_id()]},
                                    media_body=media, fields="id").execute()["id"]

    def write_rows(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        self.ensure_tab()
        _, sheets = self.up._services()
        col = sheets.spreadsheets().values().get(
            spreadsheetId=self.up.sheet_id, range=f"'{REJECT_TAB}'!A1:A").execute().get("values", [])
        start = max(2, sum(1 for _ in col) + 1)
        for i, r in enumerate(col, start=1):   # 마지막 값 있는 줄 다음
            if r and str(r[0]).strip():
                start = max(2, i + 1)
        end = start + len(rows) - 1
        sheets.spreadsheets().values().update(
            spreadsheetId=self.up.sheet_id, range=f"'{REJECT_TAB}'!A{start}:K{end}",
            valueInputOption="RAW", body={"values": rows}).execute()


# ── 수집 요청(Promo가 사진 없음으로 적은 장소) ─────────────────
def wish_updates(updates: list[dict], tab: str = "수집 요청") -> list[dict]:
    """[{row, en, status, count, memo}] → values.batchUpdate용 data(D·F·G·H 칸만). 순수 함수."""
    data = []
    for u in updates:
        r = u["row"]
        data.append({"range": f"'{tab}'!D{r}", "values": [[u.get("en", "")]]})
        data.append({"range": f"'{tab}'!F{r}:H{r}", "values": [[u["status"], str(u.get("count", 0)), u.get("memo", "")]]})
    return data


def _wish_values(self) -> list[list[str]]:
    _, sheets = self._services()
    try:
        return sheets.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range="'수집 요청'!A2:H").execute().get("values", [])
    except Exception as e:  # 탭이 아직 없으면(Promo가 처음 쓸 때 만듦) 빈 목록
        if "Unable to parse range" in str(e):
            return []
        raise


def _update_wishes(self, updates: list[dict]) -> None:
    if not updates:
        return
    _, sheets = self._services()
    sheets.spreadsheets().values().batchUpdate(spreadsheetId=self.sheet_id, body={
        "valueInputOption": "RAW", "data": wish_updates(updates)}).execute()


Uploader.wish_values = _wish_values
Uploader.update_wishes = _update_wishes
