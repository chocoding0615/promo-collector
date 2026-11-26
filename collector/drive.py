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


def creds_available() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH"))


def _parse_oauth(raw: str) -> dict:
    """붙여넣기 실수(앞뒤 공백·따옴표·안내 문구)를 걸러 {...} 부분만 읽음. 값은 절대 출력하지 않음."""
    s = (raw or "").strip().strip("'\"")
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        raise ValueError(f"GOOGLE_OAUTH 모양이 JSON이 아님(길이 {len(s)}, '{{' 없음) — "
                         "make_token.py가 출력한 {{\"client_id\": ...}} 한 줄 전체를 넣어 주세요")
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
        meta.get("category", ""),                  # C 카테고리
        "자동 수집",                                # D 세부 구분
        meta.get("subject", ""),                   # E 장소·주제
        tags,                                      # F 태그
        meta.get("source", ""),                    # G 출처 사이트
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

    def append_rows(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        _, sheets = self._services()
        sheets.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"'{CATALOG_TAB}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
