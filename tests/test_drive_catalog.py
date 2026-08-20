from backend.services.drive_catalog import patient_from_folder,search_catalog

def test_patient_folder_parsing_and_search():
    item={"id":"folder-1","name":"Tn. Pasien Sintetis / L / 30 Tahun / RS Contoh / RM 00.00.02","webViewLink":"https://drive.google.com/drive/folders/folder-1"}
    patient=patient_from_folder(item)
    assert patient["name"]=="Pasien Sintetis" and patient["rm_normalized"]=="000002"
    metadata={"patient_folder_id":"folder-1","visit_folder_id":"visit-1","search_blob":"odontektomi operator contoh"}
    assert search_catalog([patient],[metadata],"operator")[0]["visit_folder_id"]=="visit-1"
