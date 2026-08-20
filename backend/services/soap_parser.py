from __future__ import annotations
import re
from datetime import date
from backend.utils.normalization import normalize_rm
from backend.utils.roman import roman_to_int

MONTHS={"januari":1,"februari":2,"maret":3,"april":4,"mei":5,"juni":6,"juli":7,"agustus":8,"september":9,"oktober":10,"november":11,"desember":12}
SECTION_RE=re.compile(r"(?im)^\s*(S|O|A|P|Subjective|Objective|Assessment|Plan)\s*:\s*(?=\S|$)")
CARE_SETTINGS={"rawat jalan":"Rawat Jalan","rawat inap":"Rawat Inap","igd":"IGD","emergency":"Emergency","day care":"Day Care","poliklinik":"Poliklinik"}

def clean_block(value:str)->str:
    return "\n".join(re.sub(r"^\s*[•\-*]\s*", "", x).strip() for x in value.strip().splitlines() if x.strip()).strip()

def parse_date(text:str)->date|None:
    m=re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)",text)
    if m:
        try:return date(int(m[3]),int(m[2]),int(m[1]))
        except ValueError:return None
    m=re.search(r"(?i)(\d{1,2})\s+("+"|".join(MONTHS)+r")\s+(\d{4})",text)
    if m:
        try:return date(int(m[3]),MONTHS[m[2].lower()],int(m[1]))
        except ValueError:return None
    return None

def split_sections(text:str)->tuple[dict[str,str],str]:
    # Select section markers in clinical order. This prevents respiratory-rate
    # "P : 20" and temperature "S : 36.5" inside Objective becoming sections.
    all_matches=list(SECTION_RE.finditer(text)); aliases={"subjective":"S","objective":"O","assessment":"A","plan":"P"}; chosen=[]; cursor=0
    for wanted in ("S","O","A","P"):
        match=next((m for m in all_matches if m.start()>=cursor and aliases.get(m[1].lower(),m[1].upper())==wanted),None)
        if match:chosen.append(match);cursor=match.end()
    sections={};header=text[:chosen[0].start()].strip() if chosen else text
    for i,m in enumerate(chosen):
        key=aliases.get(m[1].lower(),m[1].upper());end=chosen[i+1].start() if i+1<len(chosen) else len(text);sections[key]=text[m.end():end].strip()
    return sections,header

def parse_header(header:str)->dict:
    lines=[x.strip() for x in header.splitlines() if x.strip()]
    candidate=next((x for x in lines if re.search(r"(?i)\bRM\s*\.?\s*\d",x)), lines[-1] if lines else "")
    parts=[x.strip() for x in candidate.split("/")]
    first=re.match(r"(?i)^(Tn\.|Ny\.|Nn\.|An\.|By\.|Sdr\.|Sdri\.)?\s*(.+)$",parts[0] if parts else "")
    title=(first.group(1) or "").strip() or None; name=first.group(2).strip() if first else ""
    sex_part=next((p for p in parts[1:] if re.fullmatch(r"(?i)L|P|Laki-laki|Perempuan|Male|Female",p)),None)
    sex={"laki-laki":"L","male":"L","perempuan":"P","female":"P"}.get((sex_part or "").casefold(),sex_part.upper() if sex_part else None)
    age=next((re.match(r"(?i)^(\d+)\s*(Tahun|th|Bulan|Hari)\b",p) for p in parts[1:] if re.match(r"(?i)^(\d+)\s*(Tahun|th|Bulan|Hari)\b",p)),None)
    rm=re.search(r"(?i)\bRM\s*\.?\s*([\d.\- ]+)",candidate)
    pod=re.search(r"(?i)\bPOD\s+([IVXLCDM]+)(?:\s*\((\d+)\))?",candidate)
    rm_value=rm.group(1).strip() if rm else None
    if rm_value:
        rm_value=re.sub(r"\s+(?=POD\b)","",rm_value,flags=re.I).strip()
    rm_index=next((i for i,p in enumerate(parts) if re.match(r"(?i)^RM\b",p)),len(parts))
    location_index=next((i for i,p in enumerate(parts[1:rm_index],1) if p.casefold() in CARE_SETTINGS),None)
    care_setting=CARE_SETTINGS.get(parts[location_index].casefold()) if location_index is not None else None
    hospital=insurance=None
    if location_index is not None:
        # Canonical SOAP order: service / hospital / insurance / RM. Insurance is
        # deliberately positional so providers such as Jasa Raharja also parse.
        structured=parts[location_index+1:rm_index]
        hospital=structured[0] if structured else None
        insurance=" / ".join(structured[1:]) or None
    else:
        # Backwards-compatible fallback for older app folders: insurance / hospital / RM.
        prior=[p for p in parts[1:rm_index] if p!=sex_part and not re.match(r"(?i)^\d+\s*(Tahun|th|Bulan|Hari)\b",p)]
        hospital=prior[-1] if prior else None
        insurance=" / ".join(prior[:-1]) or None
    reported=int(pod.group(2)) if pod and pod.group(2) else None; calculated=roman_to_int(pod.group(1)) if pod else None
    age_unit="Tahun" if age and age.group(2).casefold()=="th" else (age.group(2).title() if age else None)
    return {"raw_header":candidate,"title":title,"full_name":name,"sex":sex,"age":int(age.group(1)) if age else None,"age_unit":age_unit,"care_setting":care_setting,"insurance":insurance,"hospital":hospital,"medical_record_number":rm_value,"medical_record_number_normalized":normalize_rm(rm_value),"pod_roman":pod.group(1).upper() if pod else None,"pod_number":reported if reported is not None else calculated,"pod_calculated":calculated}

def extract_labeled(text:str,label:str,pattern:str=r"([^\n]+)")->str|None:
    m=re.search(rf"(?im)^\s*{label}\s*:\s*{pattern}",text); return m.group(1).strip(" ,") if m else None

def parse_vitals(obj:str)->dict:
    bp=re.search(r"(?im)^\s*TD\s*:\s*(\d{2,3})\s*/\s*(\d{2,3})",obj)
    def num(labels,decimal=False):
        m=re.search(rf"(?im)^\s*(?:{labels})\s*:\s*(\d+(?:[.,]\d+)?)",obj)
        return (float(m[1].replace(",",".")) if decimal else int(float(m[1].replace(",",".")))) if m else None
    ku=extract_labeled(obj,"KU")
    condition=ku.split(",")[0].strip() if ku else None
    consciousness=(ku.split(",",1)[1].strip() if ku and "," in ku else (re.search(r"(?i)\b(Compos\s+Mentis|Somnolen|Sopor|Koma)\b",obj).group(1) if re.search(r"(?i)\b(Compos\s+Mentis|Somnolen|Sopor|Koma)\b",obj) else None))
    spo=re.search(r"(?im)^\s*SpO[₂2]\s*:\s*(\d+)%?\s*(?:\(([^)]+)\))?",obj)
    return {"general_condition":condition,"consciousness":consciousness,"blood_pressure_systolic":int(bp[1]) if bp else None,"blood_pressure_diastolic":int(bp[2]) if bp else None,"heart_rate":num("N|HR"),"respiratory_rate":num("P|RR"),"temperature_celsius":num("S|T",True),"spo2_percent":int(spo[1]) if spo else None,"oxygen_support":spo[2].strip() if spo and spo[2] else None}

def local_status(obj:str)->tuple[str|None,str|None]:
    eo=re.search(r"(?is)(?:^|\n)\s*(?:E\s*\.?\s*O|Extra\s*Oral|Extraoral)\s*:\s*(.*?)(?=\n\s*(?:I\s*\.?\s*O|IO|Intra\s*Oral|Intraoral)\s*:|\Z)",obj)
    io=re.search(r"(?is)(?:^|\n)\s*(?:I\s*\.?\s*O|IO|Intra\s*Oral|Intraoral)\s*:\s*(.*?)(?=\n\s*(?:A|P|Izin usul|Residen|DPJP)\s*:|\Z)",obj)
    return clean_block(eo[1]) if eo else None, clean_block(io[1]) if io else None

def split_clinicians(value:str|None)->list[str]:
    return [x.strip() for x in re.split(r",\s*(?=(?:drg\.|Dr\.|[A-Z]))",value or "") if x.strip()]

def parse_people(text:str)->tuple[list[str],str|None,str|None,list[str]]:
    rm=re.search(r"(?im)^\s*Residen\s*:\s*(.+)$",text); residents=[]
    if rm: residents=split_clinicians(rm[1])
    dm=re.search(r"(?im)^\s*(?:DPJP|Dokter Penanggung Jawab)\s*:\s*(.+)$",text)
    om=re.search(r"(?im)^\s*Operator\s*:\s*(.+)$",text)
    am=re.search(r"(?im)^\s*(?:Asisten Operator|Assistant Operator)\s*:\s*(.+)$",text)
    return residents,dm[1].strip() if dm else None,om[1].strip() if om else None,split_clinicians(am[1] if am else None)

def assessment_parts(value:str)->tuple[list[str],list[str],str|None]:
    flat=clean_block(value); anesthesia="General Anesthesia" if re.search(r"(?i)general\s+anestesi",flat) else None
    before=re.split(r"(?i)\s+a\.i\s+",flat,maxsplit=1); clinical=re.sub(r"(?i)^POD\s+[IVXLCDM]+(?:\s*\(\d+\))?\s*","",before[0]).strip()
    clinical=re.sub(r"(?i)\s+dalam\s+general\s+anestesi.*$","",clinical).strip()
    procedures=[]
    for p in re.split(r"\s*\+\s*",clinical):
        p=p.strip(" •")
        if re.search(r"(?i)\b(ORIF|IDW|Arch Bar|Odontektomi|Ekstraksi|Biopsi|Enukleasi|Reseksi|Debridement|Rekonstruksi|Mandibulectomy|Maxillectomy)\b",p):
            p=re.sub(r"(?i)ORIF\s+regio\s+mandibula","ORIF Mandibula",p); procedures.append(p)
    diagnoses=[x.strip(" •") for x in re.split(r"\s*\+\s*",before[1])] if len(before)>1 else []
    return procedures,diagnoses,anesthesia

def parse_soap(text:str)->dict:
    original=text; sections,header_text=split_sections(text); patient=parse_header(header_text); obj=sections.get("O","")
    eo,io=local_status(obj); residents,dpjp,operator,assistant_operators=parse_people(text)
    plan_full=sections.get("P",""); proposal_m=re.search(r"(?is)\bIzin\s+usul\s*:\s*(.*?)(?=\n\s*(?:Mohon|Terima kasih|Residen|DPJP)\b|\Z)",plan_full)
    plan=re.split(r"(?i)\n\s*Izin\s+usul\s*:",plan_full,maxsplit=1)[0]
    procedures,diagnoses,anesthesia=assessment_parts(sections.get("A",""))
    warnings=[]
    if patient["pod_number"] is not None and patient["pod_calculated"] is not None and patient["pod_number"]!=patient["pod_calculated"]: warnings.append(f"POD mismatch: Roman {patient['pod_roman']} = {patient['pod_calculated']}, entered = {patient['pod_number']}")
    for label,value in (("Medical record number",patient["medical_record_number"]),("Visit date",parse_date(text))):
        if not value:warnings.append(f"{label} not found; review before saving")
    phase="POD" if patient["pod_roman"] else ("Intra-op" if re.search(r"(?i)\b(intra[ -]?op|laporan operasi)\b",text) else ("Pre-op" if re.search(r"(?i)\b(pre[ -]?op|pra[ -]?operasi)\b",text) else "Terjaring"))
    location=patient.get("care_setting") or ("Rawat Jalan" if re.search(r"(?i)Rawat Jalan",text) else ("Rawat Inap" if re.search(r"(?i)Rawat Inap",text) else None))
    return {"patient":patient,"visit":{"visit_date":parse_date(text).isoformat() if parse_date(text) else None,"visit_type":"Postoperative Day" if patient["pod_roman"] else "Outpatient","visit_phase":phase,"pod_roman":patient["pod_roman"],"pod_number":patient["pod_number"],"location":location,"hospital":patient["hospital"],**parse_vitals(obj),"extraoral":eo,"intraoral":io},"soap":{"subjective":clean_block(sections.get("S","")),"objective_raw":obj,"assessment":clean_block(sections.get("A","")),"plan":clean_block(plan),"plan_items":clean_block(plan).splitlines() if plan else [],"proposal":clean_block(proposal_m[1]) if proposal_m else None,"original_soap":original},"episode_candidates":[],"procedures":procedures,"diagnoses":diagnoses,"anesthesia":anesthesia,"residents":residents,"operator":operator,"assistant_operators":assistant_operators,"dpjp":{"full_name":dpjp} if dpjp else None,"warnings":warnings,"confidence":{"patient":0.95 if patient["medical_record_number"] else 0.5,"visit":0.95 if parse_date(text) else 0.55}}
