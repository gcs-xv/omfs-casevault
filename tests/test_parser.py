from backend.services.soap_parser import parse_soap
from backend.utils.normalization import normalize_rm
from backend.utils.roman import roman_to_int

def test_roman():
    assert roman_to_int("XCV")==95 and roman_to_int("XIV")==14 and roman_to_int("XLV")==45
def test_rm_normalization():
    assert {normalize_rm(x) for x in ["11.64.26","11-64-26","116426"]}=={"116426"}
def test_pod_mismatch():
    text="Pasien Contoh / RM 00.00.01 / POD XCV (96)\nS: Catatan contoh\nO: Data contoh\nA: POD XCV\nP: Tinjau"
    d=parse_soap(text);assert any("mismatch" in x.lower() for x in d["warnings"])
def test_partial_soap_does_not_invent():
    d=parse_soap("Pasien Contoh / RM 00.00.01 / POD VII\nS: Tidak ada keluhan\nO: Luka baik\nA: POD VII ORIF Mandibula\nP: Kontrol")
    assert d["patient"]["age"] is None and d["dpjp"] is None and d["visit"]["visit_date"] is None
