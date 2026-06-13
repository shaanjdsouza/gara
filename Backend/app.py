from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
import psycopg2
import psycopg2.extras
from datetime import date
from functools import wraps
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash  # ← NEW: password hashing

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = BASE_DIR / "uploads"
ASSIGNMENT_UPLOAD_DIR  = UPLOAD_ROOT / "assignments"
TEACHER_MATERIAL_DIR   = UPLOAD_ROOT / "materials"              # ← NEW: teacher PDF uploads
ALLOWED_ASSIGNMENT_EXTENSIONS = {".pdf"}
ALLOWED_MATERIAL_EXTENSIONS   = {".pdf"}                       # ← NEW

app = Flask(__name__, template_folder=str(BASE_DIR / "Frontend"))
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

def execute_returning(sql, params=()):
    """Like execute() but returns the first column of the first row (e.g. a new id)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else None

def save_assignment_pdf(file_storage, student_id, assignment_id):
    filename = secure_filename(file_storage.filename or "")
    ext = Path(filename).suffix.lower()
    if not filename or ext not in ALLOWED_ASSIGNMENT_EXTENSIONS:
        return None
    ASSIGNMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"assignment_{assignment_id}_student_{student_id}_{uuid4().hex}{ext}"
    file_storage.save(ASSIGNMENT_UPLOAD_DIR / stored_name)
    return url_for("uploaded_file", filename=f"assignments/{stored_name}")

# ─── NEW: save teacher-uploaded material PDF ──────────────────────────────────
def save_material_pdf(file_storage, assignment_id):
    """Save a teacher-uploaded PDF material and return its serve URL."""
    filename = secure_filename(file_storage.filename or "")
    ext = Path(filename).suffix.lower()
    if not filename or ext not in ALLOWED_MATERIAL_EXTENSIONS:
        return None
    TEACHER_MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"material_assignment_{assignment_id}_{uuid4().hex}{ext}"
    file_storage.save(TEACHER_MATERIAL_DIR / stored_name)
    return url_for("uploaded_file", filename=f"materials/{stored_name}")

def is_github_repo_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc.lower() != "github.com":
        return False
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    return len(parts) == 2

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

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_ROOT, filename)

# ─── AUTH ROUTES ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email    = request.form["email"].strip()
        password = request.form["password"]                    # ← NEW: read password
        user = query("SELECT * FROM users WHERE email = %s", (email,), one=True)
        if not user:
            flash("No account found with that email.", "error")
            return render_template("login.html")
        # ← NEW: verify hashed password
        if not check_password_hash(user["password_hash"], password):
            flash("Incorrect password.", "error")
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
            ORDER BY s.code
        """, (pid,))
        ungraded = query("""
            SELECT s.submission_id, s.submission_type, s.ref_id,
                   u.name AS student_name, s.submitted_at, s.status,
                   COALESCE(a.title, p.title) AS title, sub.code AS subject_code
            FROM submissions s
            JOIN students st ON st.student_id = s.student_id
            JOIN users u ON u.user_id = st.user_id
            LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
            LEFT JOIN projects p ON p.project_id = s.ref_id AND s.submission_type = 'project'
            JOIN subjects sub ON sub.subject_id = COALESCE(a.subject_id, p.subject_id)
            LEFT JOIN grades g ON g.submission_id = s.submission_id
            WHERE sub.faculty_id = %s
              AND (g.grade_id IS NULL OR g.marks_obtained IS NULL)
            ORDER BY s.submitted_at
        """, (pid,))
        graded = query("""
            SELECT s.submission_id, s.submission_type, u.name AS student_name,
                   s.submitted_at, g.marks_obtained, g.graded_at,
                   COALESCE(a.title, p.title) AS title, sub.code AS subject_code,
                   a.max_marks
            FROM submissions s
            JOIN students st ON st.student_id = s.student_id
            JOIN users u ON u.user_id = st.user_id
            JOIN grades g ON g.submission_id = s.submission_id
            LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
            LEFT JOIN projects p ON p.project_id = s.ref_id AND s.submission_type = 'project'
            JOIN subjects sub ON sub.subject_id = COALESCE(a.subject_id, p.subject_id)
            WHERE sub.faculty_id = %s AND g.marks_obtained IS NOT NULL
            ORDER BY g.graded_at DESC LIMIT 8
        """, (pid,))
        return render_template("faculty_dashboard.html",
            subjects=subjects, ungraded=ungraded, graded=graded)

# ─── STUDENT: ASSIGNMENTS ────────────────────────────────────────────────────

@app.route("/assignments")
@login_required
@role_required("student")
def assignments():
    pid = session["profile_id"]
    search = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "deadline")
    sort_options = {
        "deadline": "a.due_date ASC, a.title ASC",
        "subject": "sub.name ASC, a.due_date ASC, a.title ASC",
        "priority": "priority_rank ASC, a.due_date ASC, a.title ASC",
    }
    order_by = sort_options.get(sort_by, sort_options["deadline"])

    params = [pid, pid]
    search_sql = ""
    if search:
        search_sql = """
          AND (
              a.title ILIKE %s OR
              COALESCE(a.description, '') ILIKE %s OR
              sub.name ILIKE %s OR
              sub.code ILIKE %s
          )
        """
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern])

    rows = query("""
        SELECT a.assignment_id, a.title, a.description, a.due_date, a.max_marks,
               sub.name AS subject, sub.code,
               a.material_url,
               s.submission_id, s.status, s.submitted_at, s.file_url,
               g.marks_obtained, g.remarks,
               CASE
                   WHEN s.submission_id IS NULL AND a.due_date < CURRENT_DATE THEN 0
                   WHEN s.submission_id IS NULL AND a.due_date <= CURRENT_DATE + INTERVAL '3 days' THEN 1
                   WHEN s.submission_id IS NULL THEN 2
                   WHEN g.marks_obtained IS NULL THEN 3
                   ELSE 4
               END AS priority_rank
        FROM assignments a
        JOIN subjects sub ON sub.subject_id = a.subject_id
        JOIN subject_enrollments se ON se.subject_id = sub.subject_id AND se.student_id = %s
        LEFT JOIN submissions s ON s.ref_id = a.assignment_id
            AND s.submission_type = 'assignment' AND s.student_id = %s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        WHERE 1 = 1
    """ + search_sql + f"""
        ORDER BY {order_by}
    """, tuple(params))
    today = date.today()
    return render_template(
        "assignments.html",
        assignments=rows,
        today=today,
        search=search,
        sort_by=sort_by if sort_by in sort_options else "deadline",
    )

@app.route("/assignments/<int:aid>/submit", methods=["POST"])
@login_required
@role_required("student")
def submit_assignment(aid):
    pid      = session["profile_id"]
    existing = query("""
        SELECT submission_id FROM submissions
        WHERE student_id = %s AND submission_type = 'assignment' AND ref_id = %s
    """, (pid, aid), one=True)
    due = query("SELECT due_date FROM assignments WHERE assignment_id = %s", (aid,), one=True)
    status = "late" if due and date.today() > due["due_date"] else "submitted"
    if existing:
        flash("This assignment is already submitted. Ask your teacher to modify the submission.", "error")
    else:
        submission_file = request.files.get("submission_file")
        if not submission_file or not submission_file.filename:
            flash("Please upload a PDF file for this assignment.", "error")
            return redirect(url_for("assignments"))
        file_url = save_assignment_pdf(submission_file, pid, aid)
        if not file_url:
            flash("Assignment submissions must be PDF files.", "error")
            return redirect(url_for("assignments"))
        execute("""
            INSERT INTO submissions (student_id, submission_type, ref_id, file_url, status)
            VALUES (%s, 'assignment', %s, %s, %s)
        """, (pid, aid, file_url, status))
        flash("Assignment submitted!" + (" (marked late)" if status == "late" else ""), "success")
    return redirect(url_for("assignments"))

# ─── NEW: STUDENT — revoke their own submission ───────────────────────────────
@app.route("/assignments/<int:aid>/revoke", methods=["POST"])
@login_required
@role_required("student")
def revoke_assignment_submission(aid):
    """
    Allows a student to delete their own submitted assignment,
    but only if it has not yet been graded.
    """
    pid = session["profile_id"]
    submission = query("""
        SELECT s.submission_id, g.marks_obtained
        FROM submissions s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        WHERE s.student_id = %s AND s.submission_type = 'assignment' AND s.ref_id = %s
    """, (pid, aid), one=True)
    if not submission:
        flash("No submission found to revoke.", "error")
        return redirect(url_for("assignments"))
    if submission["marks_obtained"] is not None:
        flash("Your submission has already been graded and cannot be revoked.", "error")
        return redirect(url_for("assignments"))
    # Delete the grade row (if it exists with NULL marks) then the submission
    execute("DELETE FROM grades WHERE submission_id = %s", (submission["submission_id"],))
    execute("DELETE FROM submissions WHERE submission_id = %s", (submission["submission_id"],))
    flash("Submission revoked. You can now re-submit.", "success")
    return redirect(url_for("assignments"))

@app.route("/projects/<int:pid_proj>/revoke", methods=["POST"])
@login_required
@role_required("student")
def revoke_project_submission(pid_proj):
    """
    Allows a student to revoke their own project submission
    if it has not yet been graded.
    """
    pid = session["profile_id"]
    submission = query("""
        SELECT s.submission_id, g.marks_obtained
        FROM submissions s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        WHERE s.student_id = %s AND s.submission_type = 'project' AND s.ref_id = %s
    """, (pid, pid_proj), one=True)
    if not submission:
        flash("No submission found to revoke.", "error")
        return redirect(url_for("projects"))
    if submission["marks_obtained"] is not None:
        flash("Your submission has already been graded and cannot be revoked.", "error")
        return redirect(url_for("projects"))
    execute("DELETE FROM grades WHERE submission_id = %s", (submission["submission_id"],))
    execute("DELETE FROM submissions WHERE submission_id = %s", (submission["submission_id"],))
    flash("Project submission revoked. You can now re-submit.", "success")
    return redirect(url_for("projects"))

# ─── STUDENT: PROJECTS ───────────────────────────────────────────────────────

@app.route("/projects")
@login_required
@role_required("student")
def projects():
    pid = session["profile_id"]
    rows = query("""
        SELECT p.project_id, p.title, p.description, p.start_date, p.end_date,
               sub.name AS subject, sub.code, pm.role AS my_role,
               s.submission_id, s.status, s.submitted_at, s.file_url,
               g.marks_obtained, g.remarks
        FROM projects p
        JOIN project_members pm ON pm.project_id = p.project_id AND pm.student_id = %s
        JOIN subjects sub ON sub.subject_id = p.subject_id
        LEFT JOIN submissions s ON s.ref_id = p.project_id
            AND s.submission_type = 'project' AND s.student_id = %s
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        ORDER BY p.end_date
    """, (pid, pid))
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
    pid        = session["profile_id"]
    github_url = request.form.get("github_url", "").strip()
    if not is_github_repo_url(github_url):
        flash("Please submit a valid GitHub repository URL.", "error")
        return redirect(url_for("projects"))
    existing = query("""
        SELECT submission_id FROM submissions
        WHERE student_id = %s AND submission_type = 'project' AND ref_id = %s
    """, (pid, pid_proj), one=True)
    proj = query("SELECT end_date FROM projects WHERE project_id = %s", (pid_proj,), one=True)
    status = "late" if proj and date.today() > proj["end_date"] else "submitted"
    if existing:
        flash("This project is already submitted. Ask your teacher to modify the submission.", "error")
    else:
        execute("""
            INSERT INTO submissions (student_id, submission_type, ref_id, file_url, status)
            VALUES (%s, 'project', %s, %s, %s)
        """, (pid, pid_proj, github_url, status))
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
    pid = session["profile_id"]
    subject = query(
        "SELECT * FROM subjects WHERE subject_id = %s AND faculty_id = %s",
        (sid, pid), one=True,
    )
    if not subject:
        flash("Subject not found or access denied.", "error")
        return redirect(url_for("faculty_subjects"))
    assignments = query("""
        SELECT a.*,
               COUNT(DISTINCT s.submission_id) AS submission_count,
               COUNT(DISTINCT g.grade_id) FILTER (WHERE g.marks_obtained IS NOT NULL) AS graded_count
        FROM assignments a
        LEFT JOIN submissions s ON s.ref_id = a.assignment_id AND s.submission_type = 'assignment'
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        WHERE a.subject_id = %s
        GROUP BY a.assignment_id
        ORDER BY a.due_date
    """, (sid,))
    projects = query("""
        SELECT p.*,
               COUNT(DISTINCT s.submission_id) AS submission_count,
               COUNT(DISTINCT g.grade_id) FILTER (WHERE g.marks_obtained IS NOT NULL) AS graded_count
        FROM projects p
        LEFT JOIN submissions s ON s.ref_id = p.project_id AND s.submission_type = 'project'
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        WHERE p.subject_id = %s
        GROUP BY p.project_id
        ORDER BY p.end_date
    """, (sid,))
    students = query("""
        SELECT st.student_id, u.name, st.usn
        FROM subject_enrollments se
        JOIN students st ON st.student_id = se.student_id
        JOIN users u ON u.user_id = st.user_id
        WHERE se.subject_id = %s ORDER BY u.name
    """, (sid,))
    project_members = query("""
        SELECT pm.project_id, u.name, st.usn, pm.role
        FROM project_members pm
        JOIN students st ON st.student_id = pm.student_id
        JOIN users u ON u.user_id = st.user_id
        JOIN projects p ON p.project_id = pm.project_id
        WHERE p.subject_id = %s
        ORDER BY pm.project_id, CASE WHEN pm.role = 'leader' THEN 0 ELSE 1 END, u.name
    """, (sid,))
    members_by_project = {}
    for member in project_members:
        members_by_project.setdefault(member["project_id"], []).append(member)

    submissions = query("""
        SELECT s.submission_id, s.submission_type, s.ref_id, s.file_url,
               s.status, s.submitted_at, u.name AS student_name, st.usn,
               st.student_id,
               g.marks_obtained, g.remarks, g.graded_at,
               COALESCE(a.title, p.title) AS title, a.max_marks
        FROM submissions s
        JOIN students st ON st.student_id = s.student_id
        JOIN users u ON u.user_id = st.user_id
        LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
        LEFT JOIN projects p ON p.project_id = s.ref_id AND s.submission_type = 'project'
        LEFT JOIN grades g ON g.submission_id = s.submission_id
        WHERE COALESCE(a.subject_id, p.subject_id) = %s
        ORDER BY s.submitted_at DESC
    """, (sid,))
    return render_template("faculty_subject_detail.html",
        subject=subject,
        assignments=assignments,
        projects=projects,
        students=students,
        submissions=submissions,
        members_by_project=members_by_project)

@app.route("/faculty/assignments/create", methods=["GET", "POST"])
@login_required
@role_required("faculty")
def create_assignment():
    pid = session["profile_id"]
    subjects = query("SELECT * FROM subjects WHERE faculty_id = %s", (pid,))
    if request.method == "POST":
        # Insert assignment first to get its id
        new_id = execute_returning("""
            INSERT INTO assignments (subject_id, title, description, due_date, max_marks)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING assignment_id
        """, (request.form["subject_id"], request.form["title"],
              request.form["description"], request.form["due_date"],
              request.form["max_marks"]))
        # ← NEW: optionally save attached material PDF
        material_file = request.files.get("material_pdf")
        if material_file and material_file.filename:
            material_url = save_material_pdf(material_file, new_id)
            if material_url:
                execute("UPDATE assignments SET material_url = %s WHERE assignment_id = %s",
                        (material_url, new_id))
            else:
                flash("Material must be a PDF file — assignment created without it.", "warning")
        flash("Assignment created.", "success")
        return redirect(url_for("faculty_subjects"))
    return render_template("create_assignment.html", subjects=subjects)

# ─── NEW: FACULTY — upload / replace material PDF for an existing assignment ──
@app.route("/faculty/assignments/<int:aid>/upload-material", methods=["POST"])
@login_required
@role_required("faculty")
def upload_assignment_material(aid):
    """
    Lets a teacher upload (or replace) the reference PDF attached to an assignment.
    Ownership is verified via faculty_id on the parent subject.
    """
    pid = session["profile_id"]
    assignment = query("""
        SELECT a.assignment_id, a.subject_id
        FROM assignments a
        JOIN subjects sub ON sub.subject_id = a.subject_id
        WHERE a.assignment_id = %s AND sub.faculty_id = %s
    """, (aid, pid), one=True)
    if not assignment:
        flash("Assignment not found or access denied.", "error")
        return redirect(url_for("faculty_subjects"))

    material_file = request.files.get("material_pdf")
    if not material_file or not material_file.filename:
        flash("Please choose a PDF file to upload.", "error")
        return redirect(url_for("faculty_subject_detail", sid=assignment["subject_id"]))

    material_url = save_material_pdf(material_file, aid)
    if not material_url:
        flash("Only PDF files are accepted.", "error")
        return redirect(url_for("faculty_subject_detail", sid=assignment["subject_id"]))

    execute("UPDATE assignments SET material_url = %s WHERE assignment_id = %s",
            (material_url, aid))
    flash("Material PDF uploaded successfully.", "success")
    return redirect(url_for("faculty_subject_detail", sid=assignment["subject_id"]))

# ─── NEW: FACULTY — delete a student's submission ────────────────────────────
@app.route("/faculty/submissions/<int:sub_id>/delete", methods=["POST"])
@login_required
@role_required("faculty")
def faculty_delete_submission(sub_id):
    """
    Allows a teacher to permanently remove a student's submission
    (and its grade, if any). Only submissions belonging to the
    teacher's own subjects can be deleted.
    """
    pid = session["profile_id"]
    submission = query("""
        SELECT s.submission_id, COALESCE(a.subject_id, p.subject_id) AS subject_id
        FROM submissions s
        LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
        LEFT JOIN projects p    ON p.project_id    = s.ref_id AND s.submission_type = 'project'
        JOIN subjects sub ON sub.subject_id = COALESCE(a.subject_id, p.subject_id)
        WHERE s.submission_id = %s AND sub.faculty_id = %s
    """, (sub_id, pid), one=True)
    if not submission:
        flash("Submission not found or access denied.", "error")
        return redirect(url_for("dashboard"))
    # Cascade in DB handles grades, but we delete explicitly for clarity
    execute("DELETE FROM grades WHERE submission_id = %s",      (sub_id,))
    execute("DELETE FROM submissions WHERE submission_id = %s", (sub_id,))
    flash("Submission removed successfully.", "success")
    return redirect(url_for("faculty_subject_detail", sid=submission["subject_id"]))

@app.route("/faculty/assignments/<int:aid>/mark-in-person", methods=["POST"])
@login_required
@role_required("faculty")
def mark_assignment_in_person(aid):
    pid = session["profile_id"]
    student_id = request.form.get("student_id")
    assignment = query("""
        SELECT a.assignment_id, a.subject_id
        FROM assignments a
        JOIN subjects sub ON sub.subject_id = a.subject_id
        WHERE a.assignment_id = %s AND sub.faculty_id = %s
    """, (aid, pid), one=True)
    if not assignment:
        flash("Assignment not found or access denied.", "error")
        return redirect(url_for("faculty_subjects"))
    enrolled = query("""
        SELECT 1 FROM subject_enrollments
        WHERE subject_id = %s AND student_id = %s
    """, (assignment["subject_id"], student_id), one=True)
    if not enrolled:
        flash("Student is not enrolled in this subject.", "error")
        return redirect(url_for("faculty_subject_detail", sid=assignment["subject_id"]))
    existing = query("""
        SELECT submission_id FROM submissions
        WHERE student_id = %s AND submission_type = 'assignment' AND ref_id = %s
    """, (student_id, aid), one=True)
    if existing:
        execute("""
            UPDATE submissions
            SET status = 'submitted', submitted_at = NOW()
            WHERE submission_id = %s
        """, (existing["submission_id"],))
    else:
        execute("""
            INSERT INTO submissions (student_id, submission_type, ref_id, file_url, status)
            VALUES (%s, 'assignment', %s, NULL, 'submitted')
        """, (student_id, aid))
    flash("Assignment marked as submitted in person.", "success")
    return redirect(url_for("faculty_subject_detail", sid=assignment["subject_id"]))

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
               CASE WHEN s.submission_type = 'assignment' THEN a.max_marks ELSE NULL END AS max_marks,
               COALESCE(a.subject_id, p.subject_id) AS subject_id
        FROM submissions s
        JOIN students st ON st.student_id = s.student_id
        JOIN users u ON u.user_id = st.user_id
        LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
        LEFT JOIN projects p ON p.project_id = s.ref_id AND s.submission_type = 'project'
        LEFT JOIN subjects sub ON sub.subject_id = COALESCE(a.subject_id, p.subject_id)
        WHERE s.submission_id = %s AND sub.faculty_id = %s
    """, (sub_id, pid), one=True)
    if not submission:
        flash("Submission not found or access denied.", "error")
        return redirect(url_for("dashboard"))
    existing_grade = query("SELECT * FROM grades WHERE submission_id = %s", (sub_id,), one=True)
    if request.method == "POST":
        marks   = request.form["marks_obtained"]
        remarks = request.form.get("remarks", "")
        status  = request.form.get("status", submission["status"])
        if status not in {"submitted", "late", "resubmitted"}:
            status = submission["status"]
        file_url = submission["file_url"] or ""
        if submission["submission_type"] == "assignment":
            if request.form.get("submitted_in_person"):
                file_url = ""
                status   = "submitted"
            else:
                submission_file = request.files.get("submission_file")
                if submission_file and submission_file.filename:
                    saved_url = save_assignment_pdf(submission_file, submission["student_id"], submission["ref_id"])
                    if not saved_url:
                        flash("Assignment submissions must be PDF files.", "error")
                        return redirect(url_for("grade_submission", sub_id=sub_id))
                    file_url = saved_url
        else:
            file_url = request.form.get("github_url", "").strip()
            if not is_github_repo_url(file_url):
                flash("Please submit a valid GitHub repository URL.", "error")
                return redirect(url_for("grade_submission", sub_id=sub_id))
        execute("""
            UPDATE submissions SET file_url = %s, status = %s
            WHERE submission_id = %s
        """, (file_url, status, sub_id))
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
    app.run(
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "true").lower() == "true",
    )
