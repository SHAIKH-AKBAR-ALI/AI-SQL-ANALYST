"""Only Groq settings may fall back to the project's private root .env."""
import os
from pathlib import Path

from dotenv import dotenv_values


def groq_settings() -> tuple[str, str]:
    values = dotenv_values(Path(__file__).resolve().parents[2] / '.env', interpolate=False)
    key = os.getenv('GROQ_API_KEY') or values.get('GROQ_API_KEY') or ''
    model = os.getenv('GROQ_MODEL') or values.get('GROQ_MODEL') or 'openai/gpt-oss-120b'
    return key.strip(), model.strip()
