"""Local web interface for single images and folder batches."""

from __future__ import annotations

import threading
import uuid
import zipfile
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageOps

from pipeline import run_pipeline
from upscale import INPUT_FORMATS, MODELS, ROOT


app = Flask(__name__, static_folder="web", static_url_path="/web")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
JOBS_ROOT = ROOT / ".cache" / "jobs"
OUTPUT_ROOT = ROOT / "output" / "web"
executor = ThreadPoolExecutor(max_workers=1)
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/plan")
def plan():
    try:
        target = parse_target(request.form)
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            raise ValueError("请选择一张图片。")
        with Image.open(uploaded.stream) as image:
            source = ImageOps.exif_transpose(image).size
        from pipeline import choose_scale

        result = choose_scale(source, target)
        return jsonify({"source": source, "crop": result.crop_before_scale, "factor": result.factor})
    except (OSError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


def parse_target(form) -> tuple[int, int]:
    try:
        width = int(form.get("width", ""))
        height = int(form.get("height", ""))
    except ValueError:
        raise ValueError("请填写目标宽度和高度。") from None
    if width <= 0 or height <= 0:
        raise ValueError("目标宽度和高度必须大于 0。")
    if width * height > 60_000_000:
        raise ValueError("目标图片超过 6000 万像素，请降低尺寸。")
    return width, height


def public_job(job: dict) -> dict:
    return {
        "id": job["id"], "status": job["status"], "completed": job["completed"],
        "total": len(job["items"]),
        "download_kind": "zip" if job["selection_mode"] == "folder" or len(job["items"]) > 1 else "image",
        "items": [
            {key: item.get(key) for key in ("name", "status", "error", "source", "factor", "ai")}
            for item in job["items"]
        ],
    }


@app.post("/api/jobs")
def create_job():
    try:
        target = parse_target(request.form)
        model = request.form.get("model", "photo")
        if model not in MODELS:
            raise ValueError("请选择有效的 AI 模型。")
        focus_x = float(request.form.get("focus_x", "0.5"))
        focus_y = float(request.form.get("focus_y", "0.5"))
        if not (0 <= focus_x <= 1 and 0 <= focus_y <= 1):
            raise ValueError("裁剪位置必须在 0 到 1 之间。")
        uploads = [file for file in request.files.getlist("files") if file.filename]
        if not uploads:
            raise ValueError("请选择图片或图片文件夹。")
        if len(uploads) > 100:
            raise ValueError("一次最多处理 100 张图片。")
        if any(Path(file.filename.replace("\\", "/")).suffix.lower() not in INPUT_FORMATS for file in uploads):
            raise ValueError("文件夹中只能包含 PNG、JPEG 或 WebP 图片。")
    except (ValueError, OverflowError) as error:
        return jsonify({"error": str(error)}), 400

    selection_mode = request.form.get("selection_mode", "files")
    selection_mode = selection_mode if selection_mode in ("files", "folder") else "files"
    job_id = uuid.uuid4().hex
    input_dir = JOBS_ROOT / job_id
    input_dir.mkdir(parents=True, exist_ok=True)
    items = []
    archive_names = set()
    for index, uploaded in enumerate(uploads, start=1):
        parts = [part for part in uploaded.filename.replace("\\", "/").split("/") if part not in ("", ".", "..")]
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", parts[-1]).rstrip(" .")
        final_name = f"{Path(filename).stem}AI.png"
        parent = parts[:-1] if selection_mode == "folder" else []
        archive_name = "/".join([*parent, final_name])
        if archive_name in archive_names:
            archive_name = "/".join([*parent, f"{index:03d}", final_name])
        archive_names.add(archive_name)
        stored = input_dir / f"{index:03d}{Path(filename).suffix.lower()}"
        uploaded.save(stored)
        items.append({
            "name": filename, "path": stored, "status": "queued", "error": None,
            "index": index, "final_name": final_name, "archive_name": archive_name,
        })
    job = {
        "id": job_id, "status": "queued", "completed": 0, "items": items,
        "target": target, "model": model, "focus_x": focus_x, "focus_y": focus_y,
        "selection_mode": selection_mode,
    }
    with jobs_lock:
        jobs[job_id] = job
    executor.submit(work_job, job_id)
    return jsonify(public_job(job)), 202


def work_job(job_id: str) -> None:
    job = jobs[job_id]
    with jobs_lock:
        job["status"] = "running"
    output_dir = OUTPUT_ROOT / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    for item in job["items"]:
        with jobs_lock:
            item["status"] = "running"
        try:
            item_dir = output_dir / f"{item['index']:03d}"
            plan, ai = run_pipeline(
                item["path"], job["target"], job["model"], item_dir,
                item["final_name"], job["focus_x"], job["focus_y"],
            )
            with jobs_lock:
                item.update({
                    "status": "done", "source": plan.source, "factor": plan.factor,
                    "ai": f"/api/jobs/{job_id}/files/{item['index']:03d}/{quote(ai.name)}",
                    "result_path": ai,
                })
        except Exception as error:
            with jobs_lock:
                item.update({"status": "error", "error": str(error)})
        finally:
            with jobs_lock:
                job["completed"] += 1
    with jobs_lock:
        job["status"] = "done"


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "找不到这个任务。"}), 404
        return jsonify(public_job(job))


@app.get("/api/jobs/<job_id>/files/<path:filename>")
def get_file(job_id: str, filename: str):
    if job_id not in jobs:
        return jsonify({"error": "找不到这个任务。"}), 404
    return send_from_directory(OUTPUT_ROOT / job_id, filename, as_attachment=False)


@app.get("/api/jobs/<job_id>/download")
def download_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "找不到这个任务。"}), 404
        if job["status"] != "done":
            return jsonify({"error": "任务仍在处理。"}), 409
        successful = [item for item in job["items"] if item["status"] == "done"]
        if not successful:
            return jsonify({"error": "没有可下载的图片。"}), 404
        use_archive = job["selection_mode"] == "folder" or len(job["items"]) > 1
    if not use_archive:
        path = successful[0]["result_path"]
        return send_file(path, as_attachment=True, download_name=path.name)
    archive = OUTPUT_ROOT / job_id / "image-results.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for item in successful:
            zipped.write(item["result_path"], item["archive_name"])
    return send_file(archive, mimetype="application/zip", as_attachment=True, download_name="image-results.zip")


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "上传文件总量超过 512 MB。"}), 413


if __name__ == "__main__":
    print("本地网页：http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
