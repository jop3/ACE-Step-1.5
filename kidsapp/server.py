#!/usr/bin/env python3
"""Kid-friendly music maker frontend for ACE-Step.

Serves a picker UI and proxies simplified requests to the ACE-Step API
server (must already be running on ACESTEP_API_URL).
"""

import asyncio
import json as _json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kidsapp import qwen_lyrics

ACESTEP_API_URL = os.environ.get("ACESTEP_API_URL", "http://127.0.0.1:8001")
STATIC_DIR = Path(__file__).parent / "static"
QWEN_LYRICS_TIMEOUT_S = float(os.environ.get("QWEN_LYRICS_TIMEOUT_S", "60"))

app = FastAPI(title="Kids Music Maker")
# Single worker: CPU lyrics generation is heavy: (100% single-core-ish for
# a few seconds to a minute) — running two at once would just make both
# slower, so requests queue rather than compete for CPU.
_lyrics_executor = ThreadPoolExecutor(max_workers=1)

# ---- Vocabulary: kid picks -> caption fragments -----------------------

MOODS = {
    "happy": "cheerful, upbeat, bright and playful",
    "silly": "goofy, bouncy, cartoonish and fun",
    "epic": "big, heroic, triumphant and powerful",
    "sleepy": "soft, gentle, cozy and calm",
    "spooky": "mysterious, spooky but playful, not scary",
    "magic": "sparkly, magical, dreamy and wondrous",
}

INSTRUMENTS = {
    "guitar": "acoustic guitar",
    "electric-guitar": "electric guitar",
    "piano": "piano",
    "drums": "punchy drums",
    "synth": "bright synthesizer",
    "violin": "orchestral strings",
    "trumpet": "brass and trumpet",
    "xylophone": "xylophone and glockenspiel",
    "flute": "playful flute",
    "banjo": "banjo",
    "ukulele": "ukulele",
    "accordion": "accordion",
    "bagpipes": "bagpipes",
    "harp": "harp",
    "saxophone": "saxophone",
    "cello": "cello",
    "organ": "church organ",
    "bells": "bells and chimes",
    "handclaps": "handclaps and stomps",
    "whistle": "whistling",
}

BEATS = {
    "slow": (70, "slow, relaxed tempo"),
    "medium": (110, "medium, walking tempo"),
    "fast": (140, "fast, energetic tempo"),
    "super-fast": (170, "very fast, exciting tempo"),
}

# Style buttons: genre/era descriptors (safe by construction — no artist
# or song is ever named or referenced by real audio) plus a few
# "sounds like the vibe of..." presets. All values are pure style
# descriptors; none reference copyrighted lyrics or audio.
STYLES = {
    "movie": "epic cinematic film score style, sweeping orchestral",
    "videogame": "8-bit retro video game chiptune style",
    "circus": "bouncy circus calliope style",
    "lullaby": "gentle lullaby style, soft and slow",
    "pirate": "sea shanty pirate song style, rowdy group vocals",
    "disco": "retro 1970s disco style, funky bassline, glittery strings",
    "reggae": "sunny reggae style, offbeat guitar skank",
    "march": "marching band style, bold brass and drums",
    "country": "country hoedown style, banjo and fiddle",
    "lofi": "cozy lo-fi bedroom pop style, mellow and chill",
    "rock": "big arena rock style, driving electric guitar",
    "folk": "campfire folk singalong style, acoustic and warm",
    "synthpop80s": "1980s synth-pop style, bright synths, gated drums, neon energy",
    "kpop": "modern K-pop style, punchy production, catchy hook-driven chorus",
    "broadway": "Broadway musical theatre style, big belting showtune energy",
    "edm": "festival EDM style, build-ups and euphoric drops, four-on-the-floor",
    "reggaeton": "reggaeton style, dembow rhythm, bouncy and danceable",
    "afrobeat": "modern afrobeats style, warm percussion, groovy syncopation",
    "grunge90s": "1990s grunge/alt-rock style, raw guitars, garage energy",
    "trap": "modern trap-pop style, hi-hat rolls, punchy 808 bass",
    "gospel": "uplifting gospel choir style, powerful harmonies, clapping",
    "jazz": "playful swing jazz style, brushed drums, walking bass",
}

# "Sounds like X" buttons: named artist/act as a flavor reference,
# always paired with real descriptive detail (era, instrumentation,
# vocal texture) rather than the name doing all the work. Generated
# locally, kept on this machine, never distributed — text-conditioned
# style reference only, no audio sampling, no voice cloning.
ARTIST_STYLES = {
    "abba": "in the spirit of ABBA-era 70s disco-pop, glittering harmonies, orchestral disco strings, four-on-the-floor beat",
    "queen": "in the spirit of Queen-style arena rock, huge layered vocal harmonies, theatrical dynamic shifts, anthemic chorus",
    "mj": "in the spirit of Michael Jackson-era 80s pop-funk, syncopated bassline, punchy drums, smooth danceable groove",
    "beatles": "in the spirit of 1960s Beatles-style pop, jangly guitars, tight vocal harmonies, cheerful melodic hooks",
    "boney-m": "in the spirit of Boney M-style Eurodisco, driving disco beat, orchestral strings, dramatic storytelling vocals with choir chants",
    "beachboys": "in the spirit of Beach Boys-style surf pop, bright vocal harmonies, sunny breezy guitar",
    "daftpunk": "in the spirit of Daft Punk-style French house, robotic vocoder vocals, filtered disco samples, driving electronic groove",
    "stevie": "in the spirit of Stevie Wonder-style 70s soul-funk, warm keys, groovy bassline, joyful vocal runs",
    "taylor": "in the spirit of modern Taylor Swift-style pop-country crossover, storytelling lyrics, bright acoustic-to-pop production",
    "billie": "in the spirit of Billie Eilish-style moody bedroom pop, soft breathy vocals, minimal dark bass",
    "bts": "in the spirit of BTS-style modern K-pop, high-energy chorus, slick electronic production, group vocal trade-offs",
    "dua": "in the spirit of Dua Lipa-style disco-pop revival, retro synth bass, confident dance-pop chorus",
    "bruno": "in the spirit of Bruno Mars-style funk-pop, tight horn stabs, retro-modern groove, smooth vocal runs",
    "edsheeran": "in the spirit of Ed Sheeran-style acoustic pop, looped guitar riffs, warm intimate vocal, singalong chorus",
    "pharrell": "in the spirit of Pharrell-style upbeat funk-pop, handclaps, bouncy bassline, sunny feel-good energy",
    "adele": "in the spirit of Adele-style powerhouse pop-soul ballad, emotional piano, soaring belted chorus",
    "olivia": "in the spirit of Olivia Rodrigo-style pop-rock, punchy guitars, diary-entry lyrics, big emotional chorus",
    "ariana": "in the spirit of Ariana Grande-style R&B-pop, airy high vocal runs, slick modern production",
    "sabrina": "in the spirit of Sabrina Carpenter-style playful pop, retro-flavored groove, witty flirty energy",
    "benson": "in the spirit of Benson Boone-style piano pop-rock, soaring falsetto, big anthemic build",
    "blackpink": "in the spirit of BLACKPINK-style K-pop, punchy trap-pop beat, powerful group chorus, high-energy drops",
    "harrystyles": "in the spirit of Harry Styles-style breezy pop-rock, warm guitar tone, feel-good singalong chorus",
    "shawn": "in the spirit of Shawn Mendes-style acoustic-pop, warm heartfelt vocal, gentle guitar strumming",
    "doja": "in the spirit of Doja Cat-style playful pop-rap, bouncy beat, catchy singsong hook",
    "selena": "in the spirit of Selena Gomez-style dreamy pop, soft synths, smooth laid-back groove",
}

# Singer controls
VOCAL_GENDERS = {
    "boy": "young boy vocal",
    "girl": "young girl vocal",
    "man": "adult male vocal",
    "woman": "adult female vocal",
    "choir": "full children's choir, group vocals",
}

VOCAL_STYLES = {
    "sweet": "sweet and gentle singing",
    "powerful": "powerful, belting singing",
    "whispery": "soft, whispery singing",
    "robotic": "fun robotic vocoder-style singing",
    "operatic": "playful operatic singing",
    "rap": "fun, simple rap-style chanting",
    "growly": "silly growly monster-style singing (not scary)",
}

LANGUAGES = {
    "en": "English",
    "sv": "Swedish",
    "es": "Spanish",
    "fr": "French",
    "ja": "Japanese",
}

ANIMALS = ["cat", "dog", "dragon", "unicorn", "robot", "dinosaur", "astronaut", "penguin"]

WORD_RE = re.compile(r"^[\w\s.,!?'\"\-åäöÅÄÖ]{0,200}$", re.UNICODE)
IDEA_MAX_CHARS = 200


class SongResponse(BaseModel):
    task_id: str


class LyricsRequest(BaseModel):
    idea: Optional[str] = None


class LyricsResponse(BaseModel):
    lyrics: str
    source: str  # "qwen" or "template" — so the frontend/logs can tell which path ran


class GenerateRequest(BaseModel):
    mood: str
    instruments: list[str]
    beat: str
    styles: list[str] = []
    artist_styles: list[str] = []
    vocal_gender: str = "girl"
    vocal_style: str = "sweet"
    language: str = "en"
    duration: int = 60
    lyrics: str  # final lyrics text — already written/edited, not generated here


def _sanitize_idea(idea: Optional[str]) -> Optional[str]:
    """Keep a plain single-line phrase, truncated on a word boundary.

    Accepts anything from a two-word topic ("a dragon") up to a full
    sentence-length idea the user typed by hand.
    """
    if not idea:
        return None
    idea = idea.strip().splitlines()[0]
    if len(idea) > IDEA_MAX_CHARS:
        idea = idea[:IDEA_MAX_CHARS].rsplit(" ", 1)[0]
    if not idea or not WORD_RE.match(idea):
        return None
    return idea


def build_caption(
    mood: str,
    instruments: list[str],
    beat: str,
    styles: list[str],
    artist_styles: list[str],
    vocal_gender: str,
    vocal_style: str,
) -> tuple[str, int]:
    mood_desc = MOODS.get(mood, MOODS["happy"])
    instr_desc = ", ".join(INSTRUMENTS.get(i, i) for i in instruments) or "acoustic instruments"
    bpm, beat_desc = BEATS.get(beat, BEATS["medium"])
    style_desc = ", ".join(STYLES.get(s, s) for s in styles)
    artist_desc = ", ".join(ARTIST_STYLES.get(a, a) for a in artist_styles)
    vocal_desc = f"{VOCAL_GENDERS.get(vocal_gender, VOCAL_GENDERS['girl'])}, {VOCAL_STYLES.get(vocal_style, VOCAL_STYLES['sweet'])}"

    parts = [
        f"A {mood_desc} song for children",
        f"featuring {instr_desc}",
        f"{beat_desc}",
    ]
    if style_desc:
        parts.append(style_desc)
    if artist_desc:
        parts.append(artist_desc)
    parts.append(vocal_desc)
    parts.append("simple and catchy, clean family-friendly production, warm and inviting, sing-along energy")

    caption = ", ".join(parts)
    return caption, bpm


# NOTE: ACE-Step's LM-assisted paths (/format_input, use_format) only
# rewrite the *caption*, never the lyrics — verified empirically, lyrics
# come back essentially unchanged. sample_mode/sample_query does write
# real lyrics via the LM, but is an "inspiration mode" that ignores the
# specific idea given (tested with "a dragon who loves pizza" -> lyrics
# about an unrelated dog and rabbit) and overrides the whole caption too.
# Neither is usable for "write a song about what this kid typed", so
# lyrics are generated with hand-written templates instead.

_LYRIC_TEMPLATES = [
    lambda s, st: (
        f"[Intro]\n\n"
        f"[Verse]\n"
        f"Let's sing a song, come along with me\n"
        f"About {s}, as happy as can be\n"
        f"Clap your hands and stomp your feet\n"
        f"Come along and join the fun today\n\n"
        f"[Chorus]\n"
        f"WE ARE SINGING, WE ARE PLAYING\n"
        f"{st} IS HERE TO STAY\n"
        f"WE ARE SINGING, WE ARE PLAYING\n"
        f"COME ON AND SING WITH ME TODAY\n\n"
        f"[Verse]\n"
        f"Every friend can join the song\n"
        f"Sing about {s} all day long\n"
        f"Nothing here can go too wrong\n"
        f"When we're singing all day long\n\n"
        f"[Chorus]\n"
        f"WE ARE SINGING, WE ARE PLAYING\n"
        f"{st} IS HERE TO STAY\n"
        f"WE ARE SINGING, WE ARE PLAYING\n"
        f"COME ON AND SING WITH ME TODAY\n\n"
        f"[Outro]\n"
        f"La la la, la la la\n"
        f"See you again real soon\n"
    ),
    lambda s, st: (
        f"[Intro]\n\n"
        f"[Verse]\n"
        f"Gather round and listen close to me\n"
        f"I've got a story, wild as can be\n"
        f"It's all about {s}\n"
        f"The best story that you'll ever see\n\n"
        f"[Chorus]\n"
        f"HERE WE GO, HERE WE GO\n"
        f"{st}, EVERYBODY KNOWS\n"
        f"HERE WE GO, HERE WE GO\n"
        f"SINGING LOUD WHEREVER WE GO\n\n"
        f"[Verse]\n"
        f"Every day's an adventure, through and through\n"
        f"With {s} there's so much fun to do\n"
        f"Sing it high or sing it low\n"
        f"Everybody, ready, set, let's go\n\n"
        f"[Chorus]\n"
        f"HERE WE GO, HERE WE GO\n"
        f"{st}, EVERYBODY KNOWS\n"
        f"HERE WE GO, HERE WE GO\n"
        f"SINGING LOUD WHEREVER WE GO\n\n"
        f"[Outro]\n"
        f"Bye bye now, see you soon\n"
        f"We'll sing again beneath the moon\n"
    ),
    lambda s, st: (
        f"[Intro]\n\n"
        f"[Verse]\n"
        f"Once upon a time, not far away\n"
        f"There's a tale about {s} today\n"
        f"Open up your ears and hear\n"
        f"The happiest song you'll hear all year\n\n"
        f"[Chorus]\n"
        f"OH {st}, SHINING BRIGHT\n"
        f"OH {st}, FEELS SO RIGHT\n"
        f"EVERYBODY SING ALONG\n"
        f"COME AND JOIN OUR HAPPY SONG\n\n"
        f"[Verse]\n"
        f"Every step and every sound\n"
        f"With {s}, joy is all around\n"
        f"Reach up high and touch the sky\n"
        f"Let's sing this song, you and I\n\n"
        f"[Chorus]\n"
        f"OH {st}, SHINING BRIGHT\n"
        f"OH {st}, FEELS SO RIGHT\n"
        f"EVERYBODY SING ALONG\n"
        f"COME AND JOIN OUR HAPPY SONG\n\n"
        f"[Outro]\n"
        f"That's our song, our happy tune\n"
        f"See you again real soon\n"
    ),
]


_TRAILING_STOPWORDS = {
    "a", "an", "the", "who", "that", "which", "and", "or", "but", "to",
    "of", "with", "for", "in", "on", "at", "is", "was", "loves", "wants",
}


def _trim_phrase(text: str, max_words: int) -> str:
    """Cut to at most max_words, then drop trailing stopwords/articles
    so the cut lands on a real word ("a dragon" not "a dragon who")."""
    words = text.split()[:max_words]
    while words and words[-1].lower() in _TRAILING_STOPWORDS:
        words.pop()
    return " ".join(words) if words else text.split()[0]


def _hook_phrase(subject: str) -> str:
    """A short phrase safe to repeat as an all-caps chorus hook.

    A full sentence a kid typed ("a robot who wants a friend...") reads
    fine once in a verse but breaks meter badly as a repeated shouted
    hook — so the hook uses just the first few words of the subject,
    trimmed to end on a real word rather than a dangling article.
    """
    return _trim_phrase(subject, 3).upper()


def build_template_lyrics(idea: Optional[str]) -> str:
    """Hand-written, grammatical lyric templates that absorb any idea
    phrase — a short noun ("a dragon") or a full sentence a kid typed.

    The full idea appears once per verse (still long, but only spoken,
    not shouted-and-repeated); the chorus hook uses a short trimmed
    phrase so the repeated hook line still scans as a real song line.
    """
    raw_subject = _sanitize_idea(idea) or random.choice(ANIMALS)
    # Cap the verse-embedded phrase too — a full typed sentence still
    # needs to fit inside one sung line, just with more room than the hook.
    subject = _trim_phrase(raw_subject, 8)
    hook = _hook_phrase(subject)
    template = random.choice(_LYRIC_TEMPLATES)
    return template(subject, hook)


async def build_lyrics(idea: Optional[str]) -> tuple[str, str]:
    """Try Qwen2.5-1.5B (CPU) for real, on-topic lyrics; fall back to
    hand-written templates if the model isn't available, times out, or
    the idea is empty (templates handle "no idea given" more directly
    than prompting an LM with nothing). Returns (lyrics, source)."""
    if idea and _sanitize_idea(idea):
        loop = asyncio.get_running_loop()
        try:
            lyrics = await asyncio.wait_for(
                loop.run_in_executor(
                    _lyrics_executor, qwen_lyrics.generate_lyrics_sync, idea
                ),
                timeout=QWEN_LYRICS_TIMEOUT_S,
            )
            if lyrics:
                return lyrics, "qwen"
        except (asyncio.TimeoutError, Exception):
            pass
    return build_template_lyrics(idea), "template"


@app.post("/api/write_lyrics", response_model=LyricsResponse)
async def write_lyrics(req: LyricsRequest):
    """Write lyrics for the kid to preview/edit before generating the
    song. Separate step from generation so the frontend can kick off
    music generation with these lyrics immediately while still letting
    the kid edit and regenerate if they don't like what they see."""
    lyrics, source = await build_lyrics(req.idea)
    return LyricsResponse(lyrics=lyrics, source=source)


@app.post("/api/make_song", response_model=SongResponse)
async def make_song(req: GenerateRequest):
    """Generate the song from already-written (possibly kid-edited)
    lyrics. Does not write lyrics itself — call /api/write_lyrics first."""
    if not req.instruments:
        raise HTTPException(400, "Pick at least one instrument")
    if not req.lyrics or not req.lyrics.strip():
        raise HTTPException(400, "Lyrics are empty")
    duration = max(20, min(req.duration, 180))
    caption, bpm = build_caption(
        req.mood, req.instruments, req.beat, req.styles, req.artist_styles,
        req.vocal_gender, req.vocal_style
    )
    language = req.language if req.language in LANGUAGES else "en"

    payload = {
        "caption": caption,
        "lyrics": req.lyrics,
        "bpm": bpm,
        "timesignature": "4",
        "language": language,
        "duration": duration,
        "task_type": "text2music",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{ACESTEP_API_URL}/release_task", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Could not reach music engine: {exc}") from exc

    data = resp.json()
    task_id = data.get("data", {}).get("task_id")
    if not task_id:
        raise HTTPException(502, f"Unexpected response from music engine: {data}")
    return SongResponse(task_id=task_id)


@app.get("/api/song_status/{task_id}")
async def song_status(task_id: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{ACESTEP_API_URL}/query_result",
                json={"task_id_list": [task_id]},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Could not reach music engine: {exc}") from exc

    data = resp.json()
    items = data.get("data", [])
    if not items:
        return {"status": "pending"}

    item = items[0]
    status = item.get("status")
    if status == 1:
        result_list = _json.loads(item.get("result", "[]"))
        if not result_list:
            return {"status": "failed", "error": "No audio produced"}
        first = result_list[0]
        return {
            "status": "done",
            "audio_url": f"/api/audio_proxy?path={first['file'].split('path=', 1)[-1]}",
        }
    if status == 2:
        return {"status": "failed", "error": item.get("progress_text", "Generation failed")}
    return {"status": "pending"}


@app.get("/api/audio_proxy")
async def audio_proxy(path: str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(f"{ACESTEP_API_URL}/v1/audio", params={"path": path})
        resp.raise_for_status()
        return FileResponse(
            path=_save_temp_audio(resp.content),
            media_type="audio/mpeg",
        )


def _save_temp_audio(content: bytes) -> str:
    tmp_dir = Path("/tmp/kidsapp_audio")
    tmp_dir.mkdir(exist_ok=True)
    fname = tmp_dir / f"song_{int(time.time() * 1000)}.mp3"
    fname.write_bytes(content)
    return str(fname)


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KIDSAPP_PORT", "8099")))
