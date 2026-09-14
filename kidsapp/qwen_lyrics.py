"""CPU-based lyrics writing via Qwen2.5-1.5B-Instruct.

Runs entirely on CPU/RAM — never touches the GPU, so it can run
alongside ACE-Step generation without VRAM contention. Loaded lazily
on first use and kept resident for the life of the process.

Chosen over ACE-Step's own LM paths (format_input/use_format only
rewrite the caption, never lyrics; sample_mode writes real lyrics but
ignores the specific idea given — both verified empirically) and over
Phi-3.5-mini (better lyrics, but 3-5x slower on this CPU — not worth
the wait for an interactive kids' app).
"""

import functools
import os
import re
import threading
from typing import Optional

MODEL_PATH = os.environ.get("KIDSAPP_LYRICS_MODEL_PATH", "Qwen/Qwen2.5-1.5B-Instruct")

SYSTEM_PROMPT = (
    "You are a children's songwriter. Write short, singable song lyrics "
    "for a happy kids' song. Use clear structure tags: [Intro], [Verse], "
    "[Chorus], [Outro]. Keep lines short (6-10 syllables), simple words, "
    "and make the chorus a short repeatable hook. The song must be "
    "specifically about the idea given by the user — mention concrete "
    "details from their idea, do not write about something unrelated. "
    "Output ONLY the lyrics with structure tags, nothing else."
)

_lock = threading.Lock()
_model = None
_tokenizer = None
_load_failed = False


def _ensure_loaded() -> bool:
    """Load the model once, on first use. Thread-safe. Returns False
    (without raising) if loading fails, so callers can fall back."""
    global _model, _tokenizer, _load_failed
    if _model is not None:
        return True
    if _load_failed:
        return False
    with _lock:
        if _model is not None:
            return True
        if _load_failed:
            return False
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
            _model = AutoModelForCausalLM.from_pretrained(
                MODEL_PATH, torch_dtype=torch.bfloat16, device_map="cpu"
            )
            return True
        except Exception:
            _load_failed = True
            return False


_STRUCTURE_TAG_RE = re.compile(r"^\[[A-Za-z][A-Za-z0-9 \-]{0,30}\]$")


def _looks_like_valid_lyrics(text: str) -> bool:
    """Sanity check the model actually produced structured lyrics and
    not an empty/garbled response, before trusting it over the template
    fallback."""
    if not text or len(text.strip()) < 20:
        return False
    has_tag = any(_STRUCTURE_TAG_RE.match(line.strip()) for line in text.splitlines())
    return has_tag


def generate_lyrics_sync(idea: str, timeout_s: float = 45.0) -> Optional[str]:
    """Blocking call — must be run in a thread/executor, never directly
    in an async event loop. Returns None on any failure so the caller
    can fall back to templates."""
    if not _ensure_loaded():
        return None

    try:
        import torch

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Write lyrics about: {idea}"},
        ]
        prompt = _tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = _tokenizer(prompt, return_tensors="pt")
        with torch.no_grad():
            out = _model.generate(
                **inputs,
                max_new_tokens=300,
                temperature=0.8,
                do_sample=True,
                top_p=0.9,
                pad_token_id=_tokenizer.eos_token_id,
            )
        text = _tokenizer.decode(
            out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()
        if not _looks_like_valid_lyrics(text):
            return None
        return text
    except Exception:
        return None
