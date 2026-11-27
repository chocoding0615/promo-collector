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
    assert set(cfg.sources) == {"wikimedia", "pixabay", "openverse"}
    assert cfg.runs_per_day == 48
    assert cfg.sources["pixabay"].needs_key == "PIXABAY_KEY"
    # 카테고리는 관리표의 한글 이름과 같게
    assert {"여행", "카페·음식·공간", "반려동물", "책·독서"} <= set(cfg.categories)
    n = sum(len(c.targets) for c in cfg.categories.values())
    assert n >= 250                        # 주제 수백 개
    for c in cfg.categories.values():
        for t in c.targets:
            ko, en = config.split_target(t)
            assert ko and en and "|" not in en

def test_run_cap_splits_daily_limit_per_hour():
    cfg = config.load(ROOT / "config.yaml")
    assert cfg.run_cap("wikimedia", 0) == 125     # 6000 ÷ 48
    assert cfg.run_cap("pixabay", 0) == 11        # 500 ÷ 48 올림
    assert cfg.run_cap("openverse", 0) == 7       # 300 ÷ 48 올림
    assert cfg.run_cap("pixabay", 495) == 5       # 남은 하루 한도 이내
    assert cfg.run_cap("pixabay", 500) == 0

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

def test_plan_skips_pixabay_without_key():
    cfg = config.load(ROOT / "config.yaml")
    p = runner.plan(cfg, {}, env={})
    assert "pixabay" in p["skipped_sources"] and "pixabay" not in p["caps"]
    p2 = runner.plan(cfg, {}, env={"PIXABAY_KEY": "x"})
    assert p2["caps"]["pixabay"] == 11 and p2["subjects"] >= 250

def test_subjects_interleave_categories():
    cfg = config.load(ROOT / "config.yaml")
    first = [it["category"] for it in runner.subjects(cfg)[:len(cfg.categories)]]
    assert len(set(first)) == len(cfg.categories)   # 처음 몇 개가 한 카테고리에 몰리지 않음


# ---- 관리표 행 생성(순수 함수) ----
from collector.drive import build_row  # noqa: E402

def test_build_row_maps_columns():
    meta = {"category": "여행", "subject": "경복궁", "source": "wikimedia",
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


# ---- 진행 상태·회전 ----
from collector.progress import Progress, rotate  # noqa: E402

def test_progress_persists_offset_and_kept(tmp_path):
    p = Progress(tmp_path / "progress.json")
    p.advance("여행|경복궁", "wikimedia", 50); p.add_kept("여행|경복궁", 3); p.rr = 7; p.save()
    q = Progress(tmp_path / "progress.json")
    assert q.offset("여행|경복궁", "wikimedia") == 50 and q.kept("여행|경복궁") == 3 and q.rr == 7

def test_rotate():
    assert rotate([1, 2, 3, 4], 5) == [2, 3, 4, 1]

# ---- 새 출처 파싱 ----
from collector.sources import pixabay, openverse  # noqa: E402

def test_wikimedia_uses_thumb_and_offset():
    p = wikimedia.build_search_params("x", 50, offset=100)
    assert p["gsroffset"] == 100 and p["iiurlwidth"] == "2000"
    payload = {"query": {"pages": {"1": {"title": "File:a.jpg", "imageinfo": [{
        "url": "https://orig", "thumburl": "https://thumb", "mime": "image/jpeg", "width": 5000, "height": 3000}]}}}}
    assert wikimedia.parse_search(payload)[0].url == "https://thumb"

def test_pixabay_parse_and_paging():
    p = pixabay.build_params("K", "latte art", 30, offset=60)
    assert p["page"] == 3 and p["image_type"] == "photo"
    c = pixabay.parse({"hits": [{"id": 9, "pageURL": "pg", "largeImageURL": "L", "user": "u", "imageWidth": 4000, "imageHeight": 3000}]})[0]
    assert c.source == "pixabay" and c.url == "L" and c.license == "Pixabay Content License"

def test_openverse_parse_filters_nd_nc_and_page_cap():
    assert openverse.build_params("x", 20, offset=12 * 20) is None     # 12쪽 넘으면 멈춤
    res = openverse.parse({"results": [
        {"id": "a", "url": "u1", "license": "by", "license_version": "4.0", "creator": "c", "width": 2000, "height": 1500},
        {"id": "b", "url": "u2", "license": "by-nd", "license_version": "4.0"},
        {"id": "c", "url": "u3", "license": "cc0", "license_version": "1.0"}]})
    assert [r.license for r in res] == ["CC BY 4.0", "CC0"]

# ---- 사진 정리(2000px·JPEG) ----
from collector.imageutil import normalize  # noqa: E402

def test_normalize_downscales():
    buf = io.BytesIO(); Image.new("RGB", (5000, 2500), (10, 20, 30)).save(buf, "PNG")
    data, w, h = normalize(buf.getvalue())
    assert (w, h) == (2000, 1000) and data[:2] == b"\xff\xd8"   # JPEG


# ── 탈락 샘플·정책 완화 ──
from types import SimpleNamespace
from collector import policy as P
from collector.drive import build_reject_row, parse_judgments
from collector.run import pick_samples


def _rows(reason, wrong, right, at="2026-11-27T01:00:00+00:00"):
    return ([{"at": at, "reason": reason, "verdict": "오판"}] * wrong +
            [{"at": at, "reason": reason, "verdict": "정상"}] * right)


def test_policy_relaxes_over_50(tmp_path):
    pol = P.Policy(tmp_path / "p.json")
    ch = P.evaluate(_rows("그림·삽화·AI풍(사진 아님)", 6, 4), pol, "2026-11-27T02:00:00+00:00")
    assert ch and ch[0]["to"] == 1 and pol.level("그림·삽화·AI풍") == 1


def test_policy_needs_10_and_over_half(tmp_path):
    pol = P.Policy(tmp_path / "p.json")
    assert P.evaluate(_rows("얼굴 있음", 6, 3), pol, "2026-11-27T02:00:00+00:00") == []   # 9장
    assert P.evaluate(_rows("얼굴 있음", 5, 5), pol, "2026-11-27T02:00:00+00:00") == []   # 정확히 50%


def test_policy_once_per_day_and_since(tmp_path):
    pol = P.Policy(tmp_path / "p.json")
    rows = _rows("글자·로고·워터마크", 8, 2)
    P.evaluate(rows, pol, "2026-11-27T02:00:00+00:00")
    assert P.evaluate(rows, pol, "2026-11-27T05:00:00+00:00") == []      # 같은 날 두 번 X
    # 다음 날: 이전 판정은 since 이전이라 안 셈
    assert P.evaluate(rows, pol, "2026-11-28T02:00:00+00:00") == []
    new = _rows("글자·로고·워터마크", 8, 2, at="2026-11-27T03:00:00+00:00")
    assert P.evaluate(new, pol, "2026-11-28T02:00:00+00:00")[0]["to"] == 2


def test_policy_max_level(tmp_path):
    pol = P.Policy(tmp_path / "p.json")
    pol.data["rules"]["주제와 약함"] = {"level": 3, "since": "", "changed_at": ""}
    assert P.evaluate(_rows("주제와 약함(0.10)", 10, 0), pol, "2026-11-27T02:00:00+00:00") == []


def test_judge_level0_matches_original():
    from collector.config import ChecksCfg
    v = SimpleNamespace(photo_margin=-0.005, face_margin=0.004, text_margin=-0.001, subject_score=0.5)
    f, c = P.judge(v, {}, ChecksCfg())
    assert f["is_photo"] is False and f["face"] is True and f["has_text"] is False
    assert c.min_long_side == 1000 and c.min_subject_score == 0.22
    f2, c2 = P.judge(v, {"그림·삽화·AI풍": 1, "얼굴 있음": 1, "너무 작음": 2}, ChecksCfg())
    assert f2["is_photo"] is True and f2["face"] is False and c2.min_long_side == 850


def test_pick_samples_balances():
    cands = {r: {"key": r} for r in ["얼굴 있음", "그림·삽화·AI풍", "너무 작음"]}
    got = pick_samples(cands, {"그림·삽화·AI풍": 5, "얼굴 있음": 1}, 2)
    assert [g["key"] for g in got] == ["너무 작음", "얼굴 있음"]


def test_reject_row_and_parse():
    row = build_reject_row({"category": "여행", "subject": "경주", "source": "wikimedia", "page_url": "u"},
                           "FID", "얼굴 있음", "s", "2026-11-27T01:00:00+00:00")
    assert len(row) == 11 and row[2].endswith("/FID/view") and row[5] == "Wikimedia Commons"
    row[9] = "오판"
    js = parse_judgments([row, [], [""]])
    assert js == [{"at": "2026-11-27T01:00:00+00:00", "reason": "얼굴 있음", "verdict": "오판"}]


def test_policy_state_roundtrip(tmp_path):
    pol = P.Policy(tmp_path / "p.json")
    pol.count_sample("2026-11-27", "얼굴 있음"); pol.save()
    pol2 = P.Policy(tmp_path / "p.json")
    assert pol2.samples_today("2026-11-27")["n"] == 1
    assert pol2.samples_today("2026-11-28")["n"] == 0


# ── 수집 요청 ──
from collector import wishes as W
from collector.drive import wish_updates


def test_wish_parse_and_status():
    vals = [["t", "여행", "경복궁 경회루", "", "a", "대기"], ["t", "여행", "광화문", "Gwanghwamun", "a", "완료", "6"],
            ["t", "", "향원정", "Hyangwonjeong", "a", "진행", "2"], ["t", "여행", "", "", "", "대기"]]
    got = W.parse(vals)
    assert [(g["row"], g["place"], g["en"], g["category"]) for g in got] == [
        (2, "경복궁 경회루", "", "여행"), (4, "향원정", "Hyangwonjeong", "여행")]
    assert W.status_for(6, 6, False) == "완료"
    assert W.status_for(3, 6, True) == "못 찾음"
    assert W.status_for(3, 6, False) == "진행"


def test_wish_updates_only_touch_d_f_g_h():
    d = wish_updates([{"row": 5, "en": "Gyeonghoeru", "status": "진행", "count": 2, "memo": "m"}])
    assert [x["range"] for x in d] == ["'수집 요청'!D5", "'수집 요청'!F5:H5"]
    assert d[1]["values"] == [["진행", "2", "m"]]


def test_english_name_fallback():
    class S:
        def get(self, *a, **k):
            raise RuntimeError("offline")
    assert W.english_name("경회루", S()) == ("경회루", "영어명 없음")


def test_config_wish_need_and_half_cap():
    cfg = config.load(str(Path(__file__).resolve().parents[1] / "config.yaml"))
    assert cfg.wish_need == 6 and cfg.runs_per_day == 48
    assert cfg.run_cap("wikimedia", 0) == 125
