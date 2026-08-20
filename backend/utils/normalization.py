import re
import unicodedata

def normalize_rm(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")

def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()

def safe_name(value: str, fallback: str = "Untitled") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", value).strip(" .")
    return value[:120] or fallback

