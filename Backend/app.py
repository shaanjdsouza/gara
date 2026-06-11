from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
import psycopg2.extras
from datetime import date
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# ─── DB CONNECTION ────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        dbname=os.environ.get("DB_NAME", "academic_db"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def query(sql, params=(), one=False):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    result = cur.fetchone() if one else cur.fetchall()
    conn.close()
    return result

def execute(sql, params=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()

# ─── AUTH DECORATORS ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get("role") != role:
                flash("Access denied.", "error")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator

# ─── AUTH ROUTES ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form["email"].strip()
        user = query("SELECT * FROM users WHERE email = %s", (email,), one=True)
        if not user:
            flash("No account found with that email.", "error")
            return render_template("login.html")
        session["user_id"] = user["user_id"]
        session["name"]    = user["name"]
        session["role"]    = user["role"]
        session["email"]   = user["email"]
        if user["role"] == "student":
            s = query("SELECT student_id FROM students WHERE user_id = %s", (user["user_id"],), one=True)
            session["profile_id"] = s["student_id"]
        elif user["role"] == "faculty":
            f = query("SELECT faculty_id FROM faculty WHERE user_id = %s", (user["user_id"],), one=True)
            session["profile_id"] = f["faculty_id"]
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── SHARED DASHBOARD ────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    role = session["role"]
    pid  = session["profile_id"]
    if role == "student":
        # Upcoming deadlines (next 14 days)
        deadlines = query("""
            SELECT 'assignment' AS type, a.title, a.due_date, sub.name AS subject, a.assignment_id AS ref_id
            FROM assignments a
            JOIN subjects sub ON sub.subject_id = a.subject_id
            JOIN subject_enrollments se ON se.subject_id = sub.subject_id
            WHERE se.student_id = %s AND a.due_date >= CURRENT_DATE
              AND a.due_date <= CURRENT_DATE + INTERVAL '14 days'
            UNION ALL
            SELECT 'project', p.title, p.end_date, sub.name, p.project_id
            FROM projects p
            JOIN subjects sub ON sub.subject_id = p.subject_id
            JOIN project_members pm ON pm.project_id = p.project_id
            WHERE pm.student_id = %s AND p.end_date >= CURRENT_DATE
              AND p.end_date <= CURRENT_DATE + INTERVAL '14 days'
            ORDER BY due_date
        """, (pid, pid))
        # Recent grades
        recent_grades = query("""
            SELECT s.submission_type, s.ref_id, g.marks_obtained, g.remarks,
                   g.graded_at, s.submitted_at
            FROM submissions s
            JOIN grades g ON g.submission_id = s.submission_id
            WHERE s.student_id = %s AND g.marks_obtained IS NOT NULL
            ORDER BY g.graded_at DESC LIMIT 5
        """, (pid,))
        # Pending (enrolled but not submitted)
        pending = query("""
            SELECT a.assignment_id, a.title, a.due_date, sub.name AS subject
            FROM assignments a
            JOIN subjects sub ON a.subject_id = sub.subject_id
            JOIN subject_enrollments se ON se.subject_id = sub.subject_id
            WHERE se.student_id = %s
              AND a.assignment_id NOT IN (
                  SELECT ref_id FROM submissions
                  WHERE student_id = %s AND submission_type = 'assignment'
              )
            ORDER BY a.due_date LIMIT 5
        """, (pid, pid))
        return render_template("student_dashboard.html",
            deadlines=deadlines, recent_grades=recent_grades, pending=pending)
    else:
        # Faculty dashboard
        subjects = query("SELECT * FROM subjects WHERE faculty_id = %s", (pid,))
        ungraded = query("""
            SELECT s.submission_id, s.submission_type, s.ref_id,
                   u.name AS student_name, s.submitted_at, s.status
            FROM submissions s
            JOIN students st ON st.student_id = s.student_id
            JOIN users u ON u.user_id = st.user_id
            LEFT JOIN grades g ON g.submission_id = s.submission_id
            WHERE g.grade_id IS NULL OR g.marks_obtained IS NULL
            ORDER BY s.submitted_at
        """, ())
        return render_template("faculty_dashboard.html",
            subjects=subjects, ungraded=ungraded)

# ─── STUDENT: ASSIGNMENTS ────────────────────────────────────────────────────

@app.route("/assignments")
@login_required
@role_required("student")
def assignments():
    pid = session["profile_id"]
    rows = query("""
        SELECT a.assignment_id, a.title, a.description, a.due_date, a.max_marks,
               sub.name AS subject, sub.code,
               s.submission_id, s.status, s.submitted_at,
               g.marks_obtained, g.remarks
        FROM assignments a
        JOIN subjects sub ON sub.subject_id = a.subject_id
        JOIN subject_enrollments se ON se.subject_id = sub.subject_id AND se.student_id = %s
        LEFT JOIN submissions s ON s.ref_id = a.assignment_id
            AND s.submission_type = 'assignment' AND s.student_id = %s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        ORDER BY a.due_date
    """, (pid, pid))
    today = date.today()
    return render_template("assignments.html", assignments=rows, today=today)

@app.route("/assignments/<int:aid>/submit", methods=["POST"])
@login_required
@role_required("student")
def submit_assignment(aid):
    pid      = session["profile_id"]
    file_url = request.form.get("file_url", "").strip()
    # Check if already submitted
    existing = query("""
        SELECT submission_id FROM submissions
        WHERE student_id = %s AND submission_type = 'assignment' AND ref_id = %s
    """, (pid, aid), one=True)
    due = query("SELECT due_date FROM assignments WHERE assignment_id = %s", (aid,), one=True)
    status = "late" if due and date.today() > due["due_date"] else "submitted"
    if existing:
        execute("""
            UPDATE submissions SET file_url = %s, submitted_at = NOW(), status = 'resubmitted'
            WHERE submission_id = %s
        """, (file_url, existing["submission_id"]))
        flash("Submission updated.", "success")
    else:
        execute("""
            INSERT INTO submissions (student_id, submission_type, ref_id, file_url, status)
            VALUES (%s, 'assignment', %s, %s, %s)
        """, (pid, aid, file_url, status))
        flash("Assignment submitted!" + (" (marked late)" if status == "late" else ""), "success")
    return redirect(url_for("assignments"))

# ─── STUDENT: PROJECTS ───────────────────────────────────────────────────────

@app.route("/projects")
@login_required
@role_required("student")
def projects():
    pid = session["profile_id"]
    rows = query("""
        SELECT p.project_id, p.title, p.description, p.start_date, p.end_date,
               sub.name AS subject, sub.code, pm.role AS my_role,
               s.submission_id, s.status, s.submitted_at, g.marks_obtained
        FROM projects p
        JOIN project_members pm ON pm.project_id = p.project_id AND pm.student_id = %s
        JOIN subjects sub ON sub.subject_id = p.subject_id
        LEFT JOIN submissions s ON s.ref_id = p.project_id
            AND s.submission_type = 'project' AND s.student_id = %s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        ORDER BY p.end_date
    """, (pid, pid))
    # For each project, get team members
    projects_with_teams = []
    for p in rows:
        members = query("""
            SELECT u.name, st.usn, pm.role
            FROM project_members pm
            JOIN students st ON st.student_id = pm.student_id
            JOIN users u ON u.user_id = st.user_id
            WHERE pm.project_id = %s
            ORDER BY pm.role DESC
        """, (p["project_id"],))
        projects_with_teams.append(dict(p) | {"members": members})
    today = date.today()
    return render_template("projects.html", projects=projects_with_teams, today=today)

@app.route("/projects/<int:pid_proj>/submit", methods=["POST"])
@login_required
@role_required("student")
def submit_project(pid_proj):
    pid      = session["profile_id"]
    file_url = request.form.get("file_url", "").strip()
    existing = query("""
        SELECT submission_id FROM submissions
        WHERE student_id = %s AND submission_type = 'project' AND ref_id = %s
    """, (pid, pid_proj), one=True)
    proj = query("SELECT end_date FROM projects WHERE project_id = %s", (pid_proj,), one=True)
    status = "late" if proj and date.today() > proj["end_date"] else "submitted"
    if existing:
        execute("""
            UPDATE submissions SET file_url = %s, submitted_at = NOW(), status = 'resubmitted'
            WHERE submission_id = %s
        """, (file_url, existing["submission_id"]))
        flash("Project submission updated.", "success")
    else:
        execute("""
            INSERT INTO submissions (student_id, submission_type, ref_id, file_url, status)
            VALUES (%s, 'project', %s, %s, %s)
        """, (pid, pid_proj, file_url, status))
        flash("Project submitted!", "success")
    return redirect(url_for("projects"))

# ─── STUDENT: GRADES ─────────────────────────────────────────────────────────

@app.route("/grades")
@login_required
@role_required("student")
def grades():
    pid = session["profile_id"]
    rows = query("""
        SELECT s.submission_type, s.ref_id, s.submitted_at, s.status,
               g.marks_obtained, g.remarks, g.graded_at,
               CASE
                   WHEN s.submission_type = 'assignment' THEN a.title
                   ELSE p.title
               END AS title,
               CASE
                   WHEN s.submission_type = 'assignment' THEN a.max_marks
                   ELSE NULL
               END AS max_marks,
               sub.name AS subject, sub.code
        FROM submissions s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
        LEFT JOIN projects p ON p.project_id = s.ref_id AND s.submission_type = 'project'
        LEFT JOIN subjects sub ON sub.subject_id = COALESCE(a.subject_id, p.subject_id)
        WHERE s.student_id = %s
        ORDER BY s.submitted_at DESC
    """, (pid,))
    return render_template("grades.html", records=rows)

# ─── FACULTY: SUBJECTS & ASSIGNMENTS ─────────────────────────────────────────

@app.route("/faculty/subjects")
@login_required
@role_required("faculty")
def faculty_subjects():
    pid = session["profile_id"]
    subjects = query("""
        SELECT s.*, COUNT(DISTINCT se.student_id) AS enrolled_count,
               COUNT(DISTINCT a.assignment_id)    AS assignment_count,
               COUNT(DISTINCT p.project_id)       AS project_count
        FROM subjects s
        LEFT JOIN subject_enrollments se ON se.subject_id = s.subject_id
        LEFT JOIN assignments a ON a.subject_id = s.subject_id
        LEFT JOIN projects p ON p.subject_id = s.subject_id
        WHERE s.faculty_id = %s
        GROUP BY s.subject_id
    """, (pid,))
    return render_template("faculty_subjects.html", subjects=subjects)

@app.route("/faculty/subjects/<int:sid>")
@login_required
@role_required("faculty")
def faculty_subject_detail(sid):
    subject     = query("SELECT * FROM subjects WHERE subject_id = %s", (sid,), one=True)
    assignments = query("SELECT * FROM assignments WHERE subject_id = %s ORDER BY due_date", (sid,))
    projects    = query("SELECT * FROM projects WHERE subject_id = %s ORDER BY end_date", (sid,))
    students    = query("""
        SELECT st.student_id, u.name, st.usn
        FROM subject_enrollments se
        JOIN students st ON st.student_id = se.student_id
        JOIN users u ON u.user_id = st.user_id
        WHERE se.subject_id = %s ORDER BY u.name
    """, (sid,))
    return render_template("faculty_subject_detail.html",
        subject=subject, assignments=assignments, projects=projects, students=students)

@app.route("/faculty/assignments/create", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def create_assignment():
    pid = session["profile_id"]
    subjects = query("SELECT * FROM subjects WHERE faculty_id = %s", (pid,))
    if request.method == "POST":
        execute("""
            INSERT INTO assignments (subject_id, title, description, due_date, max_marks)
            VALUES (%s, %s, %s, %s, %s)
        """, (request.form["subject_id"], request.form["title"],
              request.form["description"], request.form["due_date"],
              request.form["max_marks"]))
        flash("Assignment created.", "success")
        return redirect(url_for("faculty_subjects"))
    return render_template("create_assignment.html", subjects=subjects)

@app.route("/faculty/projects/create", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def create_project():
    pid = session["profile_id"]
    subjects = query("SELECT * FROM subjects WHERE faculty_id = %s", (pid,))
    if request.method == "POST":
        execute("""
            INSERT INTO projects (subject_id, title, description, start_date, end_date)
            VALUES (%s, %s, %s, %s, %s)
        """, (request.form["subject_id"], request.form["title"],
              request.form["description"], request.form["start_date"],
              request.form["end_date"]))
        flash("Project created.", "success")
        return redirect(url_for("faculty_subjects"))
    return render_template("create_project.html", subjects=subjects)

# ─── FACULTY: GRADING ────────────────────────────────────────────────────────

@app.route("/faculty/grade/<int:sub_id>", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def grade_submission(sub_id):
    pid        = session["profile_id"]
    submission = query("""
        SELECT s.*, u.name AS student_name, st.usn,
               CASE WHEN s.submission_type = 'assignment' THEN a.title ELSE p.title END AS title,
               CASE WHEN s.submission_type = 'assignment' THEN a.max_marks ELSE NULL END AS max_marks
        FROM submissions s
        JOIN students st ON st.student_id = s.student_id
        JOIN users u ON u.user_id = st.user_id
        LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
        LEFT JOIN projects p ON p.project_id = s.ref_id AND s.submission_type = 'project'
        WHERE s.submission_id = %s
    """, (sub_id,), one=True)
    existing_grade = query("SELECT * FROM grades WHERE submission_id = %s", (sub_id,), one=True)
    if request.method == "POST":
        marks   = request.form["marks_obtained"]
        remarks = request.form["remarks"]
        if existing_grade:
            execute("""
                UPDATE grades SET marks_obtained = %s, remarks = %s, graded_at = NOW()
                WHERE submission_id = %s
            """, (marks, remarks, sub_id))
        else:
            execute("""
                INSERT INTO grades (submission_id, faculty_id, marks_obtained, remarks)
                VALUES (%s, %s, %s, %s)
            """, (sub_id, pid, marks, remarks))
        flash("Grade saved.", "success")
        return redirect(url_for("dashboard"))
    return render_template("grade_submission.html",
        submission=submission, existing_grade=existing_grade)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
