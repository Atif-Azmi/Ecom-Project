import os
import sqlite3
import tempfile
import secrets
import time
try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None

from datetime import date, datetime, timedelta
from functools import wraps
import csv
import io
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, jsonify, abort, make_response, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "static"),
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_url_path="/static"
)
app.secret_key = os.getenv("SECRET_KEY", "default-cloud-project-secret-key-2026")
app.config['TEMPLATES_AUTO_RELOAD'] = True

@app.route('/static/<path:filename>')
def serve_static_file(filename):
    """Explicitly serve static assets (CSS, JS) with proper MIME types on Vercel."""
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

# Database configuration from environment variables
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "root")
DB_NAME = os.getenv("DB_NAME", "library_db")
DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()

# Business rules constants
LATE_FEE_PER_DAY = 5.0  # Rs 5 per day late fee
DEFAULT_BORROW_DAYS = 14  # 14 days borrowing period

# Active database engine tracking ('mysql' or 'sqlite')
ACTIVE_DB_ENGINE = "mysql"

# In serverless environments like Vercel, the root filesystem is read-only.
# We automatically route SQLite to a writable temporary directory.
if os.getenv("VERCEL") or os.getenv("NOW_REGION") or not os.access(os.path.dirname(os.path.abspath(__file__)), os.W_OK):
    SQLITE_DB_PATH = os.path.join(tempfile.gettempdir(), "library.db")
else:
    SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")


# ==========================================================
# CSRF PROTECTION HELPERS
# ==========================================================
def get_csrf_token():
    """Generates or retrieves CSRF token from current session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


@app.context_processor
def inject_csrf_token():
    """Makes csrf_token() directly callable in all Jinja templates."""
    return dict(csrf_token=get_csrf_token)


@app.context_processor
def inject_user_counts():
    """Injects live cart_count, wishlist_count, and due_soon_count for logged-in students."""
    user_id = session.get('user_id')
    cart_count = 0
    wishlist_count = 0
    due_soon_count = 0
    if user_id and session.get('role') == 'student':
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as cnt FROM cart_items WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                cart_count = res['cnt'] if res else 0

                cursor.execute("SELECT COUNT(*) as cnt FROM wishlists WHERE user_id = %s", (user_id,))
                res = cursor.fetchone()
                wishlist_count = res['cnt'] if res else 0

                # Check for books due within 2 days (notification alert)
                today = date.today()
                target_date = today + timedelta(days=2)
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM borrow_records
                    WHERE user_id = %s AND return_date IS NULL AND due_date <= %s AND due_date >= %s
                """, (user_id, target_date, today))
                res = cursor.fetchone()
                due_soon_count = res['cnt'] if res else 0
            conn.close()
        except Exception:
            pass
    return dict(cart_count=cart_count, wishlist_count=wishlist_count, due_soon_count=due_soon_count)


def validate_csrf():
    """Validates the CSRF token from the POST request against the session."""
    token = request.form.get('csrf_token')
    expected_token = session.get('csrf_token')
    if not token or not expected_token or not secrets.compare_digest(token, expected_token):
        return False
    return True


# ==========================================================
# DATABASE ABSTRACTION & RESILIENT CONNECTION
# ==========================================================
class SQLiteCursorWrapper:
    """Wrapper around sqlite3 cursor to emulate PyMySQL DictCursor with %s placeholders and autocommit."""
    def __init__(self, cursor, conn=None):
        self._cursor = cursor
        self._conn = conn

    def execute(self, query, params=None):
        sqlite_query = query.replace("%s", "?")
        if params is not None:
            converted_params = []
            for p in params:
                if isinstance(p, (date, datetime)):
                    converted_params.append(str(p))
                else:
                    converted_params.append(p)
            res = self._cursor.execute(sqlite_query, converted_params)
        else:
            res = self._cursor.execute(sqlite_query)

        # Autocommit every DML statement to guarantee data persistence
        if self._conn is not None:
            try:
                self._conn.commit()
            except Exception:
                pass
        return res

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return self._convert_row(row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._convert_row(r) for r in rows]

    def _convert_row(self, row):
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, str) and (k.endswith('_date') or k in ('due_date', 'issue_date', 'return_date')):
                try:
                    d[k] = datetime.strptime(v, '%Y-%m-%d').date()
                except Exception:
                    pass
        return d

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and self._conn is not None:
            try:
                self._conn.commit()
            except Exception:
                pass
        self._cursor.close()

    def close(self):
        self._cursor.close()


class SQLiteConnectionWrapper:
    """Wrapper around sqlite3 connection to emulate PyMySQL connection interface."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return SQLiteCursorWrapper(self._conn.cursor(), self._conn)

    def close(self):
        try:
            self._conn.commit()
        except Exception:
            pass
        self._conn.close()

    def commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception:
                pass
        self._conn.close()


def init_sqlite_schema(conn):
    """Initializes tables, migrations, and seed data in SQLite."""
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        isbn TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        total_copies INTEGER NOT NULL DEFAULT 1,
        available_copies INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS borrow_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        order_no TEXT,
        issue_date TEXT NOT NULL,
        due_date TEXT NOT NULL,
        return_date TEXT,
        fine_amount REAL NOT NULL DEFAULT 0.0,
        fine_paid INTEGER NOT NULL DEFAULT 0,
        payment_ref TEXT,
        payment_date TEXT,
        status TEXT NOT NULL DEFAULT 'borrowed',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_number TEXT NOT NULL UNIQUE,
        supplier_name TEXT NOT NULL,
        book_id INTEGER NOT NULL,
        copies_ordered INTEGER NOT NULL DEFAULT 1,
        unit_cost REAL NOT NULL DEFAULT 0.0,
        total_cost REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'Pending',
        order_date TEXT NOT NULL,
        received_date TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cart_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
        UNIQUE(user_id, book_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wishlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
        UNIQUE(user_id, book_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        comment TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)
    conn.commit()

    # Column migrations for existing borrow_records tables
    cur.execute("PRAGMA table_info(borrow_records)")
    columns = [col[1] for col in cur.fetchall()]
    if 'order_no' not in columns:
        cur.execute("ALTER TABLE borrow_records ADD COLUMN order_no TEXT")
    if 'fine_paid' not in columns:
        cur.execute("ALTER TABLE borrow_records ADD COLUMN fine_paid INTEGER NOT NULL DEFAULT 0")
    if 'payment_ref' not in columns:
        cur.execute("ALTER TABLE borrow_records ADD COLUMN payment_ref TEXT")
    if 'payment_date' not in columns:
        cur.execute("ALTER TABLE borrow_records ADD COLUMN payment_date TEXT")
    cur.execute("UPDATE borrow_records SET fine_paid = 1 WHERE payment_ref IS NOT NULL AND (fine_paid = 0 OR fine_paid IS NULL)")
    conn.commit()

    # Seed initial accounts: 1 admin + 3 students
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        admin_hash = generate_password_hash('admin123')
        student_hash = generate_password_hash('student123')
        cur.execute("INSERT INTO users (username, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, 'admin')",
                    ('admin', 'admin@college.edu', admin_hash, 'System Administrator'))
        cur.execute("INSERT INTO users (username, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, 'student')",
                    ('student1', 'student1@college.edu', student_hash, 'Aarav Sharma'))
        cur.execute("INSERT INTO users (username, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, 'student')",
                    ('student2', 'student2@college.edu', student_hash, 'Priya Patel'))
        cur.execute("INSERT INTO users (username, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, 'student')",
                    ('student3', 'student3@college.edu', student_hash, 'Rohan Verma'))
        conn.commit()
    else:
        # Check and add student2 and student3 if missing
        student_hash = generate_password_hash('student123')
        cur.execute("SELECT id FROM users WHERE username = 'student2'")
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, 'student')",
                        ('student2', 'student2@college.edu', student_hash, 'Priya Patel'))
        cur.execute("SELECT id FROM users WHERE username = 'student3'")
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username, email, password_hash, full_name, role) VALUES (?, ?, ?, ?, 'student')",
                        ('student3', 'student3@college.edu', student_hash, 'Rohan Verma'))
        conn.commit()

    # Seed initial books if empty
    cur.execute("SELECT COUNT(*) FROM books")
    if cur.fetchone()[0] == 0:
        books_data = [
            ('Cloud Computing: Concepts, Technology & Architecture', 'Thomas Erl, Ricardo Puttini', '978-0133387520', 'Cloud Computing', 5, 4),
            ('Designing Data-Intensive Applications', 'Martin Kleppmann', '978-1449373320', 'Database Systems', 4, 3),
            ('Clean Code: A Handbook of Agile Software Craftsmanship', 'Robert C. Martin', '978-0132350884', 'Software Engineering', 6, 5),
            ('Operating System Concepts', 'Abraham Silberschatz, Peter B. Galvin', '978-1119800361', 'Operating Systems', 4, 2),
            ('Computer Networking: A Top-Down Approach', 'James Kurose, Keith Ross', '978-0136681557', 'Computer Networks', 3, 3),
            ('Introduction to Algorithms (CLRS)', 'Thomas H. Cormen, Charles E. Leiserson', '978-0262046305', 'Algorithms', 5, 5),
            ('Hands-On Machine Learning with Scikit-Learn, Keras, and TensorFlow', 'Aurélien Géron', '978-1492032649', 'Artificial Intelligence', 3, 2),
            ('Site Reliability Engineering', 'Betsy Beyer, Chris Jones', '978-1491929124', 'DevOps & Cloud', 4, 4)
        ]
        cur.executemany("INSERT INTO books (title, author, isbn, category, total_copies, available_copies) VALUES (?, ?, ?, ?, ?, ?)", books_data)
        conn.commit()

    # Seed initial borrow records if empty
    cur.execute("SELECT COUNT(*) FROM borrow_records")
    if cur.fetchone()[0] == 0:
        today = date.today()
        # Active on-time loan for student 1
        cur.execute("""
            INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, return_date, fine_amount, fine_paid, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2, 1, 'ORD-2026-1002', str(today - timedelta(days=5)), str(today + timedelta(days=9)), None, 0.0, 0, 'borrowed'))
        # Overdue loan with Rs 30.00 fine for student 1
        cur.execute("""
            INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, return_date, fine_amount, fine_paid, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2, 4, 'ORD-2026-1001', str(today - timedelta(days=20)), str(today - timedelta(days=6)), None, 30.0, 0, 'overdue'))
        # Active loan for student 2
        cur.execute("""
            INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, return_date, fine_amount, fine_paid, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (3, 3, 'ORD-2026-1003', str(today - timedelta(days=3)), str(today + timedelta(days=11)), None, 0.0, 0, 'borrowed'))
        # Returned loan for student 3
        cur.execute("""
            INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, return_date, fine_amount, fine_paid, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (4, 6, 'ORD-2026-1004', str(today - timedelta(days=16)), str(today - timedelta(days=2)), str(today - timedelta(days=2)), 0.0, 1, 'returned'))
        conn.commit()

    # Seed sample purchase orders if empty
    cur.execute("SELECT COUNT(*) FROM purchase_orders")
    if cur.fetchone()[0] == 0:
        today = date.today()
        cur.execute("""
            INSERT INTO purchase_orders (po_number, supplier_name, book_id, copies_ordered, unit_cost, total_cost, status, order_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('PO-2026-001', 'Oxford Academic Publishing', 4, 5, 650.0, 3250.0, 'Pending', str(today - timedelta(days=2)), 'Semester exam restock for Operating Systems'))
        cur.execute("""
            INSERT INTO purchase_orders (po_number, supplier_name, book_id, copies_ordered, unit_cost, total_cost, status, order_date, received_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('PO-2026-002', 'Pearson Education India', 1, 4, 720.0, 2880.0, 'Received', str(today - timedelta(days=10)), str(today - timedelta(days=2)), 'Annual textbook grant replenishment'))
        conn.commit()

    # Seed sample reviews if empty
    cur.execute("SELECT COUNT(*) FROM reviews")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO reviews (book_id, user_id, rating, comment) VALUES (?, ?, ?, ?)
        """, (3, 2, 5, 'A gold standard book on code craftsmanship. Helped me understand clean architecture and meaningful naming.'))
        cur.execute("""
            INSERT INTO reviews (book_id, user_id, rating, comment) VALUES (?, ?, ?, ?)
        """, (1, 3, 4, 'Very clear breakdown of cloud service models (IaaS, PaaS, SaaS) and multi-tenancy.'))
        cur.execute("""
            INSERT INTO reviews (book_id, user_id, rating, comment) VALUES (?, ?, ?, ?)
        """, (6, 4, 5, 'Comprehensive and rigorous. The definitive textbook for data structures and algorithms.'))
        conn.commit()

    # Seed sample wishlists if empty
    cur.execute("SELECT COUNT(*) FROM wishlists")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO wishlists (user_id, book_id) VALUES (?, ?)", (2, 2))
        cur.execute("INSERT INTO wishlists (user_id, book_id) VALUES (?, ?)", (3, 7))
        conn.commit()

    cur.close()


def get_db_connection():
    """
    Establishes database connection with resilient retry logic:
    - Retries MySQL up to 2 times for transient network/RDS hiccups.
    - Seamlessly falls back to SQLite (library.db) for effortless local testing.
    """
    global ACTIVE_DB_ENGINE

    if DB_TYPE == "sqlite" or pymysql is None or os.getenv("VERCEL"):
        ACTIVE_DB_ENGINE = "sqlite"
        raw_conn = sqlite3.connect(SQLITE_DB_PATH, timeout=10, isolation_level=None)
        raw_conn.row_factory = sqlite3.Row
        init_sqlite_schema(raw_conn)
        return SQLiteConnectionWrapper(raw_conn)

    last_error = None
    # Attempt MySQL connection (with 2 retries for production resilience)
    for attempt in range(2):
        try:
            conn = pymysql.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=2
            )
            ACTIVE_DB_ENGINE = "mysql"
            return conn
        except Exception as e:
            last_error = e
            time.sleep(0.2)

    # If MySQL is not reachable, fall back to SQLite for local development
    ACTIVE_DB_ENGINE = "sqlite"
    raw_conn = sqlite3.connect(SQLITE_DB_PATH, timeout=10, isolation_level=None)
    raw_conn.row_factory = sqlite3.Row
    init_sqlite_schema(raw_conn)
    return SQLiteConnectionWrapper(raw_conn)


# ==========================================================
# BUSINESS LOGIC & CALCULATION HELPERS
# ==========================================================
def calculate_fine(due_date, return_date=None):
    """
    Calculates fine based on Rs 5 per day after due date.
    - If book is returned: fine is based on return_date - due_date.
    - If book is still out: fine is based on today - due_date.
    """
    reference_date = return_date if return_date else date.today()
    if isinstance(reference_date, datetime):
        reference_date = reference_date.date()
    if isinstance(due_date, datetime):
        due_date = due_date.date()

    if reference_date > due_date:
        days_late = (reference_date - due_date).days
        return round(days_late * LATE_FEE_PER_DAY, 2)
    return 0.0


def update_overdue_statuses(cursor):
    """Updates status and calculated fine for overdue unreturned records."""
    today = date.today()
    cursor.execute("""
        SELECT id, due_date FROM borrow_records
        WHERE status = 'borrowed' AND due_date < %s AND (fine_paid = 0 OR fine_paid IS NULL)
    """, (today,))
    overdue_records = cursor.fetchall()

    for rec in overdue_records:
        fine = calculate_fine(rec['due_date'])
        cursor.execute("""
            UPDATE borrow_records
            SET status = 'overdue', fine_amount = %s
            WHERE id = %s AND (fine_paid = 0 OR fine_paid IS NULL)
        """, (fine, rec['id']))


def init_db_seeds():
    """Ensures admin and student seed users exist on database startup."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE username = 'admin'")
            admin = cursor.fetchone()
            if not admin:
                cursor.execute("""
                    INSERT INTO users (username, email, password_hash, full_name, role)
                    VALUES (%s, %s, %s, %s, 'admin')
                """, (
                    'admin',
                    'admin@college.edu',
                    generate_password_hash('admin123'),
                    'System Administrator'
                ))

            cursor.execute("SELECT id FROM users WHERE username = 'student1'")
            student = cursor.fetchone()
            if not student:
                cursor.execute("""
                    INSERT INTO users (username, email, password_hash, full_name, role)
                    VALUES (%s, %s, %s, %s, 'student')
                """, (
                    'student1',
                    'student1@college.edu',
                    generate_password_hash('student123'),
                    'Aarav Sharma'
                ))
        conn.close()
    except Exception as e:
        print(f"[Notice] DB init skipped or not yet available: {e}")


# ==========================================================
# AUTH DECORATORS (ROLE-BASED ACCESS CONTROL)
# ==========================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for('login', next=request.url))
        if session.get('role') != 'admin':
            flash("Access denied. Administrator privileges required.", "danger")
            return redirect(url_for('student_dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for('login', next=request.url))
        if session.get('role') != 'student':
            flash("Redirected to administrator portal.", "info")
            return redirect(url_for('admin_dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# ==========================================================
# HEALTH CHECK ENDPOINT (ALB & DevOps Monitoring)
# ==========================================================
@app.route('/health', methods=['GET'])
def health_check():
    """
    AWS Application Load Balancer health check endpoint.
    Returns HTTP 200 and checks DB connectivity without requiring login.
    """
    db_status = "connected"
    engine_used = ACTIVE_DB_ENGINE
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        conn.close()
    except Exception as e:
        db_status = f"disconnected: {str(e)}"

    return jsonify({
        "status": "healthy",
        "service": "Library Management System",
        "database": db_status,
        "engine": engine_used,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), 200


# ==========================================================
# PUBLIC CATALOG & AUTHENTICATION
# ==========================================================
@app.route('/')
def index():
    """Home / Public catalog showing books, search, ratings, and category pills."""
    search_query = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()

    books = []
    categories = []
    stats = {"total_titles": 0, "available_books": 0}
    wishlist_ids = set()
    cart_ids = set()
    borrowed_ids = set()

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT category FROM books ORDER BY category ASC")
            categories = [row['category'] for row in cursor.fetchall()]

            sql = """
                SELECT b.*,
                       (SELECT ROUND(AVG(rating), 1) FROM reviews WHERE book_id = b.id) as avg_rating,
                       (SELECT COUNT(*) FROM reviews WHERE book_id = b.id) as review_count
                FROM books b
                WHERE 1=1
            """
            params = []
            if search_query:
                sql += " AND (b.title LIKE %s OR b.author LIKE %s OR b.isbn LIKE %s)"
                term = f"%{search_query}%"
                params.extend([term, term, term])
            if category:
                sql += " AND b.category = %s"
                params.append(category)

            sql += " ORDER BY b.id DESC"
            cursor.execute(sql, tuple(params))
            books = cursor.fetchall()

            cursor.execute("SELECT COUNT(*) as titles, SUM(available_copies) as available FROM books")
            stat_res = cursor.fetchone()
            if stat_res:
                stats['total_titles'] = stat_res['titles'] or 0
                stats['available_books'] = stat_res['available'] or 0

            user_id = session.get('user_id')
            if user_id:
                cursor.execute("SELECT book_id FROM wishlists WHERE user_id = %s", (user_id,))
                wishlist_ids = {row['book_id'] for row in cursor.fetchall()}
                cursor.execute("SELECT book_id FROM cart_items WHERE user_id = %s", (user_id,))
                cart_ids = {row['book_id'] for row in cursor.fetchall()}
                cursor.execute("SELECT book_id FROM borrow_records WHERE user_id = %s AND return_date IS NULL", (user_id,))
                borrowed_ids = {row['book_id'] for row in cursor.fetchall()}
        conn.close()
    except Exception as e:
        flash(f"Database error while loading catalog: {e}", "danger")

    return render_template('index.html', books=books, categories=categories,
                           search=search_query, selected_category=category, stats=stats,
                           wishlist_ids=wishlist_ids, cart_ids=cart_ids, borrowed_ids=borrowed_ids)


@app.route('/book/<int:book_id>')
def book_detail(book_id):
    """Product-style book detail page with description, stock alert, wishlist/cart action, and reviews."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT b.*,
                       (SELECT ROUND(AVG(rating), 1) FROM reviews WHERE book_id = b.id) as avg_rating,
                       (SELECT COUNT(*) FROM reviews WHERE book_id = b.id) as review_count
                FROM books b WHERE b.id = %s
            """, (book_id,))
            book = cursor.fetchone()
            if not book:
                flash("Book not found.", "warning")
                conn.close()
                return redirect(url_for('index'))

            cursor.execute("""
                SELECT r.*, u.full_name, u.username
                FROM reviews r
                JOIN users u ON r.user_id = u.id
                WHERE r.book_id = %s
                ORDER BY r.id DESC
            """, (book_id,))
            reviews = cursor.fetchall()

            is_in_wishlist = False
            is_in_cart = False
            is_borrowed = False
            user_id = session.get('user_id')
            if user_id:
                cursor.execute("SELECT id FROM wishlists WHERE user_id = %s AND book_id = %s", (user_id, book_id))
                is_in_wishlist = bool(cursor.fetchone())
                cursor.execute("SELECT id FROM cart_items WHERE user_id = %s AND book_id = %s", (user_id, book_id))
                is_in_cart = bool(cursor.fetchone())
                cursor.execute("SELECT id FROM borrow_records WHERE user_id = %s AND book_id = %s AND return_date IS NULL", (user_id, book_id))
                is_borrowed = bool(cursor.fetchone())

        conn.close()
        return render_template('book_detail.html', book=book, reviews=reviews,
                               is_in_wishlist=is_in_wishlist, is_in_cart=is_in_cart, is_borrowed=is_borrowed)
    except Exception as e:
        flash(f"Error loading book details: {e}", "danger")
        conn.close()
        return redirect(url_for('index'))


@app.route('/book/<int:book_id>/review', methods=['POST'])
@student_required
def submit_review(book_id):
    """Students submit a rating (1-5) and feedback review for a textbook."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('book_detail', book_id=book_id))

    user_id = session['user_id']
    rating_raw = request.form.get('rating', '5')
    comment = request.form.get('comment', '').strip()

    try:
        rating = int(rating_raw)
        if rating < 1 or rating > 5:
            raise ValueError()
    except ValueError:
        flash("Rating must be between 1 and 5 stars.", "danger")
        return redirect(url_for('book_detail', book_id=book_id))

    if not comment:
        flash("Please enter a short review comment.", "danger")
        return redirect(url_for('book_detail', book_id=book_id))

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO reviews (book_id, user_id, rating, comment)
                VALUES (%s, %s, %s, %s)
            """, (book_id, user_id, rating, comment))
        conn.close()
        flash("Thank you! Your book review and rating have been posted.", "success")
    except Exception as e:
        flash(f"Error posting review: {e}", "danger")

    return redirect(url_for('book_detail', book_id=book_id))


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Student registration page with CSRF validation."""
    if 'user_id' in session:
        return redirect(url_for('admin_dashboard' if session.get('role') == 'admin' else 'student_dashboard'))

    if request.method == 'POST':
        if not validate_csrf():
            flash("Session expired or invalid security token. Please try again.", "danger")
            return render_template('register.html')

        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        full_name = request.form.get('full_name', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not username or not email or not full_name or not password:
            flash("All fields are required.", "danger")
            return render_template('register.html')

        if len(username) < 3:
            flash("Username must be at least 3 characters long.", "danger")
            return render_template('register.html')

        if '@' not in email or '.' not in email:
            flash("Please enter a valid email address.", "danger")
            return render_template('register.html')

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('register.html')

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template('register.html')

        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
                existing = cursor.fetchone()
                if existing:
                    flash("Username or email already registered. Please sign in.", "warning")
                    conn.close()
                    return redirect(url_for('login'))

                password_hash = generate_password_hash(password)
                cursor.execute("""
                    INSERT INTO users (username, email, password_hash, full_name, role)
                    VALUES (%s, %s, %s, %s, 'student')
                """, (username, email, password_hash, full_name))
            conn.close()

            flash("Registration successful! You can now sign in.", "success")
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"Registration failed: {e}", "danger")

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Unified login for students and administrators with CSRF validation."""
    if 'user_id' in session:
        return redirect(url_for('admin_dashboard' if session.get('role') == 'admin' else 'student_dashboard'))

    if request.method == 'POST':
        if not validate_csrf():
            flash("Session expired or invalid security token. Please try again.", "danger")
            return render_template('login.html')

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash("Please enter both username and password.", "danger")
            return render_template('login.html')

        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE username = %s OR email = %s", (username, username))
                user = cursor.fetchone()
            conn.close()

            if user and check_password_hash(user['password_hash'], password):
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['full_name'] = user['full_name']
                session['role'] = user['role']
                get_csrf_token()  # regenerate token for newly authenticated session

                flash(f"Welcome back, {user['full_name']}!", "success")
                next_page = request.args.get('next')
                if next_page:
                    return redirect(next_page)

                if user['role'] == 'admin':
                    return redirect(url_for('admin_dashboard'))
                return redirect(url_for('student_dashboard'))
            else:
                flash("Invalid username or password.", "danger")
        except Exception as e:
            flash(f"Login error: {e}", "danger")

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logs the user out and clears the session."""
    session.clear()
    flash("You have been signed out successfully.", "info")
    return redirect(url_for('login'))


# ==========================================================
# STUDENT PORTAL ROUTES
# ==========================================================
# ==========================================================
# STUDENT PORTAL & E-COMMERCE ROUTES
# ==========================================================
@app.route('/student/dashboard')
@student_required
def student_dashboard():
    """Student dashboard with active loans, due notifications, unpaid fines, and live stat tiles."""
    user_id = session['user_id']
    active_borrows = []
    unpaid_fines = []
    due_soon_books = []
    stats = {"active_count": 0, "overdue_count": 0, "total_fines": 0.0}

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)

            cursor.execute("""
                SELECT br.*, b.title, b.author, b.isbn, b.category
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                WHERE br.user_id = %s AND br.return_date IS NULL
                ORDER BY br.due_date ASC
            """, (user_id,))
            active_borrows = cursor.fetchall()

            today = date.today()
            for item in active_borrows:
                due_d = item['due_date']
                item['is_overdue'] = today > due_d
                item['days_remaining'] = (due_d - today).days
                if item['is_overdue']:
                    if item.get('fine_paid'):
                        item['current_fine'] = item.get('fine_amount', 0.0)
                    else:
                        item['current_fine'] = calculate_fine(due_d)
                        stats['overdue_count'] += 1
                        stats['total_fines'] += item['current_fine']
                else:
                    item['current_fine'] = 0.0
                    if 0 <= item['days_remaining'] <= 2:
                        due_soon_books.append(item)

            stats['active_count'] = len(active_borrows)

            # Unpaid fines (from overdue active loans or returned books with unpaid fines)
            cursor.execute("""
                SELECT br.*, b.title, b.author
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                WHERE br.user_id = %s AND br.fine_amount > 0 AND (br.fine_paid = 0 OR br.fine_paid IS NULL)
                ORDER BY br.id DESC
            """, (user_id,))
            unpaid_fines = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error loading dashboard: {e}", "danger")

    return render_template('student/dashboard.html', active_borrows=active_borrows,
                           unpaid_fines=unpaid_fines, due_soon_books=due_soon_books, stats=stats)


@app.route('/student/books')
@student_required
def student_books():
    """Student book browsing page with search, category filters, wishlist, and cart buttons."""
    user_id = session['user_id']
    search_query = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()

    books = []
    categories = []
    user_borrowed_book_ids = set()
    user_cart_book_ids = set()
    user_wishlist_ids = set()

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT book_id FROM borrow_records WHERE user_id = %s AND return_date IS NULL", (user_id,))
            user_borrowed_book_ids = {row['book_id'] for row in cursor.fetchall()}

            cursor.execute("SELECT book_id FROM cart_items WHERE user_id = %s", (user_id,))
            user_cart_book_ids = {row['book_id'] for row in cursor.fetchall()}

            cursor.execute("SELECT book_id FROM wishlists WHERE user_id = %s", (user_id,))
            user_wishlist_ids = {row['book_id'] for row in cursor.fetchall()}

            cursor.execute("SELECT DISTINCT category FROM books ORDER BY category ASC")
            categories = [r['category'] for r in cursor.fetchall()]

            sql = """
                SELECT b.*,
                       (SELECT ROUND(AVG(rating), 1) FROM reviews WHERE book_id = b.id) as avg_rating,
                       (SELECT COUNT(*) FROM reviews WHERE book_id = b.id) as review_count
                FROM books b
                WHERE 1=1
            """
            params = []
            if search_query:
                sql += " AND (b.title LIKE %s OR b.author LIKE %s OR b.isbn LIKE %s)"
                term = f"%{search_query}%"
                params.extend([term, term, term])
            if category:
                sql += " AND b.category = %s"
                params.append(category)

            sql += " ORDER BY b.title ASC"
            cursor.execute(sql, tuple(params))
            books = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error loading books: {e}", "danger")

    return render_template('student/books.html', books=books, categories=categories,
                           search=search_query, selected_category=category,
                           borrowed_ids=user_borrowed_book_ids,
                           cart_ids=user_cart_book_ids,
                           wishlist_ids=user_wishlist_ids)


@app.route('/student/borrow/<int:book_id>', methods=['POST'])
@student_required
def borrow_book(book_id):
    """Borrow a book immediately for 14 days with unique Order ID and availability check."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('student_books'))

    user_id = session['user_id']
    order_no = f"ORD-{date.today().year}-{secrets.randbelow(9000) + 1000}"

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Check duplicate active loan
            cursor.execute("""
                SELECT id FROM borrow_records
                WHERE user_id = %s AND book_id = %s AND return_date IS NULL
            """, (user_id, book_id))
            if cursor.fetchone():
                flash("You currently have an active copy of this book on loan.", "warning")
                conn.close()
                return redirect(url_for('student_books'))

            # Check stock
            cursor.execute("SELECT title, available_copies FROM books WHERE id = %s", (book_id,))
            book = cursor.fetchone()
            if not book:
                flash("Book not found.", "danger")
                conn.close()
                return redirect(url_for('student_books'))

            if book['available_copies'] <= 0:
                flash(f"Sorry, '{book['title']}' is currently out of stock.", "danger")
                conn.close()
                return redirect(url_for('student_books'))

            issue_date = date.today()
            due_date = issue_date + timedelta(days=DEFAULT_BORROW_DAYS)

            cursor.execute("""
                INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, status)
                VALUES (%s, %s, %s, %s, %s, 'borrowed')
            """, (user_id, book_id, order_no, issue_date, due_date))

            cursor.execute("""
                UPDATE books
                SET available_copies = available_copies - 1
                WHERE id = %s
            """, (book_id,))

        conn.close()
        flash(f"Order #{order_no} Confirmed! You borrowed '{book['title']}'. Due date: {due_date.strftime('%b %d, %Y')}.", "success")
        return redirect(url_for('student_dashboard'))
    except Exception as e:
        flash(f"Borrowing failed: {e}", "danger")
        return redirect(url_for('student_books'))


# ==========================================================
# BORROW CART ROUTES
# ==========================================================
@app.route('/student/cart')
@student_required
def student_cart():
    """Borrow cart page with added textbooks, stock checks, and checkout button."""
    user_id = session['user_id']
    cart_items = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ci.id as cart_item_id, ci.added_at, b.*
                FROM cart_items ci
                JOIN books b ON ci.book_id = b.id
                WHERE ci.user_id = %s
                ORDER BY ci.id DESC
            """, (user_id,))
            cart_items = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error loading borrow cart: {e}", "danger")

    return render_template('student/cart.html', cart_items=cart_items)


@app.route('/student/cart/add/<int:book_id>', methods=['POST'])
@student_required
def add_to_cart(book_id):
    """Add a textbook to the student borrow cart."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(request.referrer or url_for('student_books'))

    user_id = session['user_id']
    next_url = request.form.get('next') or request.referrer or url_for('student_cart')

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,))
            book = cursor.fetchone()
            if not book:
                flash("Book not found.", "danger")
                conn.close()
                return redirect(next_url)

            if book['available_copies'] <= 0:
                flash(f"'{book['title']}' is currently out of stock.", "warning")
                conn.close()
                return redirect(next_url)

            # Check if student currently has active loan
            cursor.execute("""
                SELECT id FROM borrow_records
                WHERE user_id = %s AND book_id = %s AND return_date IS NULL
            """, (user_id, book_id))
            if cursor.fetchone():
                flash(f"You already have an active loan for '{book['title']}'.", "warning")
                conn.close()
                return redirect(next_url)

            # Check if already in cart
            cursor.execute("SELECT id FROM cart_items WHERE user_id = %s AND book_id = %s", (user_id, book_id))
            if cursor.fetchone():
                flash(f"'{book['title']}' is already in your borrow cart.", "info")
            else:
                cursor.execute("INSERT INTO cart_items (user_id, book_id) VALUES (%s, %s)", (user_id, book_id))
                flash(f"Added '{book['title']}' to your borrow cart!", "success")
        conn.close()
    except Exception as e:
        flash(f"Error adding to cart: {e}", "danger")

    return redirect(next_url)


@app.route('/student/cart/remove/<int:book_id>', methods=['POST'])
@student_required
def remove_from_cart(book_id):
    """Remove a book from borrow cart."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('student_cart'))

    user_id = session['user_id']
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM cart_items WHERE user_id = %s AND book_id = %s", (user_id, book_id))
        conn.close()
        flash("Book removed from your borrow cart.", "info")
    except Exception as e:
        flash(f"Error removing from cart: {e}", "danger")

    return redirect(url_for('student_cart'))


@app.route('/student/cart/checkout', methods=['POST'])
@student_required
def cart_checkout():
    """E-Commerce style checkout: loans all books in cart under a unified Order Number."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('student_cart'))

    user_id = session['user_id']
    order_no = f"ORD-{date.today().year}-{secrets.randbelow(9000) + 1000}"

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ci.book_id, b.title, b.available_copies
                FROM cart_items ci
                JOIN books b ON ci.book_id = b.id
                WHERE ci.user_id = %s
            """, (user_id,))
            items = cursor.fetchall()

            if not items:
                flash("Your borrow cart is empty.", "warning")
                conn.close()
                return redirect(url_for('student_books'))

            borrowed_count = 0
            skipped_titles = []
            today = date.today()
            due_date = today + timedelta(days=DEFAULT_BORROW_DAYS)

            for item in items:
                b_id = item['book_id']
                # Re-verify stock and duplicate loan
                cursor.execute("""
                    SELECT id FROM borrow_records
                    WHERE user_id = %s AND book_id = %s AND return_date IS NULL
                """, (user_id, b_id))
                if cursor.fetchone():
                    skipped_titles.append(f"{item['title']} (already active on loan)")
                    continue

                if item['available_copies'] <= 0:
                    skipped_titles.append(f"{item['title']} (out of stock)")
                    continue

                # Record loan
                cursor.execute("""
                    INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, fine_amount, status)
                    VALUES (%s, %s, %s, %s, %s, 0.0, 'borrowed')
                """, (user_id, b_id, order_no, today, due_date))

                # Decrement copy
                cursor.execute("""
                    UPDATE books SET available_copies = available_copies - 1
                    WHERE id = %s
                """, (b_id,))
                borrowed_count += 1

            # Clear student cart
            cursor.execute("DELETE FROM cart_items WHERE user_id = %s", (user_id,))
        conn.close()

        if borrowed_count > 0:
            msg = f"Order #{order_no} Confirmed! Successfully borrowed {borrowed_count} title(s). Due Date: {due_date.strftime('%b %d, %Y')}."
            if skipped_titles:
                msg += f" Note: {', '.join(skipped_titles)} could not be issued."
            flash(msg, "success")
        else:
            flash(f"Checkout could not be completed: {', '.join(skipped_titles)}", "danger")

        return redirect(url_for('student_dashboard'))
    except Exception as e:
        flash(f"Checkout transaction failed: {e}", "danger")
        return redirect(url_for('student_cart'))


# ==========================================================
# WISHLIST ("SAVE FOR LATER") ROUTES
# ==========================================================
@app.route('/student/wishlist')
@student_required
def student_wishlist():
    """Student wishlist page ("Save for later")."""
    user_id = session['user_id']
    saved_books = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT w.id as wishlist_id, w.created_at as saved_at, b.*,
                       (SELECT ROUND(AVG(rating), 1) FROM reviews WHERE book_id = b.id) as avg_rating
                FROM wishlists w
                JOIN books b ON w.book_id = b.id
                WHERE w.user_id = %s
                ORDER BY w.id DESC
            """, (user_id,))
            saved_books = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error loading wishlist: {e}", "danger")

    return render_template('student/wishlist.html', books=saved_books)


@app.route('/student/wishlist/toggle/<int:book_id>', methods=['POST'])
@student_required
def toggle_wishlist(book_id):
    """Toggle adding/removing a book from student's wishlist."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(request.referrer or url_for('index'))

    user_id = session['user_id']
    next_url = request.form.get('next') or request.referrer or url_for('student_wishlist')

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM wishlists WHERE user_id = %s AND book_id = %s", (user_id, book_id))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("DELETE FROM wishlists WHERE id = %s", (existing['id'],))
                flash("Removed book from your wishlist.", "info")
            else:
                cursor.execute("INSERT INTO wishlists (user_id, book_id) VALUES (%s, %s)", (user_id, book_id))
                flash("Book saved to your wishlist!", "success")
        conn.close()
    except Exception as e:
        flash(f"Wishlist update failed: {e}", "danger")

    return redirect(next_url)


@app.route('/student/wishlist/move-to-cart/<int:book_id>', methods=['POST'])
@student_required
def wishlist_move_to_cart(book_id):
    """Move an item from wishlist directly into borrow cart."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('student_wishlist'))

    user_id = session['user_id']
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT available_copies, title FROM books WHERE id = %s", (book_id,))
            book = cursor.fetchone()
            if not book or book['available_copies'] <= 0:
                flash("This book is currently out of stock.", "warning")
                conn.close()
                return redirect(url_for('student_wishlist'))

            cursor.execute("SELECT id FROM cart_items WHERE user_id = %s AND book_id = %s", (user_id, book_id))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO cart_items (user_id, book_id) VALUES (%s, %s)", (user_id, book_id))

            cursor.execute("DELETE FROM wishlists WHERE user_id = %s AND book_id = %s", (user_id, book_id))
            flash(f"Moved '{book['title']}' to your borrow cart!", "success")
        conn.close()
    except Exception as e:
        flash(f"Error moving item to cart: {e}", "danger")

    return redirect(url_for('student_cart'))


# ==========================================================
# FINE PAYMENT GATEWAY & PRINTABLE INVOICE / RECEIPT
# ==========================================================
@app.route('/student/pay-fine/<int:record_id>', methods=['GET', 'POST'])
@student_required
def pay_fine(record_id):
    """Demo payment gateway for overdue book fines (Fake UPI / Card form)."""
    user_id = session['user_id']
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)
            cursor.execute("""
                SELECT br.*, b.title, b.isbn, b.author
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                WHERE br.id = %s AND br.user_id = %s
            """, (record_id, user_id))
            record = cursor.fetchone()

        if not record:
            flash("Borrow record not found.", "danger")
            conn.close()
            return redirect(url_for('student_dashboard'))

        # Calculate live fine if book is still overdue
        fine = record['fine_amount']
        if record['return_date'] is None and record['due_date'] < date.today():
            fine = calculate_fine(record['due_date'])

        if fine <= 0:
            flash("There are no outstanding fines on this loan.", "info")
            conn.close()
            return redirect(url_for('student_dashboard'))

        if record.get('fine_paid'):
            flash("This fine has already been paid.", "info")
            conn.close()
            return redirect(url_for('student_receipt', record_id=record_id))

        if request.method == 'POST':
            if not validate_csrf():
                flash("Security token mismatch. Please try again.", "danger")
                conn.close()
                return render_template('student/pay_fine.html', record=record, fine=fine)

            payment_method = request.form.get('payment_method', 'UPI')
            upi_id = request.form.get('upi_id', 'atifazmi2005@oksbi').strip() or 'atifazmi2005@oksbi'
            card_last4 = request.form.get('card_number', '4242')[-4:]

            txn_ref = f"PAY-{secrets.token_hex(4).upper()}"
            pay_detail = f"{payment_method} ({upi_id if payment_method == 'UPI' else 'Card ending in ' + card_last4}) - Ref: {txn_ref}"

            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE borrow_records
                    SET fine_amount = %s, fine_paid = 1, payment_ref = %s, payment_date = %s
                    WHERE id = %s
                """, (fine, pay_detail, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), record_id))
            conn.commit()
            conn.close()

            flash(f"Payment Successful! Rs {fine:.2f} received via {payment_method}. Official receipt generated.", "success")
            return redirect(url_for('student_receipt', record_id=record_id))

        conn.close()
        return render_template('student/pay_fine.html', record=record, fine=fine)
    except Exception as e:
        flash(f"Payment gateway error: {e}", "danger")
        conn.close()
        return redirect(url_for('student_dashboard'))


@app.route('/student/receipt/<int:record_id>')
@login_required
def student_receipt(record_id):
    """Printable invoice and fee payment receipt with college branding."""
    user_id = session['user_id']
    role = session.get('role')
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            if role == 'admin':
                cursor.execute("""
                    SELECT br.*, b.title, b.isbn, b.author, u.full_name, u.email, u.username
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.id = %s
                """, (record_id,))
            else:
                cursor.execute("""
                    SELECT br.*, b.title, b.isbn, b.author, u.full_name, u.email, u.username
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.id = %s AND br.user_id = %s
                """, (record_id, user_id))
            record = cursor.fetchone()
        conn.close()

        if not record:
            flash("Receipt record not found.", "danger")
            return redirect(url_for('student_dashboard' if role == 'student' else 'admin_dashboard'))

        days_overdue = 0
        if record['due_date'] < (record['return_date'] or date.today()):
            ref_d = record['return_date'] or date.today()
            days_overdue = max(0, (ref_d - record['due_date']).days)

        return render_template('student/receipt.html', record=record, days_overdue=days_overdue)
    except Exception as e:
        flash(f"Error generating receipt: {e}", "danger")
        conn.close()
        return redirect(url_for('student_dashboard'))


@app.route('/student/return/<int:record_id>', methods=['POST'])
@student_required
def student_return_book(record_id):
    """Student returns their borrowed book."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('student_dashboard'))

    user_id = session['user_id']

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT br.*, b.title, b.id as b_id, b.total_copies, b.available_copies
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                WHERE br.id = %s AND br.user_id = %s AND br.return_date IS NULL
            """, (record_id, user_id))
            record = cursor.fetchone()

            if not record:
                flash("Borrow record not found or already returned.", "danger")
                conn.close()
                return redirect(url_for('student_dashboard'))

            today = date.today()
            fine = calculate_fine(record['due_date'], today)

            cursor.execute("""
                UPDATE borrow_records
                SET return_date = %s, fine_amount = %s, status = 'returned'
                WHERE id = %s
            """, (today, fine, record_id))

            new_avail = min(record['total_copies'], record['available_copies'] + 1)
            cursor.execute("""
                UPDATE books
                SET available_copies = %s
                WHERE id = %s
            """, (new_avail, record['b_id']))

        conn.close()

        if fine > 0:
            flash(f"Book '{record['title']}' returned. Note: Late fee of Rs {fine:.2f} incurred.", "warning")
        else:
            flash(f"Book '{record['title']}' returned successfully with zero late fees!", "success")
        return redirect(url_for('student_dashboard'))
    except Exception as e:
        flash(f"Error returning book: {e}", "danger")
        return redirect(url_for('student_dashboard'))


@app.route('/student/history')
@student_required
def student_history():
    """Complete borrowing history for the student with order IDs and payment status."""
    user_id = session['user_id']
    history = []

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT br.*, b.title, b.author, b.isbn, b.category
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                WHERE br.user_id = %s
                ORDER BY br.id DESC
            """, (user_id,))
            history = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error fetching history: {e}", "danger")

    return render_template('student/history.html', history=history)


# ==========================================================
# ADMINISTRATOR PORTAL ROUTES
# ==========================================================
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard with high-level statistics, ERP metrics, and circular dial data."""
    stats = {
        "total_titles": 0,
        "total_copies": 0,
        "available_copies": 0,
        "borrowed_books": 0,
        "overdue_count": 0,
        "total_members": 0,
        "total_fines_collected": 0.0,
        "outstanding_fines": 0.0,
        "low_stock_count": 0,
        "pending_po_count": 0,
        "utilization_percent": 0
    }
    recent_borrows = []
    recent_pos = []

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)

            cursor.execute("""
                SELECT COUNT(*) as titles,
                       COALESCE(SUM(total_copies), 0) as total,
                       COALESCE(SUM(available_copies), 0) as available
                FROM books
            """)
            book_stat = cursor.fetchone()
            if book_stat:
                stats["total_titles"] = book_stat["titles"]
                stats["total_copies"] = book_stat["total"]
                stats["available_copies"] = book_stat["available"]

            cursor.execute("SELECT COUNT(*) as active FROM borrow_records WHERE return_date IS NULL")
            stats["borrowed_books"] = cursor.fetchone()["active"]

            today = date.today()
            cursor.execute("SELECT COUNT(*) as overdue FROM borrow_records WHERE return_date IS NULL AND due_date < %s", (today,))
            stats["overdue_count"] = cursor.fetchone()["overdue"]

            cursor.execute("SELECT COUNT(*) as members FROM users WHERE role = 'student'")
            stats["total_members"] = cursor.fetchone()["members"]

            # Fines paid vs unpaid
            cursor.execute("SELECT COALESCE(SUM(fine_amount), 0) as fines FROM borrow_records WHERE fine_paid = 1")
            stats["total_fines_collected"] = cursor.fetchone()["fines"]

            cursor.execute("SELECT COALESCE(SUM(fine_amount), 0) as fines FROM borrow_records WHERE fine_paid = 0 AND (status = 'overdue' OR return_date IS NOT NULL)")
            stats["outstanding_fines"] = cursor.fetchone()["fines"]

            # Low stock count (<= 2 copies)
            cursor.execute("SELECT COUNT(*) as low FROM books WHERE available_copies <= 2 AND available_copies > 0")
            stats["low_stock_count"] = cursor.fetchone()["low"]

            # Pending Purchase Orders
            cursor.execute("SELECT COUNT(*) as cnt FROM purchase_orders WHERE status = 'Pending'")
            stats["pending_po_count"] = cursor.fetchone()["cnt"]

            if stats["total_copies"] > 0:
                stats["utilization_percent"] = round((stats["borrowed_books"] / stats["total_copies"]) * 100)

            cursor.execute("""
                SELECT br.*, b.title, u.full_name, u.username
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                JOIN users u ON br.user_id = u.id
                ORDER BY br.id DESC LIMIT 6
            """)
            recent_borrows = cursor.fetchall()

            cursor.execute("""
                SELECT po.*, b.title as book_title
                FROM purchase_orders po
                JOIN books b ON po.book_id = b.id
                ORDER BY po.id DESC LIMIT 4
            """)
            recent_pos = cursor.fetchall()

        conn.close()
    except Exception as e:
        flash(f"Error loading admin dashboard: {e}", "danger")

    return render_template('admin/dashboard.html', stats=stats, recent_borrows=recent_borrows, recent_pos=recent_pos)


@app.route('/admin/books')
@admin_required
def admin_books():
    """Admin book catalog management."""
    search_query = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    books = []
    categories = []

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT category FROM books ORDER BY category ASC")
            categories = [r['category'] for r in cursor.fetchall()]

            sql = "SELECT * FROM books WHERE 1=1"
            params = []
            if search_query:
                sql += " AND (title LIKE %s OR author LIKE %s OR isbn LIKE %s)"
                term = f"%{search_query}%"
                params.extend([term, term, term])
            if category:
                sql += " AND category = %s"
                params.append(category)

            sql += " ORDER BY id DESC"
            cursor.execute(sql, tuple(params))
            books = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error loading books: {e}", "danger")

    return render_template('admin/books.html', books=books, categories=categories,
                           search=search_query, selected_category=category)


@app.route('/admin/books/add', methods=['GET', 'POST'])
@admin_required
def admin_add_book():
    """Add a new book to the catalog with validation."""
    if request.method == 'POST':
        if not validate_csrf():
            flash("Security token mismatch. Please try again.", "danger")
            return render_template('admin/add_book.html')

        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        isbn = request.form.get('isbn', '').strip()
        category = request.form.get('category', '').strip()
        copies_raw = request.form.get('total_copies', '1').strip()

        if not title or not author or not isbn or not category:
            flash("All fields are required.", "danger")
            return render_template('admin/add_book.html')

        try:
            total_copies = int(copies_raw)
            if total_copies < 1:
                raise ValueError()
        except ValueError:
            flash("Total copies must be a positive integer of at least 1.", "danger")
            return render_template('admin/add_book.html')

        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM books WHERE isbn = %s", (isbn,))
                if cursor.fetchone():
                    flash("A book with this ISBN already exists in the catalog.", "warning")
                    conn.close()
                    return render_template('admin/add_book.html')

                cursor.execute("""
                    INSERT INTO books (title, author, isbn, category, total_copies, available_copies)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (title, author, isbn, category, total_copies, total_copies))
            conn.close()

            flash(f"Book '{title}' added successfully!", "success")
            return redirect(url_for('admin_books'))
        except Exception as e:
            flash(f"Failed to add book: {e}", "danger")

    return render_template('admin/add_book.html')


@app.route('/admin/books/edit/<int:book_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_book(book_id):
    """Edit book details with duplicate ISBN and stock sanity checks."""
    conn = get_db_connection()
    book = None

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,))
            book = cursor.fetchone()

        if not book:
            flash("Book not found.", "danger")
            conn.close()
            return redirect(url_for('admin_books'))

        if request.method == 'POST':
            if not validate_csrf():
                flash("Security token mismatch. Please try again.", "danger")
                conn.close()
                return render_template('admin/edit_book.html', book=book)

            title = request.form.get('title', '').strip()
            author = request.form.get('author', '').strip()
            isbn = request.form.get('isbn', '').strip()
            category = request.form.get('category', '').strip()
            copies_raw = request.form.get('total_copies', '').strip()

            if not title or not author or not isbn or not category or not copies_raw:
                flash("All fields are required.", "danger")
                conn.close()
                return render_template('admin/edit_book.html', book=book)

            try:
                new_total = int(copies_raw)
                if new_total < 1:
                    raise ValueError()
            except ValueError:
                flash("Total copies must be a positive integer of at least 1.", "danger")
                conn.close()
                return render_template('admin/edit_book.html', book=book)

            borrowed_copies = book['total_copies'] - book['available_copies']
            if new_total < borrowed_copies:
                flash(f"Cannot set total copies below currently active loans ({borrowed_copies}).", "danger")
                conn.close()
                return render_template('admin/edit_book.html', book=book)

            with conn.cursor() as cursor:
                # Check duplicate ISBN on other books
                cursor.execute("SELECT id FROM books WHERE isbn = %s AND id != %s", (isbn, book_id))
                if cursor.fetchone():
                    flash("Another book already exists with this ISBN.", "warning")
                    conn.close()
                    return render_template('admin/edit_book.html', book=book)

                new_available = new_total - borrowed_copies
                cursor.execute("""
                    UPDATE books
                    SET title = %s, author = %s, isbn = %s, category = %s,
                        total_copies = %s, available_copies = %s
                    WHERE id = %s
                """, (title, author, isbn, category, new_total, new_available, book_id))

            conn.close()
            flash("Book details updated successfully!", "success")
            return redirect(url_for('admin_books'))

    except Exception as e:
        flash(f"Error editing book: {e}", "danger")

    conn.close()
    return render_template('admin/edit_book.html', book=book)


@app.route('/admin/books/delete/<int:book_id>', methods=['POST'])
@admin_required
def admin_delete_book(book_id):
    """Delete book if no active loans exist."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('admin_books'))

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) as active FROM borrow_records
                WHERE book_id = %s AND return_date IS NULL
            """, (book_id,))
            res = cursor.fetchone()
            if res['active'] > 0:
                flash("Cannot delete this book because active loans currently exist.", "warning")
                conn.close()
                return redirect(url_for('admin_books'))

            cursor.execute("DELETE FROM books WHERE id = %s", (book_id,))
        conn.close()
        flash("Book deleted successfully from catalog.", "success")
    except Exception as e:
        flash(f"Failed to delete book: {e}", "danger")

    return redirect(url_for('admin_books'))


@app.route('/admin/issued-books')
@admin_required
def admin_issued_books():
    """View all issued books across students with filter chips."""
    filter_status = request.args.get('status', 'all')
    records = []

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)

            sql = """
                SELECT br.*, b.title, b.isbn, u.full_name, u.username, u.email
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                JOIN users u ON br.user_id = u.id
                WHERE 1=1
            """
            params = []
            if filter_status == 'borrowed':
                sql += " AND br.return_date IS NULL"
            elif filter_status == 'overdue':
                sql += " AND br.return_date IS NULL AND br.due_date < %s"
                params.append(date.today())
            elif filter_status == 'returned':
                sql += " AND br.return_date IS NOT NULL"

            sql += " ORDER BY br.id DESC"
            cursor.execute(sql, tuple(params))
            records = cursor.fetchall()

            today = date.today()
            for r in records:
                if r['return_date'] is None:
                    due = r['due_date']
                    r['is_overdue'] = today > due
                    if r['is_overdue']:
                        r['fine_amount'] = calculate_fine(due)
        conn.close()
    except Exception as e:
        flash(f"Error fetching issued books: {e}", "danger")

    return render_template('admin/issued_books.html', records=records, current_filter=filter_status)


@app.route('/admin/return/<int:record_id>', methods=['POST'])
@admin_required
def admin_mark_returned(record_id):
    """Admin marks an issued book as returned and safely restocks inventory."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('admin_issued_books'))

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT br.*, b.title, b.id as b_id, b.total_copies, b.available_copies, u.full_name
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                JOIN users u ON br.user_id = u.id
                WHERE br.id = %s AND br.return_date IS NULL
            """, (record_id,))
            record = cursor.fetchone()

            if not record:
                flash("Record not found or already returned.", "danger")
                conn.close()
                return redirect(url_for('admin_issued_books'))

            today = date.today()
            fine = calculate_fine(record['due_date'], today)

            cursor.execute("""
                UPDATE borrow_records
                SET return_date = %s, fine_amount = %s, status = 'returned'
                WHERE id = %s
            """, (today, fine, record_id))

            new_avail = min(record['total_copies'], record['available_copies'] + 1)
            cursor.execute("""
                UPDATE books
                SET available_copies = %s
                WHERE id = %s
            """, (new_avail, record['b_id']))

        conn.close()
        flash(f"Book '{record['title']}' returned by {record['full_name']}. Fine: Rs {fine:.2f}.", "success")
    except Exception as e:
        flash(f"Error marking book returned: {e}", "danger")

    return redirect(url_for('admin_issued_books'))


# ==========================================================
# ERP: MEMBER MANAGEMENT ROUTES
# ==========================================================
@app.route('/admin/members')
@admin_required
def admin_members():
    """ERP Member Management: list students, active loans, overdue count, and fines."""
    search_query = request.args.get('search', '').strip()
    members = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)
            sql = """
                SELECT u.id, u.username, u.full_name, u.email, u.created_at,
                       (SELECT COUNT(*) FROM borrow_records WHERE user_id = u.id AND return_date IS NULL) as active_loans,
                       (SELECT COUNT(*) FROM borrow_records WHERE user_id = u.id AND return_date IS NULL AND due_date < %s) as overdue_loans,
                       (SELECT COALESCE(SUM(fine_amount), 0) FROM borrow_records WHERE user_id = u.id AND fine_paid = 0 AND (status = 'overdue' OR return_date IS NOT NULL)) as unpaid_fines
                FROM users u
                WHERE u.role = 'student'
            """
            params = [date.today()]
            if search_query:
                sql += " AND (u.full_name LIKE %s OR u.username LIKE %s OR u.email LIKE %s)"
                term = f"%{search_query}%"
                params.extend([term, term, term])
            sql += " ORDER BY u.id DESC"
            cursor.execute(sql, tuple(params))
            members = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error fetching members: {e}", "danger")

    return render_template('admin/members.html', members=members, search=search_query)


@app.route('/admin/members/<int:user_id>')
@admin_required
def admin_member_detail(user_id):
    """ERP Member Profile: view individual student's loans and history."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, username, full_name, email, role, created_at FROM users WHERE id = %s", (user_id,))
            member = cursor.fetchone()
            if not member:
                flash("Member not found.", "danger")
                conn.close()
                return redirect(url_for('admin_members'))

            cursor.execute("""
                SELECT br.*, b.title, b.isbn, b.category
                FROM borrow_records br
                JOIN books b ON br.book_id = b.id
                WHERE br.user_id = %s
                ORDER BY br.id DESC
            """, (user_id,))
            loans = cursor.fetchall()

            today = date.today()
            for l in loans:
                if l['return_date'] is None:
                    l['is_overdue'] = today > l['due_date']
                    if l['is_overdue']:
                        l['fine_amount'] = calculate_fine(l['due_date'])
        conn.close()
        return render_template('admin/member_detail.html', member=member, loans=loans)
    except Exception as e:
        flash(f"Error fetching member details: {e}", "danger")
        conn.close()
        return redirect(url_for('admin_members'))


# ==========================================================
# ERP: REPORTS & CSV EXPORT ROUTES
# ==========================================================
@app.route('/admin/reports')
@admin_required
def admin_reports():
    """ERP Reports: Issued books, overdue books, and fine collections."""
    report_tab = request.args.get('tab', 'issued')
    conn = get_db_connection()
    records = []
    stats = {}

    try:
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)
            today = date.today()

            cursor.execute("SELECT COUNT(*) as cnt FROM borrow_records WHERE return_date IS NULL")
            stats['issued_count'] = cursor.fetchone()['cnt']
            cursor.execute("SELECT COUNT(*) as cnt FROM borrow_records WHERE return_date IS NULL AND due_date < %s", (today,))
            stats['overdue_count'] = cursor.fetchone()['cnt']
            cursor.execute("SELECT COALESCE(SUM(fine_amount), 0) as paid FROM borrow_records WHERE fine_paid = 1")
            stats['fines_paid'] = cursor.fetchone()['paid']
            cursor.execute("SELECT COALESCE(SUM(fine_amount), 0) as unpaid FROM borrow_records WHERE fine_paid = 0 AND (status = 'overdue' OR (return_date IS NOT NULL AND fine_amount > 0))")
            stats['fines_unpaid'] = cursor.fetchone()['unpaid']

            if report_tab == 'overdue':
                cursor.execute("""
                    SELECT br.*, b.title, b.isbn, u.full_name, u.email
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.return_date IS NULL AND br.due_date < %s
                    ORDER BY br.due_date ASC
                """, (today,))
                records = cursor.fetchall()
                for r in records:
                    r['days_overdue'] = (today - r['due_date']).days
                    r['fine_amount'] = calculate_fine(r['due_date'])
            elif report_tab == 'fines':
                cursor.execute("""
                    SELECT br.*, b.title, u.full_name, u.email
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.fine_amount > 0 OR br.due_date < %s
                    ORDER BY br.id DESC
                """, (today,))
                records = cursor.fetchall()
            else:  # issued
                cursor.execute("""
                    SELECT br.*, b.title, b.isbn, u.full_name, u.email
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.return_date IS NULL
                    ORDER BY br.due_date ASC
                """)
                records = cursor.fetchall()
                for r in records:
                    r['days_remaining'] = (r['due_date'] - today).days

        conn.close()
    except Exception as e:
        flash(f"Error loading report: {e}", "danger")
        conn.close()

    return render_template('admin/reports.html', records=records, current_tab=report_tab, stats=stats)


@app.route('/admin/reports/export')
@admin_required
def admin_export_csv():
    """Exports library ERP reports into downloadable CSV format."""
    export_type = request.args.get('type', 'issued')
    conn = get_db_connection()
    output = io.StringIO()
    writer = csv.writer(output)
    today = date.today()

    try:
        with conn.cursor() as cursor:
            update_overdue_statuses(cursor)
            if export_type == 'overdue':
                filename = f"library_overdue_report_{today}.csv"
                writer.writerow(["Loan Record ID", "Order No", "Student Name", "Email", "Book Title", "ISBN", "Issue Date", "Due Date", "Days Overdue", "Fine Incurred (INR)", "Fine Status"])
                cursor.execute("""
                    SELECT br.*, b.title, b.isbn, u.full_name, u.email
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.return_date IS NULL AND br.due_date < %s
                    ORDER BY br.due_date ASC
                """, (today,))
                for r in cursor.fetchall():
                    days_overdue = (today - r['due_date']).days
                    fine = calculate_fine(r['due_date'])
                    fine_status = "PAID" if r.get('fine_paid') else "UNPAID"
                    writer.writerow([r['id'], r.get('order_no') or 'N/A', r['full_name'], r['email'], r['title'], r['isbn'], r['issue_date'], r['due_date'], days_overdue, f"{fine:.2f}", fine_status])

            elif export_type == 'fines':
                filename = f"library_fines_revenue_report_{today}.csv"
                writer.writerow(["Record ID", "Order No", "Student Name", "Email", "Book Title", "Due Date", "Return Date", "Fine Amount (INR)", "Payment Status", "Payment Reference", "Payment Date"])
                cursor.execute("""
                    SELECT br.*, b.title, u.full_name, u.email
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.fine_amount > 0 OR br.due_date < %s
                    ORDER BY br.id DESC
                """, (today,))
                for r in cursor.fetchall():
                    status = "PAID" if r.get('fine_paid') else "UNPAID"
                    writer.writerow([r['id'], r.get('order_no') or 'N/A', r['full_name'], r['email'], r['title'], r['due_date'], r['return_date'] or 'Not Returned', f"{r['fine_amount']:.2f}", status, r.get('payment_ref') or 'N/A', r.get('payment_date') or 'N/A'])

            else:  # issued
                filename = f"library_active_loans_report_{today}.csv"
                writer.writerow(["Record ID", "Order No", "Student Name", "Email", "Book Title", "ISBN", "Issue Date", "Due Date", "Status"])
                cursor.execute("""
                    SELECT br.*, b.title, b.isbn, u.full_name, u.email
                    FROM borrow_records br
                    JOIN books b ON br.book_id = b.id
                    JOIN users u ON br.user_id = u.id
                    WHERE br.return_date IS NULL
                    ORDER BY br.due_date ASC
                """)
                for r in cursor.fetchall():
                    st = "OVERDUE" if today > r['due_date'] else "ACTIVE"
                    writer.writerow([r['id'], r.get('order_no') or 'N/A', r['full_name'], r['email'], r['title'], r['isbn'], r['issue_date'], r['due_date'], st])

        conn.close()
    except Exception as e:
        flash(f"CSV export failed: {e}", "danger")
        conn.close()
        return redirect(url_for('admin_reports'))

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-type'] = 'text/csv'
    return response


# ==========================================================
# ERP: PURCHASE / SUPPLIER PROCUREMENT MODULE
# ==========================================================
@app.route('/admin/purchases')
@admin_required
def admin_purchases():
    """ERP Supplier Procurement: Manage purchase orders and automated stock receipts."""
    status_filter = request.args.get('status', 'all')
    orders = []
    books = []

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, title, isbn, total_copies, available_copies FROM books ORDER BY title ASC")
            books = cursor.fetchall()

            sql = """
                SELECT po.*, b.title as book_title, b.isbn, b.available_copies, b.total_copies
                FROM purchase_orders po
                JOIN books b ON po.book_id = b.id
                WHERE 1=1
            """
            params = []
            if status_filter in ('Pending', 'Received', 'Cancelled'):
                sql += " AND po.status = %s"
                params.append(status_filter)
            sql += " ORDER BY po.id DESC"
            cursor.execute(sql, tuple(params))
            orders = cursor.fetchall()
        conn.close()
    except Exception as e:
        flash(f"Error loading purchase orders: {e}", "danger")

    return render_template('admin/purchases.html', orders=orders, books=books, current_status=status_filter)


@app.route('/admin/purchases/new', methods=['POST'])
@admin_required
def admin_new_purchase_order():
    """Record a purchase order for new textbook copies from a supplier."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('admin_purchases'))

    supplier_name = request.form.get('supplier_name', '').strip()
    book_id = request.form.get('book_id')
    copies_raw = request.form.get('copies_ordered', '1').strip()
    unit_cost_raw = request.form.get('unit_cost', '0').strip()
    notes = request.form.get('notes', '').strip()

    if not supplier_name or not book_id:
        flash("Supplier name and book are required.", "danger")
        return redirect(url_for('admin_purchases'))

    try:
        copies = int(copies_raw)
        unit_cost = float(unit_cost_raw)
        if copies < 1 or unit_cost < 0:
            raise ValueError()
    except ValueError:
        flash("Copies must be >= 1 and unit cost must be a non-negative number.", "danger")
        return redirect(url_for('admin_purchases'))

    total_cost = round(copies * unit_cost, 2)
    po_number = f"PO-{date.today().year}-{secrets.randbelow(9000) + 1000}"
    order_date = date.today()

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO purchase_orders (po_number, supplier_name, book_id, copies_ordered, unit_cost, total_cost, status, order_date, notes)
                VALUES (%s, %s, %s, %s, %s, %s, 'Pending', %s, %s)
            """, (po_number, supplier_name, book_id, copies, unit_cost, total_cost, order_date, notes))
        conn.close()
        flash(f"Purchase Order #{po_number} placed with {supplier_name} for {copies} copies.", "success")
    except Exception as e:
        flash(f"Error creating purchase order: {e}", "danger")

    return redirect(url_for('admin_purchases'))


@app.route('/admin/purchases/receive/<int:po_id>', methods=['POST'])
@admin_required
def admin_receive_purchase_order(po_id):
    """Marks purchase order as 'Received' and automatically increases textbook inventory!"""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('admin_purchases'))

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT po.*, b.title
                FROM purchase_orders po
                JOIN books b ON po.book_id = b.id
                WHERE po.id = %s
            """, (po_id,))
            po = cursor.fetchone()

            if not po:
                flash("Purchase order not found.", "danger")
                conn.close()
                return redirect(url_for('admin_purchases'))

            if po['status'] == 'Received':
                flash("This purchase order was already marked as received.", "info")
                conn.close()
                return redirect(url_for('admin_purchases'))

            # Mark PO as Received
            today = date.today()
            cursor.execute("""
                UPDATE purchase_orders
                SET status = 'Received', received_date = %s
                WHERE id = %s
            """, (today, po_id))

            # Automatically increment book inventory
            cursor.execute("""
                UPDATE books
                SET total_copies = total_copies + %s,
                    available_copies = available_copies + %s
                WHERE id = %s
            """, (po['copies_ordered'], po['copies_ordered'], po['book_id']))
        conn.close()

        flash(f"Inventory Restocked! Received {po['copies_ordered']} copies of '{po['title']}'. Book stock updated automatically.", "success")
    except Exception as e:
        flash(f"Error receiving purchase order: {e}", "danger")

    return redirect(url_for('admin_purchases'))


@app.route('/admin/purchases/cancel/<int:po_id>', methods=['POST'])
@admin_required
def admin_cancel_purchase_order(po_id):
    """Cancel a pending purchase order."""
    if not validate_csrf():
        flash("Security token mismatch. Please try again.", "danger")
        return redirect(url_for('admin_purchases'))

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("UPDATE purchase_orders SET status = 'Cancelled' WHERE id = %s AND status = 'Pending'", (po_id,))
        conn.close()
        flash("Purchase order cancelled.", "info")
    except Exception as e:
        flash(f"Error cancelling purchase order: {e}", "danger")

    return redirect(url_for('admin_purchases'))


# ==========================================================
# ERROR HANDLERS (404 and 500)
# ==========================================================
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html', error=str(e)), 500


# Call DB seed initialization if DB is available
init_db_seeds()

# Export WSGI callable for Vercel Serverless Function
handler = app

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    print(f"[*] Starting Library System on port {port}...")
    print(f"[*] Database Engine Active: {ACTIVE_DB_ENGINE.upper()}")
    if ACTIVE_DB_ENGINE == "sqlite":
        print(f"[*] (Local SQLite mode active - no MySQL installation required for local testing!)")
    else:
        print(f"[*] (Connected to MySQL at {DB_HOST}:{DB_PORT})")
    app.run(host='0.0.0.0', port=port, debug=debug)
