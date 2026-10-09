"""Pick an upscale factor and produce an exact-size AI image."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from refine import refine_image
from upscale import INPUT_FORMATS, process


MAX_OUTPUT_PIXELS = 60_000_000
MAX_WORK_PIXELS = 240_000_000


@dataclass(frozen=True)
class ScalePlan:
    source: tuple[int, int]
    crop_before_scale: tuple[int, int]
    target: tuple[int, int]
    factor: int


def choose_scale(source: tuple[int, int], target: tuple[int, int]) -> ScalePlan:
    sw, sh = source
    tw, th = target
    if min(sw, sh, tw, th) <= 0:
        raise ValueError("图片尺寸和目标尺寸必须大于 0。")
    if tw * th > MAX_OUTPUT_PIXELS:
        raise ValueError("目标图片超过 6000 万像素，请降低宽度或高度。")
    divisor = math.gcd(tw, th)
    rw, rh = tw // divisor, th // divisor
    multiplier = min(sw // rw, sh // rh)
    if multiplier == 0:
        raise ValueError("目标宽高比过于极端，源图无法按此比例裁剪。")
    cropped = (rw * multiplier, rh * multiplier)
    factor = next(
        (value for value in (1, 2, 4) if cropped[0] * value >= tw and cropped[1] * value >= th),
        None,
    )
    if factor is None:
        raise ValueError(
            f"裁剪后可用尺寸为 {cropped[0]}×{cropped[1]}，目标需要超过 4 倍放大。"
        )
    # The bundled photo and anime models always infer at 4x before resizing.
    if sw * sh * 16 > MAX_WORK_PIXELS:
        raise ValueError("AI 的 4 倍中间图片超过 2.4 亿像素，请缩小源图后再试。")
    return ScalePlan(source, cropped, target, factor)


def inspect_and_plan(image_path: Path, target: tuple[int, int]) -> ScalePlan:
    if image_path.suffix.lower() not in INPUT_FORMATS:
        raise ValueError("只支持 PNG、JPEG 和 WebP 图片。")
    with Image.open(image_path) as image:
        oriented = ImageOps.exif_transpose(image)
        return choose_scale(oriented.size, target)


def run_pipeline(
    image_path: Path,
    target: tuple[int, int],
    model: str,
    output_dir: Path,
    final_name: str,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    tile: int = 0,
) -> tuple[ScalePlan, Path]:
    plan = inspect_and_plan(image_path, target)
    _, ai = process(image_path, plan.factor, model, output_dir, tile, save_standard=False)
    ai_final = output_dir / final_name
    refine_image(
        ai, ai_final, width=target[0], height=target[1],
        focus_x=focus_x, focus_y=focus_y,
    )
    ai.unlink()
    return plan, ai_final
