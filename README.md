# SJEC Academic Task Manager — Flask Web App

## Project structure

```
app/
├── app.py               # All Flask routes
├── requirements.txt
├── README.md
└── templates/
    ├── base.html                  # Shared layout + nav
    ├── login.html
    ├── student_dashboard.html
    ├── faculty_dashboard.html
    ├── assignments.html
    ├── projects.html
    ├── grades.html
    ├── faculty_subjects.html
    ├── faculty_subject_detail.html
    ├── create_assignment.html
    ├── create_project.html
    └── grade_submission.html
```

## Prerequisites

- Python 3.10+
- PostgreSQL running locally
- The database schema loaded (`01_schema.sql` + `05_seed.sql`)

## Setup

```bash
# 1. Create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create the database
createdb academic_db
psql -U postgres -d academic_db -f ../db/01_schema.sql
psql -U postgres -d academic_db -f ../db/05_seed.sql
```

## Configuration

Set environment variables before running (or create a `.env` file):

```bash
export DB_HOST=localhost
export DB_NAME=academic_db
export DB_USER=postgres
export DB_PASSWORD=yourpassword
export SECRET_KEY=change-this-in-production
```

## Run

```bash
python app.py
```

Open http://localhost:5000 in your browser.

## Demo accounts (from seed data)

| Role    | Email               |
|---------|---------------------|
| Student | shaan@sjec.ac.in    |
| Student | asha@sjec.ac.in     |
| Student | rohan@sjec.ac.in    |
| Faculty | meera@sjec.ac.in    |
| Faculty | ravi@sjec.ac.in     |

> No passwords — login is email-only for now. Add password hashing
> (bcrypt) before any real deployment.

## What each role can do

### Student
- Dashboard: upcoming deadlines, pending assignments, recent grades
- Assignments: view all, submit (file path/URL), see grade and feedback
- Projects: view team, submit, see grade
- Grades: full history of all submissions and marks

### Faculty
- Dashboard: subjects overview, queue of ungraded submissions
- Subjects: drill into any subject to see assignments, projects, students
- Create assignments and projects for any of their subjects
- Grade any submission with marks + remarks
