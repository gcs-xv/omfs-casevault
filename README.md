# OMFS CaseVault

Clinical Case, SOAP, Follow-up & Photo Archive for Oral and Maxillofacial Surgery.

CaseVault is a local-first FastAPI + Streamlit application built around one workflow: **paste the full WhatsApp SOAP → add photos → save**. It uses deterministic Python parsing, SQLite metadata, private Google Drive file storage, and Google OAuth. It does **not** require OpenAI, Gemini, Claude, any AI API key, paid database, or paid storage service.

## What works

- Deterministic Indonesian/English SOAP, patient, RM, date, POD, vital-sign, local-status, procedure, diagnosis, DPJP, and resident parsing
- Editable review before database commit; original SOAP always retained unchanged
- Patient reuse by normalized RM; episode scoring; duplicate-visit protection
- Structured Patient → Episode → Visit → Media database with UUIDs, indexes, foreign keys, audit records, and soft-delete fields
- Google OAuth allowlist and private Drive `drive.file` access
- Human-readable patient/episode/visit folders, `SOAP.txt`, standardized multi-photo filenames, image metadata, and partial-failure reporting
- Patient list, timeline API, visit viewer API, SQLite search, backup, restore, and CSV export

## macOS setup

Prerequisites: Python 3.11+ and a Google account. Google Cloud is used only to create free OAuth credentials and enable the Google Drive API; no Google compute service is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Create a long session secret:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Put that value in `SESSION_SECRET` in `.env`.

## Google OAuth and Drive setup

1. Open Google Cloud Console and create or select a project.
2. Enable only **Google Drive API**.
3. Configure the OAuth consent screen. For a personal/testing app, add the resident Google accounts as test users.
4. Create an **OAuth client ID → Web application**.
5. Add `http://127.0.0.1:8000/auth/callback` as an authorized redirect URI. It must exactly match `GOOGLE_REDIRECT_URI`.
6. In Google Drive, create a private folder named `OMFS CaseVault`. Open it and copy the folder ID from its URL.
7. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_DRIVE_ROOT_FOLDER_ID` in `.env`.
8. Set `ALLOWED_GOOGLE_EMAILS` to a comma-separated allowlist. Do not leave it empty on a shared machine.

CaseVault requests `openid email profile` and `drive.file`. It can manage only files/folders it creates through the app. It never applies public sharing permissions.

## Initialize and start

```bash
python scripts/init_db.py
chmod +x run_casevault.sh
./run_casevault.sh
```

Open `http://127.0.0.1:8501`. FastAPI documentation is at `http://127.0.0.1:8000/docs`.

For a strictly private local development/demo session, `AUTH_DISABLED=true` bypasses sign-in only when `ENVIRONMENT=development`. Never use this setting on a network-accessible or production system.

## Running separately

```bash
uvicorn backend.main:app --reload
streamlit run frontend/app.py
```

## Tests

```bash
pytest -q
```

The repository test suite uses synthetic, non-identifying fixtures. The complete clinical-format acceptance fixture is intentionally kept out of GitHub.

## Backup, restore, and export

Stop writes before restore. Backups are timestamped and never overwrite the only copy.

```bash
python scripts/backup_database.py --output-dir backups
python scripts/restore_database.py backups/casevault_backup_YYYY-MM-DD_HHMMSS.db --confirm
python scripts/export_metadata.py
```

Keep backups private: they contain identifiable clinical data. A Drive backup can be uploaded manually to the private CaseVault `System` folder; automatic scheduled Drive backup is intentionally not enabled by default.

## Troubleshooting

- **Authentication required:** click Sign in with Google in Settings. Confirm the email is in `ALLOWED_GOOGLE_EMAILS`.
- **OAuth redirect mismatch:** copy the redirect URI exactly in both Google Cloud and `.env`.
- **Drive authorization expired:** sign in again; failed images can be reselected without overwriting successful media records.
- **Drive permission missing:** confirm Drive API is enabled and the OAuth consent includes `drive.file`.
- **Database locked:** close duplicate processes, allow the current request to finish, then retry. Do not place the live SQLite file in a synced folder.
- **Unsupported photo:** use JPEG, PNG, or WebP. HEIC may first be exported as JPEG on macOS.
- **POD conflict:** CaseVault retains both values and requires review; it never silently changes clinical text.

Detailed errors are written locally to `data/casevault.log`; normal users see concise messages.

## Updating

Create a database backup, stop CaseVault, update the repository, activate the virtual environment, then run:

```bash
pip install -r requirements.txt
python scripts/init_db.py
./run_casevault.sh
```

## Privacy boundary

SOAP text and photos are never sent to an AI service, analytics tracker, or third-party image host. SQLite is the metadata/search source of truth; Google Drive is the original-file source of truth. Do not expose ports 8000 or 8501 publicly without TLS, a reviewed reverse proxy, and production hardening.
