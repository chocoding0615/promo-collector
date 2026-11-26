"""사진 검사(모델 필요). CLIP 주제·사진여부, 얼굴 감지, 큰 글자 감지.

이 모듈은 모델을 내려받아 CPU로 돌립니다. 제 개발 환경에서는 모델 다운로드가 막혀 있어
작동 검증은 GitHub Actions 첫 실행에서 합니다. 모델은 처음 한 번만 받고 캐시됩니다.

- CLIP(open_clip, ViT-B-32): "사진 vs 그림/삽화/AI풍" 판정과 주제 일치 점수
- 얼굴: OpenCV Haar(가볍고 설치 쉬움). 얼굴이 하나라도 뚜렷하면 탈락
- 글자: 글자가 화면에서 차지하는 비율 추정(로고·워터마크 거르기). 1차는 가벼운 방식 사용

무거운 라이브러리를 import 시점이 아니라 처음 쓸 때 불러와(lazy) 설정·시험 단계가 가볍습니다.
"""
from __future__ import annotations
import dataclasses
import io
from functools import lru_cache
from PIL import Image

# 그림/삽화/AI풍 판정에 쓰는 CLIP 라벨
PHOTO_LABELS = [
    "a real photograph taken with a camera",
]
NONPHOTO_LABELS = [
    "a painting or watercolor",
    "a drawing or illustration or sketch",
    "a 3d render or digital illustration",
    "an AI generated image",
    "a cartoon or anime",
    "a screenshot or graphic with text",
]


@dataclasses.dataclass
class VisionResult:
    is_photo: bool
    photo_score: float
    subject_score: float          # 요청 주제와의 일치(0~1), 주제 없으면 1.0
    face: bool
    text_ratio: float
    note: str = ""


@lru_cache(maxsize=1)
def _clip():
    import open_clip  # type: ignore
    import torch  # noqa: F401
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k")
    model.eval()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    return model, preprocess, tokenizer


@lru_cache(maxsize=1)
def _face_cascade():
    import cv2  # type: ignore
    path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    return cv2.CascadeClassifier(path)


def _pil(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _clip_scores(img: Image.Image, prompts: list[str]):
    import torch
    model, preprocess, tokenizer = _clip()
    with torch.no_grad():
        image = preprocess(img).unsqueeze(0)
        text = tokenizer(prompts)
        img_f = model.encode_image(image)
        txt_f = model.encode_text(text)
        img_f /= img_f.norm(dim=-1, keepdim=True)
        txt_f /= txt_f.norm(dim=-1, keepdim=True)
        sims = (img_f @ txt_f.T).squeeze(0)  # 코사인 유사도
        return sims.tolist()


def detect_faces(data: bytes) -> bool:
    import cv2
    import numpy as np
    arr = np.frombuffer(data, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]
    faces = _face_cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                             minSize=(max(40, h // 12), max(40, h // 12)))
    return len(faces) > 0   # 뚜렷한(작지 않은) 얼굴이 하나라도 있으면 True


def text_ratio(data: bytes) -> float:
    """글자가 차지하는 면적 비율(근사). MSER로 글자 같은 영역을 잡아 비율을 추정.
    정밀 OCR 대신 '큰 글자·워터마크가 있는지'만 보려는 1차 필터."""
    import cv2
    import numpy as np
    arr = np.frombuffer(data, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return 0.0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    mser = cv2.MSER_create()
    regions, _ = mser.detectRegions(gray)
    mask = np.zeros((H, W), dtype=np.uint8)
    for r in regions:
        x, y, w, h = cv2.boundingRect(r.reshape(-1, 1, 2))
        if 0.1 < (w / max(1, h)) < 10 and h < H * 0.5:  # 글자 비슷한 모양만
            mask[y:y + h, x:x + w] = 1
    return float(mask.sum()) / float(H * W)


def inspect(data: bytes, subject: str = "", *, want_face: bool = False) -> VisionResult:
    """한 장 종합 검사. subject가 있으면 주제 일치 점수도 계산."""
    img = _pil(data)
    photo_sims = _clip_scores(img, PHOTO_LABELS + NONPHOTO_LABELS)
    photo_score = max(photo_sims[:len(PHOTO_LABELS)])
    nonphoto_score = max(photo_sims[len(PHOTO_LABELS):])
    is_photo = photo_score >= nonphoto_score
    subject_score = 1.0
    if subject.strip():
        s = _clip_scores(img, [f"a photo of {subject}", "an unrelated photo"])
        # 소프트맥스 비슷하게 주제 쪽 비중
        import math
        a, b = s[0], s[1]
        subject_score = math.exp(a) / (math.exp(a) + math.exp(b))
    face = detect_faces(data)
    tr = text_ratio(data)
    return VisionResult(is_photo=is_photo, photo_score=float(photo_score),
                        subject_score=float(subject_score), face=face, text_ratio=float(tr))
