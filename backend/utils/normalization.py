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

def clinical_photo_name(patient_name:str,medical_record_number:str,episode_number:int,visit_sequence:int,visit_date:str,visit_phase:str,pod_roman:str|None,index:int,original_name:str)->str:
    """Build a stable, readable Drive filename while preserving the original label."""
    patient="-".join(normalize_text(patient_name).title().split())[:34] or "Patient"
    rm=re.sub(r"[^A-Za-z0-9.-]+","-",medical_record_number or "").strip("-.")[:18] or "Unknown"
    if visit_phase=="POD":phase=f"POD-{pod_roman or '0'}"
    else:phase="-".join(normalize_text(visit_phase).title().split())[:16] or "Visit"
    original=original_name.rsplit("/",1)[-1];stem,dot,extension=original.rpartition(".")
    if not dot:stem=original;extension=""
    stem="-".join(normalize_text(stem).title().split())[:24] or "Image"
    extension=("."+re.sub(r"[^A-Za-z0-9]","",extension).lower()[:8]) if extension else ""
    prefix=f"{patient}_RM-{rm}_EP{int(episode_number):02d}_V{int(visit_sequence):02d}_{visit_date}_{phase}_{int(index):02d}"
    max_stem=max(1,118-len(prefix)-len(extension));name=f"{prefix}_{stem[:max_stem]}{extension}"
    return safe_name(name,"Clinical-Image"+extension)
