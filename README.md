# Imago Youth

Full-stack points and leaderboard app for the Imago Youth Sanawy meeting. Authentication is handled by Supabase Auth with separate Youth and Servant roles; there are no servant passcodes.

## Local run

1. Create a Supabase project and run `schema.sql` in the SQL editor. Enable Email auth in Supabase Authentication settings.
2. Copy `.env.example` to `.env` and fill in the values.
3. Install dependencies with `pip install -r requirements.txt`.
4. Start with `uvicorn app:app --reload` and open http://127.0.0.1:8000.

## Render + Supabase

Create a Render Web Service from this repository. Render can use `render.yaml`; add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the dashboard. The service serves both the frontend and API.

The included Render configuration pins Python to 3.12 so dependencies such as `pydantic-core` install from prebuilt wheels instead of attempting a Rust compilation on Python 3.14.

The landing page lets people choose Youth or Servant, then log in or sign up with a username and password. Supabase uses an internal server-only account identifier; no email address is shown to users. Never put the Supabase service-role key in the frontend or commit it.
