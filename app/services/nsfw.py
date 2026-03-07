from __future__ import annotations

import asyncio
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import cv2
import numpy as np
from aiogram import Bot

from app.utils.telegram_ops import with_retry


@dataclass(slots=True)
class NSFWScanResult:
    has_photo: bool
    score: float | None
    flagged: bool


class OpenNSFWService:
    _MODEL_URLS = {
        "deploy.prototxt": "https://raw.githubusercontent.com/yahoo/open_nsfw/master/nsfw_model/deploy.prototxt",
        "resnet_50_1by2_nsfw.caffemodel": "https://raw.githubusercontent.com/yahoo/open_nsfw/master/nsfw_model/resnet_50_1by2_nsfw.caffemodel",
    }

    def __init__(self, model_dir: str, threshold: float, logger: logging.Logger) -> None:
        self._model_dir = Path(model_dir)
        self._threshold = threshold
        self._logger = logger
        self._lock = asyncio.Lock()
        self._net: cv2.dnn.Net | None = None

    async def scan_user_profile(self, bot: Bot, user_id: int) -> NSFWScanResult:
        image_bytes = await self._download_profile_photo(bot, user_id)
        if image_bytes is None:
            return NSFWScanResult(has_photo=False, score=None, flagged=False)

        try:
            async with self._lock:
                score = await asyncio.to_thread(self._predict_score_sync, image_bytes)
        except Exception:
            self._logger.exception("opennsfw_scan_failed telegram_id=%s", user_id)
            return NSFWScanResult(has_photo=True, score=None, flagged=False)

        return NSFWScanResult(has_photo=True, score=score, flagged=score >= self._threshold)

    async def _download_profile_photo(self, bot: Bot, user_id: int) -> bytes | None:
        try:
            photos = await with_retry(lambda: bot.get_user_profile_photos(user_id=user_id, limit=1))
        except Exception:
            self._logger.exception("profile_photo_lookup_failed telegram_id=%s", user_id)
            return None

        if not photos or not photos.photos or not photos.photos[0]:
            return None

        best = max(
            photos.photos[0],
            key=lambda item: ((item.file_size or 0), item.width * item.height),
        )
        try:
            file = await with_retry(lambda: bot.get_file(best.file_id))
        except Exception:
            self._logger.exception("profile_photo_file_failed telegram_id=%s", user_id)
            return None

        file_path = getattr(file, "file_path", None)
        if not file_path:
            return None

        url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    return await response.read()
        except Exception:
            self._logger.exception("profile_photo_download_failed telegram_id=%s", user_id)
            return None

    def _predict_score_sync(self, image_bytes: bytes) -> float:
        net = self._ensure_loaded_sync()
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("failed to decode profile image")

        resized = cv2.resize(image, (256, 256), interpolation=cv2.INTER_LINEAR)
        cropped = resized[16:240, 16:240]
        blob = cv2.dnn.blobFromImage(cropped, scalefactor=1.0, size=(224, 224), mean=(104, 117, 123), swapRB=False)

        net.setInput(blob)
        output = net.forward()
        values = output.reshape(-1)
        if values.size < 2:
            raise RuntimeError(f"unexpected OpenNSFW output shape: {output.shape}")
        return float(values[1])

    def _ensure_loaded_sync(self) -> cv2.dnn.Net:
        if self._net is not None:
            return self._net

        self._model_dir.mkdir(parents=True, exist_ok=True)
        for filename, url in self._MODEL_URLS.items():
            target = self._model_dir / filename
            if target.exists():
                continue
            with urllib.request.urlopen(url, timeout=300) as response:
                target.write_bytes(response.read())

        prototxt = str(self._model_dir / "deploy.prototxt")
        weights = str(self._model_dir / "resnet_50_1by2_nsfw.caffemodel")
        self._net = cv2.dnn.readNetFromCaffe(prototxt, weights)
        return self._net
