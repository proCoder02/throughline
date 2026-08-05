from __future__ import annotations

import os
import re

from dotenv import load_dotenv
import psycopg2

load_dotenv()

DB_URL = os.getenv('DATABASE_URL')
if not DB_URL:
    raise SystemExit('DATABASE_URL is required')

CREATE_SQL = '''
CREATE TABLE IF NOT EXISTS emotional_intelligence.episode_speaker_transcript (
    id SERIAL PRIMARY KEY,
    season INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    episode_number INTEGER NOT NULL,
    episode_name TEXT,
    scene_number INTEGER,
    scene_name TEXT,
    speaker_name TEXT NOT NULL,
    turn_number INTEGER,
    raw_transcript TEXT NOT NULL,
    source_url TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, scene_number, turn_number, speaker_name)
);
CREATE INDEX IF NOT EXISTS idx_ei_episode_speaker_transcript_season_episode
    ON emotional_intelligence.episode_speaker_transcript (season, episode_id);
CREATE INDEX IF NOT EXISTS idx_ei_episode_speaker_transcript_speaker
    ON emotional_intelligence.episode_speaker_transcript (speaker_name);
'''

SELECT_SQL = '''
SELECT
    season,
    episode_id,
    episode_number,
    title,
    source_url,
    transcript_text
FROM emotional_intelligence.transcript
ORDER BY season, episode_number
'''

INSERT_SQL = '''
INSERT INTO emotional_intelligence.episode_speaker_transcript (
    season,
    episode_id,
    episode_number,
    episode_name,
    scene_number,
    scene_name,
    speaker_name,
    turn_number,
    raw_transcript,
    source_url,
    ingested_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (episode_id, scene_number, turn_number, speaker_name)
DO UPDATE SET
    season = EXCLUDED.season,
    episode_number = EXCLUDED.episode_number,
    episode_name = EXCLUDED.episode_name,
    scene_name = EXCLUDED.scene_name,
    raw_transcript = EXCLUDED.raw_transcript,
    source_url = EXCLUDED.source_url,
    ingested_at = EXCLUDED.ingested_at
'''


def parse_episode_turns(transcript_text: str) -> list[tuple[int, str, str, int, str]]:
    scene_number = 1
    current_scene = 'Unknown Scene'
    turn_number = 0
    current_speaker: str | None = None
    current_dialogue: list[str] = []
    rows: list[tuple[int, str, str, int, str]] = []

    def flush_current_turn() -> None:
        nonlocal current_speaker, current_dialogue, turn_number
        if not current_speaker:
            return

        dialogue_text = ' '.join(part for part in current_dialogue if part).strip()
        if not dialogue_text:
            current_speaker = None
            current_dialogue = []
            return

        turn_number += 1
        rows.append((scene_number, current_scene, current_speaker, turn_number, dialogue_text))
        current_speaker = None
        current_dialogue = []

    for raw_line in transcript_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        scene_match = re.match(r'^\[Scene:?\s*(.+?)\]$', line, re.IGNORECASE)
        if scene_match:
            flush_current_turn()
            current_scene = scene_match.group(1).strip() or current_scene
            scene_number += 1
            continue

        speaker_match = re.match(
            r'^([A-Za-z][A-Za-z0-9\.\'\-]*(?:\s+[A-Za-z][A-Za-z0-9\.\'\-]*)*)\s*:\s*(.*)$',
            line,
        )
        if speaker_match:
            flush_current_turn()
            speaker_name = speaker_match.group(1).strip()
            dialogue_text = speaker_match.group(2).strip()
            if not speaker_name:
                continue

            current_speaker = speaker_name
            if dialogue_text:
                current_dialogue = [dialogue_text]
            else:
                current_dialogue = []
            continue

        if current_speaker:
            current_dialogue.append(line)

    flush_current_turn()
    return rows


def main() -> int:
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute('TRUNCATE TABLE emotional_intelligence.episode_speaker_transcript')
        cur.execute(CREATE_SQL)
        cur.execute(SELECT_SQL)
        rows = cur.fetchall()

        for season, episode_id, episode_number, title, source_url, transcript_text in rows:
            turns = parse_episode_turns(transcript_text)
            for scene_number, scene_name, speaker_name, turn_number, dialogue_text in turns:
                cur.execute(
                    INSERT_SQL,
                    (
                        season,
                        episode_id,
                        episode_number,
                        title,
                        scene_number,
                        scene_name,
                        speaker_name,
                        turn_number,
                        dialogue_text,
                        source_url,
                    ),
                )

        conn.commit()
        cur.execute('SELECT COUNT(*) FROM emotional_intelligence.episode_speaker_transcript')
        count = cur.fetchone()[0]
        print(f'flat_rows={count}')
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
