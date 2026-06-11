# SJEC Academic Task Manager - Flask Web App

## Project Structure

```text
.
├── Backend/
│   ├── app.py
│   └── sql_main.sql
├── Frontend/
│   └── *.html
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Run With Docker

Prerequisite: Docker Desktop or Docker Engine with Docker Compose.

```bash
docker compose up --build
```

Open http://localhost:5000 in your browser.

The Compose setup starts:

- `web`: the Flask app served by Gunicorn
- `db`: PostgreSQL 16 with the schema and sample data loaded from `Backend/sql_main.sql`

The database is stored in the `postgres_data` Docker volume. If you edit `Backend/sql_main.sql` and want to recreate the database from scratch, run:

```bash
docker compose down -v
docker compose up --build
```

## Demo Accounts

Login is email-only.

| Role    | Email            |
|---------|------------------|
| Student | shaan@sjec.ac.in |
| Student | asha@sjec.ac.in  |
| Student | rohan@sjec.ac.in |
| Faculty | meera@sjec.ac.in |
| Faculty | ravi@sjec.ac.in  |

## Configuration

The Docker defaults are defined in `docker-compose.yml`:

```text
DB_HOST=db
DB_NAME=academic_db
DB_USER=postgres
DB_PASSWORD=postgres
SECRET_KEY=change-this-before-production
```

Change `SECRET_KEY` before any real deployment.

## Optional Local Python Run

You can still run the app without Docker if you already have PostgreSQL and Python set up:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python Backend\app.py
```

For local Python runs, set these environment variables if your PostgreSQL settings differ:

```bash
set DB_HOST=localhost
set DB_NAME=academic_db
set DB_USER=postgres
set DB_PASSWORD=yourpassword
set SECRET_KEY=change-this-in-production
```
