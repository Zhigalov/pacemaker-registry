"""Small, re-encoded public avatars in Yandex Object Storage, using runtime IAM."""
from io import BytesIO
import os
import re
from threading import Semaphore
import warnings
from uuid import uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
KEY_PATTERN = r'avatars/[1-9][0-9]*/[0-9a-f]{32}\.webp'
_decode_slot = Semaphore(1)


def avatar_url(key: str | None) -> str | None:
    bucket = os.getenv('AVATAR_BUCKET', '')
    if not key or not bucket or not re.fullmatch(KEY_PATTERN, key):
        return None
    return f'https://storage.yandexcloud.net/{bucket}/{key}'


def prepare_avatar(data: bytes) -> bytes:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Выберите фотографию размером до 5 МБ.')
    try:
        with _decode_slot, warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('Поддерживаются фотографии JPEG, PNG и WebP.')
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError('Фотография слишком большая: максимум 16 мегапикселей.')
                # Bound memory in the existing 256 MiB container before copying/rotating pixels.
                original.draft('RGB', (1024, 1024))
                original.thumbnail((1024, 1024), reducing_gap=3)
                image = ImageOps.exif_transpose(original).convert('RGB')
                image = ImageOps.fit(image, (512, 512), method=Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format='WEBP', quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Не удалось прочитать фотографию. Выберите JPEG, PNG или WebP.') from None


def _request(method: str, key: str, data: bytes | None = None) -> None:
    url = avatar_url(key)
    if not url:
        raise RuntimeError('Avatar storage is not configured.')
    # No static keys: the existing container service account supplies a short-lived token.
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.get('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                                  headers={'Metadata-Flavor': 'Google'})
            response.raise_for_status()
            token = response.json()['access_token']
            response = client.request(method, url, content=data, headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'image/webp',
                'Cache-Control': 'public, max-age=31536000, immutable',
            })
            response.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError):
        raise RuntimeError('Avatar storage request failed.') from None


def upload_avatar(pacemaker_id: int, data: bytes) -> str:
    key = f'avatars/{pacemaker_id}/{uuid4().hex}.webp'
    _request('PUT', key, prepare_avatar(data))
    return key


def delete_avatar(key: str | None) -> bool:
    if not key:
        return True
    try:
        _request('DELETE', key)
        return True
    except RuntimeError:
        # A failed cleanup must not undo an already committed profile edit.
        return False
