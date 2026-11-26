"""네트워크·모델 없이 되는 부분만 시험: 설정·한도·중복·Wikimedia 파싱."""
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import config, ratelimit  # noqa: E402
from collector.checks.dedup import Deduper, phash_hex  # noqa: E402
from collector.sources import wikimedia  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


# ---- 설정 ----
def test_config_loads_and_validates():
    cfg = config.load(ROOT / "config.yaml")
    assert "wikimedia" in cfg.sources
    assert cfg.sources["wikimedia"].enabled is True
    assert "wikimedia" in cfg.enabled_sources()
    assert "pixabay" not in cfg.enabled_sources()  # 키 넣기 전엔 꺼짐
    assert cfg.categories["travel"].need == 5
    # source_order는 존재하는 출처만 남김
    assert all(s in cfg.sources for s in cfg.categories["cafe"].source_order)


def test_config_rejects_unknown_source_order(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text(
        "sources:\n  wikimedia:\n    enabled: true\n    daily_limit: 10\n    per_query: 5\n"
        "categories:\n  x:\n    targets: [a]\n    source_order: [doesnotexist]\n    need: 1\n"
        "checks: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config.load(bad)


# ---- 한도 ----
def test_budget_blocks_over_limit(tmp_path):
    b = ratelimit.Budget(tmp_path / "state.json")
    assert b.can_take("pixabay", 300, 20) is True
    b.take("pixabay", 295)
    assert b.remaining("pixabay", 300) == 5
    assert b.can_take("pixabay", 300, 20) is False   # 20장 더는 못 받음
    assert b.can_take("pixabay", 300, 5) is True

def test_budget_persists_same_day(tmp_path):
    p = tmp_path / "state.json"
    b = ratelimit.Budget(p); b.take("wikimedia", 100); b.save()
    b2 = ratelimit.Budget(p)
    assert b2.used("wikimedia") == 100   # 새 VM에서 이어 세기

def test_interval_waits(tmp_path):
    slept = []
    b = ratelimit.Budget(tmp_path / "s.json")
    b.wait_interval("pixabay", 1.0, sleep=slept.append)   # 첫 호출은 안 기다림
    b.wait_interval("pixabay", 1.0, sleep=slept.append)   # 직후 호출은 기다림
    assert len(slept) == 1 and slept[0] > 0


# ---- 중복 ----
def _img(seed, size=(400, 400), fmt="JPEG") -> bytes:
    """무늬가 있는 시험 이미지(단색은 지문이 구분 안 돼 실제 사진처럼 패턴을 넣음)."""
    from PIL import ImageDraw
    im = Image.new("RGB", size, (240, 240, 240))
    dr = ImageDraw.Draw(im)
    rng = (seed * 2654435761) & 0xFFFFFFFF
    for i in range(40):
        rng = (rng * 1103515245 + 12345) & 0xFFFFFFFF
        x = rng % size[0]; y = (rng >> 8) % size[1]; r = 10 + (rng >> 16) % 60
        c = ((rng >> 3) % 256, (rng >> 11) % 256, (rng >> 19) % 256)
        dr.ellipse([x, y, x + r, y + r], fill=c)
    buf = io.BytesIO(); im.save(buf, fmt)
    return buf.getvalue()

def test_dedup_catches_same_and_near():
    d = Deduper(max_distance=5)
    dup, h1 = d.check_bytes(_img(1))
    assert dup is False                      # 처음 보는 사진
    dup2, _ = d.check_bytes(_img(1))
    assert dup2 is True                      # 같은 사진 → 중복
    dup3, _ = d.check_bytes(_img(999))
    assert dup3 is False                     # 다른 사진 → 통과

def test_dedup_against_known_hashes():
    first = _img(7)
    known = phash_hex(first)
    d = Deduper(max_distance=5, known_hex=[known])
    dup, _ = d.check_bytes(first)
    assert dup is True                       # 이미 관리표에 있는 사진


# ---- Wikimedia 파싱 ----
def test_wikimedia_query_params():
    p = wikimedia.build_search_params("경복궁", 50)
    assert p["gsrsearch"].startswith("경복궁")
    assert "filetype:bitmap" in p["gsrsearch"]
    assert p["gsrnamespace"] == "6"
    assert p["gsrlimit"] == 50

def test_wikimedia_parse_extracts_license_author():
    payload = {"query": {"pages": {"123": {
        "title": "File:Gyeongbokgung.jpg",
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/x/Gyeongbokgung.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Gyeongbokgung.jpg",
            "mime": "image/jpeg", "width": 1600, "height": 1067,
            "extmetadata": {
                "Artist": {"value": "Hong Gildong"},
                "LicenseShortName": {"value": "CC BY-SA 4.0"},
            },
        }],
    }}}}
    cands = wikimedia.parse_search(payload)
    assert len(cands) == 1
    c = cands[0]
    assert c.title == "Gyeongbokgung.jpg"
    assert c.author == "Hong Gildong"
    assert c.license == "CC BY-SA 4.0"
    assert c.width == 1600

def test_wikimedia_parse_skips_non_image():
    payload = {"query": {"pages": {"1": {"title": "File:x.ogg",
        "imageinfo": [{"url": "u", "mime": "audio/ogg", "width": 0, "height": 0}]}}}}
    assert wikimedia.parse_search(payload) == []


# ---- 판정 규칙(순수 함수) ----
from collector.pipeline import decide  # noqa: E402
from collector import run as runner  # noqa: E402

def _checks():
    return config.load(ROOT / "config.yaml").checks

def test_decide_passes_clean_photo():
    v = decide(_checks(), long_side=1600, is_photo=True, subject_score=0.4,
               face=False, has_text=False, is_duplicate=False)
    assert v.ok and v.reasons == []

def test_decide_rejects_each_reason():
    c = _checks()
    assert "중복" in decide(c, long_side=1600, is_photo=True, subject_score=0.4,
                            face=False, has_text=False, is_duplicate=True).reasons
    assert any("작음" in r for r in decide(c, long_side=400, is_photo=True, subject_score=0.4,
                            face=False, has_text=False, is_duplicate=False).reasons)
    assert any("사진 아님" in r for r in decide(c, long_side=1600, is_photo=False, subject_score=0.4,
                            face=False, has_text=False, is_duplicate=False).reasons)
    assert any("얼굴" in r for r in decide(c, long_side=1600, is_photo=True, subject_score=0.4,
                            face=True, has_text=False, is_duplicate=False).reasons)
    assert any("글자" in r for r in decide(c, long_side=1600, is_photo=True, subject_score=0.4,
                            face=False, has_text=True, is_duplicate=False).reasons)
    assert any("주제" in r for r in decide(c, long_side=1600, is_photo=True, subject_score=0.05,
                            face=False, has_text=False, is_duplicate=False).reasons)

def test_plan_only_uses_enabled_sources():
    cfg = config.load(ROOT / "config.yaml")
    items = runner.plan(cfg)
    assert len(items) > 0
    # pixabay/openverse는 꺼져 있으니 지금은 모든 주제가 wikimedia로 감
    assert all(it["source"] == "wikimedia" for it in items)
    assert {it["category"] for it in items} >= {"travel", "cafe", "food", "animal"}


# ---- 관리표 행 생성(순수 함수) ----
from collector.drive import build_row  # noqa: E402

def test_build_row_maps_columns():
    meta = {"category": "travel", "subject": "경복궁", "source": "wikimedia",
            "title": "Gyeongbokgung.jpg", "page_url": "https://commons/...", 
            "author": "Hong", "license": "CC BY-SA 4.0", "phash": "abc123"}
    row = build_row(meta, file_id="DRIVEID1")
    assert len(row) == 14
    assert row[0] == "DRIVEID1"          # A 파일 ID
    assert row[2] == "여행"              # C 카테고리(한글)
    assert row[6] == "Wikimedia Commons" # G 출처 사이트
    assert row[4] == "경복궁"             # E 장소·주제
    assert row[9] == "CC BY-SA 4.0"      # J 라이선스
    assert row[10] == "사용 가능"         # K 상태(Promo가 쓰는 값)
    assert "자동수집" in row[13] and "abc123" in row[13]  # N 메모


# ---- GOOGLE_OAUTH 붙여넣기 실수 허용 ----
from collector.drive import _parse_oauth  # noqa: E402

def test_parse_oauth_tolerates_paste_noise():
    good = '{"client_id": "a", "client_secret": "b", "refresh_token": "c"}'
    assert _parse_oauth(good)["refresh_token"] == "c"
    assert _parse_oauth("  '" + good + "'\n")["client_id"] == "a"
    assert _parse_oauth("=== 아래 한 줄 ===\n" + good)["client_secret"] == "b"

def test_parse_oauth_rejects_code_without_leaking():
    with pytest.raises(ValueError) as e:
        _parse_oauth("4/0AUn6Hd_secretlookingcode")
    assert "secretlookingcode" not in str(e.value)   # 값은 오류에 안 나옴
    with pytest.raises(ValueError):
        _parse_oauth('{"client_id": "a"}')

def test_parse_oauth_missing_braces():
    body = '"client_id": "a", "client_secret": "b", "refresh_token": "c"'
    assert _parse_oauth(body)["refresh_token"] == "c"


# ---- 관리표 기록 위치: 표 맨 아래 다음 줄 ----
from collector.drive import next_free_row  # noqa: E402

def test_next_free_row_after_last_data():
    col = [[], ["Promo 이미지 보관함 관리표"], ["설명"], [], ["파일 ID"], ["id1"], ["id2"]]
    assert next_free_row(col) == 8          # 7행이 마지막 → 8행부터

def test_next_free_row_refuses_without_header():
    with pytest.raises(ValueError):
        next_free_row([["a"], ["b"]])


def test_wikimedia_author_html_stripped():
    payload = {"query": {"pages": {"1": {"title": "File:a.jpg", "imageinfo": [{
        "url": "u", "mime": "image/jpeg", "width": 1600, "height": 900, "descriptionurl": "d",
        "extmetadata": {"Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:Basile_Morin" title="User:Basile Morin">Basile Morin</a>'},
                        "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}}}}
    assert wikimedia.parse_search(payload)[0].author == "Basile Morin"


from collector.drive import phashes_from_memos  # noqa: E402

def test_phashes_from_memos():
    memos = [["메모"], ["자동수집(collector) · phash:d202886fbf153af1 · 등록:2026-11-26"], [], ["사진 직접 확인: ..."]]
    assert phashes_from_memos(memos) == ["d202886fbf153af1"]
