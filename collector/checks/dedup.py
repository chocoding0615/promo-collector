"""중복 사진 걸러내기. 지각 해시(perceptual hash)로 같은/거의 같은 사진을 잡습니다.
모델이 필요 없어 로컬에서 시험 가능합니다(imagehash + Pillow, 둘 다 PyPI).

- 이번에 받은 사진들 사이의 중복
- 이미 관리표에 있는 사진의 해시 목록(known_hashes)과의 중복
해시 거리가 max_distance 이하이면 중복으로 봅니다.
"""
from __future__ import annotations
import io
from typing import Iterable
import imagehash
from PIL import Image


def phash(data: bytes) -> imagehash.ImageHash:
    with Image.open(io.BytesIO(data)) as im:
        return imagehash.phash(im.convert("RGB"))


def phash_hex(data: bytes) -> str:
    return str(phash(data))


class Deduper:
    def __init__(self, max_distance: int, known_hex: Iterable[str] = ()):
        self.max_distance = int(max_distance)
        self.seen: list[imagehash.ImageHash] = []
        for h in known_hex:
            try:
                self.seen.append(imagehash.hex_to_hash(h))
            except Exception:
                continue

    def is_duplicate(self, h: imagehash.ImageHash) -> bool:
        return any((h - prev) <= self.max_distance for prev in self.seen)

    def add(self, h: imagehash.ImageHash) -> None:
        self.seen.append(h)

    def check_bytes(self, data: bytes) -> tuple[bool, str]:
        """(중복이면 True, 해시 hex). 새 사진이면 기억에 추가."""
        h = phash(data)
        if self.is_duplicate(h):
            return True, str(h)
        self.add(h)
        return False, str(h)
