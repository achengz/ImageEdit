"""Crop to an exact aspect ratio, then optionally downscale without enlarging."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageOps


def parse_ratio(value: str) -> tuple[int, int]:
    try:
        left, right = value.split(":")
        width, height = int(left), int(right)
    except (ValueError, AttributeError):
        raise ValueError("宽高比请写成 W:H，例如 16:9。") from None
    if width <= 0 or height <= 0:
        raise ValueError("宽高比的两个数字必须大于 0。")
    divisor = math.gcd(width, height)
    return width // divisor, height // divisor


def refine_image(
    input_path: Path,
    output_path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    ratio: str | None = None,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> tuple[tuple[int, int], tuple[int, int, int, int], tuple[int, int]]:
    if ratio is not None:
        if width is not None or height is not None:
            raise ValueError("指定宽高比时，不要同时指定最终宽度或高度。")
        ratio_width, ratio_height = parse_ratio(ratio)
        target_size = None
    else:
        if width is None or height is None or width <= 0 or height <= 0:
            raise ValueError("请同时提供大于 0 的最终宽度和高度，或提供宽高比。")
        divisor = math.gcd(width, height)
        ratio_width, ratio_height = width // divisor, height // divisor
        target_size = (width, height)
    if not (0 <= focus_x <= 1 and 0 <= focus_y <= 1):
        raise ValueError("裁剪位置必须在 0 到 1 之间。")
    if not input_path.is_file():
        raise ValueError(f"找不到图片：{input_path}")

    with Image.open(input_path) as source:
        image = ImageOps.exif_transpose(source)
        image.load()
    source_size = image.size
    multiplier = min(image.width // ratio_width, image.height // ratio_height)
    if multiplier == 0:
        raise ValueError("输入图片太小，无法按这个宽高比裁剪出完整像素。")
    crop_width = ratio_width * multiplier
    crop_height = ratio_height * multiplier
    left = round((image.width - crop_width) * focus_x)
    top = round((image.height - crop_height) * focus_y)
    crop_box = (left, top, left + crop_width, top + crop_height)
    if target_size is not None and (width > crop_width or height > crop_height):
        raise ValueError(
            f"目标 {width}×{height} 超过裁剪后可用的 {crop_width}×{crop_height} 像素；"
            "此步骤不会放大图片。"
        )
    image = image.crop(crop_box)
    if target_size is not None and image.size != target_size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return source_size, crop_box, image.size


def main() -> int:
    parser = argparse.ArgumentParser(description="按目标宽高或宽高比裁剪图片；只能缩小，不能放大。")
    parser.add_argument("image", type=Path, help="输入图片路径")
    parser.add_argument("--width", type=int, help="最终宽度（像素），需与 --height 同时使用")
    parser.add_argument("--height", type=int, help="最终高度（像素），需与 --width 同时使用")
    parser.add_argument("--ratio", help="只裁剪成指定宽高比，例如 16:9；保留最大像素尺寸")
    parser.add_argument("--focus-x", type=float, default=0.5, help="水平裁剪位置：0 左、0.5 中、1 右")
    parser.add_argument("--focus-y", type=float, default=0.5, help="垂直裁剪位置：0 上、0.5 中、1 下")
    parser.add_argument("--output", type=Path, help="输出路径；默认为原文件名加 _refined.png")
    args = parser.parse_args()
    output = args.output or args.image.with_name(f"{args.image.stem}_refined.png")
    try:
        source_size, crop_box, final_size = refine_image(
            args.image, output, width=args.width, height=args.height,
            ratio=args.ratio, focus_x=args.focus_x, focus_y=args.focus_y,
        )
    except (OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    print(f"原图：{source_size[0]}×{source_size[1]}")
    print(f"裁剪区域：{crop_box}")
    print(f"结果：{final_size[0]}×{final_size[1]} → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
