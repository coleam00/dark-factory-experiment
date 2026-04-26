"""One-shot transcription runner for issue #147.

Reads `lesson_map.csv` (output of the dynamous-engine matching subagents),
downloads each Drive video, transcribes via OpenAI Whisper, writes a
markdown transcript with `## [HH:MM:SS]` segment headings under
`<output-dir>/<bucket>/<slug>.md`. Idempotent — if the output already exists
and content_hash matches the source video's md5, it's skipped.

Operator-side: not part of the deployed backend. Cole runs this locally
before committing transcripts to the private `dynamous-content` repo.

Requires:
  - ffmpeg on PATH (used to extract a Whisper-friendly audio file from the
    raw MP4/MOV — Whisper has a 25 MB file-size limit; raw videos are 100+ MB)
  - OPENAI_API_KEY env var
  - Google OAuth: pass `--google-token` pointing at the JSON token created
    by `dynamous-engine/.claude/scripts/integrations/auth.py`. The full
    `https://www.googleapis.com/auth/drive` scope is sufficient.

Usage:
    cd scripts && uv sync
    uv run python transcribe_all.py \\
        --lesson-map ../../dynamous-engine/Dynamous/Memory/plans/dynamous-content-mapping/lesson_map.csv \\
        --google-token ../../dynamous-engine/.claude/scripts/integrations/google_token.json \\
        --output-dir /path/to/dynamous-content/content/dynamous \\
        --max-parallel 4

Arguments:
    --lesson-map      CSV from dynamous-engine matching work
    --google-token    Path to OAuth token JSON
    --output-dir      Where transcripts land (typically inside a checkout of
                      coleam00/dynamous-content)
    --max-parallel    Number of concurrent transcriptions (default: 4)
    --filter          Substring filter on `course_slug` or `post_slug` to run
                      a single course or one workshop (debugging)
    --dry-run         Print what would be transcribed; don't call APIs
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("transcribe_all")


# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "untitled"


def _format_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{ss:02d}"


def _output_path_for(row: dict[str, str], output_dir: Path) -> Path:
    """Pick a stable output path under output_dir based on the lesson_map row."""
    course = row.get("course_slug") or ""
    post = row.get("post_slug") or ""
    title = row.get("title") or "untitled"
    if course:
        # Course lesson — slug under the course dir, named for the lesson.
        bucket = course
        # Lesson titles often start with "1.4 -" — keep that prefix readable.
        name = _slugify(title)[:120]
        return output_dir / "courses" / bucket / f"{name}.md"
    if post:
        return output_dir / "workshops" / f"{post}.md"
    # Fallback — should not happen if lesson_map is well-formed.
    return output_dir / "misc" / f"{_slugify(title)}.md"


def _frontmatter_for(row: dict[str, str], video_md5: str) -> str:
    """Build the YAML frontmatter block that ingest_dynamous_content reads."""
    lines = ["---"]

    def _add(key: str, value: str) -> None:
        if value:
            # Quote string-typed fields that contain special characters.
            if any(c in value for c in ":#"):
                value = f'"{value}"'
            lines.append(f"{key}: {value}")

    _add("title", row.get("title", ""))
    _add("source_type", "dynamous")
    _add("course_slug", row.get("course_slug", ""))
    _add("section_id", row.get("section_id", ""))
    _add("lesson_id", row.get("lesson_id", ""))
    _add("post_slug", row.get("post_slug", ""))
    _add("lesson_url", row.get("lesson_url", ""))
    _add("source_video_md5", video_md5)
    lines.append("---")
    return "\n".join(lines)


def _format_transcript(segments: list[dict[str, Any]]) -> str:
    """Whisper segments → markdown body with `## [HH:MM:SS]` headings."""
    parts: list[str] = []
    for seg in segments:
        start = float(seg.get("start", 0.0))
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"## [{_format_timestamp(start)}]\n\n{text}\n")
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# Drive download + ffmpeg audio extraction
# ----------------------------------------------------------------------------


def _build_drive(token_path: Path) -> Any:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path))
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _resolve_drive_file_id(svc: Any, drive_path: str, root_id: str) -> str | None:
    """Walk the root folder by name components to find the file ID for drive_path.

    drive_path is a forward-slash separated path under the Drive root, e.g.:
      'Courses/AI Agent Mastery/Module 5/5.4 - Building a Complete Front End.mp4'
    """
    parts = [p for p in drive_path.split("/") if p]
    parent = root_id
    for part in parts:
        safe = part.replace("'", "\\'")
        res = (
            svc.files()
            .list(
                q=f"'{parent}' in parents and name='{safe}' and trashed=false",
                fields="files(id,name,mimeType)",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = res.get("files", [])
        if not files:
            return None
        parent = files[0]["id"]
    return parent  # the last resolved id is the file


def _download_file(svc: Any, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    with dest.open("wb") as f:
        downloader = MediaIoBaseDownload(f, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def _extract_audio(video: Path, audio: Path) -> None:
    """Use ffmpeg to make a 16 kHz mono Ogg-Opus file.

    Whisper's accepted formats are flac/m4a/mp3/mp4/mpeg/mpga/oga/ogg/wav/webm,
    and rejects bare `.opus`. We use `.ogg` extension (Ogg container) with
    libopus inside — Whisper handles it.

    Bitrate is 16 kbps mono. Whisper's hard upload cap is 25 MB; at 16 kbps
    that's enough headroom for ~3.5-hour audio. Empirically tried 32 kbps
    first; two workshops in the ~100-min range hit `413: Maximum content size
    limit exceeded` (26 MB ogg). 16 kbps is well within Opus's "still
    intelligible speech" zone and Whisper transcribes it cleanly.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg is required on PATH to extract audio for Whisper. Install it first."
        )
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vn",  # audio only — without this, ffmpeg tries to also re-encode
                    # the video stream into the .ogg container (libtheora) which
                    # crashes hard on some inputs.
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libopus",
            "-b:a",
            "16k",
            str(audio),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Surface ffmpeg's tail-of-stderr — silent failures are nightmare to debug
        # over a 5-hour run. The Windows access-violation exit code makes the
        # subprocess module's default error message useless on its own.
        tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}) for {video.name}:\n{tail}"
        )


def _md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# Whisper transcription
# ----------------------------------------------------------------------------


def _transcribe_with_whisper(audio: Path) -> dict[str, Any]:
    """Call Whisper with verbose_json so we get segment timestamps.

    Returns the parsed response dict (includes 'segments' list).
    """
    from openai import OpenAI

    client = OpenAI()
    with audio.open("rb") as f:
        # Whisper's max upload size is 25 MB; 32 kbps Opus puts a 30-min
        # video at ~7 MB so we're comfortably under.
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    # The SDK returns a Pydantic-like object; coerce to plain dict.
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    return json.loads(resp.json()) if hasattr(resp, "json") else dict(resp)


# ----------------------------------------------------------------------------
# Per-row processor
# ----------------------------------------------------------------------------


def _process_row(
    row: dict[str, str],
    drive_root_id: str,
    output_dir: Path,
    google_token: Path,
    dry_run: bool,
) -> tuple[str, str]:
    """Returns (status, detail). status ∈ {ok, skipped, error}."""
    drive_path = row.get("drive_path", "")
    if not drive_path:
        return "skipped", "no drive_path"
    out_path = _output_path_for(row, output_dir)

    # Idempotency: if the file already exists AND its frontmatter records the
    # same source_video_md5 we'd compute below, skip. We don't need to actually
    # download/md5 the source — but for simplicity we recompute once and
    # compare.
    if dry_run:
        return "ok", f"would write -> {out_path.relative_to(output_dir)}"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    svc = _build_drive(google_token)
    file_id = _resolve_drive_file_id(svc, drive_path, drive_root_id)
    if not file_id:
        return "error", f"drive_path not found in Drive: {drive_path}"

    with tempfile.TemporaryDirectory(prefix="dynachat-") as tmp_str:
        tmp = Path(tmp_str)
        video = tmp / Path(drive_path).name
        audio = tmp / "audio.ogg"
        _download_file(svc, file_id, video)
        video_md5 = _md5_of_file(video)

        # Idempotency check 2: existing transcript frontmatter has matching md5
        if out_path.exists():
            existing = out_path.read_text(encoding="utf-8")
            if f"source_video_md5: {video_md5}" in existing:
                return "skipped", f"unchanged: {out_path.name}"

        _extract_audio(video, audio)
        resp = _transcribe_with_whisper(audio)

    segments = resp.get("segments") or []
    body = _format_transcript(segments)
    fm = _frontmatter_for(row, video_md5)
    out_path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")
    return "ok", f"wrote {out_path.relative_to(output_dir)}"


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--lesson-map", type=Path, required=True)
    p.add_argument("--google-token", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument(
        "--drive-root",
        type=str,
        default="10mKOnXHz242ZY0tj1CxchLFJlXhuCgpc",
        help="Drive root folder ID for 'Dynamous Content' (default: production)",
    )
    p.add_argument("--max-parallel", type=int, default=4)
    p.add_argument("--filter", type=str, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY env var not set; aborting")
        return 2

    if not args.lesson_map.exists():
        logger.error("lesson_map not found: %s", args.lesson_map)
        return 2

    if not args.google_token.exists():
        logger.error("google_token not found: %s", args.google_token)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    with args.lesson_map.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("match_status") not in {"matched", "ambiguous"}:
                continue
            if not r.get("drive_path"):
                continue
            if args.filter and args.filter not in (r.get("course_slug") or "") + (r.get("post_slug") or ""):
                continue
            rows.append(r)

    logger.info("Processing %d rows (max_parallel=%d)", len(rows), args.max_parallel)

    results = {"ok": 0, "skipped": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
        futures = {
            pool.submit(
                _process_row,
                r,
                args.drive_root,
                args.output_dir,
                args.google_token,
                args.dry_run,
            ): r
            for r in rows
        }
        for fut in as_completed(futures):
            r = futures[fut]
            try:
                status, detail = fut.result()
            except Exception as exc:
                status, detail = "error", str(exc)
            results[status] = results.get(status, 0) + 1
            level = logging.INFO if status == "ok" else (
                logging.WARNING if status == "skipped" else logging.ERROR
            )
            logger.log(level, "[%s] %s -> %s", status, r.get("drive_path", "?"), detail)

    logger.info("Done. ok=%d skipped=%d error=%d", *(results[k] for k in ("ok", "skipped", "error")))
    return 0 if results["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
