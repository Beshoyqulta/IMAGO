# Imago Youth

Full-stack points and leaderboard app for the Imago Youth Sanawy meeting.

## Local run

1. Create a Supabase project and run `schema.sql` in the SQL editor.
2. Copy `.env.example` to `.env` and fill in the values.
3. Install dependencies with `pip install -r requirements.txt`.
4. Start with `uvicorn app:app --reload` and open http://127.0.0.1:8000.

## Render + Supabase

Create a Render Web Service from this repository. Render can use `render.yaml`; add `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `SERVANT_PASSCODES` in the dashboard. `APP_SECRET` is generated automatically by the blueprint. The service serves both the frontend and API.

Use unique passcodes per servant. Never put the Supabase service-role key in the frontend or commit it.
