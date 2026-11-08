"""Stripe Studio: local diagonal photo/video compositor, HTTP API and CLI."""
from __future__ import annotations

import functools
import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.2.0"
RESOURCE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
HOME = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
DATA = HOME / "user-data"
EXPORTS = HOME / "exports"
TOKEN = secrets.token_urlsafe(32)
MEDIA: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
DEFAULTS = {"schema": "stripe-studio/v1", "angle": -26, "randomAngle": True,
            "period": 34, "share": 42, "irregularity": 0, "seed": 1234,
            "videoOpacity": .80, "format": "9:16", "resolution": 1080, "fps": 30,
            "start": 0, "duration": 0, "audio": True, "encoder": "auto"}


def resolve_template(raw, choose_angle=True):
    if not isinstance(raw, dict):
        raise ValueError("template должен быть JSON-объектом.")
    resolved = {**DEFAULTS, **raw}
    random_angle = raw.get("randomAngle", "angle" not in raw) is True
    if random_angle and choose_angle:
        resolved["angle"] = secrets.randbelow(161) - 80
    resolved["angle"] = number(resolved.get("angle"), -80, 80, -26)
    resolved["randomAngle"] = False
    resolved["videoOpacity"] = number(resolved.get("videoOpacity"), 0, 1, .80)
    return resolved


def binary(name: str) -> str:
    candidates = [HOME / "bin" / (name + ".exe"), RESOURCE / "bin" / (name + ".exe")]
    for p in candidates:
        if p.is_file():
            return str(p)
    result = shutil.which(name)
    if not result:
        raise ValueError(f"Не найден {name}. Поместите его в папку bin рядом с программой.")
    return result


def command(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, timeout=timeout)


def probe(path: Path) -> dict:
    result = command([binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], 60)
    if result.returncode:
        raise ValueError("Не удалось прочитать файл. Проверьте формат и целостность медиа.")
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams", [])
    stream = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if not stream:
        raise ValueError("В файле не найдено изображение или видеодорожка.")
    width, height = int(stream["width"]), int(stream["height"])
    sar = stream.get("sample_aspect_ratio", "1:1").split(":")
    if len(sar) == 2 and float(sar[1] or 0) > 0:
        width = round(width * float(sar[0]) / float(sar[1]))
    rotation = float(stream.get("tags", {}).get("rotate", 0))
    for side in stream.get("side_data_list", []):
        rotation = float(side.get("rotation", rotation))
    if abs(round(rotation / 90)) % 2:
        width, height = height, width
    return {"width": width, "height": height,
            "duration": float(metadata.get("format", {}).get("duration", stream.get("duration", 0)) or 0),
            "hasAudio": any(s.get("codec_type") == "audio" for s in streams),
            "codec": stream.get("codec_name"), "pix_fmt": stream.get("pix_fmt"),
            "audioCodecs": [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"]}


def prepare_media(path: Path, kind: str, name: str | None = None, preview_video: bool = True) -> dict:
    info = probe(path)
    ident = secrets.token_hex(12)
    folder = DATA / ident
    folder.mkdir(parents=True, exist_ok=True)
    original = path.resolve()
    preview = original
    if kind == "photo":
        preview = folder / "photo.png"
        result = command([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(original),
                          "-frames:v", "1", "-update", "1", str(preview)], 120)
        if result.returncode:
            raise ValueError("Это фото не удалось открыть. Попробуйте JPG, PNG или WebP.")
        info.update(probe(preview))
        info["duration"] = 0
    elif kind == "video":
        if info["duration"] <= 0:
            raise ValueError("Не удалось определить длительность видео.")
        browser_ready = (path.suffix.lower() in (".mp4", ".m4v") and info["codec"] == "h264"
                         and info["pix_fmt"] == "yuv420p" and all(a in ("aac", "mp3") for a in info["audioCodecs"]))
        if not browser_ready and preview_video:
            preview = folder / "preview.mp4"
            result = command([binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(original),
                              "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=720:720:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
                              "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                              "-threads", "4", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(preview)])
            if result.returncode:
                raise ValueError("Не удалось подготовить видео для просмотра. Попробуйте MP4 с кодеком H.264.")
    else:
        raise ValueError("kind должен быть photo или video.")
    record = {"id": ident, "name": name or path.name, "kind": kind, "url": f"/media/{ident}",
              **{k: info[k] for k in ("width", "height", "duration", "hasAudio")},
              "path": original, "preview": preview}
    MEDIA[ident] = record
    return record


def public_media(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("path", "preview")}


def number(value, low, high, default):
    try:
        value = float(value)
        if value != value or value in (float("inf"), -float("inf")):
            return default
        return max(low, min(high, value))
    except (TypeError, ValueError):
        return default


def framing(value) -> dict:
    value = value if isinstance(value, dict) else {}
    return {"zoom": number(value.get("zoom"), 1, 3, 1), "x": number(value.get("x"), 0, 1, .5),
            "y": number(value.get("y"), 0, 1, .5), "mirror": value.get("mirror") is True}


def settings_from_template(template: dict) -> dict:
    if not isinstance(template, dict):
        raise ValueError("template должен быть JSON-объектом.")
    fmt = template.get("format", "9:16")
    res = int(number(template.get("resolution"), 720, 2160, 1080))
    ratios = {"9:16": (1, 16 / 9), "16:9": (16 / 9, 1), "1:1": (1, 1), "4:5": (1, 1.25)}
    a, b = ratios.get(fmt if isinstance(fmt, str) else "9:16", ratios["9:16"])
    return {**template, "width": template.get("width", round(res * a / 2) * 2),
            "height": template.get("height", round(res * b / 2) * 2)}


def validate_settings(raw: dict, media: dict) -> dict:
    width = int(number(raw.get("width"), 128, 3840, 1080)) // 2 * 2
    height = int(number(raw.get("height"), 128, 3840, 1920)) // 2 * 2
    try:
        fps = int(raw.get("fps", 30))
    except (ValueError, TypeError) as exc:
        raise ValueError("Некорректная частота кадров.") from exc
    if fps not in (24, 25, 30, 50, 60):
        raise ValueError("Частота кадров должна быть 24, 25, 30, 50 или 60.")
    full = media["duration"]
    start = number(raw.get("start"), 0, full, 0)
    duration = number(raw.get("duration"), 0, full, 0) or full - start
    duration = min(duration, full - start)
    if duration < .05:
        raise ValueError("Выберите непустой отрезок видео.")
    return {"width": width, "height": height, "fps": fps, "start": start, "duration": duration,
            "videoOpacity": number(raw.get("videoOpacity"), 0, 1, .80),
            "encoder": raw.get("encoder", "auto"), "audio": raw.get("audio", True) is True, "photo": framing(raw.get("photo")), "video": framing(raw.get("video"))}


def make_mask(template: dict, width: int, height: int, target: Path):
    if not isinstance(template, dict):
        raise ValueError("template должен быть JSON-объектом.")
    # Deterministic geometry is shared with the browser implementation.
    from mask import save_mask
    save_mask(template, width, height, target)


def crop_filter(width: int, height: int, crop: dict) -> str:
    zoom = crop["zoom"]
    filters = ["scale=ceil(iw*sar/2)*2:ih", "setsar=1",
               f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2",
               f"scale=ceil(iw*{zoom:.8f}/2)*2:ceil(ih*{zoom:.8f}/2)*2",
               f"crop={width}:{height}:(iw-ow)*{crop['x']:.8f}:(ih-oh)*{crop['y']:.8f}"]
    if crop["mirror"]:
        filters.append("hflip")
    return ",".join(filters)


def start_render(payload: dict, out_path: Path | None = None) -> dict:
    photo_id, video_id = payload.get("photo"), payload.get("video")
    if not isinstance(photo_id, str) or not isinstance(video_id, str):
        raise ValueError("Сначала выберите своё фото и своё видео.")
    photo = MEDIA.get(photo_id)
    video = MEDIA.get(video_id)
    if not photo or photo["kind"] != "photo" or not video or video["kind"] != "video":
        raise ValueError("Сначала выберите своё фото и своё видео.")
    template = resolve_template(payload.get("template", {}), choose_angle=not bool(payload.get("mask")))
    raw = payload.get("settings", {})
    if not isinstance(raw, dict):
        raise ValueError("settings должен быть объектом.")
    settings = validate_settings({**template, **raw}, video)
    with LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise ValueError("Уже идёт экспорт. Дождитесь завершения или отмените его.")
        ident = secrets.token_hex(12)
        folder = DATA / ("job-" + ident)
        folder.mkdir(parents=True)
        mask_path = folder / "mask.png"
        if payload.get("mask"):
            try:
                png = base64.b64decode(payload["mask"], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("Некорректная PNG-маска.") from exc
            if len(png) < 24 or len(png) > 16 * 1024 * 1024 or png[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("Некорректная PNG-маска.")
            import struct
            if struct.unpack(">II", png[16:24]) != (settings["width"], settings["height"]):
                raise ValueError("Размер маски не совпадает с размером видео.")
            mask_path.write_bytes(png)
        else:
            make_mask(template, settings["width"], settings["height"], mask_path)
        EXPORTS.mkdir(parents=True, exist_ok=True)
        output = out_path.resolve() if out_path else EXPORTS / (f"stripes-{time.strftime('%Y%m%d-%H%M%S')}-{ident[:4]}.mp4")
        if output.exists():
            raise ValueError("Выходной файл уже существует. Укажите новое имя.")
        if output.suffix.lower() != ".mp4":
            raise ValueError("Для результата укажите файл .mp4.")
        if output == photo["path"] or output == video["path"]:
            raise ValueError("Результат не может заменить исходный файл.")
        output.parent.mkdir(parents=True, exist_ok=True)
        job = {"id": ident, "status": "running", "progress": 0, "url": f"/exports/{ident}", "name": output.name,
               "output": output, "folder": folder, "settings": settings, "template": template,
               "angle": template["angle"],
               "cancel": threading.Event(), "process": None, "error": None}
        JOBS[ident] = job
        threading.Thread(target=run_render, args=(job, photo, video, mask_path), daemon=True).start()
    return job


@functools.lru_cache(maxsize=1)
def nvenc_available():
    try:
        result = command([binary("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:r=1",
                          "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"], 20)
        return result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def choose_encoder(mode="auto"):
    if mode not in ("auto", "cpu", "nvenc"):
        raise ValueError("encoder должен быть auto, cpu или nvenc.")
    if mode == "cpu":
        return "cpu"
    if nvenc_available():
        return "nvenc"
    if mode == "nvenc":
        raise ValueError("NVENC недоступен. Проверьте NVIDIA GPU, драйвер и сборку FFmpeg.")
    return "cpu"


def prepare_static_layer(photo, mask, cfg, target):
    from PIL import Image
    cropped = target.with_name("cropped-photo.png")
    result = command([binary("ffmpeg"), "-v", "error", "-nostdin", "-n", "-i", str(photo),
                      "-vf", crop_filter(cfg["width"], cfg["height"], cfg["photo"]),
                      "-frames:v", "1", "-update", "1", str(cropped)], 120)
    if result.returncode:
        raise ValueError("Не удалось подготовить неподвижный слой фото.")
    with Image.open(cropped) as image, Image.open(mask) as mask_image:
        layer = image.convert("RGBA")
        alpha = mask_image.convert("L").point(lambda x: round(255 - x * cfg["videoOpacity"]))
        layer.putalpha(alpha)
        layer.save(target)


def run_render(job, photo, video, mask):
    cfg = job["settings"]
    width, height, fps = cfg["width"], cfg["height"], cfg["fps"]
    process = None
    try:
        # Render the static photo crop once, rather than decoding a large original every frame.
        layer = job["folder"] / "static-layer.png"
        prepare_static_layer(photo["preview"], mask, cfg, layer)
        mode = cfg.get("encoder", "auto")
        encoder = choose_encoder("cpu" if mode == "auto" and min(width, height) < 256 else mode)
        job["encoder"] = encoder
        graph = (f"[0:v:0]setpts=PTS-STARTPTS,fps={fps},{crop_filter(width, height, cfg['video'])},hue=s=0,format=yuv420p[v];"
                 "[v][1:v:0]overlay=shortest=1:format=yuv420[out]")
        args = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                "-threads", "4", "-ss", str(cfg["start"]), "-i", str(video["path"]),
                "-loop", "1", "-framerate", str(fps), "-i", str(layer),
                "-filter_complex_threads", "2", "-filter_complex", graph, "-map", "[out]"]
        if cfg["audio"]:
            args += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"]
        codec = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18", "-b:v", "0"]
                 if encoder == "nvenc" else ["-c:v", "libx264", "-preset", "fast", "-crf", "18"])
        args += ["-t", str(cfg["duration"]), "-r", str(fps), *codec, "-threads", "4", "-pix_fmt", "yuv420p",
                 "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1",
                 "-movflags", "+faststart", "-progress", "pipe:1", str(job["output"])]
        with (job["folder"] / "ffmpeg.log").open("wb") as err:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=err, text=True, creationflags=NO_WINDOW)
            job["process"] = process
            if job["cancel"].is_set():
                process.terminate()
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us":
                    try:
                        elapsed = int(value) / 1e6
                    except ValueError:
                        continue
                    job["progress"] = min(99.5, max(0, elapsed / cfg["duration"] * 100))
            code = process.wait()
        if job["cancel"].is_set():
            job["status"] = "cancelled"
            job["output"].unlink(missing_ok=True)
        elif code:
            job["status"] = "error"
            job["error"] = "Экспорт не завершён. Подробности сохранены в user-data / job / ffmpeg.log."
            job["output"].unlink(missing_ok=True)
        else:
            sidecar = {"schema": "stripe-studio/v1", **job["template"], "render": cfg}
            job["output"].with_suffix(".preset.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
            job["progress"] = 100
            job["status"] = "done"
    except Exception as exc:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        job["error"] = str(exc)
        job["status"] = "error"
        job["output"].unlink(missing_ok=True)
    finally:
        if process and process.stdout:
            process.stdout.close()
        job["process"] = None


def public_job(job: dict) -> dict:
    return {**{k: job[k] for k in ("id", "status", "progress", "url", "name", "error", "angle")},
            "encoder": job.get("encoder")}


def cancel_job(job):
    job["cancel"].set()
    process = job.get("process")
    if process and process.poll() is None:
        process.terminate()


DOCS = """<!doctype html><meta charset=utf-8><title>Stripe Studio · API / CLI</title>
<style>body{max-width:900px;margin:48px auto;padding:24px;background:#151619;color:#edece7;font:16px/1.6 system-ui}pre{background:#22252a;padding:20px;white-space:pre-wrap}a{color:#d3f56a}</style>
<h1>Stripe Studio · API и CLI</h1><p>Все операции выполняются на этом компьютере. API слушает только 127.0.0.1. Для POST передавайте заголовок <code>X-Template-Token</code>.</p>
<h2>CLI</h2><pre>StripeStudio.exe render --photo "photo.jpg" --video "video.mp4" --out "result.mp4" --preset "preset.json"
StripeStudio.exe mask --preset "preset.json" --out "mask.png"
StripeStudio.exe serve --port 18786 --no-open
StripeStudio.exe render --help</pre><p>Из исходников: <code>python app.py</code> вместо <code>StripeStudio.exe</code>. Параметр preset необязателен. Существующий результат не перезаписывается.</p>
<h2>Токен текущего запуска</h2><pre>__SESSION_TOKEN__</pre><p>Для CLI-клиента прочитайте <code>user-data/session.json</code>: там URL, PID и token текущего сервера. Не используйте устаревшую запись.</p>
<h2>Методы</h2><pre>GET /api/info
POST /api/upload?kind=photo|video&amp;name=filename   — тело: байты файла
POST /api/render — JSON:
{
  "photo": "id фото", "video": "id видео",
  "settings": {"width":1080,"height":1920,"fps":30,"start":0,"duration":0,
    "audio":true,"photo":{"zoom":1,"x":0.5,"y":0.5,"mirror":false},
    "video":{"zoom":1,"x":0.5,"y":0.5,"mirror":false}},
  "template": {"randomAngle":true,"period":34,"share":42,"irregularity":0,"videoOpacity":0.80}
}
GET /api/jobs/ID
POST /api/jobs/ID/cancel — {}
GET /exports/ID — MP4 после завершения
POST /api/render-paths — JSON:
{"photoPath":"C:/media/photo.jpg","videoPath":"C:/media/video.mp4",
 "outputPath":"C:/media/result.mp4","template":{"format":"9:16","resolution":1080}}
</pre><p>duration:0 — до конца ролика. x/y:0..1 — положение кадра, zoom:1..4. Белое в PNG-маске выбирает видео, чёрное — фото. Вместо template можно передать mask: base64 PNG без префикса, с размерами выходного кадра.</p>
<p>POST возвращает 400 с полем error при неверном вводе, 403 при неверном токене. Экспорт асинхронный, в одном процессе одновременно выполняется один экспорт.</p><p><a href=/>Вернуться в редактор</a></p>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, fmt, *args):
        pass

    def reply(self, data, status=200, content_type="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def file(self, path, download=False):
        if not path.is_file():
            return self.reply({"error": "Файл не найден."}, 404)
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get("Range", "")
        if requested:
            try:
                unit, value = requested.split("=", 1)
                if unit != "bytes" or "," in value:
                    raise ValueError
                a, b = value.split("-", 1)
                start = int(a) if a else max(0, size - int(b))
                end = min(int(b), size - 1) if b and a else size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                status = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(path.name))
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/info":
            return self.reply({"version": VERSION, "ffmpeg": bool(binary("ffmpeg")), "photoDemo": "/assets/demo-photo.png", "videoDemo": "/assets/demo-video.mp4"})
        if route == "/api/docs":
            return self.reply(DOCS.replace("__SESSION_TOKEN__", TOKEN), content_type="text/html; charset=utf-8")
        if route.startswith("/api/jobs/"):
            job = JOBS.get(route.split("/")[-1])
            return self.reply(public_job(job) if job else {"error": "Задание не найдено."}, 200 if job else 404)
        if route.startswith("/media/"):
            record = MEDIA.get(route.split("/")[-1])
            return self.file(record["preview"]) if record else self.reply({"error": "Медиа не найдено."}, 404)
        if route.startswith("/exports/"):
            job = JOBS.get(route.split("/")[-1])
            return self.file(job["output"], True) if job and job["status"] == "done" else self.reply({"error": "Результат ещё не готов."}, 404)
        if route in ("/", "/index.html"):
            html = (RESOURCE / "web" / "index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", TOKEN)
            return self.reply(html, content_type="text/html; charset=utf-8")
        if route in ("/app.js", "/styles.css"):
            return self.file(RESOURCE / "web" / route[1:])
        if route in ("/assets/demo-photo.png", "/assets/demo-video.mp4"):
            return self.file(RESOURCE / route[1:])
        return self.reply({"error": "Не найдено."}, 404)

    def do_POST(self):
        if self.headers.get("X-Template-Token") != TOKEN:
            self.close_connection = True
            return self.reply({"error": "Неверный токен текущего запуска."}, 403)
        route = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                raise ValueError("Пустое тело запроса.")
            if route.path == "/api/upload":
                query = urllib.parse.parse_qs(route.query)
                kind = query.get("kind", [""])[0]
                if kind not in ("photo", "video"):
                    raise ValueError("kind должен быть photo или video.")
                name = Path(query.get("name", ["media"])[0]).name
                upload_dir = DATA / ("upload-" + secrets.token_hex(12))
                upload_dir.mkdir(parents=True)
                suffix = Path(name).suffix[:12] or ".bin"
                target = upload_dir / ("source" + suffix)
                with target.open("wb") as out:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Загрузка прервана.")
                        out.write(chunk)
                        remaining -= len(chunk)
                return self.reply(public_media(prepare_media(target, kind, name)))
            if length > 24 * 1024 * 1024:
                self.close_connection = True
                raise ValueError("JSON-запрос слишком большой.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ожидается JSON-объект.")
            if route.path == "/api/render-paths":
                template = payload.get("template", {})
                if payload.get("presetPath"):
                    template = json.loads(Path(payload["presetPath"]).read_text(encoding="utf-8-sig"))
                photo = prepare_media(Path(payload["photoPath"]), "photo")
                video = prepare_media(Path(payload["videoPath"]), "video", preview_video=False)
                job = start_render({"photo": photo["id"], "video": video["id"], "settings": settings_from_template(template), "template": template},
                                   Path(payload["outputPath"]) if payload.get("outputPath") else None)
                return self.reply({"id": job["id"], "angle": job["angle"]})
            if route.path == "/api/render":
                job = start_render(payload)
                return self.reply({"id": job["id"], "angle": job["angle"]})
            if route.path.startswith("/api/jobs/") and route.path.endswith("/cancel"):
                job = JOBS.get(route.path.split("/")[-2])
                if not job:
                    raise ValueError("Задание не найдено.")
                if job["status"] == "running":
                    cancel_job(job)
                return self.reply(public_job(job))
            return self.reply({"error": "Не найдено."}, 404)
        except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
            self.close_connection = True
            self.reply({"error": str(exc)}, 400)


def serve(args):
    binary("ffmpeg")
    binary("ffprobe")
    DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}"
    session = DATA / "session.json"
    session.write_text(json.dumps({"url": url, "token": TOKEN, "pid": os.getpid()}), encoding="utf-8")
    print(f"Stripe Studio {VERSION}\n{url}\nAPI: {url}/api/docs\nClose this window or press Ctrl+C to stop.", flush=True)
    if not args.no_open:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in JOBS.values():
            if job["status"] == "running":
                cancel_job(job)
        server.server_close()
        try:
            if json.loads(session.read_text(encoding="utf-8")).get("pid") == os.getpid():
                session.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass


def load_template(path):
    template = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    if not isinstance(template, dict):
        raise ValueError("Пресет должен содержать JSON-объект.")
    if template.get("schema", "stripe-studio/v1") != "stripe-studio/v1":
        raise ValueError("Неизвестная версия пресета.")
    return template


def main():
    parser = argparse.ArgumentParser(description="Stripe Studio — static color photo + grayscale video through diagonal stripes.")
    parser.add_argument("--version", action="version", version=f"StripeStudio {VERSION}")
    sub = parser.add_subparsers(dest="mode")
    server_parser = sub.add_parser("serve", help="Open local editor and HTTP API")
    server_parser.add_argument("--port", type=int, default=0)
    server_parser.add_argument("--no-open", action="store_true")
    render = sub.add_parser("render", help="Render MP4 without browser")
    render.add_argument("--photo", required=True)
    render.add_argument("--video", required=True)
    render.add_argument("--out", required=True)
    render.add_argument("--preset")
    render.add_argument("--encoder", choices=("auto", "cpu", "nvenc"), default=None, help="Auto prefers NVIDIA NVENC; otherwise CPU")
    render.add_argument("--angle", type=float, help="Fixed common angle -80..80; default random")
    render.add_argument("--video-opacity", type=float, help="Video fraction 0..1; default .80")
    render.add_argument("--start", type=float)
    render.add_argument("--duration", type=float)
    render.add_argument("--fps", type=int, choices=(24, 25, 30, 50, 60))
    render.add_argument("--width", type=int)
    render.add_argument("--height", type=int)
    render.add_argument("--silent", action="store_true", help="Omit original video sound")
    mask_parser = sub.add_parser("mask", help="Save reusable white-video / black-photo PNG mask")
    mask_parser.add_argument("--preset")
    mask_parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.mode in (None, "serve"):
        if args.mode is None:
            args.port, args.no_open = 0, False
        return serve(args)
    template = load_template(args.preset)
    if args.mode == "mask":
        template = resolve_template(template)
        settings = settings_from_template(template)
        target = Path(args.out).resolve()
        if target.exists():
            raise ValueError("Файл уже существует. Укажите новое имя.")
        target.parent.mkdir(parents=True, exist_ok=True)
        make_mask(template, settings["width"], settings["height"], target)
        print(str(target))
        return 0
    for field in ("start", "duration", "fps", "width", "height", "encoder"):
        if getattr(args, field) is not None:
            template[field] = getattr(args, field)
    if args.silent:
        template["audio"] = False
    if args.angle is not None:
        template.update(angle=args.angle, randomAngle=False)
    if args.video_opacity is not None:
        template["videoOpacity"] = args.video_opacity
    photo = prepare_media(Path(args.photo), "photo")
    video = prepare_media(Path(args.video), "video", preview_video=False)
    cfg = settings_from_template(template)
    if args.width is not None:
        cfg["width"] = args.width
    if args.height is not None:
        cfg["height"] = args.height
    job = start_render({"photo": photo["id"], "video": video["id"], "settings": cfg, "template": template}, Path(args.out))
    last = -1
    try:
        while job["status"] == "running":
            progress = int(job["progress"])
            if progress != last:
                print(json.dumps({"status": "running", "progress": progress}), flush=True)
                last = progress
            time.sleep(.25)
    except KeyboardInterrupt:
        cancel_job(job)
        return 130
    print(json.dumps(public_job(job), ensure_ascii=False), flush=True)
    return 0 if job["status"] == "done" else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
