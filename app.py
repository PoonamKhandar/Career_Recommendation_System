from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import openai
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
import sqlite3
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import hashlib
import secrets
from functools import wraps

load_dotenv()

app = Flask(__name__)
CORS(app, origins=[
    "https://career-recommendations-system.onrender.com",  # Render deployment (FIX 1)
    "https://lambent-croquembouche-a5bdcb.netlify.app",    # Old Netlify frontend
    "http://localhost:5000",
    "http://127.0.0.1:5000",
])

# ── Serve HTML Pages ─────────────────────────

@app.route('/')
def serve_root():
    return send_from_directory('.', 'Auth.html')  # Serve login page directly

@app.route('/home')
@app.route('/index.html')
def serve_home():
    return send_from_directory('.', 'index.html')

@app.route('/auth')
@app.route('/Auth.html')
def serve_auth():
    return send_from_directory('.', 'Auth.html')

@app.route('/history')
@app.route('/History.html')
@app.route('/history.html')
def serve_history():
    return send_from_directory('.', 'History.html')

@app.route('/admin')
@app.route('/admin.html')
def serve_admin():
    return send_from_directory('.', 'admin.html')

# ── Config ───────────────────────────────────

openai.api_key = os.getenv('OPENAI_API_KEY')

DATABASE = 'career_guidance.db'
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
EMAIL_USER = os.getenv('EMAIL_USER', '')
EMAIL_PASS = os.getenv('EMAIL_PASS', '')
OTP_EXPIRY_MINUTES = 10

# ── Admin Config ─────────────────────────────
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin@1234')
admin_sessions = {}   # token -> expiry

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Admin-Token')
        if not token or token not in admin_sessions:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        if datetime.utcnow() > admin_sessions[token]:
            del admin_sessions[token]
            return jsonify({"success": False, "error": "Session expired"}), 401
        return f(*args, **kwargs)
    return decorated


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, education TEXT NOT NULL,
        percentage REAL NOT NULL, skills TEXT NOT NULL,
        interests TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, career TEXT NOT NULL,
        explanation TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS auth_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL, verified INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS otp_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL, otp TEXT NOT NULL,
        purpose TEXT NOT NULL, expires_at TIMESTAMP NOT NULL,
        used INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()
    print("Database initialized successfully!")


try:
    init_db()
except Exception as e:
    print(f"Warning: DB init error: {e}")


def generate_otp():
    return ''.join(random.choices(string.digits, k=6))


def send_email(to_email, subject, html_body):
    if not EMAIL_USER or not EMAIL_PASS:
        print(f"[EMAIL SIMULATION] To: {to_email} | Subject: {subject}")
        return True, "simulated"
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = EMAIL_USER
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, to_email, msg.as_string())
        return True, "sent"
    except Exception as e:
        print(f"Email error: {str(e)}")
        return False, str(e)


def save_otp(email, otp, purpose):
    conn = get_db_connection()
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE otp_tokens SET used=1 WHERE email=? AND purpose=? AND used=0", (email, purpose))
    conn.execute("INSERT INTO otp_tokens (email,otp,purpose,expires_at) VALUES (?,?,?,?)", (email, otp, purpose, expires_at))
    conn.commit()
    conn.close()


def verify_otp_token(email, otp, purpose):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM otp_tokens WHERE email=? AND purpose=? AND used=0 ORDER BY created_at DESC LIMIT 1",
        (email, purpose)
    ).fetchone()
    conn.close()

    if not row:
        return False, "No OTP found. Please request a new one."
    if row['otp'] != otp:
        return False, "Invalid OTP. Please try again."
    expires_at = datetime.strptime(row['expires_at'], '%Y-%m-%d %H:%M:%S')
    if datetime.utcnow() > expires_at:
        return False, "OTP has expired. Please request a new one."
    return True, row['id']


def mark_otp_used(otp_id):
    conn = get_db_connection()
    conn.execute("UPDATE otp_tokens SET used=1 WHERE id=?", (otp_id,))
    conn.commit()
    conn.close()


def otp_email_html(otp, purpose, name="User"):
    purpose_text = "Email Verification" if purpose == "registration" else "Password Reset"
    action_text = "complete your registration" if purpose == "registration" else "reset your password"
    return f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#0a0e1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:480px;margin:40px auto;background:rgba(15,23,42,0.98);border-radius:20px;padding:40px;border:1px solid rgba(59,130,246,0.4);">
  <div style="text-align:center;margin-bottom:24px;"><div style="width:70px;height:70px;background:linear-gradient(135deg,#2563eb,#7c3aed);border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:2rem;">🎓</div></div>
  <h2 style="color:#f1f5f9;text-align:center;margin-bottom:8px;">{purpose_text}</h2>
  <p style="color:#94a3b8;text-align:center;margin-bottom:28px;">Hi {name}, use the code below to {action_text}.</p>
  <div style="background:rgba(37,99,235,0.15);border:2px solid rgba(37,99,235,0.4);border-radius:16px;padding:28px;text-align:center;margin-bottom:24px;">
    <div style="letter-spacing:14px;font-size:2.6rem;font-weight:700;color:#60a5fa;">{otp}</div>
  </div>
  <p style="color:#64748b;text-align:center;font-size:0.85rem;">Expires in <strong style="color:#94a3b8;">{OTP_EXPIRY_MINUTES} minutes</strong>. If you didn't request this, ignore this email.</p>
  <div style="border-top:1px solid rgba(100,116,139,0.2);margin-top:24px;padding-top:16px;text-align:center;">
    <p style="color:#475569;font-size:0.8rem;">AI Career Guide — Your personalized career advisor</p>
  </div>
</div></body></html>"""


# ── Auth Routes ──────────────────────────────

@app.route('/auth/send-otp', methods=['POST'])
def send_otp():
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        purpose = data.get('purpose', '')
        name = data.get('name', 'User').strip()

        if not email or not purpose:
            return jsonify({"success": False, "error": "Email and purpose are required"}), 400
        if purpose not in ('registration', 'password_reset'):
            return jsonify({"success": False, "error": "Invalid purpose"}), 400

        conn = get_db_connection()
        existing = conn.execute("SELECT * FROM auth_users WHERE email=?", (email,)).fetchone()
        conn.close()

        if purpose == 'registration' and existing:
            return jsonify({"success": False, "error": "An account with this email already exists"}), 400
        if purpose == 'password_reset' and not existing:
            return jsonify({"success": True, "message": "If an account exists, an OTP has been sent"})

        display_name = existing['name'] if (purpose == 'password_reset' and existing) else name
        otp = generate_otp()
        save_otp(email, otp, purpose)

        sent, msg = send_email(email, "Your AI Career Guide Verification Code", otp_email_html(otp, purpose, display_name))
        if not sent:
            return jsonify({"success": False, "error": f"Failed to send email: {msg}"}), 500

        resp = {"success": True, "message": f"OTP sent to {email}"}
        if not EMAIL_USER:
            resp["dev_otp"] = otp
        return jsonify(resp)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/auth/verify-registration', methods=['POST'])
def verify_registration():
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        otp = data.get('otp', '').strip()

        if not all([name, email, password, otp]):
            return jsonify({"success": False, "error": "All fields are required"}), 400
        if len(password) < 8:
            return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400

        valid, result = verify_otp_token(email, otp, 'registration')
        if not valid:
            return jsonify({"success": False, "error": result}), 400

        conn = get_db_connection()
        if conn.execute("SELECT id FROM auth_users WHERE email=?", (email,)).fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Account already exists"}), 400

        conn.execute("INSERT INTO auth_users (name,email,password,verified) VALUES (?,?,?,1)",
                     (name, email, hash_password(password)))
        conn.commit()
        user = conn.execute("SELECT * FROM auth_users WHERE email=?", (email,)).fetchone()
        conn.close()
        mark_otp_used(result)

        return jsonify({
            "success": True, "message": "Account created successfully!",
            "user": {"id": user['id'], "name": user['name'], "email": user['email']}
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM auth_users WHERE email=? AND password=?",
                            (email, hash_password(password))).fetchone()
        conn.close()

        if not user:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401
        if not user['verified']:
            return jsonify({"success": False, "error": "Please verify your email before logging in"}), 403

        return jsonify({
            "success": True, "message": "Login successful!",
            "user": {"id": user['id'], "name": user['name'], "email": user['email']}
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/auth/forgot-password', methods=['POST'])
def forgot_password():
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM auth_users WHERE email=?", (email,)).fetchone()
        conn.close()

        if not user:
            return jsonify({"success": True, "message": "If an account exists, an OTP has been sent"})

        otp = generate_otp()
        save_otp(email, otp, 'password_reset')
        sent, msg = send_email(email, "Reset Your AI Career Guide Password", otp_email_html(otp, 'password_reset', user['name']))
        if not sent:
            return jsonify({"success": False, "error": f"Failed to send email: {msg}"}), 500

        resp = {"success": True, "message": "Password reset OTP sent to your email"}
        if not EMAIL_USER:
            resp["dev_otp"] = otp
        return jsonify(resp)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/auth/reset-password', methods=['POST'])
def reset_password():
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        otp = data.get('otp', '').strip()
        new_password = data.get('new_password', '')

        if not all([email, otp, new_password]):
            return jsonify({"success": False, "error": "All fields are required"}), 400
        if len(new_password) < 8:
            return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400

        valid, result = verify_otp_token(email, otp, 'password_reset')
        if not valid:
            return jsonify({"success": False, "error": result}), 400

        conn = get_db_connection()
        conn.execute("UPDATE auth_users SET password=? WHERE email=?", (hash_password(new_password), email))
        conn.commit()
        conn.close()
        mark_otp_used(result)

        return jsonify({"success": True, "message": "Password reset successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Career Guidance Routes ────────────────────

@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({"message": "AI Career Guide Backend is running!", "status": "success", "database": "connected"})


@app.route('/get-career-guidance', methods=['POST'])
def get_career_guidance():
    try:
        data = request.get_json()
        required_fields = ['name', 'education', 'percentage', 'skills', 'interests']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        name = data['name']; education = data['education']
        percentage = data['percentage']; skills = data['skills']; interests = data['interests']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (name,education,percentage,skills,interests) VALUES (?,?,?,?,?)",
                       (name, education, percentage, json.dumps(skills), json.dumps(interests)))
        user_id = cursor.lastrowid
        conn.commit()

        prompt = f"""As an expert career counselor, analyze the following student profile and provide 3-4 personalized career recommendations with detailed explanations.
Student Profile: Name: {name}, Education: {education}, Performance: {percentage}%, Skills: {', '.join(skills)}, Interests: {', '.join(interests)}
Respond ONLY in this JSON format:
{{"career_recommendations": [{{"career": "Career Title", "explanation": "Detailed explanation..."}}]}}
Make each explanation 3-4 sentences, personalized."""

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert career counselor. Provide thoughtful, personalized career guidance."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500, temperature=0.7
        )
        ai_response = response.choices[0].message.content.strip()

        try:
            career_data = json.loads(ai_response)
        except json.JSONDecodeError:
            career_data = {"career_recommendations": [{"career": "Personalized Career Path", "explanation": ai_response}]}

        for rec in career_data.get('career_recommendations', []):
            cursor.execute("INSERT INTO recommendations (user_id,career,explanation) VALUES (?,?,?)",
                           (user_id, rec['career'], rec['explanation']))
        conn.commit()
        conn.close()

        career_data.update({"success": True, "user_id": user_id, "message": "Data saved successfully!"})
        return jsonify(career_data)
    except openai.error.OpenAIError as e:
        return jsonify({"success": False, "error": f"OpenAI API error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": f"Internal server error: {str(e)}"}), 500


@app.route('/get-user-history/<int:user_id>', methods=['GET'])
def get_user_history(user_id):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        user = cursor.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
        recs = cursor.execute('SELECT * FROM recommendations WHERE user_id=? ORDER BY created_at DESC', (user_id,)).fetchall()
        conn.close()
        return jsonify({"success": True,
            "user": {"id": user['id'], "name": user['name'], "education": user['education'],
                     "percentage": user['percentage'], "skills": json.loads(user['skills']),
                     "interests": json.loads(user['interests']), "created_at": user['created_at']},
            "recommendations": [{"career": r['career'], "explanation": r['explanation'], "created_at": r['created_at']} for r in recs]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/get-all-users', methods=['GET'])
def get_all_users():
    try:
        conn = get_db_connection()
        users = conn.execute('SELECT id,name,education,percentage,created_at FROM users ORDER BY created_at DESC').fetchall()
        conn.close()
        return jsonify({"success": True, "total_users": len(users),
            "users": [{"id": u['id'], "name": u['name'], "education": u['education'], "percentage": u['percentage'], "created_at": u['created_at']} for u in users]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/search-users', methods=['GET'])
def search_users():
    try:
        search_term = request.args.get('name', '')
        conn = get_db_connection()
        users = conn.execute('SELECT * FROM users WHERE name LIKE ? ORDER BY created_at DESC', (f'%{search_term}%',)).fetchall()
        conn.close()
        return jsonify({"success": True, "results": len(users),
            "users": [{"id": u['id'], "name": u['name'], "education": u['education'], "percentage": u['percentage'],
                       "skills": json.loads(u['skills']), "interests": json.loads(u['interests']), "created_at": u['created_at']} for u in users]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/delete-user/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM recommendations WHERE user_id=?', (user_id,))
        conn.execute('DELETE FROM users WHERE id=?', (user_id,))
        conn.commit(); conn.close()
        return jsonify({"success": True, "message": "User deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "service": "AI Career Guide Backend", "database": "connected"})


@app.route('/stats', methods=['GET'])
def get_stats():
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        total_users = cursor.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
        total_recs = cursor.execute('SELECT COUNT(*) as count FROM recommendations').fetchone()['count']
        top = cursor.execute('SELECT career,COUNT(*) as count FROM recommendations GROUP BY career ORDER BY count DESC LIMIT 5').fetchall()
        conn.close()
        return jsonify({"success": True, "stats": {"total_users": total_users, "total_recommendations": total_recs,
            "top_careers": [{"career": c['career'], "count": c['count']} for c in top]}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Admin Routes ─────────────────────────────

@app.route('/admin/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            token = secrets.token_hex(32)
            admin_sessions[token] = datetime.utcnow() + timedelta(hours=8)
            return jsonify({"success": True, "token": token, "username": username})
        return jsonify({"success": False, "error": "Invalid username or password"}), 401
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/stats', methods=['GET'])
@require_admin
def admin_stats():
    try:
        conn = get_db_connection()
        total_queries = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
        total_recs = conn.execute('SELECT COUNT(*) as c FROM recommendations').fetchone()['c']
        registered_users = conn.execute('SELECT COUNT(*) as c FROM auth_users').fetchone()['c']
        verified_users = conn.execute('SELECT COUNT(*) as c FROM auth_users WHERE verified=1').fetchone()['c']
        top_careers = conn.execute(
            'SELECT career, COUNT(*) as count FROM recommendations GROUP BY career ORDER BY count DESC LIMIT 8'
        ).fetchall()
        conn.close()
        return jsonify({
            "success": True,
            "total_queries": total_queries,
            "total_recommendations": total_recs,
            "registered_users": registered_users,
            "verified_users": verified_users,
            "top_careers": [{"career": r['career'], "count": r['count']} for r in top_careers]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/recent-activity', methods=['GET'])
@require_admin
def admin_recent_activity():
    try:
        conn = get_db_connection()
        recent_users = conn.execute(
            'SELECT name, created_at FROM users ORDER BY created_at DESC LIMIT 4'
        ).fetchall()
        recent_auth = conn.execute(
            'SELECT name, email, verified, created_at FROM auth_users ORDER BY created_at DESC LIMIT 4'
        ).fetchall()
        conn.close()

        activities = []
        for u in recent_users:
            activities.append({
                "icon": "🧠",
                "message": f"{u['name']} requested career guidance",
                "time": u['created_at']
            })
        for u in recent_auth:
            activities.append({
                "icon": "✅" if u['verified'] else "📧",
                "message": f"{u['name']} {'verified account' if u['verified'] else 'registered'} ({u['email']})",
                "time": u['created_at']
            })

        activities.sort(key=lambda x: x['time'], reverse=True)
        return jsonify({"success": True, "activities": activities[:8]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/auth-users', methods=['GET'])
@require_admin
def admin_auth_users():
    try:
        conn = get_db_connection()
        users = conn.execute(
            'SELECT id, name, email, verified, created_at FROM auth_users ORDER BY created_at DESC'
        ).fetchall()
        conn.close()
        return jsonify({
            "success": True,
            "users": [{"id": u['id'], "name": u['name'], "email": u['email'],
                       "verified": bool(u['verified']), "created_at": u['created_at']} for u in users]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/delete-auth-user/<int:user_id>', methods=['DELETE'])
@require_admin
def admin_delete_auth_user(user_id):
    try:
        conn = get_db_connection()
        user = conn.execute('SELECT email FROM auth_users WHERE id=?', (user_id,)).fetchone()
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "User not found"}), 404
        conn.execute('DELETE FROM otp_tokens WHERE email=?', (user['email'],))
        conn.execute('DELETE FROM auth_users WHERE id=?', (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Auth user deleted"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/config', methods=['GET'])
@require_admin
def admin_config():
    try:
        openai_key = os.getenv('OPENAI_API_KEY', '')
        masked_key = (openai_key[:8] + '••••••••' + openai_key[-4:]) if len(openai_key) > 12 else ('Set' if openai_key else 'Not set')
        return jsonify({
            "success": True,
            "admin_username": ADMIN_USERNAME,
            "config": {
                "openai_key": masked_key,
                "email_user": EMAIL_USER or None,
                "smtp_server": SMTP_SERVER,
                "smtp_port": SMTP_PORT,
                "otp_expiry": OTP_EXPIRY_MINUTES
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Run Server ───────────────────────────────

if __name__ == '__main__':
    if not os.getenv('OPENAI_API_KEY'):
        print("Warning: OPENAI_API_KEY not set.")
    if not os.getenv('EMAIL_USER'):
        print("Warning: EMAIL_USER not set — OTPs will be shown in console (dev mode).")
    print("\n=== AI Career Guide Backend ===")
    print("Starting server on http://127.0.0.1:5000")
    print("Open in browser: http://127.0.0.1:5000")
    print("================================\n")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
