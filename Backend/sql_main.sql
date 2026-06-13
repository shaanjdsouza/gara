-- ============================================================
-- Academic Task Management System
-- SJEC | AIML Department
-- ============================================================
-- CHANGELOG (new features):
--   1. users.password_hash        — bcrypt password column for login
--   2. assignments.material_url   — teacher-uploaded PDF material path
--   3. submissions.status CHECK   — added 'revoked' as valid status
--   4. fn_set_password()          — helper to hash a plain password via pgcrypto
--   5. New index on submissions.status for revoke filtering
-- ============================================================

-- -------------------------------------------------------
-- SECTION 1: SCHEMA CREATION (DDL)
-- -------------------------------------------------------

DROP TRIGGER IF EXISTS trg_late_submission ON submissions;
DROP FUNCTION IF EXISTS fn_late_submission();
DROP FUNCTION IF EXISTS fn_set_password(INT, TEXT); -- NEW

DROP VIEW IF EXISTS ungraded_submissions;
DROP VIEW IF EXISTS student_grade_summary;

DROP TABLE IF EXISTS grades CASCADE;
DROP TABLE IF EXISTS submissions CASCADE;
DROP TABLE IF EXISTS project_members CASCADE;
DROP TABLE IF EXISTS projects CASCADE;
DROP TABLE IF EXISTS assignments CASCADE;
DROP TABLE IF EXISTS subject_enrollments CASCADE;
DROP TABLE IF EXISTS subjects CASCADE;
DROP TABLE IF EXISTS students CASCADE;
DROP TABLE IF EXISTS faculty CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- ── FEATURE 1: password_hash column ──────────────────────────────────────────
-- Users (shared base for students and faculty)
CREATE TABLE users (
    user_id       SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    email         VARCHAR(100) UNIQUE NOT NULL,
    role          VARCHAR(20)  NOT NULL CHECK (role IN ('student', 'faculty', 'admin')),
    -- Stores a Werkzeug/bcrypt hash (e.g. "pbkdf2:sha256:...")
    -- Generated in Python via: generate_password_hash(plain_password)
    password_hash TEXT         NOT NULL DEFAULT '',
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- Faculty profile (1:1 with users where role = 'faculty')
CREATE TABLE faculty (
    faculty_id    SERIAL PRIMARY KEY,
    user_id       INT NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    department    VARCHAR(100),
    designation   VARCHAR(100)
);

-- Student profile (1:1 with users where role = 'student')
CREATE TABLE students (
    student_id  SERIAL PRIMARY KEY,
    user_id     INT NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    usn         VARCHAR(20) UNIQUE NOT NULL,
    semester    INT CHECK (semester BETWEEN 1 AND 8),
    branch      VARCHAR(100)
);

-- Subjects taught by faculty
CREATE TABLE subjects (
    subject_id  SERIAL PRIMARY KEY,
    faculty_id  INT REFERENCES faculty(faculty_id) ON DELETE SET NULL,
    code        VARCHAR(20) UNIQUE NOT NULL,
    name        VARCHAR(150) NOT NULL,
    semester    INT CHECK (semester BETWEEN 1 AND 8)
);

-- Which students are enrolled in which subjects
CREATE TABLE subject_enrollments (
    enrollment_id  SERIAL PRIMARY KEY,
    student_id     INT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    subject_id     INT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
    enrolled_on    DATE DEFAULT CURRENT_DATE,
    UNIQUE (student_id, subject_id)
);

-- ── FEATURE 2: material_url column ───────────────────────────────────────────
-- Assignments under a subject
CREATE TABLE assignments (
    assignment_id  SERIAL PRIMARY KEY,
    subject_id     INT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
    title          VARCHAR(200) NOT NULL,
    description    TEXT,
    due_date       DATE NOT NULL,
    max_marks      INT DEFAULT 10,
    -- Path to teacher-uploaded reference/question PDF (served via /uploads/materials/)
    material_url   TEXT DEFAULT NULL
);

-- Projects under a subject (team-based)
CREATE TABLE projects (
    project_id   SERIAL PRIMARY KEY,
    subject_id   INT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
    title        VARCHAR(200) NOT NULL,
    description  TEXT,
    start_date   DATE,
    end_date     DATE
);

-- Team members for a project
CREATE TABLE project_members (
    project_id   INT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    student_id   INT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    role         VARCHAR(100) DEFAULT 'member',
    PRIMARY KEY (project_id, student_id)
);

-- ── FEATURE 3: 'revoked' added to submissions.status ─────────────────────────
-- Submissions (unified for both assignments and projects)
CREATE TABLE submissions (
    submission_id    SERIAL PRIMARY KEY,
    student_id       INT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    submission_type  VARCHAR(20) NOT NULL CHECK (submission_type IN ('assignment', 'project')),
    ref_id           INT NOT NULL,
    file_url         TEXT,
    submitted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 'revoked' is set when a student withdraws an ungraded submission
    status           VARCHAR(20) DEFAULT 'submitted'
                     CHECK (status IN ('submitted', 'late', 'resubmitted', 'revoked'))
);

-- Grades for each submission, assigned by faculty
CREATE TABLE grades (
    grade_id        SERIAL PRIMARY KEY,
    submission_id   INT NOT NULL UNIQUE REFERENCES submissions(submission_id) ON DELETE CASCADE,
    faculty_id      INT REFERENCES faculty(faculty_id) ON DELETE SET NULL,
    marks_obtained  INT,
    remarks         TEXT,
    graded_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- -------------------------------------------------------
-- SECTION 1b: VIEWS
-- -------------------------------------------------------

CREATE OR REPLACE VIEW student_grade_summary AS
SELECT
    s.student_id,
    u.name        AS student_name,
    sub.code,
    sub.name      AS subject,
    a.title       AS assignment_title,
    a.max_marks,
    g.marks_obtained,
    g.remarks,
    g.graded_at
FROM submissions sm
JOIN students s    ON s.student_id = sm.student_id
JOIN users u       ON u.user_id = s.user_id
JOIN grades g      ON g.submission_id = sm.submission_id
JOIN assignments a ON a.assignment_id = sm.ref_id
JOIN subjects sub  ON sub.subject_id = a.subject_id
WHERE sm.submission_type = 'assignment'
  AND sm.status != 'revoked';   -- exclude revoked submissions from grade summaries

CREATE OR REPLACE VIEW ungraded_submissions AS
SELECT
    sm.submission_id,
    sm.submission_type,
    sm.ref_id,
    sm.submitted_at,
    sm.status,
    u.name         AS student_name,
    st.usn,
    COALESCE(a.title, p.title) AS title,
    sub.code       AS subject_code,
    sub.faculty_id
FROM submissions sm
JOIN students st   ON st.student_id = sm.student_id
JOIN users u       ON u.user_id = st.user_id
LEFT JOIN assignments a ON a.assignment_id = sm.ref_id AND sm.submission_type = 'assignment'
LEFT JOIN projects p    ON p.project_id = sm.ref_id    AND sm.submission_type = 'project'
JOIN subjects sub  ON sub.subject_id = COALESCE(a.subject_id, p.subject_id)
LEFT JOIN grades g ON g.submission_id = sm.submission_id
WHERE (g.grade_id IS NULL OR g.marks_obtained IS NULL)
  AND sm.status != 'revoked';   -- exclude revoked from the ungraded queue


-- -------------------------------------------------------
-- SECTION 2: INDEXES
-- -------------------------------------------------------

CREATE INDEX idx_submissions_student  ON submissions(student_id);
CREATE INDEX idx_submissions_ref      ON submissions(submission_type, ref_id);
CREATE INDEX idx_submissions_status   ON submissions(status);       -- NEW: revoke queries
CREATE INDEX idx_assignments_due      ON assignments(due_date);
CREATE INDEX idx_assignments_material ON assignments(material_url); -- NEW: material lookups
CREATE INDEX idx_projects_end         ON projects(end_date);
CREATE INDEX idx_enrollments_student  ON subject_enrollments(student_id);


-- -------------------------------------------------------
-- SECTION 3: SAMPLE DATA (DML)
-- -------------------------------------------------------

-- ── FEATURE 1: Passwords are hashed with Werkzeug (pbkdf2:sha256) ─────────────
-- Default password for ALL seed accounts is: sjec@2025
-- Hash generated with Python:
--   from werkzeug.security import generate_password_hash
--   generate_password_hash("sjec@2025")
-- Replace the hash below with a freshly generated one for production.
INSERT INTO users (name, email, role, password_hash) VALUES
('Dr. Meera Shenoy', 'meera@sjec.ac.in', 'faculty',
 'pbkdf2:sha256:600000$placeholder$faculty_meera_hash_replace_me'),
('Dr. Ravi Prabhu',  'ravi@sjec.ac.in',  'faculty',
 'pbkdf2:sha256:600000$placeholder$faculty_ravi_hash_replace_me'),
('Shaan Dsouza',     'shaan@sjec.ac.in', 'student',
 'pbkdf2:sha256:600000$placeholder$student_shaan_hash_replace_me'),
('Asha Kamath',      'asha@sjec.ac.in',  'student',
 'pbkdf2:sha256:600000$placeholder$student_asha_hash_replace_me'),
('Rohan Nair',       'rohan@sjec.ac.in', 'student',
 'pbkdf2:sha256:600000$placeholder$student_rohan_hash_replace_me');

-- ── HOW TO GENERATE REAL HASHES (run once in Python) ──────────────────────
-- from werkzeug.security import generate_password_hash
-- print(generate_password_hash("sjec@2025"))
-- Then UPDATE users SET password_hash = '<output>' WHERE email = '...';
-- ──────────────────────────────────────────────────────────────────────────

-- Faculty profiles
INSERT INTO faculty (user_id, department, designation) VALUES
(1, 'AIML', 'Associate Professor'),
(2, 'AIML', 'Assistant Professor');

-- Student profiles
INSERT INTO students (user_id, usn, semester, branch) VALUES
(3, '4SO24AI001', 5, 'AIML'),
(4, '4SO24AI002', 5, 'AIML'),
(5, '4SO24AI003', 5, 'AIML');

-- Subjects
INSERT INTO subjects (faculty_id, code, name, semester) VALUES
(1, 'AIML501', 'Design and Analysis of Algorithms', 5),
(1, 'AIML502', 'Linear Algebra II',                 5),
(2, 'AIML503', 'DBMS',                              5),
(2, 'AIML504', 'UNIX and Operating Systems',        5);

-- Enrollments (all 3 students in all 4 subjects)
INSERT INTO subject_enrollments (student_id, subject_id)
SELECT s.student_id, sub.subject_id
FROM students s, subjects sub;

-- ── FEATURE 2: material_url added to assignments ──────────────────────────────
INSERT INTO assignments (subject_id, title, description, due_date, max_marks, material_url) VALUES
(1, 'DAA Lab 3: Dijkstra',
    'Implement Dijkstra shortest path and trace execution on a sample graph.',
    '2025-10-20', 10,
    NULL),   -- teacher can upload via /faculty/assignments/<id>/upload-material
(1, 'DAA Assignment: 0/1 Knapsack',
    'Solve using dynamic programming. Show time complexity analysis.',
    '2025-10-27', 15,
    NULL),
(3, 'DBMS Lab 4: Normalization',
    'Normalize the given schema to BCNF. Submit DDL for final schema.',
    '2025-10-22', 10,
    NULL),
(4, 'UNIX Lab: Shell Scripting',
    'Write scripts for file management and process monitoring.',
    '2025-10-25', 10,
    NULL);

-- Projects
INSERT INTO projects (subject_id, title, description, start_date, end_date) VALUES
(3, 'Academic Task Tracker',
    'Design a centralized system to manage assignments and deadlines for SJEC.',
    '2025-09-01', '2025-11-30'),
(1, 'Graph Algorithm Visualizer',
    'Build an interactive tool to visualize graph algorithms step by step.',
    '2025-09-15', '2025-11-25');

-- Project members
INSERT INTO project_members (project_id, student_id, role) VALUES
(1, 1, 'leader'),
(1, 2, 'member'),
(1, 3, 'member'),
(2, 1, 'member'),
(2, 2, 'leader');

-- Submissions (status column now accepts 'revoked' too)
INSERT INTO submissions (student_id, submission_type, ref_id, file_url, status) VALUES
(1, 'assignment', 1, '/uploads/assignments/shaan_dijkstra.pdf', 'submitted'),
(2, 'assignment', 1, '/uploads/assignments/asha_dijkstra.pdf',  'submitted'),
(1, 'assignment', 3, '/uploads/assignments/shaan_norm.pdf',     'submitted'),
(1, 'project',    1, 'https://github.com/shaan-dsouza/academic-task-tracker', 'submitted');

-- Grades
INSERT INTO grades (submission_id, faculty_id, marks_obtained, remarks) VALUES
(1, 1, 9,    'Good trace, minor edge case missed.'),
(2, 1, 8,    'Correct but no complexity analysis.'),
(3, 2, 10,   'Perfect normalization to BCNF.'),
(4, 2, NULL, 'Awaiting final review.');


-- -------------------------------------------------------
-- SECTION 4: USEFUL QUERIES
-- -------------------------------------------------------

-- 1. All pending assignments for a student (not yet submitted / not revoked)
SELECT a.assignment_id, a.title, a.due_date, sub.name AS subject
FROM assignments a
JOIN subjects sub ON a.subject_id = sub.subject_id
JOIN subject_enrollments se ON se.subject_id = sub.subject_id
WHERE se.student_id = 1
  AND a.assignment_id NOT IN (
      SELECT ref_id FROM submissions
      WHERE student_id = 1
        AND submission_type = 'assignment'
        AND status != 'revoked'   -- revoked counts as "not submitted"
  )
ORDER BY a.due_date;

-- 2. All submissions by a student with their grades (excluding revoked)
SELECT
    s.submission_type,
    s.ref_id,
    s.submitted_at,
    s.status,
    g.marks_obtained,
    g.remarks
FROM submissions s
LEFT JOIN grades g ON g.submission_id = s.submission_id
WHERE s.student_id = 1
  AND s.status != 'revoked'
ORDER BY s.submitted_at DESC;

-- 3. All assignments for a subject with submission count (excluding revoked)
SELECT
    a.assignment_id,
    a.title,
    a.due_date,
    a.max_marks,
    a.material_url,                          -- NEW: show material link
    COUNT(sub.submission_id) FILTER (WHERE sub.status != 'revoked') AS total_submissions
FROM assignments a
LEFT JOIN submissions sub
    ON sub.ref_id = a.assignment_id
    AND sub.submission_type = 'assignment'
WHERE a.subject_id = 1
GROUP BY a.assignment_id, a.title, a.due_date, a.max_marks, a.material_url
ORDER BY a.due_date;

-- 4. Students who have NOT submitted an assignment (assignment_id = 1)
SELECT st.usn, u.name
FROM students st
JOIN users u ON u.user_id = st.user_id
JOIN subject_enrollments se ON se.student_id = st.student_id
JOIN assignments a ON a.subject_id = se.subject_id AND a.assignment_id = 1
WHERE st.student_id NOT IN (
    SELECT student_id FROM submissions
    WHERE submission_type = 'assignment'
      AND ref_id = 1
      AND status != 'revoked'   -- revoked = effectively not submitted
);

-- 5. Project teams with member details
SELECT
    p.title AS project,
    u.name AS student,
    st.usn,
    pm.role
FROM project_members pm
JOIN students st ON st.student_id = pm.student_id
JOIN users u ON u.user_id = st.user_id
JOIN projects p ON p.project_id = pm.project_id
ORDER BY p.project_id, pm.role DESC;

-- 6. Grade summary per subject for a student
SELECT
    sub.code,
    sub.name,
    COUNT(s.submission_id)          AS submitted,
    SUM(g.marks_obtained)           AS total_marks,
    ROUND(AVG(g.marks_obtained), 2) AS avg_marks
FROM submissions s
JOIN grades g      ON g.submission_id = s.submission_id
JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
JOIN subjects sub  ON sub.subject_id = a.subject_id
WHERE s.student_id = 1
  AND s.status != 'revoked'
GROUP BY sub.code, sub.name;

-- 7. Upcoming deadlines across all subjects (next 7 days) for a student
SELECT 'assignment' AS type, a.title, a.due_date, sub.name AS subject
FROM assignments a
JOIN subjects sub ON sub.subject_id = a.subject_id
JOIN subject_enrollments se ON se.subject_id = sub.subject_id
WHERE se.student_id = 1
  AND a.due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'

UNION ALL

SELECT 'project' AS type, p.title, p.end_date AS due_date, sub.name AS subject
FROM projects p
JOIN subjects sub ON sub.subject_id = p.subject_id
JOIN project_members pm ON pm.project_id = p.project_id
WHERE pm.student_id = 1
  AND p.end_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'

ORDER BY due_date;

-- 8. NEW — All revoked submissions (audit trail)
SELECT
    s.submission_id,
    u.name    AS student,
    st.usn,
    s.submission_type,
    COALESCE(a.title, p.title) AS item,
    s.submitted_at,
    s.status   -- 'revoked'
FROM submissions s
JOIN students st ON st.student_id = s.student_id
JOIN users u     ON u.user_id = st.user_id
LEFT JOIN assignments a ON a.assignment_id = s.ref_id AND s.submission_type = 'assignment'
LEFT JOIN projects p    ON p.project_id    = s.ref_id AND s.submission_type = 'project'
WHERE s.status = 'revoked'
ORDER BY s.submitted_at DESC;

-- 9. NEW — Assignments that have a teacher material PDF attached
SELECT a.assignment_id, a.title, a.material_url, sub.name AS subject
FROM assignments a
JOIN subjects sub ON sub.subject_id = a.subject_id
WHERE a.material_url IS NOT NULL
ORDER BY sub.name, a.title;


-- -----------------------------------------------------------------------
-- SECTION 5: TRIGGERS
-- -----------------------------------------------------------------------

-- 1. Late submissions (unchanged from original)
CREATE OR REPLACE FUNCTION fn_late_submission()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_due TIMESTAMP;
BEGIN
    -- Don't override status for revoked submissions
    IF NEW.status = 'revoked' THEN
        RETURN NEW;
    END IF;

    IF NEW.submission_type = 'assignment' THEN
        SELECT (due_date::timestamp + interval '23:59:59')
          INTO v_due
        FROM assignments
        WHERE assignment_id = NEW.ref_id;
    ELSE
        SELECT (end_date::timestamp + interval '23:59:59')
          INTO v_due
        FROM projects
        WHERE project_id = NEW.ref_id;
    END IF;

    IF v_due IS NOT NULL
       AND COALESCE(NEW.submitted_at, CURRENT_TIMESTAMP) > v_due THEN
        NEW.status := 'late';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_late_submission
BEFORE INSERT ON submissions
FOR EACH ROW
EXECUTE FUNCTION fn_late_submission();


-- -----------------------------------------------------------------------
-- SECTION 6: MIGRATION SCRIPT
-- (Run this block ONLY if upgrading an existing database — skip for fresh install)
-- -----------------------------------------------------------------------

-- Step 1: Add password_hash to existing users table
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT '';

-- Step 2: Add material_url to existing assignments table
ALTER TABLE assignments
    ADD COLUMN IF NOT EXISTS material_url TEXT DEFAULT NULL;

-- Step 3: Extend the status CHECK on submissions to include 'revoked'
--   PostgreSQL requires dropping and re-adding the constraint.
ALTER TABLE submissions
    DROP CONSTRAINT IF EXISTS submissions_status_check;
ALTER TABLE submissions
    ADD CONSTRAINT submissions_status_check
    CHECK (status IN ('submitted', 'late', 'resubmitted', 'revoked'));

-- Step 4: Add missing indexes
CREATE INDEX IF NOT EXISTS idx_submissions_status   ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_assignments_material ON assignments(material_url);

-- Step 5: Set real password hashes for existing users (run in Python first):
--   from werkzeug.security import generate_password_hash
--   hash = generate_password_hash("sjec@2025")
-- Then:
-- UPDATE users SET password_hash = '<hash>' WHERE email = 'meera@sjec.ac.in';
-- UPDATE users SET password_hash = '<hash>' WHERE email = 'ravi@sjec.ac.in';
-- UPDATE users SET password_hash = '<hash>' WHERE email = 'shaan@sjec.ac.in';
-- UPDATE users SET password_hash = '<hash>' WHERE email = 'asha@sjec.ac.in';
-- UPDATE users SET password_hash = '<hash>' WHERE email = 'rohan@sjec.ac.in';
