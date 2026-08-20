from backend.utils.normalization import clinical_photo_name


def test_clinical_photo_name_contains_filing_context_and_original_label():
    name=clinical_photo_name(
        "Pasien Sintetis",
        "11.95.46",
        2,
        3,
        "2026-08-20",
        "POD",
        "VII",
        1,
        "OPG kontrol.JPG",
    )
    assert name=="Pasien-Sintetis_RM-11.95.46_EP02_V03_2026-08-20_POD-VII_01_Opg-Kontrol.jpg"


def test_clinical_photo_name_is_drive_safe_and_bounded():
    name=clinical_photo_name("Nama / Sangat Panjang"*8,"00/00/01",1,1,"2026-08-20","Terjaring",None,12,"foto:*? panjang.webp")
    assert len(name)<=120
    assert not any(char in name for char in '\\/:*?"<>|')
    assert name.endswith(".webp")
