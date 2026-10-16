import os
import sqlite3
import random
import time
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
app.config['SECRET_KEY'] = 'enterprise_secure_vault_2026'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DB_PATH = os.path.join(BASE_DIR, 'chat_history.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Users Table (Profile Pic, Permanent PIN)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            mobile TEXT PRIMARY KEY,
            pin TEXT NOT NULL,
            name TEXT DEFAULT 'User',
            avatar TEXT DEFAULT '',
            wallpaper TEXT DEFAULT '#eae6df',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. OTP Temporary Storage
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS otps (
            mobile TEXT PRIMARY KEY,
            otp TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
    ''')

    # 3. Confidential Chats Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            target TEXT NOT NULL,
            text TEXT,
            file_url TEXT,
            file_type TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 4. Contacts / Recent Chats Directory
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_contacts (
            owner TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            PRIMARY KEY (owner, contact_number)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def home():
    return render_template('index.html')

# --- 1. OTP ENGINE ---
@app.route('/send_otp', methods=['POST'])
def send_otp():
    data = request.get_json(silent=True) or {}
    mobile = str(data.get('mobile', '')).strip()

    if not mobile or len(mobile) < 10:
        return jsonify({"status": "error", "message": "Kripya 10-digit sahi mobile number dalein!"}), 400

    otp = str(random.randint(1000, 9999))
    expires = time.time() + 300 # 5 Minute validity

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO otps (mobile, otp, expires_at) VALUES (?, ?, ?)', (mobile, otp, expires))
    conn.commit()
    conn.close()

    # Yahan simulation alert ke liye OTP return ho raha hai
    return jsonify({"status": "success", "message": "OTP sent!", "otp_preview": otp})

# --- 2. AUTHENTICATION (Register with OTP & Permanent Login) ---
@app.route('/auth', methods=['POST'])
def auth():
    data = request.get_json(silent=True) or {}
    action = data.get('action') # 'signup' ya 'login'
    mobile = str(data.get('mobile', '')).strip()
    pin = str(data.get('pin', '')).strip()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if action == 'signup':
        otp = str(data.get('otp', '')).strip()
        cursor.execute('SELECT otp, expires_at FROM otps WHERE mobile = ?', (mobile,))
        row = cursor.fetchone()
        
        if not row or row[0] != otp or time.time() > row[1]:
            conn.close()
            return jsonify({"status": "error", "message": "Galat ya Expired OTP!"}), 400

        cursor.execute('SELECT mobile FROM users WHERE mobile = ?', (mobile,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"status": "error", "message": "Ye number pehle se registered hai! Log In karein."}), 400

        cursor.execute('INSERT INTO users (mobile, pin) VALUES (?, ?)', (mobile, pin))
        cursor.execute('DELETE FROM otps WHERE mobile = ?', (mobile,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "user": mobile, "message": "Account successfully ban gaya!"})

    elif action == 'login':
        cursor.execute('SELECT pin, avatar, wallpaper FROM users WHERE mobile = ?', (mobile,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] == pin:
            return jsonify({"status": "success", "user": mobile, "avatar": row[1], "wallpaper": row[2]})
        return jsonify({"status": "error", "message": "Galat Mobile Number ya Password/PIN!"}), 401

    conn.close()
    return jsonify({"status": "error", "message": "Invalid Action"}), 400

# --- 3. PROFILE & DP UPDATE ---
@app.route('/update_profile', methods=['POST'])
def update_profile():
    mobile = request.form.get('mobile')
    wallpaper = request.form.get('wallpaper')
    dp = request.files.get('dp')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if dp and dp.filename != '':
        fname = f"dp_{mobile}_{int(time.time())}_{secure_filename(dp.filename)}"
        dp.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
        dp_url = f"/uploads/{fname}"
        cursor.execute('UPDATE users SET avatar = ? WHERE mobile = ?', (dp_url, mobile))

    if wallpaper:
        cursor.execute('UPDATE users SET wallpaper = ? WHERE mobile = ?', (wallpaper, mobile))

    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# --- 4. CONTACTS / DIRECTORY ---
@app.route('/add_contact', methods=['POST'])
def add_contact():
    data = request.get_json(silent=True) or {}
    owner = data.get('owner')
    contact = data.get('contact').strip()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT mobile, avatar FROM users WHERE mobile = ?', (contact,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return jsonify({"status": "error", "message": "Ye number MyChatApp par maujood nahi hai!"}), 404

    cursor.execute('INSERT OR IGNORE INTO user_contacts (owner, contact_number) VALUES (?, ?)', (owner, contact))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "contact": contact, "avatar": user[1]})

@app.route('/my_contacts', methods=['GET'])
def get_my_contacts():
    owner = request.args.get('user', '')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.mobile, u.avatar 
        FROM user_contacts c 
        JOIN users u ON c.contact_number = u.mobile 
        WHERE c.owner = ?
    ''', (owner,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"mobile": r[0], "avatar": r[1]} for r in rows])

# --- 5. STRICT CONFIDENTIAL CHATS (No Third-Party Read) ---
@app.route('/messages', methods=['GET'])
def get_messages():
    target = request.args.get('target', 'All')
    user = request.args.get('user', '')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if target == 'All':
        cursor.execute('SELECT id, sender, text, file_url, file_type, is_deleted FROM chats WHERE target = "All" ORDER BY id ASC')
    else:
        # Strictly unhi do users ke beech ka data uthega
        cursor.execute('''
            SELECT id, sender, text, file_url, file_type, is_deleted FROM chats 
            WHERE (sender = ? AND target = ?) OR (sender = ? AND target = ?) 
            ORDER BY id ASC
        ''', (user, target, target, user))

    rows = cursor.fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "sender": r[1], "text": r[2], "file_url": r[3], "file_type": r[4], "is_deleted": r[5]
    } for r in rows])

@app.route('/send', methods=['POST'])
def send_message():
    sender = request.form.get('sender')
    target = request.form.get('target')
    text = request.form.get('text', '').strip()
    file = request.files.get('file')

    file_url = None
    file_type = None

    if file and file.filename != '':
        orig = secure_filename(file.filename)
        ext = orig.rsplit('.', 1)[-1].lower() if '.' in orig else ''
        file_type = 'image' if ext in ['jpg', 'png', 'jpeg', 'webp'] else ('video' if ext in ['mp4', 'mov'] else 'audio')
        uname = f"{os.urandom(6).hex()}_{orig}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], uname))
        file_url = f"/uploads/{uname}"

    if text or file_url:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO chats (sender, target, text, file_url, file_type) VALUES (?, ?, ?, ?, ?)',
            (sender, target, text, file_url, file_type)
        )
        msg_id = cursor.lastrowid
        
        # Dono users ke contact list me auto-add
        if target != 'All':
            cursor.execute('INSERT OR IGNORE INTO user_contacts (owner, contact_number) VALUES (?, ?)', (sender, target))
            cursor.execute('INSERT OR IGNORE INTO user_contacts (owner, contact_number) VALUES (?, ?)', (target, sender))
            
        conn.commit()
        conn.close()

        payload = {
            "id": msg_id, "sender": sender, "target": target, "text": text,
            "file_url": file_url, "file_type": file_type, "is_deleted": 0
        }
        socketio.emit('new_message', payload)
        return jsonify({"status": "sent"})

    return jsonify({"status": "empty"}), 400

@app.route('/delete_for_everyone/<int:msg_id>', methods=['POST'])
def delete_msg(msg_id):
    data = request.get_json(silent=True) or {}
    requester = data.get('sender')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT sender, file_url, target FROM chats WHERE id = ?', (msg_id,))
    row = cursor.fetchone()

    if row and row[0] == requester:
        if row[1]:
            try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(row[1])))
            except: pass
        cursor.execute('UPDATE chats SET text="", file_url=NULL, file_type=NULL, is_deleted=1 WHERE id=?', (msg_id,))
        conn.commit()
        socketio.emit('message_deleted', {"id": msg_id, "target": row[2]})
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/uploads/<path:filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
