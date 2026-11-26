"""socks398 계정의 Drive·Sheets refresh_token을 한 번 만드는 스크립트(본인 컴퓨터에서 실행).

준비(한 번):
  1) https://console.cloud.google.com 에서 프로젝트 하나 만들기(아무 이름)
  2) 'API 및 서비스 → 라이브러리'에서 Google Drive API, Google Sheets API 둘 다 사용 설정
  3) 'OAuth 동의 화면' 만들기(외부/테스트). 테스트 사용자에 socks398 이메일 추가
  4) '사용자 인증 정보 → OAuth 클라이언트 ID → 데스크톱 앱' 만들고 client_id, client_secret 복사

실행:
  pip install google-auth-oauthlib
  python scripts/make_token.py <client_id> <client_secret>
  → 브라우저가 열리면 socks398 계정으로 로그인·동의
  → 출력되는 JSON 한 줄을 GitHub 저장소의 Secrets에 GOOGLE_OAUTH 로 저장
"""
import json
import sys

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main():
    if len(sys.argv) != 3:
        print("사용법: python scripts/make_token.py <client_id> <client_secret>")
        return 1
    client_id, client_secret = sys.argv[1], sys.argv[2]
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)
    out = {"client_id": client_id, "client_secret": client_secret,
           "refresh_token": creds.refresh_token}
    print("\n=== 아래 한 줄을 GitHub Secrets의 GOOGLE_OAUTH 에 넣으세요 ===\n")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
