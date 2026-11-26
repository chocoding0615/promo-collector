"""socks398 계정의 Drive·Sheets refresh_token을 한 번 만드는 스크립트(본인 컴퓨터에서 실행).

준비(한 번): Google Cloud에서 Drive API·Sheets API 사용 설정, OAuth 클라이언트(데스크톱 앱) 생성.

실행:
  pip install google-auth-oauthlib
  python3 make_token.py <client_id> <client_secret>

  1) 터미널에 나온 주소를 브라우저에서 열고 socks398 계정으로 로그인·동의
     ("확인되지 않은 앱" 경고 → 고급 → 이동)
  2) 마지막에 'localhost' 페이지가 안 열려도 괜찮습니다.
     그때 브라우저 **주소창의 전체 주소**(http://localhost/?...code=...)를 복사해
     터미널에 붙여넣고 Enter
  3) 출력되는 JSON 한 줄을 GitHub Secrets의 GOOGLE_OAUTH 에 저장
"""
import json
import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main():
    if len(sys.argv) < 3:
        print("사용법: python3 make_token.py <client_id> <client_secret>")
        return 1
    client_id, client_secret = sys.argv[1], sys.argv[2]
    # localhost(http) 리디렉션을 수동으로 받기 위한 설정
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
        redirect_uri="http://localhost",
    )
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    print("\n1) 아래 주소를 브라우저에서 열고 socks398 계정으로 로그인·동의하세요:\n")
    print(auth_url)
    print("\n2) 마지막에 localhost 페이지가 안 열려도 괜찮아요.")
    print("   브라우저 주소창의 전체 주소를 복사해서 여기에 붙여넣고 Enter:\n")
    redirected = input("> ").strip()
    flow.fetch_token(authorization_response=redirected)
    creds = flow.credentials
    if not creds.refresh_token:
        print("refresh_token이 없습니다. 위 단계를 처음부터 다시 해 주세요.")
        return 1
    out = {"client_id": client_id, "client_secret": client_secret,
           "refresh_token": creds.refresh_token}
    print("\n=== 아래 한 줄 전체를 GitHub Secrets의 GOOGLE_OAUTH 에 넣으세요 ===\n")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
