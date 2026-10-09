"""Compare proportional Lanczos resizing with Real-ESRGAN restoration."""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests
from PIL import Image, ImageOps

from refine import refine_image


ROOT = Path(__file__).resolve().parent
ENGINE_DIR = ROOT / ".cache" / "realesrgan"
ENGINE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
    "realesrgan-ncnn-vulkan-20220424-windows.zip"
)
ENGINE_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
REALESRGAN_MODELS = {"photo": "realesrgan-x4plus", "anime": "realesrgan-x4plus-anime"}
MODELS = {**REALESRGAN_MODELS, "swinir": "swinir-real-sr-x4-gan"}
INPUT_FORMATS = {".png", ".jpg", ".jpeg", ".webp"}


def install_engine() -> Path:
    executable = ENGINE_DIR / "realesrgan-ncnn-vulkan.exe"
    model_dir = ENGINE_DIR / "models"
    if executable.is_file() and all(
        (model_dir / f"{name}.{extension}").is_file()
        for name in REALESRGAN_MODELS.values()
        for extension in ("bin", "param")
    ):
        return executable

    print("首次运行：下载 Real-ESRGAN 推理程序和模型（约 45 MB）…")
    response = requests.get(ENGINE_URL, timeout=120)
    response.raise_for_status()
    archive = response.content
    if hashlib.sha256(archive).hexdigest() != ENGINE_SHA256:
        raise RuntimeError("模型压缩包校验失败，请重试。")

    allowed = {
        "realesrgan-ncnn-vulkan.exe",
        "vcomp140.dll",
        "vcomp140d.dll",
        "models/realesrgan-x4plus.bin",
        "models/realesrgan-x4plus.param",
        "models/realesrgan-x4plus-anime.bin",
        "models/realesrgan-x4plus-anime.param",
    }
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for name in allowed:
            destination = ENGINE_DIR / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(name) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    return executable


def process(
    image_path: Path, scale: float, model: str, output_dir: Path, tile: int,
    save_standard: bool = True,
) -> tuple[Path | None, Path]:
    if not image_path.is_file():
        raise ValueError(f"找不到图片：{image_path}")
    if image_path.suffix.lower() not in INPUT_FORMATS:
        raise ValueError("输入图片需为 PNG、JPEG 或 WebP。")

    with Image.open(image_path) as source:
        original = ImageOps.exif_transpose(source)
        original.load()
    if original.mode not in ("RGB", "RGBA"):
        original = original.convert("RGB")
    target_size = tuple(max(1, round(length * scale)) for length in original.size)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem
    normal_path = output_dir / f"{stem}_lanczos.png"
    ai_path = output_dir / f"{stem}_ai_{model}.png"

    if save_standard:
        original.resize(target_size, Image.Resampling.LANCZOS).save(normal_path)
        print(f"普通放大：{normal_path} ({target_size[0]}×{target_size[1]})")

    if model == "swinir":
        from swinir import restore

        print("AI 修复：SwinIR 模型，正在处理…")
        enhanced = restore(original, tile)
        if enhanced.size != target_size:
            enhanced = enhanced.resize(target_size, Image.Resampling.LANCZOS)
        enhanced.save(ai_path)
        print(f"AI 放大：  {ai_path} ({target_size[0]}×{target_size[1]})")
        return (normal_path if save_standard else None), ai_path

    engine = install_engine()
    # Use short ASCII paths for the native executable, including images with Chinese names.
    with tempfile.TemporaryDirectory(prefix="upscale_", dir=ROOT / ".cache") as temporary:
        work = Path(temporary)
        input_file = work / "input.png"
        engine_output = work / "result.png"
        original.save(input_file)
        command = [
            str(engine), "-i", str(input_file), "-o", str(engine_output),
            "-m", str(ENGINE_DIR / "models"), "-n", MODELS[model], "-s", "4",
        ]
        if tile:
            command.extend(["-t", str(tile)])
        print(f"AI 修复：{model} 模型，正在处理…")
        result = subprocess.run(command, cwd=ENGINE_DIR, capture_output=True, text=True)
        if result.returncode != 0 or not engine_output.is_file():
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"AI 引擎运行失败。请确认显卡支持 Vulkan，并尝试 --tile 128。\n{detail}")
        with Image.open(engine_output) as enhanced:
            enhanced.load()
            if enhanced.size != target_size:
                enhanced = enhanced.resize(target_size, Image.Resampling.LANCZOS)
            enhanced.save(ai_path)
    print(f"AI 放大：  {ai_path} ({target_size[0]}×{target_size[1]})")
    return (normal_path if save_standard else None), ai_path


def main() -> int:
    parser = argparse.ArgumentParser(description="等比例放大图片，并对比 Real-ESRGAN AI 修复效果。")
    parser.add_argument("image", type=Path, help="输入图片路径（PNG/JPEG/WebP）")
    parser.add_argument("--scale", type=float, default=2, help="目标倍率，范围 1 到 4，默认 2")
    parser.add_argument("--model", choices=MODELS, default="photo", help="photo 照片，anime 插画，swinir 真实图片")
    parser.add_argument("--output", type=Path, default=ROOT / "output", help="输出目录")
    parser.add_argument("--tile", type=int, default=0, help="显存不足时使用 128 或 256；默认自动")
    parser.add_argument("--refine-width", type=int, help="放大后裁剪并缩小到这个宽度；与 --refine-height 同用")
    parser.add_argument("--refine-height", type=int, help="放大后裁剪并缩小到这个高度；与 --refine-width 同用")
    parser.add_argument("--refine-ratio", help="放大后只裁剪到指定宽高比，例如 16:9")
    parser.add_argument("--focus-x", type=float, default=0.5, help="裁剪位置：0 左、0.5 中、1 右")
    parser.add_argument("--focus-y", type=float, default=0.5, help="裁剪位置：0 上、0.5 中、1 下")
    args = parser.parse_args()
    if not 1 <= args.scale <= 4:
        parser.error("--scale 必须在 1 到 4 之间。")
    if args.tile != 0 and args.tile < 32:
        parser.error("--tile 必须为 0 或不小于 32。")
    try:
        if args.refine_ratio and (args.refine_width is not None or args.refine_height is not None):
            parser.error("--refine-ratio 不能和 --refine-width/--refine-height 同时使用。")
        if (args.refine_width is None) != (args.refine_height is None):
            parser.error("--refine-width 和 --refine-height 必须同时提供。")
        paths = process(args.image.resolve(), args.scale, args.model, args.output.resolve(), args.tile)
        if args.refine_ratio or args.refine_width is not None:
            for path in paths:
                refined = path.with_name(f"{path.stem}_refined.png")
                _, _, size = refine_image(
                    path, refined, width=args.refine_width, height=args.refine_height,
                    ratio=args.refine_ratio, focus_x=args.focus_x, focus_y=args.focus_y,
                )
                print(f"裁剪微调：{refined} ({size[0]}×{size[1]})")
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
