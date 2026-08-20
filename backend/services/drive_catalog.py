from __future__ import annotations
import re
from backend.utils.normalization import normalize_rm

FOLDER_MIME="application/vnd.google-apps.folder"
HONORIFICS={"tn":"Tn.","ny":"Ny.","nn":"Nn.","an":"An.","by":"By.","sdr":"Sdr.","sdri":"Sdri."}
CARE_SETTINGS={"rawat jalan":"Rawat Jalan","rawat inap":"Rawat Inap","igd":"IGD","emergency":"Emergency","day care":"Day Care","poliklinik":"Poliklinik"}

def patient_from_folder(item:dict)->dict:
    folder_name=item.get("name","").strip()
    rm_match=re.search(r"(?i)\bRM\s*\.?\s*([\d.\- ]+)",folder_name)
    rm=rm_match.group(1).strip() if rm_match else ""
    parts=[x.strip(" *_") for x in re.split(r"\s*/\s*|\s+-\s+|\s{2,}",folder_name) if x.strip(" *_")]
    identity=parts[0] if parts else folder_name
    title_match=re.match(r"(?i)^(Tn|Ny|Nn|An|By|Sdr|Sdri)\.?\s+",identity)
    honorific=HONORIFICS.get(title_match.group(1).casefold(),"") if title_match else ""
    name=identity[title_match.end():].strip() if title_match else identity
    sex=next((p.upper() for p in parts if p.upper() in {"L","P"}),"")
    age_match=next((re.fullmatch(r"(?i)(\d+)\s*(Tahun|Bulan|Hari)",p) for p in parts if re.fullmatch(r"(?i)(\d+)\s*(Tahun|Bulan|Hari)",p)),None)
    age=int(age_match.group(1)) if age_match else None
    age_unit=age_match.group(2).title() if age_match else ""
    rm_index=next((i for i,p in enumerate(parts) if re.match(r"(?i)^RM\b",p)),len(parts))
    location_index=next((i for i,p in enumerate(parts[1:rm_index],1) if p.casefold() in CARE_SETTINGS),None)
    location=CARE_SETTINGS.get(parts[location_index].casefold()) if location_index is not None else ""
    if location_index is not None:
        structured=parts[location_index+1:rm_index]
        hospital=structured[0] if structured else ""
        insurance=" - ".join(structured[1:]) if len(structured)>1 else ""
    else:
        remaining=[p for p in parts[1:rm_index] if p.upper() not in {"L","P"} and not re.fullmatch(r"(?i)\d+\s*(Tahun|Bulan|Hari)",p)]
        hospital=remaining[-1] if remaining else ""
        insurance=" - ".join(remaining[:-1]) if len(remaining)>1 else ""
    return {"id":item.get("id"),"name":name,"title":honorific,"rm":rm,"rm_normalized":normalize_rm(rm),"sex":sex,"age":age,"age_unit":age_unit,"care_setting":location,"insurance":insurance,"hospital":hospital,"folder_name":folder_name,"drive_url":item.get("webViewLink") or f"https://drive.google.com/drive/folders/{item.get('id')}"}

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
