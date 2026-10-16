import os
import sqlite3
import time
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
app.config['SECRET_KEY'] = 'mychatapp_enterprise_secret_key_2026'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Uploads Folder
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DB_PATH = os.path.join(BASE_DIR, 'chat_history.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Unlimited Users Table (Self Registration)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            mobile TEXT PRIMARY KEY,
            pin TEXT NOT NULL,
            name TEXT,
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. Chats Table (Group & Direct 1-to-1)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            target TEXT,
            text TEXT,
            file_url TEXT,
            file_type TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 3. Status Updates (24-Hour Expiry)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            content TEXT,
            media_type TEXT,
            created_at REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def home():
    return render_template('index.html')

# --- AUTHENTICATION (Self Registration + Login) ---
@app.route('/auth', methods=['POST'])
def auth():
    data = request.get_json(silent=True) or {}
    action = data.get('action') # 'signup' ya 'login'
    mobile = str(data.get('mobile', '')).strip()
    pin = str(data.get('pin', '')).strip()

    if not mobile or not pin:
        return jsonify({"status": "error", "message": "Mobile aur PIN daalna anivarya hai!"}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if action == 'signup':
        cursor.execute('SELECT mobile FROM users WHERE mobile = ?', (mobile,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"status": "error", "message": "Ye number pehle se registered hai! Log in karein."}), 400
        cursor.execute('INSERT INTO users (mobile, pin) VALUES (?, ?)', (mobile, pin))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "user": mobile, "message": "Account ban gaya!"})

    elif action == 'login':
        cursor.execute('SELECT pin FROM users WHERE mobile = ?', (mobile,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] == pin:
            return jsonify({"status": "success", "user": mobile})
        return jsonify({"status": "error", "message": "Galat Mobile Number ya PIN!"}), 401

    conn.close()
    return jsonify({"status": "error", "message": "Invalid request"}), 400

# Registered users list (Contact Directory)
@app.route('/contacts', methods=['GET'])
def get_contacts():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT mobile FROM users ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return jsonify([r[0] for r in rows])

# --- MESSAGING SYSTEM ---
@app.route('/messages', methods=['GET'])
def get_messages():
    target = request.args.get('target', 'All')
    user = request.args.get('user', '')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if target == 'All':
        cursor.execute('SELECT id, sender, text, file_url, file_type, is_deleted FROM chats WHERE target = "All" ORDER BY id ASC')
    else:
        cursor.execute('''
            SELECT id, sender, text, file_url, file_type, is_deleted FROM chats 
            WHERE (sender = ? AND target = ?) OR (sender = ? AND target = ?) 
            ORDER BY id ASC
        ''', (user, target, target, user))
        
    rows = cursor.fetchall()
    conn.close()

    msgs = []
    for r in rows:
        msgs.append({
            "id": r[0],
            "sender": r[1],
            "text": r[2],
            "file_url": r[3],
            "file_type": r[4],
            "is_deleted": r[5]
        })
    return jsonify(msgs)

@app.route('/send', methods=['POST'])
def send_message():
    sender = request.form.get('sender', 'Unknown')
    target = request.form.get('target', 'All')
    text = request.form.get('text', '').strip()
    file = request.files.get('file')

    file_url = None
    file_type = None

    if file and file.filename != '':
        orig_name = secure_filename(file.filename)
        ext = orig_name.rsplit('.', 1)[-1].lower() if '.' in orig_name else ''
        if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            file_type = 'image'
        elif ext in ['mp4', 'mkv', 'webm', '3gp', 'mov']:
            file_type = 'video'
        elif ext in ['mp3', 'wav', 'ogg', 'm4a', 'aac']:
            file_type = 'audio'
        else:
            file_type = 'doc'

        unique_name = f"{os.urandom(6).hex()}_{orig_name}"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(save_path)
        file_url = f"/uploads/{unique_name}"

    if text or file_url:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO chats (sender, target, text, file_url, file_type, is_deleted) VALUES (?, ?, ?, ?, ?, 0)',
            (sender, target, text, file_url, file_type)
        )
        msg_id = cursor.lastrowid
        conn.commit()
        conn.close()

        socketio.emit('new_message', {
            "id": msg_id,
            "sender": sender,
            "target": target,
            "text": text,
            "file_url": file_url,
            "file_type": file_type,
            "is_deleted": 0
        })
        return jsonify({"status": "sent"})

    return jsonify({"status": "empty"}), 400

@app.route('/delete_for_everyone/<int:msg_id>', methods=['POST'])
def delete_for_everyone(msg_id):
    data = request.get_json(silent=True) or {}
    requester = str(data.get('sender', '')).strip()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT sender, file_url, target FROM chats WHERE id = ?', (msg_id,))
    row = cursor.fetchone()

    if not row or row[0] != requester:
        conn.close()
        return jsonify({"status": "error", "message": "Unauthorized action"}), 403

    file_url, target = row[1], row[2]
    if file_url:
        path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(file_url))
        if os.path.exists(path):
            try: os.remove(path)
            except: pass

    cursor.execute('UPDATE chats SET text = "", file_url = NULL, file_type = NULL, is_deleted = 1 WHERE id = ?', (msg_id,))
    conn.commit()
    conn.close()

    socketio.emit('message_deleted', {"id": msg_id, "target": target})
    return jsonify({"status": "success"})

# --- 24-HOUR EXPIRING STATUS ---
@app.route('/status', methods=['GET', 'POST'])
def handle_status():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if request.method == 'POST':
        sender = request.form.get('sender')
        text = request.form.get('text', '')
        file = request.files.get('file')
        media_url = text
        media_type = 'text'

        if file:
            filename = f"status_{os.urandom(4).hex()}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            media_url = f"/uploads/{filename}"
            media_type = 'image'

        cursor.execute('INSERT INTO status (sender, content, media_type, created_at) VALUES (?, ?, ?, ?)',
                       (sender, media_url, media_type, time.time()))
        conn.commit()
        conn.close()
        return jsonify({"status": "posted"})

    # Auto remove older than 24 hours
    cutoff = time.time() - 86400
    cursor.execute('DELETE FROM status WHERE created_at < ?', (cutoff,))
    conn.commit()
    cursor.execute('SELECT sender, content, media_type, created_at FROM status ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"sender": r[0], "content": r[1], "media_type": r[2]} for r in rows])

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# WebRTC Calling Signal Handlers
@socketio.on('join_call_channel')
def on_join(data):
    join_room(data['room'])

@socketio.on('call_signal')
def on_signal(data):
    emit('call_signal', data, room=data['room'], include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
