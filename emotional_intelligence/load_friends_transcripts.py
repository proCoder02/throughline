from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError as exc:  # pragma: no cover
    print("Missing dependency. Install: pip install psycopg2-binary requests beautifulsoup4 python-dotenv")
    raise SystemExit(1) from exc

load_dotenv()

SITE_ROOT = "https://edersoncorbari.github.io/friends/"
SEASON_URL_ROOT = "https://edersoncorbari.github.io/friends-scripts/season/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load Friends transcript pages into Postgres.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="Postgres connection string.")
    parser.add_argument("--source", choices=["site", "local"], default="site", help="Where to read transcripts from.")
    parser.add_argument("--root-url", default=SITE_ROOT, help="Friends site root URL when using --source site.")
    parser.add_argument("--input-dir", default=".", help="Directory containing HTML transcript files when using --source local.")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on number of episodes to ingest; 0 means all.")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds.")
    return parser.parse_args()


def get_text(url: str, timeout: float) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_episode_metadata(source_url: str, title_text: str, transcript_text: str) -> tuple[int, int, str, str]:
    path = urlparse(source_url).path
    url_name = Path(path).name
    stem = Path(url_name).stem

    season_value = None
    episode_value = None

    # Friends season pages use a slug like 0101.html, 0201.html, 0212-0213.html, etc.
    match = re.search(r"(\d{2})(\d{2})(?:-(\d{2})(\d{2}))?", stem)
    if match:
        season_value = int(match.group(1))
        episode_value = int(match.group(2))

    if season_value is None or episode_value is None:
        season_match = re.search(r"Season\s+(\d+)", title_text, re.IGNORECASE)
        episode_match = re.search(r"\b(\d{1,2})\b", title_text)
        if season_match:
            season_value = int(season_match.group(1))
        if episode_match:
            episode_value = int(episode_match.group(1))

    if season_value is None:
        season_value = 1
    if episode_value is None:
        episode_value = 1

    episode_id = f"s{season_value:02d}e{episode_value:02d}"
    title = title_text.strip() or url_name
    return season_value, episode_value, episode_id, title


def clean_text(raw_text: str) -> str:
    soup = BeautifulSoup(raw_text, "html.parser")
    for bad_tag in soup(["script", "style", "noscript"]):
        bad_tag.decompose()

    title_tag = soup.find("title")
    title_text = title_tag.get_text(" ", strip=True) if title_tag else ""

    pre_tag = soup.find("pre")
    if pre_tag:
        text = pre_tag.get_text("\n", strip=True)
    else:
        text = soup.get_text("\n", strip=True)

    lines = [line.strip() for line in text.splitlines()]
    cleaned_lines = []
    for line in lines:
        if not line:
            continue
        if line.startswith("Friends Scripts"):
            continue
        if line.startswith("FOLLOW:") or line.startswith("Additional Links"):
            continue
        if line.startswith("©"):
            continue
        if line.startswith("The One Where") or line.startswith("Written by:"):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def parse_transcript_structure(transcript_text: str) -> tuple[dict, list[dict]]:
    scenes = []
    speakers = []
    turns = []
    current_scene = None
    scene_number = 0
    turn_number = 0

    for raw_line in transcript_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        scene_match = re.match(r"^\[Scene:\s*(.+?)\]$", line)
        if scene_match:
            current_scene = scene_match.group(1)
            scene_number += 1
            turns.append({"scene_number": scene_number, "scene_name": current_scene, "speaker": None, "dialogue_text": None})
            scenes.append(current_scene)
            continue

        speaker_match = re.match(r"^([^\(\[]+?):\s+(.+)$", line)
        if speaker_match and current_scene:
            speaker_name = speaker_match.group(1).strip()
            dialogue_text = speaker_match.group(2).strip()
            if speaker_name and dialogue_text:
                if speaker_name not in speakers:
                    speakers.append(speaker_name)
                turn_number += 1
                turns.append({
                    "scene_number": scene_number,
                    "scene_name": current_scene,
                    "speaker": speaker_name,
                    "dialogue_text": dialogue_text,
                    "turn_number": turn_number,
                })

    structure = {
        "scene_headers": scenes,
        "speakers": speakers,
        "speaker_count": len(speakers),
        "scene_count": len(scenes),
    }
    return structure, turns


def iter_site_pages(root_url: str, timeout: float) -> Iterable[tuple[str, str]]:
    home_html = get_text(root_url, timeout=timeout)
    soup = BeautifulSoup(home_html, "html.parser")
    discovered = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/friends-scripts/season/" not in href or not href.endswith(".html"):
            continue
        url = href if href.startswith("http") else f"https://edersoncorbari.github.io{href}"
        discovered.append(url)

    deduped = list(dict.fromkeys(discovered))
    for url in deduped:
        yield url, get_text(url, timeout=timeout)


def iter_local_pages(input_dir: str) -> Iterable[tuple[str, str]]:
    directory = Path(input_dir)
    for path in sorted(directory.glob("*.html")):
        yield str(path), path.read_text(encoding="utf-8")


def ingest(database_url: str, source: str, root_url: str, input_dir: str, limit: int, timeout: float) -> int:
    if not database_url:
        raise SystemExit("DATABASE_URL is required. Set it in the environment or pass --database-url.")

    if source == "site":
        iterator = iter_site_pages(root_url, timeout=timeout)
    else:
        iterator = iter_local_pages(input_dir)

    count = 0
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        for source_url, raw_html in iterator:
            transcript_text = clean_text(raw_html)
            if not transcript_text:
                continue

            title_text = BeautifulSoup(raw_html, "html.parser").find("title")
            title = title_text.get_text(" ", strip=True) if title_text else Path(source_url).name
            season, episode_number, episode_id, normalized_title = extract_episode_metadata(source_url, title, transcript_text)
            scene_structure, turns = parse_transcript_structure(transcript_text)

            cur.execute(
                """
                INSERT INTO emotional_intelligence.transcript (
                    episode_id,
                    season,
                    episode_number,
                    title,
                    scene_structure,
                    source_url,
                    transcript_text,
                    ingested_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (episode_id) DO UPDATE SET
                    season = EXCLUDED.season,
                    episode_number = EXCLUDED.episode_number,
                    title = EXCLUDED.title,
                    scene_structure = EXCLUDED.scene_structure,
                    source_url = EXCLUDED.source_url,
                    transcript_text = EXCLUDED.transcript_text,
                    ingested_at = now()
                RETURNING id
                """,
                (
                    episode_id,
                    season,
                    episode_number,
                    normalized_title,
                    Json(scene_structure),
                    source_url,
                    transcript_text,
                ),
            )
            transcript_id = cur.fetchone()[0]

            cur.execute(
                "DELETE FROM emotional_intelligence.transcript_speaker_turns WHERE transcript_id = %s",
                (transcript_id,),
            )
            cur.execute(
                "DELETE FROM emotional_intelligence.transcript_speakers WHERE transcript_id = %s",
                (transcript_id,),
            )
            cur.execute(
                "DELETE FROM emotional_intelligence.transcript_scenes WHERE transcript_id = %s",
                (transcript_id,),
            )

            speaker_map = {}
            scene_id_map = {}
            for scene_number, scene_name in enumerate(scene_structure["scene_headers"], start=1):
                cur.execute(
                    """
                    INSERT INTO emotional_intelligence.transcript_scenes (
                        transcript_id,
                        scene_number,
                        scene_name
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (transcript_id, scene_number) DO UPDATE SET
                        scene_name = EXCLUDED.scene_name
                    RETURNING id
                    """,
                    (transcript_id, scene_number, scene_name),
                )
                scene_id_map[scene_number] = cur.fetchone()[0]

            for speaker_name in scene_structure["speakers"]:
                cur.execute(
                    """
                    INSERT INTO emotional_intelligence.transcript_speakers (
                        transcript_id,
                        speaker_name
                    ) VALUES (%s, %s)
                    ON CONFLICT (transcript_id, speaker_name) DO UPDATE SET
                        speaker_name = EXCLUDED.speaker_name
                    RETURNING id
                    """,
                    (transcript_id, speaker_name),
                )
                speaker_map[speaker_name] = cur.fetchone()[0]

            for turn in turns:
                if turn.get("speaker") is None or turn.get("dialogue_text") is None:
                    continue
                speaker_id = speaker_map.get(turn["speaker"])
                scene_id = scene_id_map.get(turn["scene_number"])
                turn_number = turn.get("turn_number", 1)
                cur.execute(
                    """
                    INSERT INTO emotional_intelligence.transcript_speaker_turns (
                        transcript_id,
                        scene_id,
                        speaker_id,
                        turn_number,
                        dialogue_text
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (transcript_id, scene_id, turn_number) DO UPDATE SET
                        speaker_id = EXCLUDED.speaker_id,
                        dialogue_text = EXCLUDED.dialogue_text
                    """,
                    (
                        transcript_id,
                        scene_id,
                        speaker_id,
                        turn_number,
                        turn["dialogue_text"],
                    ),
                )

            count += 1
            if limit and count >= limit:
                break

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return count


def main() -> int:
    args = parse_args()
    count = ingest(
        database_url=args.database_url,
        source=args.source,
        root_url=args.root_url,
        input_dir=args.input_dir,
        limit=args.limit,
        timeout=args.timeout,
    )
    print(f"Ingested {count} transcript rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
