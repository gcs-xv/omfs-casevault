from __future__ import annotations
import re
import unicodedata
from difflib import SequenceMatcher
from backend.utils.normalization import normalize_rm

FOLDER_MIME="application/vnd.google-apps.folder"
HONORIFICS={"tn":"Tn.","ny":"Ny.","nn":"Nn.","an":"An.","by":"By.","sdr":"Sdr.","sdri":"Sdri."}
CARE_SETTINGS={"rawat jalan":"Rawat Jalan","rawat inap":"Rawat Inap","igd":"IGD","emergency":"Emergency","day care":"Day Care","poliklinik":"Poliklinik"}

def normalize_patient_name(value:str|None)->str:
    value=unicodedata.normalize("NFKD",value or "").encode("ascii","ignore").decode().casefold()
    value=re.sub(r"^(?:tn|ny|nn|an|by|sdr|sdri)\.?\s+","",value)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",value).split())

def edit_distance(left:str,right:str)->int:
    """Small Damerau-Levenshtein distance for normalized RM typo detection."""
    rows=[[0]*(len(right)+1) for _ in range(len(left)+1)]
    for i in range(len(left)+1):rows[i][0]=i
    for j in range(len(right)+1):rows[0][j]=j
    for i in range(1,len(left)+1):
        for j in range(1,len(right)+1):
            rows[i][j]=min(rows[i-1][j]+1,rows[i][j-1]+1,rows[i-1][j-1]+(left[i-1]!=right[j-1]))
            if i>1 and j>1 and left[i-1]==right[j-2] and left[i-2]==right[j-1]:
                rows[i][j]=min(rows[i][j],rows[i-2][j-2]+1)
    return rows[-1][-1]

def match_patient(patients:list[dict],incoming:dict)->dict:
    """Resolve an archive patient conservatively; never silently merge conflicts."""
    incoming_rm=normalize_rm(incoming.get("medical_record_number"));incoming_name=normalize_patient_name(incoming.get("full_name"))
    incoming_sex=(incoming.get("sex") or "").upper();incoming_age=incoming.get("age")
    assessed=[]
    for patient in patients:
        existing_rm=patient.get("rm_normalized") or normalize_rm(patient.get("rm"));existing_name=normalize_patient_name(patient.get("name"))
        name_similarity=SequenceMatcher(None,incoming_name,existing_name).ratio() if incoming_name and existing_name else 1.0
        existing_sex=(patient.get("sex") or "").upper();sex_conflict=bool(incoming_sex and existing_sex and incoming_sex!=existing_sex)
        existing_age=patient.get("age");age_conflict=bool(incoming_age is not None and existing_age is not None and abs(int(incoming_age)-int(existing_age))>3)
        rm_distance=edit_distance(incoming_rm,existing_rm) if incoming_rm and existing_rm else 99
        assessed.append({"patient":patient,"exact_rm":bool(incoming_rm and incoming_rm==existing_rm),"rm_distance":rm_distance,"name_similarity":name_similarity,"sex_conflict":sex_conflict,"age_conflict":age_conflict})

    exact=[x for x in assessed if x["exact_rm"]]
    if exact:
        best=max(exact,key=lambda x:x["name_similarity"])
        if best["sex_conflict"] or best["name_similarity"]<0.60:
            return {"status":"conflict","reason":"RM sama, tetapi nama atau gender berbeda. Periksa identitas sebelum menyimpan.",**best}
        return {"status":"match","match_type":"exact_rm","confidence":1.0,**best}

    fuzzy=[x for x in assessed if x["rm_distance"]<=1 and x["name_similarity"]>=0.86 and not x["sex_conflict"] and not x["age_conflict"]]
    fuzzy.sort(key=lambda x:(x["rm_distance"],-x["name_similarity"]))
    if len(fuzzy)==1:
        return {"status":"match","match_type":"identity_typo","confidence":round(0.82+0.15*fuzzy[0]["name_similarity"],2),**fuzzy[0]}
    if len(fuzzy)>1:
        return {"status":"conflict","reason":"Lebih dari satu folder memiliki identitas yang terlalu mirip. Periksa RM sebelum menyimpan.",**fuzzy[0]}

    suspicious=[x for x in assessed if x["name_similarity"]>=0.94 and not x["sex_conflict"] and not x["age_conflict"]]
    if suspicious:
        best=min(suspicious,key=lambda x:x["rm_distance"])
        return {"status":"conflict","reason":"Nama, gender, dan usia cocok tetapi RM berbeda. Koreksi RM atau verifikasi pasien sebelum membuat folder baru.",**best}
    return {"status":"new","patient":None,"confidence":1.0}

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
