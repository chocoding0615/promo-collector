"""사진 검사(모델 필요). CLIP 하나로 사진여부·주제·얼굴·글자를 모두 봅니다.

처음엔 OpenCV로 얼굴·글자를 봤는데, 서버(headless OpenCV)에서 불안정해서(CascadeClassifier 없음)
이미 작동이 확인된 CLIP zero-shot으로 통일했습니다. 의존성이 torch/open_clip만 남아 더 단순합니다.
모델은 처음 한 번 내려받아 캐시됩니다.

판정 방식: 긍정/부정 문장 쌍의 코사인 유사도를 비교(argmax). 절대 임계값에 덜 민감합니다.
"""
from __future__ import annotations
import dataclasses
import io
import math
from functools import lru_cache
from PIL import Image

PHOTO_LABELS = ["a real photograph taken with a camera"]
NONPHOTO_LABELS = [
    "a painting or watercolor",
    "a drawing or illustration or sketch",
    "a 3d render or digital illustration",
    "an AI generated image",
    "a cartoon or anime",
    "a screenshot or graphic poster with text",
]
FACE_POS = "a photo that clearly shows one or more human faces up close"
FACE_NEG = "a photo with no visible human face"
TEXT_POS = "a graphic with large text, a logo, or a watermark"
TEXT_NEG = "a clean photo without text, logo, or watermark"


@dataclasses.dataclass
class VisionResult:
    is_photo: bool
    photo_score: float
    subject_score: float   # 요청 주제와의 일치(0~1), 주제 없으면 1.0
    face: bool
    has_text: bool


@lru_cache(maxsize=1)
def _clip():
    import open_clip  # type: ignore
    import torch  # noqa: F401
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k")
    model.eval()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    return model, preprocess, tokenizer


def _sims(img: Image.Image, prompts: list[str]) -> list[float]:
    import torch
    model, preprocess, tokenizer = _clip()
    with torch.no_grad():
        image = preprocess(img).unsqueeze(0)
        text = tokenizer(prompts)
        imf = model.encode_image(image)
        txf = model.encode_text(text)
        imf /= imf.norm(dim=-1, keepdim=True)
        txf /= txf.norm(dim=-1, keepdim=True)
        return (imf @ txf.T).squeeze(0).tolist()


def _softmax_first(a: float, b: float) -> float:
    return math.exp(a) / (math.exp(a) + math.exp(b))


def inspect(data: bytes, subject: str = "") -> VisionResult:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    # 사진 vs 그림/AI풍: 모든 라벨을 한 번에 비교
    labels = PHOTO_LABELS + NONPHOTO_LABELS + [FACE_POS, FACE_NEG, TEXT_POS, TEXT_NEG]
    s = _sims(img, labels)
    i = 0
    photo = max(s[i:i + len(PHOTO_LABELS)]); i += len(PHOTO_LABELS)
    nonphoto = max(s[i:i + len(NONPHOTO_LABELS)]); i += len(NONPHOTO_LABELS)
    face_pos, face_neg, text_pos, text_neg = s[i], s[i + 1], s[i + 2], s[i + 3]
    subject_score = 1.0
    if subject.strip():
        ss = _sims(img, [f"a photo of {subject}", "an unrelated photo"])
        subject_score = _softmax_first(ss[0], ss[1])
    return VisionResult(
        is_photo=photo >= nonphoto,
        photo_score=float(photo),
        subject_score=float(subject_score),
        face=face_pos > face_neg,
        has_text=text_pos > text_neg,
    )
