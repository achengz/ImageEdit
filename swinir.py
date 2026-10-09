"""Tiled inference with the official SwinIR real-world x4 GAN checkpoint."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests
import torch
import torch.nn.functional as F
from PIL import Image

from third_party.swinir.network_swinir import SwinIR


ROOT = Path(__file__).resolve().parent
MODEL_NAME = "003_realSR_BSRGAN_DFO_s64w8_SwinIR-M_x4_GAN.pth"
MODEL_PATH = ROOT / ".cache" / "swinir" / MODEL_NAME
MODEL_URL = f"https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/{MODEL_NAME}"
MODEL_SHA256 = "b9afb61e65e04eb7f8aba5095d070bbe9af28df76acd0c9405aeb33b814bcfc6"
SCALE = 4
WINDOW = 8


def install_model() -> Path:
    if MODEL_PATH.is_file() and _sha256(MODEL_PATH) == MODEL_SHA256:
        return MODEL_PATH
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_PATH.with_suffix(".download")
    print("首次使用 SwinIR：下载官方模型（约 64 MB）…")
    try:
        with requests.get(MODEL_URL, stream=True, timeout=120) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
        if _sha256(temporary) != MODEL_SHA256:
            raise RuntimeError("SwinIR 模型校验失败，请重试。")
        temporary.replace(MODEL_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    return MODEL_PATH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def load_model() -> tuple[SwinIR, torch.device]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    network = SwinIR(
        upscale=SCALE, in_chans=3, img_size=64, window_size=WINDOW,
        img_range=1., depths=[6] * 6, embed_dim=180,
        num_heads=[6] * 6, mlp_ratio=2,
        upsampler="nearest+conv", resi_connection="1conv",
    )
    checkpoint = torch.load(install_model(), map_location="cpu", weights_only=True)
    network.load_state_dict(checkpoint.get("params_ema", checkpoint), strict=True)
    return network.eval().to(device), device


def restore(image: Image.Image, tile: int = 0) -> Image.Image:
    """Upscale an RGB image fourfold, using context around each output tile."""
    model, device = load_model()
    core = tile or 128
    if core < 32:
        raise ValueError("SwinIR 分块尺寸不能小于 32。")
    margin = 24
    source = image.convert("RGB")
    result = Image.new("RGB", (source.width * SCALE, source.height * SCALE))
    with torch.inference_mode():
        for top in range(0, source.height, core):
            for left in range(0, source.width, core):
                right = min(source.width, left + core)
                bottom = min(source.height, top + core)
                box = (max(0, left - margin), max(0, top - margin),
                       min(source.width, right + margin), min(source.height, bottom + margin))
                patch = np.asarray(source.crop(box), dtype=np.float32) / 255.0
                input_tensor = torch.from_numpy(patch).permute(2, 0, 1).unsqueeze(0).to(device)
                height, width = input_tensor.shape[-2:]
                input_tensor = F.pad(input_tensor, (0, -width % WINDOW, 0, -height % WINDOW), mode="replicate")
                try:
                    output = model(input_tensor)[..., :height * SCALE, :width * SCALE]
                except torch.cuda.OutOfMemoryError as error:
                    torch.cuda.empty_cache()
                    raise RuntimeError("SwinIR 显存不足，请缩小原图或选择 Real-ESRGAN。") from error
                crop = output[0, :, (top - box[1]) * SCALE:(bottom - box[1]) * SCALE,
                              (left - box[0]) * SCALE:(right - box[0]) * SCALE]
                pixels = (crop.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8)
                result.paste(Image.fromarray(pixels, "RGB"), (left * SCALE, top * SCALE))
    return result
