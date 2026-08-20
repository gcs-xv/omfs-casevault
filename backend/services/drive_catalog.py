from __future__ import annotations
import re
from backend.utils.normalization import normalize_rm

FOLDER_MIME="application/vnd.google-apps.folder"

def patient_from_folder(item:dict)->dict:
    title=item.get("name","").strip()
    rm_match=re.search(r"(?i)\bRM\s*\.?\s*([\d.\- ]+)",title)
    rm=rm_match.group(1).strip() if rm_match else ""
    parts=[x.strip(" *_") for x in re.split(r"\s*/\s*|\s{2,}",title) if x.strip(" *_")]
    name=re.sub(r"(?i)^(Tn\.|Ny\.|Nn\.|An\.|By\.|Sdr\.|Sdri\.)\s*","",parts[0]).strip() if parts else title
    sex=next((p.upper() for p in parts if p.upper() in {"L","P"}),"")
    hospital=next((p for p in parts if re.search(r"(?i)RSGMP|RSUD|Rumah Sakit|Hospital",p)),"")
    return {"id":item.get("id"),"name":name,"rm":rm,"rm_normalized":normalize_rm(rm),"sex":sex,"hospital":hospital,"folder_name":title,"drive_url":item.get("webViewLink") or f"https://drive.google.com/drive/folders/{item.get('id')}"}

def search_catalog(patients:list[dict],metadata:list[dict],query:str)->list[dict]:
    q=query.casefold().strip();results=[];seen=set()
    for row in metadata:
        if q and q not in row.get("search_blob","").casefold():continue
        key=(row.get("patient_folder_id"),row.get("visit_folder_id"))
        if key in seen:continue
        seen.add(key);results.append(row)
    for patient in patients:
        if q and q not in f"{patient.get('folder_name','')} {patient.get('rm','')}".casefold():continue
        key=(patient.get("id"),None)
        if key not in seen:
            seen.add(key);results.append({"patient":patient,"patient_folder_id":patient.get("id"),"patient_drive_url":patient.get("drive_url"),"legacy":True})
    return results
