"""받은 사진 정리: 긴 변 2000px로 줄이고 JPEG로 저장(용량 10분의 1 수준). 실제 크기도 돌려줌."""
from __future__ import annotations
import io
from PIL import Image

MAX_SIDE = 2000


def normalize(data: bytes, max_side: int = MAX_SIDE, quality: int = 88) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            s = max_side / max(w, h)
            im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue(), im.size[0], im.size[1]
