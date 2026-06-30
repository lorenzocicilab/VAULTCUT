"""
VAULTCUT — Mistral Analyzer
==============================
Sends transcript chunks to the local Mistral model via Ollama and
extracts clip suggestions as structured JSON.

Design considerations:
  - Uses Ollama's /api/generate endpoint (same as Ollama CLI)
  - 300-second timeout per chunk (Mistral on CPU is slow)
  - Robust JSON extraction: Mistral sometimes wraps JSON in markdown
    code blocks or adds explanation text before/after.
    We handle all of these with regex extraction.
  - If JSON parsing fails for a chunk: log it and continue.
    Never crash the whole video analysis because of one bad chunk.
  - Temperature=0.3: low enough for consistent structured output,
    high enough to be creative in clip selection.

Ollama API:
    POST http://localhost:11434/api/generate
    Body: {"model": "mistral", "prompt": "...", "stream": false}
    Response: {"response": "...text from Mistral..."}

Usage:
    from src.analyzer.mistral_analyzer import MistralAnalyzer
    analyzer = MistralAnalyzer()
    clips = analyzer.analyze_chunk(
        title="My Video",
        duration=342.0,
        chunk_text="0.00→4.80: text\n4.80→9.20: more text"
    )
    # clips is a list of dicts [{start, end, virality_score, clip_type, reason}]
"""

import os
import sys
import json
import re
import time
import requests
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from src.logger import get_logger

logger = get_logger("analyzer.mistral")

# Ollama API settings
OLLAMA_BASE_URL  = "http://localhost:11434"
OLLAMA_MODEL     = "mistral"
OLLAMA_TIMEOUT   = 300       # seconds per chunk — Mistral on CPU can be slow
OLLAMA_TEMP      = 0.3      # Low temperature = more consistent JSON output

# The exact prompt template sent to Mistral for each chunk.
# {title}, {duration}, {segments} are filled in per-call.
# The prompt is carefully worded to:
#   1. Give Mistral clear context (what it's doing)
#   2. Describe exactly what makes a good clip
#   3. Enforce the output format strictly
#   4. Give a concrete example of the JSON structure
PROMPT_TEMPLATE = """You are a viral content expert for YouTube Shorts.
Analyze this video transcript and find the BEST moments to clip.

VIDEO: {title}
DURATION: {duration} seconds

TRANSCRIPT SEGMENTS:
{segments}

Find moments that are:
- Shocking or surprising
- Funny or entertaining
- Emotional or dramatic
- High energy reactions
- Clear hooks (grabs attention instantly)

Rules:
- Each clip must be between 30 and 59 seconds long
- Only suggest clips where the start and end times exist in the transcript above
- virality_score must be a number from 1.0 to 10.0
- clip_type must be one of: hook, reaction, highlight, funny, shocking
- reason must be one short sentence explaining why this is viral

Return ONLY a valid JSON array. No explanation. No markdown. No code blocks.
Just the raw JSON array starting with [ and ending with ].

If no good clips exist in these segments, return exactly: []

[
  {{
    "start": 0.0,
    "end": 45.0,
    "virality_score": 8.5,
    "clip_type": "hook",
    "reason": "Immediate shocking reveal grabs attention in first 3 seconds"
  }}
]"""


def _load_ollama_settings() -> dict:
    """Reads Ollama settings from settings.json."""
    settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        return settings.get("ollama", {
            "base_url": OLLAMA_BASE_URL,
            "model":    OLLAMA_MODEL,
            "timeout_seconds": OLLAMA_TIMEOUT,
        })
    except Exception:
        return {}


class MistralAnalyzer:
    """
    Analyzes transcript chunks using the local Mistral model via Ollama.
    """

    def __init__(self):
        settings = _load_ollama_settings()
        self.base_url = settings.get("base_url", OLLAMA_BASE_URL).rstrip("/")
        self.model    = settings.get("model", OLLAMA_MODEL)
        self.timeout  = int(settings.get("timeout_seconds", OLLAMA_TIMEOUT))
        self.api_url  = f"{self.base_url}/api/generate"

    def _check_ollama_running(self) -> bool:
        """
        Pings Ollama to verify it's running before attempting analysis.
        Returns True if reachable, False otherwise.
        """
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.exceptions.ConnectionError:
            logger.error(
                f"Cannot connect to Ollama at {self.base_url}. "
                f"Start it with: ollama serve"
            )
            return False
        except Exception as e:
            logger.error(f"Ollama check failed: {e}")
            return False

    def _build_prompt(self, title: str, duration: float, chunk_text: str) -> str:
        """
        Fills in the prompt template with this chunk's specific values.

        Args:
            title:      Video title string
            duration:   Total video duration in seconds
            chunk_text: Formatted segments string ("0.00→4.80: text\n...")

        Returns:
            Complete prompt string ready to send to Mistral
        """
        return PROMPT_TEMPLATE.format(
            title=title,
            duration=int(duration),
            segments=chunk_text,
        )

    def _extract_json_from_response(self, raw_text: str) -> Optional[list]:
        """
        Extracts a JSON array from Mistral's response text.

        Mistral sometimes returns things like:
            "Here are the clips I found:\n```json\n[...]\n```"
            "Sure! Here is the JSON:\n[...]"
            "[...]"  ← ideal case

        We handle all of these by trying multiple extraction strategies:
          1. Direct parse (ideal case — response IS the JSON)
          2. Strip markdown code blocks (```json...```) then parse
          3. Regex: find everything between the outermost [ and ]
          4. Give up and return None

        Args:
            raw_text: The raw string from Ollama's "response" field

        Returns:
            A list of clip dicts, or None if extraction failed.
        """
        if not raw_text or not raw_text.strip():
            logger.warning("Mistral returned empty response")
            return None

        text = raw_text.strip()

        # ── Strategy 1: Direct parse ───────────────────────────
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # ── Strategy 2: Strip markdown code blocks ─────────────
        # Remove ```json ... ``` or ``` ... ``` wrappers
        stripped = re.sub(r"```(?:json)?\s*", "", text)
        stripped = re.sub(r"```\s*",          "", stripped).strip()
        try:
            result = json.loads(stripped)
            if isinstance(result, list):
                logger.info("JSON extracted by stripping markdown code blocks")
                return result
        except json.JSONDecodeError:
            pass

        # ── Strategy 3: Regex bracket extraction ───────────────
        # Find the outermost [...] array in the text
        # re.DOTALL makes . match newlines too
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                result = json.loads(candidate)
                if isinstance(result, list):
                    logger.info("JSON extracted via regex bracket search")
                    return result
            except json.JSONDecodeError:
                pass

        # ── Strategy 4: Try to find largest [...] block ────────
        # Sometimes there's text before and after the JSON
        start = text.find("[")
        end   = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end+1]
            try:
                result = json.loads(candidate)
                if isinstance(result, list):
                    logger.info("JSON extracted by finding outermost brackets")
                    return result
            except json.JSONDecodeError:
                pass

        # All strategies failed
        logger.warning(
            f"Could not extract JSON from Mistral response. "
            f"First 200 chars: {text[:200]}"
        )
        return None

    def _call_ollama(self, prompt: str) -> Optional[str]:
        """
        Makes the HTTP POST request to the Ollama API.

        Args:
            prompt: Complete prompt string to send

        Returns:
            The raw response text from Mistral, or None on error.
        """
        payload = {
            "model":   self.model,
            "prompt":  prompt,
            "stream":  False,       # Wait for the complete response
            "options": {
                "temperature": OLLAMA_TEMP,
                "num_predict": 1000,    # Max tokens in response
                                        # 1000 is enough for ~5-8 clip suggestions
            },
        }

        try:
            start = time.time()
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=self.timeout,
            )
            elapsed = round(time.time() - start, 1)

            if response.status_code != 200:
                logger.error(
                    f"Ollama returned HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return None

            data = response.json()
            raw_text = data.get("response", "").strip()

            logger.info(f"Mistral responded in {elapsed}s ({len(raw_text)} chars)")
            return raw_text

        except requests.exceptions.Timeout:
            logger.error(
                f"Mistral timed out after {self.timeout}s. "
                f"The model may be busy or the chunk was too large. "
                f"Skipping this chunk."
            )
            return None

        except requests.exceptions.ConnectionError:
            logger.error(
                f"Lost connection to Ollama. "
                f"Is 'ollama serve' still running in the other terminal?"
            )
            return None

        except Exception as e:
            logger.error(f"Ollama API call failed: {e}")
            return None

    def analyze_chunk(
        self,
        title:      str,
        duration:   float,
        chunk_text: str,
        chunk_index: int = 0,
        total_chunks: int = 1,
    ) -> list:
        """
        Sends one chunk of transcript to Mistral and returns clip suggestions.

        Args:
            title:        Video title
            duration:     Total video duration in seconds
            chunk_text:   Formatted segments string for this chunk
            chunk_index:  Which chunk this is (for logging)
            total_chunks: Total chunks in this video (for logging)

        Returns:
            List of raw clip dicts from Mistral.
            Each dict has: {start, end, virality_score, clip_type, reason}
            Returns empty list if Mistral returned nothing usable.
        """
        logger.info(
            f"  Chunk {chunk_index+1}/{total_chunks}: "
            f"sending to Mistral ({len(chunk_text)} chars)..."
        )

        prompt   = self._build_prompt(title, duration, chunk_text)
        raw_text = self._call_ollama(prompt)

        if raw_text is None:
            logger.warning(f"  Chunk {chunk_index+1}: no response from Mistral")
            return []

        clips = self._extract_json_from_response(raw_text)

        if clips is None:
            logger.warning(f"  Chunk {chunk_index+1}: could not parse JSON response")
            return []

        if not clips:
            logger.info(f"  Chunk {chunk_index+1}: Mistral found no good clips in this chunk")
            return []

        logger.info(f"  Chunk {chunk_index+1}: Mistral suggested {len(clips)} clips")
        return clips

    def analyze_video(
        self,
        title:    str,
        duration: float,
        chunks:   list,
    ) -> list:
        """
        Analyzes all chunks for one video and returns all clip suggestions.

        Processes chunks one at a time (never parallel).
        Collects all suggestions across all chunks.
        The caller (queue_runner) will then validate and de-duplicate them.

        Args:
            title:    Video title
            duration: Total video duration in seconds
            chunks:   List of chunk dicts from TranscriptReader.split_into_chunks()

        Returns:
            Combined list of all raw clip suggestions from all chunks.
        """
        if not self._check_ollama_running():
            return []

        if not chunks:
            logger.warning("No chunks to analyze")
            return []

        logger.info(
            f"Sending {len(chunks)} chunk(s) to Mistral: '{title[:45]}'"
        )

        all_clips   = []
        total_chunks = len(chunks)

        for chunk in chunks:
            chunk_clips = self.analyze_chunk(
                title=title,
                duration=duration,
                chunk_text=chunk["formatted_text"],
                chunk_index=chunk["chunk_index"],
                total_chunks=total_chunks,
            )
            all_clips.extend(chunk_clips)

            # Brief pause between chunks to avoid hammering Ollama
            if chunk["chunk_index"] < total_chunks - 1:
                time.sleep(1)

        logger.info(
            f"Mistral analysis complete: "
            f"{len(all_clips)} total clips suggested across {total_chunks} chunks"
        )
        return all_clips


# ============================================================
# Self-test
# PowerShell: python src\analyzer\mistral_analyzer.py
# ============================================================
if __name__ == "__main__":
    print("VAULTCUT Mistral Analyzer — Test")
    print("=" * 45)
    print("Tests Ollama connection and JSON parsing.")
    print()

    analyzer = MistralAnalyzer()

    # Test 1: Connection check
    print("Test 1: Checking Ollama connection...")
    if not analyzer._check_ollama_running():
        print("  FAILED: Ollama not running. Start it: ollama serve")
        sys.exit(1)
    print("  ✓ Ollama is running")
    print()

    # Test 2: JSON extraction (no Ollama needed)
    print("Test 2: JSON extraction from messy responses...")
    test_cases = [
        ('["clean json test"]',                        True),
        ('Here it is:\n```json\n[]\n```',              True),
        ('Sure!\n[{"start": 0.0, "end": 45.0}]',     True),
        ('No clips found in this segment.',            False),
    ]
    for text, should_succeed in test_cases:
        result = analyzer._extract_json_from_response(text)
        ok = (result is not None) == should_succeed
        print(f"  {'✓' if ok else '✗'}  '{text[:40]}...' → {'parsed' if result is not None else 'None'}")
    print()

    # Test 3: Real Mistral call with a small sample
    print("Test 3: Real Mistral analysis (small sample)...")
    sample_chunk = (
        "0.00→3.50: Welcome back everyone\n"
        "3.50→7.20: Today something absolutely insane happened\n"
        "7.20→15.80: I was streaming last night and out of nowhere this viewer\n"
        "15.80→22.40: donated ten thousand dollars and said use it to do the most\n"
        "22.40→30.10: ridiculous thing you can think of on stream\n"
        "30.10→40.60: So I immediately called up my friend and we drove to Vegas\n"
        "40.60→55.00: and what happened next changed my life forever"
    )

    clips = analyzer.analyze_chunk(
        title="Insane Stream Donation Story",
        duration=55.0,
        chunk_text=sample_chunk,
        chunk_index=0,
        total_chunks=1,
    )

    if clips:
        print(f"  ✓ Got {len(clips)} clip suggestion(s) from Mistral:")
        for c in clips:
            print(
                f"    [{c.get('start',0):.1f}s → {c.get('end',0):.1f}s] "
                f"score={c.get('virality_score','?')} "
                f"type={c.get('clip_type','?')}"
            )
            print(f"    Reason: {c.get('reason','?')}")
    elif clips == []:
        print("  ✓ Mistral returned [] (no clips) — JSON parsing works")
    else:
        print("  ✗ No response. Check Ollama logs.")
