from __future__ import annotations

import argparse
import copy
import gc
import html
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# No HF token is required. If HF_TOKEN is set in the environment it is used
# automatically; otherwise models download anonymously.

import gradio as gr
import requests
import srt
from opencc import OpenCC
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:  # hide the "unauthenticated requests" warning
    from huggingface_hub.utils import logging as _hf_logging
    _hf_logging.set_verbosity_error()
except Exception:
    pass


# ============================================================
# Configuration
# ============================================================

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)

API = "https://api.elevenlabs.io/v1"
MAX_BYTES = 500 * 1024 * 1024
YT_MAX_SECONDS = 3600
LOCK = threading.Lock()

WHISPER_MODEL = None
WHISPER_KEY = None      # (model_name, device)
FORCE_CPU = False
CONVERTER = OpenCC("s2twp")
YTDLP_UPDATED = False

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo"]
DEFAULT_WHISPER = "small"
CJK = {"zh", "ja", "ko"}

YT_NOTICE = (
    "⚠️ YouTube download may not always work (YouTube often blocks cloud "
    "servers such as Colab). If it fails, download the video yourself and "
    "use the Upload option.\n"
    "⚠️ YouTube 下載不一定能成功（YouTube 常封鎖 Colab 等雲端伺服器）。"
    "若失敗，請自行下載影片後改用「上傳」。"
)

LANGUAGES = {
    "English / 英語": "en",
    "Taiwan Mandarin / 臺灣華語": "zh-TW",
    "Chinese / 中文": "zh",
    "Japanese / 日語": "ja",
    "Korean / 韓語": "ko",
    "Spanish / 西班牙語": "es",
    "French / 法語": "fr",
    "German / 德語": "de",
    "Italian / 義大利語": "it",
    "Portuguese / 葡萄牙語": "pt",
    "Arabic / 阿拉伯語": "ar",
    "Hindi / 印地語": "hi",
}

AUTO = "Auto detect / 自動偵測"

WORKFLOW_DUB = "Dubbing + subtitles / 配音＋字幕（付費 API）"
WORKFLOW_SUB = "Subtitles only / 僅字幕（免費、本機 Whisper）"
WORKFLOWS = [WORKFLOW_DUB, WORKFLOW_SUB]

SUB_MODES = [
    "Translated / 翻譯字幕",
    "Original / 原文字幕",
    "Bilingual / 雙語字幕",
    "None / 無字幕",
]

AUDIO_MODES = [
    "Dubbed / 配音",
    "Original / 原音",
]

EXPORT_MODES = [
    "Soft subtitles / 可關閉字幕 — fast",
    "Burn in / 永久燒錄字幕 — slower",
]

CSS = """
.gradio-container {
    max-width: 1180px !important;
    margin: auto !important;
}
.status-card {
    padding: 16px;
    border: 1px solid #6366f1;
    border-radius: 12px;
    background: rgba(99,102,241,.07);
}
.status-row {
    display: flex;
    gap: 14px;
    align-items: center;
}
.spinner {
    width: 26px;
    height: 26px;
    flex-shrink: 0;
    border: 4px solid rgba(99,102,241,.2);
    border-top-color: #6366f1;
    border-radius: 50%;
    animation: spin .8s linear infinite;
}
.activity {
    margin-top: 12px;
    height: 5px;
    overflow: hidden;
    background: rgba(99,102,241,.12);
    border-radius: 5px;
}
.activity span {
    display: block;
    width: 35%;
    height: 100%;
    background: #6366f1;
    animation: slide 1.5s ease-in-out infinite;
}
.yt-warning {
    padding: 10px 14px;
    border: 1px solid #f59e0b;
    border-radius: 10px;
    background: rgba(245,158,11,.10);
}
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes slide {
    from { transform: translateX(-110%); }
    to { transform: translateX(390%); }
}
#editor-video video::cue {
    color: white;
    background: rgba(0,0,0,.75);
}
@media (prefers-reduced-motion: reduce) {
    .spinner, .activity span { animation: none; }
}
"""

LIVE_JS = r"""
(original, translated, mode) => {
    const rows = value => {
        const data = Array.isArray(value) ? value : (value?.data || []);
        return data.map(r => [
            Number(r[0]), Number(r[1]), String(r[2] ?? "").trim()
        ]).filter(r =>
            Number.isFinite(r[0]) &&
            Number.isFinite(r[1]) &&
            r[0] >= 0 && r[1] > r[0] && r[2]
        );
    };

    let selected = [];
    if (mode.startsWith("Original")) selected = rows(original);
    if (mode.startsWith("Translated")) selected = rows(translated);
    if (mode.startsWith("Bilingual")) {
        selected = [...rows(original), ...rows(translated)];
    }

    const points = [...new Set(
        selected.flatMap(r => [r[0], r[1]])
    )].sort((a, b) => a - b);

    const cues = [];
    for (let i = 0; i + 1 < points.length; i++) {
        const a = points[i], b = points[i + 1];
        const text = selected
            .filter(r => r[0] < b && r[1] > a)
            .map(r => r[2])
            .join("\n");
        if (!text) continue;

        const previous = cues[cues.length - 1];
        if (previous && previous[1] === a && previous[2] === text) {
            previous[1] = b;
        } else {
            cues.push([a, b, text]);
        }
    }

    const state = window.__editableCaptions ||= {
        version: 0,
        cues: [],
        video: null,
        source: null,
        appliedVersion: -1
    };

    state.cues = cues;
    state.version++;

    if (!state.timer) {
        state.timer = setInterval(() => {
            const video = document.querySelector("#editor-video video");
            if (!video) return;

            if (
                state.video === video &&
                state.source === video.currentSrc &&
                state.appliedVersion === state.version
            ) return;

            const track = video.__editableCaptionTrack ||=
                video.addTextTrack("subtitles", "Editable subtitles", "und");

            track.mode = "hidden";

            for (const cue of Array.from(track.cues || [])) {
                track.removeCue(cue);
            }

            const escape = text => text
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;");

            for (const [a, b, text] of state.cues) {
                const cue = new VTTCue(a, b, escape(text));
                cue.align = "center";
                track.addCue(cue);
            }

            track.mode = state.cues.length ? "showing" : "disabled";
            state.video = video;
            state.source = video.currentSrc;
            state.appliedVersion = state.version;
        }, 200);
    }

    return [];
}
"""


# ============================================================
# Utilities
# ============================================================

def command(args, cwd=None, timeout=1800):
    try:
        p = subprocess.run(
            [str(x) for x in args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Local command timed out / 本機處理逾時"
        ) from None

    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-1800:])

    return p.stdout


def ffmpeg(args, cwd=None):
    return command(
        ["ffmpeg", "-nostdin", "-y", "-v", "error"] + args,
        cwd=cwd,
    )


def probe(path):
    return json.loads(command([
        "ffprobe", "-v", "error",
        "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams",
        "-of", "json", path,
    ], timeout=60))


def media_duration(path):
    return float(probe(path)["format"]["duration"])


def plain(text):
    text = str(text or "").replace("\x00", "").replace("\r", "")
    text = re.sub(r"<[^>]*>", "", text)
    return html.unescape(text).strip()


def validate_rows(rows, limit=None):
    result = []
    for number, row in enumerate(rows or [], 1):
        if len(row) < 3 or not plain(row[2]):
            continue

        try:
            start, end = float(row[0]), float(row[1])
        except (TypeError, ValueError):
            raise ValueError(
                f"Subtitle row {number}: invalid times / 字幕時間無效"
            ) from None

        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            raise ValueError(
                f"Subtitle row {number}: use 0 ≤ start < end."
            )

        if limit is not None:
            if start >= limit:
                continue
            end = min(end, limit)

        start, end = round(start, 3), round(end, 3)
        if end > start:
            result.append([start, end, plain(row[2])])

    if len(result) > 2000:
        raise ValueError("Maximum 2,000 subtitle rows / 字幕最多 2,000 行")

    return sorted(result, key=lambda r: (r[0], r[1]))


def save_job(job):
    if job.get("directory"):
        path = Path(job["directory"]) / "job.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)


def selected_video(job, audio_mode):
    key = "dub_video" if audio_mode.startswith("Dubbed") else "clip"
    path = job.get(key)

    if not path or not Path(path).is_file():
        if key == "dub_video":
            raise ValueError(
                "No dubbed video exists (subtitles-only mode?). "
                "Choose 'Original / 原音' as the audio.\n"
                "尚無配音影片（僅字幕模式？）。請改選「原音」。"
            )
        raise ValueError(
            "Selected audio is unavailable. Prepare a clip first.\n"
            "尚無所選音訊，請先準備影片。"
        )

    return path


# ============================================================
# YouTube import (robust, multi-strategy)
# ============================================================

class YouTubeLimit(ValueError):
    """Video is not allowed (live / too long). Do not retry."""


class YouTubeFatal(RuntimeError):
    """Retrying will not help (private, removed, etc.)."""


FATAL_MARKERS = (
    "private video",
    "video unavailable",
    "this video is not available",
    "has been removed",
    "members-only",
    "members only",
    "copyright",
    "account associated with this video has been terminated",
    "not made this video available in your country",
)


def find_js_runtimes():
    """Return yt-dlp --js-runtimes arguments for whatever is installed."""
    args = []
    deno = shutil.which("deno")
    if not deno:
        candidate = Path(sys.executable).parent / "deno"
        if candidate.is_file():
            deno = str(candidate)
    if deno:
        args += ["--js-runtimes", f"deno:{deno}"]

    node = shutil.which("node") or shutil.which("nodejs")
    if node:
        args += ["--js-runtimes", f"node:{node}"]

    return args


def update_ytdlp(emit):
    """Upgrade yt-dlp once per session; each download is a new process."""
    global YTDLP_UPDATED
    if YTDLP_UPDATED:
        return False
    YTDLP_UPDATED = True

    emit("Updating yt-dlp / 更新 yt-dlp（YouTube 常常改版）")
    try:
        command([
            sys.executable, "-m", "pip", "install", "-U", "-q",
            "yt-dlp[default]",
        ], timeout=300)
        return True
    except Exception as exc:
        emit("yt-dlp update failed: " + str(exc)[-200:])
        return False


def friendly_youtube_error(text, has_cookies):
    low = text.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        hint = (
            "YouTube is blocking this server (bot check). "
            "Upload a cookies.txt exported from your own logged-in browser "
            "(Netscape format), or upload the video file instead.\n"
            "YouTube 封鎖了此伺服器（機器人驗證）。請上傳由您自己瀏覽器匯出的 "
            "cookies.txt，或直接上傳影片檔。"
        )
        if has_cookies:
            hint = (
                "YouTube still blocked the request even with your cookies. "
                "They may be expired; export fresh cookies, or upload the "
                "video file.\n" + hint
            )
        return hint
    if "age" in low and ("confirm" in low or "restricted" in low):
        return (
            "Age-restricted video: a cookies.txt from a logged-in account "
            "is required.\n年齡限制影片需要登入帳號的 cookies.txt。"
        )
    if "429" in low or "too many requests" in low:
        return (
            "YouTube rate-limited this server (HTTP 429). Wait a while, "
            "use cookies.txt, or upload the file."
        )
    if "private video" in low:
        return "This video is private / 此影片為私人影片"
    if "unavailable" in low or "removed" in low:
        return "This video is unavailable / 此影片無法使用"
    if "requested format is not available" in low:
        return (
            "No downloadable format was found (possibly needs a JS runtime "
            "or cookies). Run `pip install deno` and restart, or upload the "
            "file."
        )
    return "YouTube import failed: " + text[-700:]


def _yt_clean(directory):
    for path in directory.glob("youtube.*"):
        try:
            path.unlink()
        except OSError:
            pass


def _yt_attempt(base, extra, url, directory, emit):
    cmd = base + extra

    emit("Reading YouTube information / 讀取 YouTube 資訊")
    try:
        raw = command(
            cmd + ["--dump-single-json", "--skip-download", url],
            timeout=180,
        )
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Could not read YouTube metadata") from None
    except RuntimeError as exc:
        if any(m in str(exc).lower() for m in FATAL_MARKERS):
            raise YouTubeFatal(str(exc)) from None
        raise

    if metadata.get("is_live") or metadata.get("live_status") in {
        "is_live", "is_upcoming"
    }:
        raise YouTubeLimit("Live streams are not supported / 不支援直播")

    duration = metadata.get("duration")
    if not duration:
        raise YouTubeLimit("Could not determine video length")
    if duration > YT_MAX_SECONDS:
        raise YouTubeLimit(
            f"Video is longer than {YT_MAX_SECONDS // 60} minutes / "
            f"影片超過 {YT_MAX_SECONDS // 60} 分鐘"
        )

    emit(
        f"Downloading: {str(metadata.get('title', ''))[:60]} "
        f"({duration:.0f}s) / 下載中"
    )

    _yt_clean(directory)

    command(cmd + [
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--max-filesize", "500M",
        "-o", str(directory / "youtube.%(ext)s"),
        url,
    ])

    candidates = [
        p for p in directory.glob("youtube.*")
        if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
        and p.stat().st_size > 0
    ]
    if not candidates:
        raise RuntimeError(
            "Download produced no file (it may exceed 500 MB)."
        )

    return max(candidates, key=lambda p: p.stat().st_size)


def youtube_download(url, directory, emit, cookies=None):
    url = (url or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if (
        parsed.scheme not in {"http", "https"}
        or not (
            host in {"youtube.com", "youtu.be"}
            or host.endswith(".youtube.com")
        )
    ):
        raise ValueError("Enter a valid YouTube URL / 請輸入有效的 YouTube 網址")

    emit(YT_NOTICE)

    base = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config", "--no-playlist", "--no-progress",
        "--no-warnings", "--socket-timeout", "30", "--retries", "3",
    ]

    runtimes = find_js_runtimes()
    if runtimes:
        base += runtimes
    else:
        emit(
            "No JS runtime (deno/node) found. YouTube may fail; "
            "run `pip install deno` or upload a local file."
        )

    if cookies:
        base += ["--cookies", str(cookies)]

    attempts = [
        ("standard", []),
        ("alternate clients", [
            "--extractor-args", "youtube:player_client=default,mweb,tv",
        ]),
        ("IPv4 + remote solver", [
            "--force-ipv4",
            "--remote-components", "ejs:github",
            "--extractor-args", "youtube:player_client=tv",
        ]),
    ]

    last_error = None

    for round_number in range(2):
        for name, extra in attempts:
            try:
                emit(f"YouTube strategy: {name} / 嘗試方式")
                return _yt_attempt(base, extra, url, directory, emit)
            except (YouTubeLimit, ValueError):
                raise
            except YouTubeFatal as exc:
                raise RuntimeError(
                    friendly_youtube_error(str(exc), bool(cookies))
                ) from None
            except Exception as exc:
                last_error = str(exc)
                emit(f"Strategy '{name}' failed: {last_error[-180:]}")

        if round_number == 0:
            if not update_ytdlp(emit):
                break
            emit("Retrying with updated yt-dlp / 使用新版重試")

    raise RuntimeError(
        friendly_youtube_error(last_error or "unknown error", bool(cookies))
    )


# ============================================================
# Video preparation
# ============================================================

def prepare(job, config, emit):
    start = float(config["start"] or 0)
    length = float(config["length"] or 20)

    if not math.isfinite(start) or start < 0:
        raise ValueError("Invalid start time")
    if not math.isfinite(length) or not 5 <= length <= 120:
        raise ValueError("Clip duration must be 5–120 seconds")

    directory = Path(tempfile.mkdtemp(prefix="job_", dir=WORK))
    job["directory"] = str(directory)

    if config["input_mode"] == "YouTube":
        cookie_file = None
        if config.get("cookies"):
            src = Path(config["cookies"])
            if src.is_file() and src.stat().st_size < 2 * 1024 * 1024:
                cookie_file = directory / "cookies.txt"
                shutil.copyfile(src, cookie_file)
                emit("Using uploaded cookies / 使用上傳的 cookies")
        try:
            source = youtube_download(
                config["url"], directory, emit, cookie_file
            )
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}\n\n{YT_NOTICE}") from None
        finally:
            # Never keep login cookies on disk after the download.
            if cookie_file and cookie_file.exists():
                cookie_file.unlink()
    else:
        if not config["upload"]:
            raise ValueError("Upload a video first / 請先上傳影片")
        source = Path(config["upload"])

    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("Source must be a file under 500 MB")

    info = probe(source)
    types = {stream.get("codec_type") for stream in info["streams"]}
    if not {"video", "audio"}.issubset(types):
        raise ValueError("Source must contain video and audio")

    duration = float(info["format"]["duration"])
    if start >= duration:
        raise ValueError("Start time exceeds source duration")
    length = min(length, duration - start)

    width, height = (
        (854, 480) if config["quality"].startswith("480") else (1280, 720)
    )

    clip = directory / "original.mp4"
    audio = directory / "original.m4a"

    emit("Preparing H.264 clip / 準備 H.264 影片片段")

    vf = (
        f"scale=w='min({width},iw)':h='min({height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,"
        "fps=30,setsar=1"
    )

    ffmpeg([
        "-ss", str(start),
        "-protocol_whitelist", "file,pipe",
        "-i", source,
        "-t", str(length),
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "26" if height == 480 else "24",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        clip,
    ])

    ffmpeg([
        "-i", clip, "-map", "0:a:0",
        "-vn", "-c:a", "copy", audio,
    ])

    job.update({
        "clip": str(clip),
        "source_audio": str(audio),
        "duration": media_duration(clip),
        "source_language": (
            None if config["source"] == AUTO
            else LANGUAGES[config["source"]].split("-")[0]
        ),
        "target_language": LANGUAGES[config["target"]],
        "original_rows": [],
        "translated_rows": [],
    })
    save_job(job)


# ============================================================
# ElevenLabs (dubbing mode only)
# ============================================================

def api_session(key):
    session = requests.Session()
    session.headers["xi-api-key"] = key

    retries = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def api_request(session, method, path, **kwargs):
    try:
        response = session.request(
            method, API + path,
            timeout=(30, 300),
            **kwargs,
        )
    except requests.RequestException:
        extra = (
            " The server may already have accepted the project. "
            "Check ElevenLabs before creating another."
            if method == "POST" else ""
        )
        raise RuntimeError("ElevenLabs connection failed." + extra) from None

    if not response.ok:
        raise RuntimeError(
            f"ElevenLabs HTTP {response.status_code}: "
            + response.text[:1200]
        )

    return response.json()


def download_audio(url, destination, emit):
    if urlparse(url).scheme != "https":
        raise RuntimeError("Unexpected audio download URL")

    # Never forward the ElevenLabs API key to a storage URL.
    try:
        with requests.get(
            url, stream=True, timeout=(30, 180)
        ) as response:
            response.raise_for_status()
            received = 0
            last_report = 0

            with open(destination, "wb") as file:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue

                    received += len(chunk)
                    if received > 1024 * 1024 * 1024:
                        raise RuntimeError("Downloaded audio exceeds 1 GB")
                    file.write(chunk)

                    if time.monotonic() - last_report > 2:
                        emit(
                            "Downloading dub / 下載配音："
                            f"{received / 1024 / 1024:.1f} MB"
                        )
                        last_report = time.monotonic()

    except requests.RequestException:
        raise RuntimeError(
            "Audio download failed. Use Resume instead of creating a new dub."
        ) from None


def dub(job, config, emit, create=False):
    key = (config["key"] or "").strip()
    if not key:
        raise ValueError("Enter your ElevenLabs API key / 請輸入 API 金鑰")

    with api_session(key) as session:
        if create:
            emit("Creating paid dubbing project / 建立配音專案，將使用額度")

            data = {
                "reference": "video dubber and subtitle editor",
                "model_id": "dubbing_v2",
                "target_language": job["target_language"],
            }
            if job["source_language"]:
                data["source_language"] = job["source_language"]

            with open(job["source_audio"], "rb") as audio:
                result = api_request(
                    session, "POST", "/dubbing/project",
                    data=data,
                    files={"file": ("audio.m4a", audio, "audio/mp4")},
                )

            job["project_id"] = result["project_id"]
            ids = result.get("language_ids") or []
            if ids:
                job["language_id"] = ids[0]

            save_job(job)
            emit("Project ID / 專案 ID: " + job["project_id"])

        if not job.get("project_id"):
            raise ValueError("No project to resume in this session")

        if job.get("dub_video") and Path(job["dub_video"]).exists():
            emit("Using existing dub / 使用已完成的配音")
            return

        pid = job["project_id"]
        deadline = time.monotonic() + 1800
        previous = None
        language = None

        while time.monotonic() < deadline:
            project = api_request(
                session, "GET", f"/dubbing/project/{pid}"
            )

            if project.get("status") == "failed":
                raise RuntimeError(
                    "Dubbing project failed: " + str(project.get("error"))
                )

            if not job.get("language_id"):
                ids = project.get("language_ids") or []
                if ids:
                    job["language_id"] = ids[0]
                    save_job(job)

            status = "waiting"
            if job.get("language_id"):
                lid = job["language_id"]
                language = api_request(
                    session, "GET",
                    f"/dubbing/project/{pid}/language/{lid}",
                )
                status = language.get("status", "unknown")

            stage = f"Source: {project.get('status')} | Dub: {status}"
            if stage != previous:
                emit(stage)
                previous = stage

            if status == "completed":
                break

            if status in {"failed", "stale"}:
                raise RuntimeError(
                    f"Dub is {status}: {language.get('error')}. "
                    "Resume does not regenerate failed/stale targets."
                )

            time.sleep(8)
        else:
            raise TimeoutError(
                "Cloud job may still be running. Use Resume / 請按繼續查詢。"
            )

        url = (language.get("outputs") or {}).get("lossless_audio")
        if not url:
            raise RuntimeError("Completed dub has no audio URL")

        directory = Path(job["directory"])
        dubbed_audio = directory / "dubbed_audio.bin"
        download_audio(url, dubbed_audio, emit)

        difference = abs(
            media_duration(dubbed_audio) - job["duration"]
        )
        if difference > 1:
            emit(
                "Warning: audio/video durations differ. "
                "Audio will be trimmed or padded to the video duration."
            )

        output = directory / "dubbed.mp4"
        emit("Fast video assembly / 快速合併配音影片")

        ffmpeg([
            "-i", job["clip"], "-i", dubbed_audio,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-af", "apad",
            "-t", str(job["duration"]),
            "-movflags", "+faststart",
            output,
        ])

        probe(output)
        job["dub_video"] = str(output)
        save_job(job)


# ============================================================
# Whisper (faster-whisper): no HF token needed; GPU when available
# ============================================================

def cuda_available():
    if FORCE_CPU:
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def load_audio(path, sample_rate=16000):
    """Decode any media file to a mono float32 numpy array using FFmpeg.

    This deliberately bypasses faster-whisper's own decoder (PyAV), which
    crashes with "open() got an unexpected keyword argument
    'metadata_errors'" when the installed `av` package is too old.
    """
    import numpy as np

    try:
        p = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error",
                "-protocol_whitelist", "file,pipe",
                "-i", str(path),
                "-vn", "-map", "0:a:0",
                "-ac", "1", "-ar", str(sample_rate),
                "-f", "f32le", "-",
            ],
            capture_output=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Audio decoding timed out") from None

    if p.returncode:
        raise RuntimeError(
            "Could not decode audio: "
            + p.stderr.decode("utf-8", errors="replace")[-600:]
        )

    audio = np.frombuffer(p.stdout, dtype=np.float32).copy()
    if audio.size == 0:
        raise RuntimeError("The media file contains no audio samples")

    return audio


def get_whisper(name, emit):
    """Load (or reuse) a Whisper model. GPU first, then CPU int8.
    Models download anonymously from the HF Hub; no token required."""
    global WHISPER_MODEL, WHISPER_KEY

    name = name if name in WHISPER_MODELS else DEFAULT_WHISPER
    use_cuda = cuda_available()
    wanted = (name, "cuda" if use_cuda else "cpu")

    if WHISPER_MODEL is not None and WHISPER_KEY == wanted:
        return WHISPER_MODEL

    WHISPER_MODEL = None
    WHISPER_KEY = None
    gc.collect()

    emit(
        f"Loading Whisper '{name}' on {wanted[1].upper()}; "
        "first run downloads the model (no token needed) / "
        "載入辨識模型，首次執行需要下載（不需要 token）"
    )
    from faster_whisper import WhisperModel

    attempts = []
    if use_cuda:
        attempts += [("cuda", "float16"), ("cuda", "int8_float16")]
    attempts.append(("cpu", "int8"))

    last_error = None
    for device, compute_type in attempts:
        try:
            model = WhisperModel(
                name,
                device=device,
                compute_type=compute_type,
                cpu_threads=min(4, os.cpu_count() or 2),
            )
            WHISPER_MODEL = model
            WHISPER_KEY = (name, device)
            emit(f"Whisper ready: {name} / {device} / {compute_type}")
            return model
        except Exception as exc:
            last_error = exc
            emit(f"Whisper {device}/{compute_type} unavailable: {str(exc)[:160]}")

    raise RuntimeError(
        f"Could not load Whisper '{name}': {last_error}\n"
        "If this is a download/rate-limit error, wait a bit or choose a "
        "smaller model (tiny/base/small)."
    )


def _transcribe_once(path, language, traditional, emit, model_name, task):
    model = get_whisper(model_name, emit)
    language = language.split("-")[0] if language else None

    # Decode with FFmpeg ourselves (avoids the PyAV 'metadata_errors' bug).
    emit("Decoding audio with FFmpeg / 使用 FFmpeg 解碼音訊")
    samples = load_audio(path)

    segments, info = model.transcribe(
        samples,
        language=language,
        task=task,
        beam_size=3,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    detected = info.language
    max_chars = 20 if detected in CJK else 42
    label = "Translating" if task == "translate" else "Transcribing"

    rows = []
    for segment in segments:
        words = list(segment.words or [])
        groups = []
        current = []

        for word in words:
            current_text = "".join(w.word for w in current)
            if current and (
                len(current_text) + len(word.word) > max_chars
                or word.end - current[0].start > 4.5
            ):
                groups.append(current)
                current = []
            current.append(word)

        if current:
            groups.append(current)

        if groups:
            entries = [
                [g[0].start, g[-1].end, "".join(w.word for w in g)]
                for g in groups
            ]
        else:
            entries = [[segment.start, segment.end, segment.text]]

        for start, end, text in entries:
            text = plain(text)
            if traditional and detected == "zh" and task == "transcribe":
                text = CONVERTER.convert(text)
            if text and end > start:
                rows.append([round(start, 3), round(end, 3), text])

        emit(f"{label} / 辨識中：{segment.end:.1f}s ({detected})")

    return rows, detected


def transcribe(path, language, traditional, emit,
               model_name=DEFAULT_WHISPER, task="transcribe"):
    """Returns (rows, detected_language). Retries on CPU if CUDA fails."""
    global FORCE_CPU, WHISPER_MODEL, WHISPER_KEY

    try:
        return _transcribe_once(
            path, language, traditional, emit, model_name, task
        )
    except Exception as exc:
        text = str(exc).lower()
        cuda_problem = any(
            token in text
            for token in ("cuda", "cudnn", "cublas", "libcu", "out of memory")
        )
        if WHISPER_KEY and WHISPER_KEY[1] == "cuda" and cuda_problem:
            emit(
                "GPU failed, retrying on CPU / GPU 失敗，改用 CPU："
                + str(exc)[:160]
            )
            FORCE_CPU = True
            WHISPER_MODEL = None
            WHISPER_KEY = None
            gc.collect()
            return _transcribe_once(
                path, language, traditional, emit, model_name, task
            )
        raise


def generate_subtitles(job, config, emit,
                       do_original=True, do_translated=True):
    model_name = config.get("whisper_model") or DEFAULT_WHISPER
    traditional = config["traditional"]

    if do_original:
        emit("Generating ORIGINAL captions / 產生原文字幕")

        rows, detected = transcribe(
            job["clip"],
            job.get("source_language"),
            traditional,
            emit,
            model_name,
        )
        job["original_rows"] = validate_rows(rows, job["duration"])
        job["detected_language"] = detected
        save_job(job)

    if not do_translated:
        return

    target = (job.get("target_language") or "").split("-")[0]

    if job.get("dub_video"):
        emit("Generating TRANSLATED captions from dub / 從配音產生翻譯字幕")

        rows, _ = transcribe(
            job["dub_video"],
            job["target_language"],
            traditional,
            emit,
            model_name,
        )
        job["translated_rows"] = validate_rows(rows, job["duration"])
        save_job(job)

    elif target == "en":
        detected = job.get("detected_language") or job.get("source_language")
        if detected == "en":
            emit(
                "Source is already English; skipping translation / "
                "來源已是英文，略過翻譯"
            )
            return

        emit(
            "Whisper translate → English captions (no dub) / "
            "Whisper 翻譯成英文字幕（無配音）"
        )
        rows, _ = transcribe(
            job["clip"],
            job.get("source_language"),
            False,
            emit,
            model_name,
            task="translate",
        )
        job["translated_rows"] = validate_rows(rows, job["duration"])
        save_job(job)

    else:
        emit(
            "Free subtitles-only mode can translate into English only. "
            "Original captions were created; translated captions skipped. / "
            "免費僅字幕模式只能翻譯成英文；已產生原文字幕，略過翻譯字幕。"
        )


# ============================================================
# Subtitle import, combination, and export
# ============================================================

def import_srt(path):
    if not path:
        raise gr.Error("Upload an SRT file / 請上傳 SRT 檔案")
    if Path(path).stat().st_size > 2 * 1024 * 1024:
        raise gr.Error("SRT limit: 2 MB")

    try:
        content = Path(path).read_text(encoding="utf-8-sig")
        rows = [
            [
                item.start.total_seconds(),
                item.end.total_seconds(),
                plain(item.content),
            ]
            for item in srt.parse(content)
        ]
        return validate_rows(rows)
    except Exception as exc:
        raise gr.Error(f"Invalid UTF-8 SRT: {exc}") from None


def combine_rows(original, translated, mode, duration):
    if mode.startswith("None"):
        return []

    if mode.startswith("Original"):
        selected = validate_rows(original, duration)
    elif mode.startswith("Translated"):
        selected = validate_rows(translated, duration)
    else:
        selected = (
            validate_rows(original, duration)
            + validate_rows(translated, duration)
        )

    if not selected:
        raise ValueError(
            "No subtitles for the selected mode. Generate/import them first.\n"
            "所選模式沒有字幕，請先產生或匯入。"
        )

    points = sorted({t for row in selected for t in row[:2]})
    result = []

    for start, end in zip(points, points[1:]):
        text = "\n".join(
            row[2] for row in selected
            if row[0] < end and row[1] > start
        )
        if not text:
            continue

        if result and result[-1][1] == start and result[-1][2] == text:
            result[-1][1] = end
        else:
            result.append([start, end, text])

    return result


def write_srt(path, rows):
    entries = [
        srt.Subtitle(
            index=i,
            start=timedelta(seconds=start),
            end=timedelta(seconds=end),
            content=text,
        )
        for i, (start, end, text) in enumerate(rows, 1)
    ]
    path.write_text(srt.compose(entries), encoding="utf-8")


def stamp(seconds, separator="."):
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return (
        f"{hours:02}:{minutes:02}:{seconds:02}"
        f"{separator}{milliseconds:03}"
    )


def write_vtt(path, rows):
    blocks = [
        f"{stamp(start)} --> {stamp(end)}\n{html.escape(text, quote=False)}"
        for start, end, text in rows
    ]
    path.write_text(
        "WEBVTT\n\n" + "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )


def ass_time(seconds):
    centiseconds = round(seconds * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def write_ass(path, rows, width, height, size):
    effective_size = max(12, round(float(size) * height / 720))
    margin = max(12, round(height * 0.045))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK TC,{effective_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,{margin},{margin},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for start, end, text in rows:
        text = (
            text.replace("\\", "＼")
            .replace("{", "｛")
            .replace("}", "｝")
            .replace("\n", r"\N")
        )
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
            f"Default,,0,0,0,,{text}"
        )

    path.write_text(header + "\n".join(lines), encoding="utf-8")


def export_job(job, config, emit):
    video = selected_video(job, config["audio_mode"])
    duration = job["duration"]

    original = validate_rows(config["original_rows"], duration)
    translated = validate_rows(config["translated_rows"], duration)

    job["original_rows"] = original
    job["translated_rows"] = translated
    save_job(job)

    rows = combine_rows(
        original, translated, config["subtitle_mode"], duration
    )

    directory = Path(job["directory"]) / ("export_" + uuid.uuid4().hex[:10])
    directory.mkdir()
    output = directory / "video.mp4"
    files = []

    for name, table in [
        ("original", original),
        ("translated", translated),
        ("selected", rows),
    ]:
        if table:
            path = directory / f"{name}.srt"
            write_srt(path, table)
            files.append(str(path))

    if rows:
        vtt = directory / "selected.vtt"
        write_vtt(vtt, rows)
        files.append(str(vtt))

    edits = directory / "subtitle_edits.json"
    edits.write_text(json.dumps({
        "original": original,
        "translated": translated,
        "times_relative_to": "prepared clip",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(str(edits))

    if not rows:
        emit("Copying clean video / 複製無字幕影片")
        shutil.copyfile(video, output)

    elif config["export_mode"].startswith("Soft"):
        emit("Fast export: selectable subtitles / 快速匯出可關閉字幕")

        ffmpeg([
            "-i", video, "-i", directory / "selected.srt",
            "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0",
            "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=und",
            "-metadata:s:s:0", "title=Subtitles",
            "-disposition:s:0", "default",
            "-movflags", "+faststart",
            output,
        ])

    else:
        emit("Burning subtitles: encoding video / 燒錄字幕，正在編碼影像")

        stream = next(
            x for x in probe(video)["streams"]
            if x["codec_type"] == "video"
        )
        write_ass(
            directory / "captions.ass",
            rows,
            stream["width"],
            stream["height"],
            config["font_size"],
        )

        ffmpeg([
            "-i", video,
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "ass=captions.ass",
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "24", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output,
        ], cwd=directory)

    probe(output)
    files.insert(0, str(output))
    emit(f"Export ready / 匯出完成：{output.stat().st_size / 1048576:.1f} MB")
    return files


# ============================================================
# Background tasks and responsive status
# ============================================================

def task_worker(action, config, job, events):
    acquired = False

    def emit(message):
        events.put(("progress", message, copy.deepcopy(job), None))

    try:
        acquired = LOCK.acquire(blocking=False)
        if not acquired:
            raise RuntimeError(
                "Another task is still running / 另一項工作仍在執行"
            )

        files = None

        if action in {"prepare", "dub"}:
            if not config["consent"]:
                if action == "dub":
                    raise ValueError(
                        "Confirm permission and any API charges first.\n"
                        "請先確認內容授權及可能產生的 API 費用。"
                    )
                raise ValueError(
                    "Confirm you have permission to use this content first.\n"
                    "請先確認您擁有此內容的使用授權。"
                )

            if action == "dub":
                if not (config["key"] or "").strip():
                    raise ValueError("Enter your API key first")
                source = (
                    None if config["source"] == AUTO
                    else LANGUAGES[config["source"]].split("-")[0]
                )
                target = LANGUAGES[config["target"]].split("-")[0]
                if source == target:
                    raise ValueError("Select a different target language")

            prepare(job, config, emit)
            emit("Prepared clip saved / 影片片段已儲存")

            if action == "dub":
                dub(job, config, emit, create=True)

            if config["auto_subtitles"]:
                generate_subtitles(job, config, emit)

        elif action == "resume":
            if not job.get("clip"):
                raise ValueError("No session to resume")
            dub(job, config, emit)

            if config["auto_subtitles"]:
                need_original = not job.get("original_rows")
                need_translated = not job.get("translated_rows")
                if need_original or need_translated:
                    generate_subtitles(
                        job, config, emit,
                        do_original=need_original,
                        do_translated=need_translated,
                    )

        elif action == "captions":
            if not job.get("clip"):
                raise ValueError("Prepare a clip first")
            generate_subtitles(job, config, emit)

        elif action == "export":
            if not job.get("clip"):
                raise ValueError("Prepare a clip first")
            files = export_job(job, config, emit)

        save_job(job)
        events.put(("done", "Ready / 完成", copy.deepcopy(job), files))

    except Exception as exc:
        message = str(exc)
        key = (config.get("key") or "").strip()
        if key:
            message = message.replace(key, "[REDACTED]")
        if job.get("project_id"):
            message += "\nProject ID: " + job["project_id"]

        events.put(("error", message, copy.deepcopy(job), None))

    finally:
        if acquired:
            LOCK.release()


def status_card(message, elapsed, running=True, failed=False):
    icon = (
        '<div class="spinner"></div>'
        if running
        else f'<div>{"⚠️" if failed else "✅"}</div>'
    )
    bar = '<div class="activity"><span></span></div>' if running else ""

    return (
        '<div class="status-card"><div class="status-row">'
        + icon
        + "<div><strong>"
        + html.escape(message).replace("\n", "<br>")
        + f"</strong><br><small>{elapsed:.0f}s / 秒</small></div>"
        + "</div>" + bar + "</div>"
    )


INPUT_NAMES = [
    "workflow", "key", "input_mode", "upload", "url", "cookies",
    "source", "target", "start", "length", "quality",
    "auto_subtitles", "traditional", "whisper_model",
    "consent", "audio_mode", "subtitle_mode", "export_mode",
    "font_size", "original_rows", "translated_rows", "state",
]

BUTTON_COUNT = 4  # start, resume, captions, export


def run_ui(action, *values):
    config = dict(zip(INPUT_NAMES, values))
    subtitles_only = (config["workflow"] or "").startswith("Subtitles only")

    # The Start button picks its action from the selected workflow.
    if action == "start":
        if subtitles_only:
            action = "prepare"
            config["auto_subtitles"] = True   # captions are the whole point
        else:
            action = "dub"

    new_job = action in {"prepare", "dub"}
    job = {} if new_job else copy.deepcopy(config["state"] or {})

    if not new_job:
        job["original_rows"] = config["original_rows"] or []
        job["translated_rows"] = config["translated_rows"] or []

    events = queue.Queue()
    started = time.monotonic()
    logs = []
    message = "Starting / 開始處理"

    disabled = [gr.update(interactive=False) for _ in range(BUTTON_COUNT)]
    enabled = [gr.update(interactive=True) for _ in range(BUTTON_COUNT)]

    yield (
        status_card(message, 0),
        "",
        job,
        gr.update(
            value=job.get("original_rows", []), interactive=False
        ),
        gr.update(
            value=job.get("translated_rows", []), interactive=False
        ),
        None if new_job else gr.skip(),
        None,
        None if new_job else gr.skip(),
        *disabled,
    )

    threading.Thread(
        target=task_worker,
        args=(action, config, job, events),
        daemon=True,
    ).start()

    while True:
        try:
            kind, message, snapshot, files = events.get(timeout=1)
            elapsed = time.monotonic() - started
            logs.append(f"[{elapsed:6.1f}s] {message}")
        except queue.Empty:
            yield (
                status_card(message, time.monotonic() - started),
                *[gr.skip() for _ in range(7 + BUTTON_COUNT)],
            )
            continue

        finished = kind in {"done", "error"}
        if not finished:
            yield (
                status_card(message, elapsed),
                "\n".join(logs[-100:]),
                snapshot,
                *[gr.skip() for _ in range(5 + BUTTON_COUNT)],
            )
            continue

        base_file = snapshot.get("dub_video") or snapshot.get("clip")

        yield (
            status_card(
                message, elapsed, running=False, failed=kind == "error"
            ),
            "\n".join(logs[-100:]),
            snapshot,
            gr.update(
                value=snapshot.get("original_rows", []), interactive=True
            ),
            gr.update(
                value=snapshot.get("translated_rows", []), interactive=True
            ),
            base_file,
            files,
            gr.skip(),
            *enabled,
        )
        break


def load_preview(job, audio_mode):
    try:
        return selected_video(job or {}, audio_mode)
    except Exception as exc:
        raise gr.Error(str(exc)) from None


def apply_workflow(workflow):
    """Show/hide controls and set sensible defaults for the chosen mode."""
    if workflow.startswith("Subtitles only"):
        return (
            gr.update(visible=False),                       # API key
            gr.update(
                label=(
                    "I have permission to use this content. / "
                    "我已取得此內容的使用授權。"
                )
            ),                                              # consent
            gr.update(
                value="Start subtitling (free) / 開始製作字幕（免費）"
            ),                                              # start button
            gr.update(visible=False),                       # resume button
            gr.update(value=AUDIO_MODES[1]),                # original audio
            gr.update(value=SUB_MODES[1]),                  # original subs
            gr.update(
                label=(
                    "Subtitle translation language / 字幕翻譯語言 "
                    "(free: English only / 免費僅支援英文)"
                )
            ),                                              # target
        )

    return (
        gr.update(visible=True),
        gr.update(
            label=(
                "I have content/voice permission and accept API charges "
                "when dubbing. / 我已取得內容與聲音授權，並同意配音 API 費用。"
            )
        ),
        gr.update(
            value="Start dubbing + subtitles / 開始配音＋字幕"
        ),
        gr.update(visible=True),
        gr.update(value=AUDIO_MODES[0]),
        gr.update(value=SUB_MODES[0]),
        gr.update(label="Dub language / 配音語言"),
    )


# ============================================================
# Gradio UI
# ============================================================

def build_app():
    with gr.Blocks(
        title="Video Dubber + Live Subtitle Editor",
        analytics_enabled=False,
        delete_cache=(3600, 86400),
    ) as app:

        gr.Markdown("""
# 🎬 Video Dubber + Subtitle Editor
## 影片配音與即時字幕編輯

Choose a workflow: **Dubbing + subtitles** (paid ElevenLabs key) or
**Subtitles only** (free, local Whisper, no API key).
Whisper needs **no Hugging Face token**.  
請選擇模式：**配音＋字幕**（需 ElevenLabs 金鑰，付費）或
**僅字幕**（免費、本機 Whisper、不需金鑰）。Whisper **不需要 Hugging Face token**。

**Editing subtitles does not change spoken audio.**  
**修改字幕不會重新生成語音。**

Live preview updates after a cell edit is committed.
Click **Export** to save changes into a new video.  
完成儲存格編輯後，預覽字幕會更新；請按「匯出」產生新影片。
""")

        state = gr.State({})

        with gr.Accordion("1. Source and mode / 來源與模式", open=True):
            workflow = gr.Radio(
                WORKFLOWS,
                value=WORKFLOW_DUB,
                label="Workflow / 工作模式",
            )
            key = gr.Textbox(
                label="ElevenLabs API key / API 金鑰 (only for dubbing / 僅配音需要)",
                type="password",
            )
            input_mode = gr.Radio(
                ["Upload / 上傳", "YouTube"],
                value="Upload / 上傳",
                label="Input / 輸入來源",
            )

            with gr.Row():
                upload = gr.File(
                    label="Video / 影片 — maximum 500 MB",
                    type="filepath",
                    file_types=[".mp4", ".mov", ".mkv", ".webm", ".avi"],
                )
                url = gr.Textbox(label="YouTube URL / 網址")

            gr.Markdown(
                "⚠️ **YouTube download may not always work.** YouTube often "
                "blocks cloud servers such as Colab. If it fails, download "
                "the video yourself and use **Upload** instead.  \n"
                "⚠️ **YouTube 下載不一定能成功。** YouTube 常封鎖 Colab 等雲端伺服器，"
                "若失敗，請自行下載影片後改用「上傳」。",
                elem_classes="yt-warning",
            )

            cookies = gr.File(
                label=(
                    "Optional cookies.txt for YouTube (Netscape format) / "
                    "YouTube 選用 cookies.txt — use if you get a bot-check error"
                ),
                type="filepath",
                file_types=[".txt"],
            )
            gr.Markdown(
                "Cookies are used only for this download and deleted right "
                "after. Public share links have no privacy guarantee, so "
                "use cookies from a throwaway account if possible.  \n"
                "Cookies 僅用於本次下載，完成後立即刪除。公開連結無隱私保證，"
                "建議使用備用帳號的 cookies。"
            )

            with gr.Row():
                source = gr.Dropdown(
                    [AUTO] + list(LANGUAGES),
                    value=AUTO,
                    label="Source language / 原始語言",
                )
                target = gr.Dropdown(
                    list(LANGUAGES),
                    value="Taiwan Mandarin / 臺灣華語",
                    label="Dub language / 配音語言",
                )

            with gr.Row():
                start = gr.Number(
                    value=0, minimum=0,
                    label="Source start seconds / 原始影片開始秒數",
                )
                length = gr.Slider(
                    5, 120, value=20, step=1,
                    label="Clip duration / 片段長度（秒）",
                )
                quality = gr.Dropdown(
                    ["480p / Smaller", "720p / Clearer"],
                    value="720p / Clearer",
                    label="Video size / 畫質",
                )

            with gr.Row():
                whisper_model = gr.Dropdown(
                    WHISPER_MODELS,
                    value=DEFAULT_WHISPER,
                    label="Whisper model / 辨識模型 (GPU auto-detected / 自動偵測 GPU)",
                    info="tiny = fastest · large-v3-turbo = most accurate · no HF token needed",
                )

            auto_subtitles = gr.Checkbox(
                value=True,
                label="Generate captions automatically / 自動產生字幕 (always on in subtitles-only mode / 僅字幕模式一律開啟)",
            )
            traditional = gr.Checkbox(
                value=True,
                label="Convert recognized Chinese to Traditional / 中文辨識轉繁體",
            )
            consent = gr.Checkbox(
                value=False,
                label=(
                    "I have content/voice permission and accept API charges "
                    "when dubbing. / 我已取得內容與聲音授權，並同意配音 API 費用。"
                ),
            )

            with gr.Row():
                start_button = gr.Button(
                    "Start dubbing + subtitles / 開始配音＋字幕",
                    variant="primary",
                )
                resume_button = gr.Button("Resume dub / 繼續查詢配音")

        status = gr.HTML("<p>Ready / 準備就緒</p>")
        log = gr.Textbox(
            label="Activity / 處理紀錄",
            lines=7,
            interactive=False,
        )
        clean_download = gr.File(
            label="Prepared video without added subtitles / 未加字幕的影片"
        )

        gr.Markdown("""
## 2. Live subtitle editor / 即時字幕編輯

Times are **seconds relative to the prepared clip**, not the original full video.  
時間以**裁切後片段的秒數**計算，而非完整原始影片時間。

Use Enter or click outside a cell to commit an edit.
Add/delete rows using the table controls. Empty text rows are ignored.  
按 Enter 或點選儲存格外完成編輯；可新增或刪除字幕列，空白文字列會被忽略。

Automatic captions can contain recognition and timing errors. Review them.  
自動字幕可能有辨識及時間誤差，請自行檢查。
""")

        with gr.Row():
            audio_mode = gr.Radio(
                AUDIO_MODES,
                value=AUDIO_MODES[0],
                label="Preview/export audio / 預覽及匯出音訊",
            )
            subtitle_mode = gr.Radio(
                SUB_MODES,
                value=SUB_MODES[0],
                label="Subtitle display / 字幕顯示",
            )

        preview_button = gr.Button("Load / switch preview · 載入／切換預覽")
        preview = gr.Video(
            label="Live subtitle preview / 即時字幕預覽",
            elem_id="editor-video",
            interactive=False,
        )

        with gr.Tabs():
            with gr.Tab("Original / 原文"):
                original_rows = gr.Dataframe(
                    headers=["Start / 開始", "End / 結束", "Text / 文字"],
                    datatype=["number", "number", "str"],
                    type="array",
                    value=[],
                    row_count=(0, "dynamic"),
                    column_count=(3, "fixed"),
                    interactive=True,
                    label="Original captions / 原文字幕",
                )
                original_srt = gr.File(
                    label="Import UTF-8 original SRT / 匯入原文 SRT",
                    file_types=[".srt"],
                    type="filepath",
                )

            with gr.Tab("Translated / 翻譯"):
                translated_rows = gr.Dataframe(
                    headers=["Start / 開始", "End / 結束", "Text / 文字"],
                    datatype=["number", "number", "str"],
                    type="array",
                    value=[],
                    row_count=(0, "dynamic"),
                    column_count=(3, "fixed"),
                    interactive=True,
                    label="Translated captions / 翻譯字幕",
                )
                translated_srt = gr.File(
                    label="Import UTF-8 translated SRT / 匯入翻譯 SRT",
                    file_types=[".srt"],
                    type="filepath",
                )

        captions_button = gr.Button(
            "Regenerate captions — replaces tables / 重新辨識字幕，取代表格"
        )

        gr.Markdown("""
## 3. Export / 匯出

**Soft subtitles:** fastest, selectable in supporting players.  
**可關閉字幕：** 較快，需播放器支援字幕軌。

**Burn in:** permanently visible; requires video encoding.  
**永久燒錄：** 字幕直接寫入畫面，需要重新編碼影片。

The live preview checks text/timing; burned-in styling may look different.  
即時預覽主要用於檢查文字與時間；燒錄後的字型排版可能不同。
""")

        with gr.Row():
            export_mode = gr.Radio(
                EXPORT_MODES,
                value=EXPORT_MODES[0],
                label="Export method / 匯出方式",
            )
            font_size = gr.Slider(
                18, 54, value=32, step=1,
                label="Burn-in font size at 720p / 燒錄字級（以 720p 為基準）",
            )

        export_button = gr.Button(
            "Export edited video + subtitles / 匯出影片與字幕",
            variant="primary",
        )
        downloads = gr.File(
            label="Export downloads / 匯出檔案下載",
            file_count="multiple",
        )

        gr.Markdown("""
Keep this tab and the Colab cell open while processing.
Download files before the runtime ends.  
處理時請保持分頁及 Colab 儲存格執行，並於執行階段結束前下載檔案。

Public share links have **no login or per-user file privacy guarantee**.
Use only trusted instances and avoid sensitive media.  
公開分享連結**沒有登入及個別使用者檔案隱私保證**，請勿上傳敏感內容。
""")

        buttons = [
            start_button, resume_button, captions_button, export_button,
        ]

        # Order MUST match INPUT_NAMES.
        inputs = [
            workflow, key, input_mode, upload, url, cookies,
            source, target, start, length, quality,
            auto_subtitles, traditional, whisper_model,
            consent, audio_mode, subtitle_mode, export_mode,
            font_size, original_rows, translated_rows, state,
        ]

        outputs = [
            status, log, state, original_rows, translated_rows,
            clean_download, downloads, preview,
            *buttons,
        ]

        for button, action in zip(
            buttons, ["start", "resume", "captions", "export"]
        ):
            button.click(
                partial(run_ui, action),
                inputs=inputs,
                outputs=outputs,
                concurrency_limit=1,
                concurrency_id="heavy-work",
                trigger_mode="once",
                api_visibility="private",
                show_progress="minimal",
            )

        workflow.change(
            apply_workflow,
            inputs=workflow,
            outputs=[
                key, consent, start_button, resume_button,
                audio_mode, subtitle_mode, target,
            ],
            queue=False,
            api_visibility="private",
        )

        original_srt.upload(
            import_srt,
            inputs=original_srt,
            outputs=original_rows,
            api_visibility="private",
        )
        translated_srt.upload(
            import_srt,
            inputs=translated_srt,
            outputs=translated_rows,
            api_visibility="private",
        )

        preview_button.click(
            load_preview,
            inputs=[state, audio_mode],
            outputs=preview,
            api_visibility="private",
        ).then(
            fn=None,
            inputs=[original_rows, translated_rows, subtitle_mode],
            outputs=[],
            js=LIVE_JS,
            queue=False,
        )

        gr.on(
            triggers=[
                original_rows.change,
                translated_rows.change,
                subtitle_mode.change,
            ],
            fn=None,
            inputs=[original_rows, translated_rows, subtitle_mode],
            outputs=[],
            js=LIVE_JS,
            queue=False,
        )

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    for executable in ["ffmpeg", "ffprobe"]:
        if not shutil.which(executable):
            raise SystemExit(
                f"Missing {executable}. Install FFmpeg and add it to PATH."
            )

    if args.share or args.host != "127.0.0.1":
        print(
            "WARNING: no login is enabled. Public users can consume host "
            "CPU, storage, and bandwidth. Do not use this as private storage."
        )

    print(
        "Whisper device: "
        + ("CUDA GPU detected" if cuda_available() else "CPU (int8)")
    )
    runtimes = find_js_runtimes()
    print(
        "YouTube JS runtime: "
        + (runtimes[1] if runtimes else "NONE (pip install deno)")
    )
    print("Note: YouTube download may not always work; use Upload as a fallback.")

    app = build_app()
    app.queue(max_size=8)
    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        auth=None,
        inbrowser=not args.share,
        inline=False,
        css=CSS,
        theme=gr.themes.Soft(),
        max_file_size="500mb",
        show_error=False,
        run_history=False,
        blocked_paths=[
            str(ROOT / ".git"),
            str(ROOT / ".env"),
            str(ROOT / ".venv"),
        ],
    )


if __name__ == "__main__":
    main()
