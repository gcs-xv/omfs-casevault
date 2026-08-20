# OMFS CaseVault

Clinical Case, SOAP, Follow-up & Photo Archive for Oral and Maxillofacial Surgery.

CaseVault is a FastAPI + Streamlit application built around one workflow: **paste the full WhatsApp SOAP → choose episode/stage → add photos → save**. The Streamlit Cloud deployment uses Google Drive as its clinical source of truth. App users sign in with a CaseVault username/password; one archive Google account supplies Drive access in the background. It does **not** require an AI API key or a paid service.

## What works

- Deterministic Indonesian/English SOAP, patient, RM, date, POD, vital-sign, local-status, procedure, diagnosis, DPJP, and resident parsing
- Editable review before database commit; original SOAP always retained unchanged
- Patient reuse by normalized RM; episode scoring; duplicate-visit protection
- Structured Patient → Episode → Visit → Media database with UUIDs, indexes, foreign keys, audit records, and soft-delete fields
- Simple `user`/`admin` application login with an independently connected archive Google account
- Human-readable patient/episode/visit folders, `SOAP.txt`, standardized multi-photo filenames, image metadata, and partial-failure reporting
- Patient list, timeline API, visit viewer API, SQLite search, backup, restore, and CSV export
- Streamlit Cloud patient catalog read live from the configured Drive root, direct folder links, Drive-synchronized deletion, and searchable metadata for newly saved visits
- Explicit episode number/title and visit stage: Terjaring, Pre-op, Intra-op, or POD
- Separate DPJP, operator, assistant-operator, and resident parsing
- Expandable visit records with inline SOAP, private photo previews, and web downloads

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

## Free Streamlit Cloud archive-account setup

1. Open Google Cloud Console and create or select a project.
2. Enable only **Google Drive API**.
3. Configure the OAuth consent screen and set the audience to **In production**. This prevents the archive refresh token from expiring after seven days. Only the archive account completes this consent flow.
4. Create an **OAuth client ID → Web application**.
5. Add the exact Streamlit app root, for example `https://your-app.streamlit.app/`, as an authorized redirect URI.
6. Use a dedicated personal Google account as the archive owner. Keep the existing patient tree inside that account's **My Drive**, then copy the root folder ID from its URL.
7. Add these initial values to Streamlit App → Settings → Secrets:

```toml
EMBEDDED_MODE = "true"
CASEVAULT_PUBLIC_URL = "https://your-app.streamlit.app/"
GOOGLE_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "your-client-secret"
GOOGLE_DRIVE_ROOT_FOLDER_ID = "your-root-folder-id"
SESSION_SECRET = "a-long-random-value"
CASEVAULT_USER_PASSWORD = "user"
CASEVAULT_ADMIN_PASSWORD = "admin"
```

8. Sign in to CaseVault as `admin` / `admin`, open **Archive control**, and select **Connect archive Google account**. The unverified warning and Drive consent are completed once by the archive owner only.
9. CaseVault returns one `GOOGLE_ARCHIVE_REFRESH_TOKEN` line. Copy it into Streamlit Secrets and save. After the app restarts, all normal users see only the CaseVault username/password screen.

The literal `user/user` and `admin/admin` credentials are a temporary convenience requested for initial rollout. Replace both password values before storing real clinical data. The two roles currently have identical application capabilities; the role is recorded with new uploads for future access-control rules.

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

- **Authentication required:** use `user/user` or `admin/admin` until the two passwords are changed in Streamlit Secrets.
- **OAuth redirect mismatch during one-time setup:** make `CASEVAULT_PUBLIC_URL` exactly match an authorized redirect URI, including its trailing slash.
- **Archive authorization expired:** reconnect the archive account from Archive control and replace `GOOGLE_ARCHIVE_REFRESH_TOKEN`.
- **Drive permission missing:** confirm Drive API is enabled, the archive account owns or can edit the configured root folder, and the OAuth grant includes Drive access.
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

SOAP text and photos are never sent to an AI service, analytics tracker, or third-party image host. In Streamlit Cloud mode, Google Drive is the patient/file/search-metadata source of truth. Legacy Drive folders without `casevault-metadata.json` remain visible but cannot be searched by clinical fields that do not appear in their folder names. Do not use the deployment as a regulated production medical record without an organizational security, retention, access-control, and compliance review.
