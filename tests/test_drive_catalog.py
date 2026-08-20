from backend.services.drive_catalog import patient_from_folder,search_catalog

def test_patient_folder_parsing_and_search():
    item={"id":"folder-1","name":"Tn. Pasien Sintetis / L / 30 Tahun / BPJS / RS Contoh / RM 00.00.02","webViewLink":"https://drive.google.com/drive/folders/folder-1"}
    patient=patient_from_folder(item)
    assert patient["name"]=="Pasien Sintetis" and patient["title"]=="Tn."
    assert patient["sex"]=="L" and patient["age"]==30 and patient["age_unit"]=="Tahun"
    assert patient["insurance"]=="BPJS" and patient["hospital"]=="RS Contoh"
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
