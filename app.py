from __future__ import annotations

import argparse
import copy
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

import gradio as gr
import requests
import srt
from opencc import OpenCC
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# Configuration
# ============================================================

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)

API = "https://api.elevenlabs.io/v1"
MAX_BYTES = 500 * 1024 * 1024
LOCK = threading.Lock()

WHISPER_MODEL = None
CONVERTER = OpenCC("s2twp")

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

# Client-side caption updates.
# This does NOT replace the video src or reset currentTime.
#
# Dataframe changes are delivered after a cell edit is committed,
# e.g. Enter or clicking outside the cell.
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

    // Merge overlapping intervals into one caption.
    // Original text appears before translated text in bilingual mode.
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

    const state = window.__digimarketingaCaptions ||= {
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
    # Subtitle editing is plain text, not HTML/ASS markup editing.
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
        # API keys are never added to job.
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
        raise ValueError(
            "Selected audio is unavailable. Prepare a clip or create a dub first.\n"
            "尚無所選音訊，請先準備影片或建立配音。"
        )

    return path


# ============================================================
# Video preparation and YouTube import
# ============================================================

def youtube_download(url, directory, emit):
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

    base = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config", "--no-playlist", "--no-progress",
        "--socket-timeout", "30", "--retries", "2",
    ]

    if shutil.which("deno"):
        base += ["--js-runtimes", "deno"]
    elif shutil.which("node"):
        base += ["--js-runtimes", "node"]
    else:
        emit(
            "YouTube: no JS runtime detected; upload a local file if import fails."
        )

    emit("Reading YouTube information / 讀取 YouTube 資訊")
    metadata = json.loads(command(
        base + ["--dump-single-json", "--skip-download", url],
        timeout=180,
    ))

    if (
        metadata.get("is_live")
        or not metadata.get("duration")
        or metadata["duration"] > 1200
    ):
        raise ValueError(
            "Use a non-live YouTube video under 20 minutes."
        )

    emit("Downloading YouTube source / 下載 YouTube 原始影片")

    command(base + [
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--merge-output-format", "mp4",
        "--max-filesize", "500M",
        "-o", str(directory / "youtube.%(ext)s"),
        url,
    ])

    candidates = [
        p for p in directory.glob("youtube.*")
        if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
    ]
    if not candidates:
        raise RuntimeError(
            "YouTube import failed. Upload an authorized local video instead."
        )

    return max(candidates, key=lambda p: p.stat().st_size)


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
        source = youtube_download(config["url"], directory, emit)
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
# ElevenLabs
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
                "reference": "digimarketinga video subtitle editor",
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
# Automatic subtitles
# ============================================================

def transcribe(path, language, traditional, emit):
    global WHISPER_MODEL

    if WHISPER_MODEL is None:
        emit(
            "Loading Whisper small; first run downloads the model / "
            "載入辨識模型，首次執行需要下載"
        )
        from faster_whisper import WhisperModel

        # CPU/int8 avoids CUDA dependency problems in basic Colab runtimes.
        WHISPER_MODEL = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8",
            cpu_threads=min(4, os.cpu_count() or 2),
        )

    language = language.split("-")[0] if language else None

    segments, info = WHISPER_MODEL.transcribe(
        path,
        language=language,
        task="transcribe",
        beam_size=3,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    rows = []
    for segment in segments:
        words = list(segment.words or [])
        groups = []
        current = []

        # Shorter caption groups are easier to edit and read.
        for word in words:
            current_text = "".join(w.word for w in current)
            if current and (
                len(current_text) + len(word.word) > 42
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
            if traditional and info.language == "zh":
                text = CONVERTER.convert(text)
            if text and end > start:
                rows.append([round(start, 3), round(end, 3), text])

        emit(
            f"Transcribing / 辨識中：{segment.end:.1f}s "
            f"({info.language})"
        )

    return rows


def generate_subtitles(job, config, emit):
    emit("Generating ORIGINAL captions / 產生原文字幕")

    job["original_rows"] = validate_rows(
        transcribe(
            job["clip"],
            job.get("source_language"),
            config["traditional"],
            emit,
        ),
        job["duration"],
    )
    save_job(job)

    if job.get("dub_video"):
        emit("Generating TRANSLATED captions from dub / 從配音產生翻譯字幕")

        job["translated_rows"] = validate_rows(
            transcribe(
                job["dub_video"],
                job["target_language"],
                config["traditional"],
                emit,
            ),
            job["duration"],
        )
        save_job(job)


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
        # Prevent user text from injecting ASS styling commands.
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

        # Fixed relative filter filename avoids filter-path escaping problems.
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
                raise ValueError(
                    "Confirm permission and any API charges first.\n"
                    "請先確認內容授權及可能產生的 API 費用。"
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
            # Resume does not overwrite existing subtitle edits.
            if config["auto_subtitles"] and not job.get("translated_rows"):
                generate_subtitles(job, config, emit)

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
    "key", "input_mode", "upload", "url", "source", "target",
    "start", "length", "quality", "auto_subtitles", "traditional",
    "consent", "audio_mode", "subtitle_mode", "export_mode",
    "font_size", "original_rows", "translated_rows", "state",
]

BUTTON_COUNT = 5


def run_ui(action, *values):
    config = dict(zip(INPUT_NAMES, values))
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

    # Outputs:
    # status, log, state, original table, translated table,
    # clean video file, exported files, preview, five action buttons
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
## 影片配音與即時字幕編輯 · digimarketinga

No app login. Each user supplies their own ElevenLabs key for dubbing.  
不需登入本工具；配音時請使用您自己的 ElevenLabs API 金鑰。

**Editing subtitles does not change spoken audio.**  
**修改字幕不會重新生成語音。**

Live preview updates after a cell edit is committed.
Click **Export** to save changes into a new video.  
完成儲存格編輯後，預覽字幕會更新；請按「匯出」產生新影片。
""")

        state = gr.State({})

        with gr.Accordion("1. Source and dubbing / 來源與配音", open=True):
            key = gr.Textbox(
                label="ElevenLabs API key / API 金鑰",
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

            auto_subtitles = gr.Checkbox(
                value=True,
                label="Generate captions automatically / 自動產生字幕",
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
                prepare_button = gr.Button(
                    "Prepare original only / 僅準備原音影片"
                )
                dub_button = gr.Button(
                    "Create new dub / 建立新配音",
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
                    col_count=(3, "fixed"),
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
                    col_count=(3, "fixed"),
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
            prepare_button, dub_button, resume_button,
            captions_button, export_button,
        ]

        inputs = [
            key, input_mode, upload, url, source, target,
            start, length, quality, auto_subtitles, traditional,
            consent, audio_mode, subtitle_mode, export_mode,
            font_size, original_rows, translated_rows, state,
        ]

        outputs = [
            status, log, state, original_rows, translated_rows,
            clean_download, downloads, preview,
            *buttons,
        ]

        for button, action in zip(
            buttons, ["prepare", "dub", "resume", "captions", "export"]
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

        # Browser-only updates: no video re-encoding or re-download.
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
