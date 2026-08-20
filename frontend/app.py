from __future__ import annotations
import os
import httpx
import streamlit as st

API=os.getenv("API_URL","http://127.0.0.1:8000")
st.set_page_config(page_title="OMFS CaseVault",page_icon="🗂️",layout="wide",initial_sidebar_state="expanded")
if st.query_params.get("session_token"):
    st.session_state.api_session_token=st.query_params["session_token"]
    st.query_params.clear()
st.markdown("""<style>
[data-testid="stSidebar"]{background:#111d2e;color:#e9eef5}[data-testid="stSidebar"] *{color:#e9eef5}
.block-container{max-width:1440px;padding-top:2rem}.cv-card{border:1px solid #dce3ea;border-radius:14px;padding:18px;background:#fff;margin:8px 0}
.eyebrow{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:#557087;font-weight:700}.muted{color:#66788a}.ok{color:#16794b}.warn{color:#a05a00}
h1,h2,h3{color:#17283b}div.stButton>button[kind="primary"]{background:#176b67;border-color:#176b67;border-radius:9px;font-weight:700}
</style>""",unsafe_allow_html=True)

def api(method,path,**kwargs):
    try:
        if st.session_state.get("api_session_token"):
            kwargs.setdefault("headers",{})["Authorization"]=f"Bearer {st.session_state.api_session_token}"
        r=httpx.request(method,API+path,timeout=90,**kwargs)
        if r.status_code==401:
            st.error("Sign in is required. Configure Google OAuth, or use AUTH_DISABLED=true only for a private local development session.");return None
        r.raise_for_status();return r.json()
    except Exception as e:st.error(f"CaseVault service unavailable: {e}");return None

def sidebar():
    st.sidebar.markdown("## OMFS CaseVault\nClinical Case Archive")
    page=st.sidebar.radio("Navigation",["Quick Upload","Patients","Search","Settings"],label_visibility="collapsed")
    st.sidebar.markdown("---")
    health=api("GET","/health")
    if health:
        st.sidebar.caption("● Service connected")
        if not health["auth_configured"]:st.sidebar.warning("Google OAuth needs setup")
    return page

def preview(d):
    p=d["patient"];v=d["visit"]
    st.markdown('<div class="eyebrow">Parse preview · edit before save</div>',unsafe_allow_html=True)
    a,b=st.columns(2)
    with a:
        st.markdown("#### Patient")
        p["full_name"]=st.text_input("Full name",p.get("full_name") or "")
        c1,c2=st.columns(2);p["medical_record_number"]=c1.text_input("RM",p.get("medical_record_number") or "");p["sex"]=c2.text_input("Sex",p.get("sex") or "")
        c1,c2=st.columns(2);p["age"]=c1.number_input("Age",0,150,p.get("age") or 0);p["insurance"]=c2.text_input("Insurance",p.get("insurance") or "")
        p["hospital"]=st.text_input("Hospital",p.get("hospital") or "")
    with b:
        st.markdown("#### Visit")
        v["visit_date"]=st.text_input("Date (YYYY-MM-DD)",v.get("visit_date") or "")
        c1,c2=st.columns(2);v["pod_roman"]=c1.text_input("POD Roman",v.get("pod_roman") or "");v["pod_number"]=c2.number_input("POD",0,1000,v.get("pod_number") or 0)
        d["diagnoses"]=st.text_area("Diagnosis · one per line","\n".join(d.get("diagnoses",[]))).splitlines()
        d["procedures"]=st.text_area("Procedures · one per line","\n".join(d.get("procedures",[]))).splitlines()
    if d["warnings"]:
        for w in d["warnings"]:st.warning(w)
    st.markdown("#### SOAP")
    t1,t2,t3=st.tabs(["Subjective","Assessment","Plan"])
    d["soap"]["subjective"]=t1.text_area("Subjective",d["soap"].get("subjective","") or "",label_visibility="collapsed")
    d["soap"]["assessment"]=t2.text_area("Assessment",d["soap"].get("assessment","") or "",label_visibility="collapsed")
    d["soap"]["plan"]=t3.text_area("Plan",d["soap"].get("plan","") or "",label_visibility="collapsed")

def quick_upload():
    st.markdown("# New clinical visit")
    st.caption("Paste the WhatsApp SOAP, review what was recognized, add photos, and save.")
    soap=st.text_area("WhatsApp SOAP",height=260,placeholder="Paste the complete SOAP here…",key="soap_input")
    if st.button("PARSE SOAP",type="primary",disabled=not soap):
        result=api("POST","/parser/soap",json={"soap":soap})
        if result:st.session_state.parsed=result
    if "parsed" in st.session_state:
        st.divider();preview(st.session_state.parsed)
        photos=st.file_uploader("Clinical photos",type=["jpg","jpeg","png","webp"],accept_multiple_files=True,help="1–30 photos; upload begins only when you save")
        if photos:st.caption(f"{len(photos)} photo(s) ready · originals remain private in Google Drive")
        if st.button("SAVE VISIT",type="primary",use_container_width=True):
            result=api("POST","/visits",json=st.session_state.parsed)
            if result:
                upload={"uploaded":[],"failed":[],"save_state":"uploading"}
                if photos:
                    files=[("files",(f.name,f.getvalue(),f.type)) for f in photos];upload=api("POST",f"/visits/{result['visit_id']}/media",files=files) or upload
                st.session_state.saved={**result,**upload,"name":st.session_state.parsed["patient"]["full_name"],"rm":st.session_state.parsed["patient"]["medical_record_number"],"pod":st.session_state.parsed["visit"].get("pod_number")}
    if "saved" in st.session_state:
        x=st.session_state.saved;st.success(f"✓ VISIT SAVED — {x['name']} · RM {x['rm']} · POD {x['pod']} · {len(x.get('uploaded',[]))} photos uploaded")
        if x.get("failed"):st.warning(f"{len(x['failed'])} photo(s) failed. Re-select only those files to retry.")
        if x.get("drive_url"):st.link_button("OPEN DRIVE FOLDER",x["drive_url"])

def patients():
    st.markdown("# Patients");rows=api("GET","/patients") or []
    for p in rows:
        with st.container(border=True):st.markdown(f"**{p['name']}**  \nRM {p['rm']} · {p.get('sex') or '—'} · {p.get('hospital') or '—'}")

def search():
    st.markdown("# Search");q=st.text_input("Name, RM, diagnosis, or procedure")
    if q:
        for p in api("GET","/search",params={"q":q}) or []:st.markdown(f'<div class="cv-card"><b>{p["name"]}</b><br><span class="muted">RM {p["rm"]} · {p.get("hospital") or "—"}</span></div>',unsafe_allow_html=True)

def settings_page():
    st.markdown("# Settings & setup")
    h=api("GET","/health") or {};st.json(h)
    st.markdown(f"[Sign in with Google]({API}/auth/login)")
    st.info("OAuth uses Drive's app-created-files scope. CaseVault never creates public sharing links and sends no clinical data to AI services.")

page=sidebar()
{"Quick Upload":quick_upload,"Patients":patients,"Search":search,"Settings":settings_page}[page]()
