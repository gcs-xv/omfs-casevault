from backend.services.drive_catalog import match_patient,patient_from_folder,search_catalog

def test_patient_folder_parsing_and_search():
    item={"id":"folder-1","name":"Tn. Pasien Sintetis / L / 30 Tahun / Rawat Jalan / RS Contoh / BPJS / RM 00.00.02","webViewLink":"https://drive.google.com/drive/folders/folder-1"}
    patient=patient_from_folder(item)
    assert patient["name"]=="Pasien Sintetis" and patient["title"]=="Tn."
    assert patient["sex"]=="L" and patient["age"]==30 and patient["age_unit"]=="Tahun"
    assert patient["insurance"]=="BPJS" and patient["hospital"]=="RS Contoh"
    assert patient["care_setting"]=="Rawat Jalan"
    assert patient["rm_normalized"]=="000002"
    metadata={"patient_folder_id":"folder-1","visit_folder_id":"visit-1","search_blob":"odontektomi operator contoh"}
    assert search_catalog([patient],[metadata],"operator")[0]["visit_folder_id"]=="visit-1"

def test_patient_folder_parsing_with_hyphen_delimiters():
    item={"id":"folder-2","name":"Ny. Pasien Contoh - P - 29 Tahun - Umum - RSGMP Contoh - RM 11.90.93"}
    patient=patient_from_folder(item)
    assert patient["name"]=="Pasien Contoh"
    assert patient["title"]=="Ny."
    assert patient["sex"]=="P" and patient["age"]==29
    assert patient["insurance"]=="Umum" and patient["hospital"]=="RSGMP Contoh"
    assert patient["rm"]=="11.90.93"

def test_exact_rm_reuses_folder_despite_minor_name_typo():
    existing=patient_from_folder({"id":"folder-1","name":"Tn. Pasien Sintetis / L / 30 Tahun / Rawat Jalan / RS Contoh / BPJS / RM 00.00.02"})
    result=match_patient([existing],{"full_name":"Pasien Sintetiss","sex":"L","age":30,"medical_record_number":"00-00-02"})
    assert result["status"]=="match" and result["match_type"]=="exact_rm"
    assert result["patient"]["id"]=="folder-1"

def test_small_rm_and_name_typo_can_reuse_folder_with_crosschecks():
    existing=patient_from_folder({"id":"folder-1","name":"Ny. Pasien Sintetis / P / 29 Tahun / Rawat Jalan / RS Contoh / Umum / RM 12.34.56"})
    result=match_patient([existing],{"full_name":"Pasien Sintetiss","sex":"P","age":29,"medical_record_number":"12.34.65"})
    assert result["status"]=="match" and result["match_type"]=="identity_typo"

def test_identity_conflict_never_silently_merges():
    existing=patient_from_folder({"id":"folder-1","name":"Tn. Pasien Satu / L / 30 Tahun / Rawat Jalan / RS Contoh / BPJS / RM 12.34.56"})
    gender_conflict=match_patient([existing],{"full_name":"Pasien Satu","sex":"P","age":30,"medical_record_number":"12.34.56"})
    different_rm=match_patient([existing],{"full_name":"Pasien Satu","sex":"L","age":30,"medical_record_number":"98.76.54"})
    assert gender_conflict["status"]=="conflict"
    assert different_rm["status"]=="conflict"
