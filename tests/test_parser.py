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

def test_people_roles_and_phase_are_separate():
    text="""Pasien Sintetis / L / 30 Tahun / Rawat Jalan / RS Contoh / RM 00.00.02 POD VII Prosedur Contoh
S: Keluhan contoh
O: Kondisi stabil
A: POD VII Odontektomi gigi 38
P: Kontrol
Operator: drg. Operator Contoh
Asisten Operator: drg. Asisten Satu, drg. Asisten Dua
Residen: drg. Residen Contoh
DPJP: drg. DPJP Contoh, Sp.B.M.Mf."""
    d=parse_soap(text)
    assert d["visit"]["visit_phase"]=="POD" and d["visit"]["pod_number"]==7
    assert d["operator"]=="drg. Operator Contoh"
    assert d["assistant_operators"]==["drg. Asisten Satu","drg. Asisten Dua"]
    assert "Residen" not in d["operator"]

def test_outpatient_screening_is_not_pod_zero():
    text="""Pasien Sintetis / L / 30 Tahun / Rawat Jalan / RS Contoh / RM 00.00.03
S: Keluhan contoh
O: Kondisi stabil
A: Impaksi gigi contoh
P: Rencana pemeriksaan dan tindakan"""
    d=parse_soap(text)
    assert d["visit"]["visit_phase"]=="Terjaring"
    assert d["visit"]["pod_number"] is None and d["visit"]["pod_roman"] is None

def test_identity_header_uses_canonical_soap_field_order():
    text="""Izin melaporkan pasien Rawat Jalan RS Contoh, Kamis, 20/08/2026.

Tn. Pasien Sintetis / L / 29 Tahun / Rawat Jalan / RSGMP Contoh / Jasa Raharja / RM 00.00.04

S: Keluhan sintetis
O: Kondisi stabil
A: Diagnosis contoh
P: Rencana contoh"""
    d=parse_soap(text)
    assert d["patient"]["title"]=="Tn." and d["patient"]["full_name"]=="Pasien Sintetis"
    assert d["patient"]["sex"]=="L" and d["patient"]["age"]==29 and d["patient"]["age_unit"]=="Tahun"
    assert d["visit"]["location"]=="Rawat Jalan"
    assert d["patient"]["hospital"]=="RSGMP Contoh"
    assert d["patient"]["insurance"]=="Jasa Raharja"
    assert d["patient"]["medical_record_number"]=="00.00.04"
