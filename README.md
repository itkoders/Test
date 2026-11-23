# MIRZAYEV GROUP — Online Test & Certificate

Uzbek-language online testing MVP with student ID, admin-created test codes, automatic scoring, and printable certificates.

## Render
- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Python app listens through Gunicorn on Render's assigned port.

## Required environment variables
- `SECRET_KEY`: long random value
- `ADMIN_USERNAME`: admin login (default: admin)
- `ADMIN_PASSWORD`: set a strong private password before using the admin panel

## Test question format
One question per line:
`Question text|Option A|Option B|Option C|Option D|Correct letter`

Example:
`Uzbekistan capital?|Tashkent|Samarkand|Bukhara|Khiva|A`

## Current MVP limitations
- SQLite is used for the first test build. Render's local filesystem is not durable across all redeploy/restart scenarios. Connect PostgreSQL before real student data or production use.
- Test documents (PDF/DOCX), timed enforcement, Telegram bot/Mini App, QR-based certificate verification, and persistent file storage are next development stages.
- Set `ADMIN_PASSWORD` and `SECRET_KEY` in Render before opening admin access.
