import os
import json
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import wraps

import psycopg2
import psycopg2.extras

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    g
)

from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from googlesearch import search


DATABASE_URL = os.getenv("DATABASE_URL", "")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_REFRESH_EACH_REQUEST"] = True


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, sslmode="require")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute_db(query, args=()):
    db = get_db()
    cur = db.cursor()
    cur.execute(query, args)
    db.commit()
    cur.close()


def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    try:
        cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id INTEGER")
    except Exception:
        db.rollback()

    db.commit()
    cur.close()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def inject_user():
    return {"current_user_email": session.get("email")}


# =========================================================
# HERRAMIENTAS
# =========================================================

def obtener_hora(timezone_str="America/Havana"):
    try:
        zona = ZoneInfo(timezone_str)
    except (ZoneInfoNotFoundError, Exception):
        zona = ZoneInfo("America/Havana")

    ahora = datetime.now(zona)
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    dia_semana = dias[ahora.weekday()]
    mes = meses[ahora.month - 1]

    return (
        f"Son las {ahora.strftime('%H:%M')} "
        f"del {dia_semana} {ahora.day} de {mes} de {ahora.year}."
    )


def buscar_en_google(query):
    try:
        resultados = search(query, num_results=3, lang="es")
        lineas = []
        for i, url in enumerate(resultados, 1):
            lineas.append(f"{i}. {url}")
        if not lineas:
            return "No encontré resultados."
        return "Resultados de Google:\n" + "\n".join(lineas)
    except Exception as e:
        return f"Error al buscar en Google: {str(e)}"


def reproducir_musica(query):
    from urllib.parse import quote_plus
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    return f"[MUSICA:{url}] Buscando '{query}' en YouTube."


HERRAMIENTAS = [
    {
        "type": "function",
        "function": {
            "name": "obtener_hora",
            "description": "Útil para saber la hora y fecha actuales.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_google",
            "description": "Busca información actual en Google.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Término de búsqueda"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reproducir_musica",
            "description": "Busca música o video en YouTube para reproducir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Nombre de la canción o artista"}
                },
                "required": ["query"],
            },
        },
    },
]


def detectar_comando_app(mensaje):
    mensaje_lower = mensaje.lower()

    apps = {
        "whatsapp": "com.whatsapp",
        "youtube": "com.google.android.youtube",
        "gmail": "com.google.android.gm",
        "correo": "com.google.android.gm",
        "calculadora": "com.google.android.calculator",
        "calendario": "com.google.android.calendar",
        "camara": "com.android.camera",
        "cámara": "com.android.camera",
        "galeria": "com.google.android.apps.photos",
        "galería": "com.google.android.apps.photos",
        "mapas": "com.google.android.apps.maps",
        "configuracion": "com.android.settings",
        "configuración": "com.android.settings",
        "ajustes": "com.android.settings",
        "reloj": "com.google.android.deskclock",
        "spotify": "com.spotify.music",
        "chrome": "com.android.chrome",
        "instagram": "com.instagram.android",
        "facebook": "com.facebook.katana",
        "telegram": "org.telegram.messenger",
    }

    if "abre" in mensaje_lower or "abrir" in mensaje_lower:
        for nombre, paquete in apps.items():
            if nombre in mensaje_lower:
                return f"[ABRIR:{paquete}] Abriendo {nombre}..."

    return None


def preguntar_jarvis(mensaje, timezone_str="America/Havana"):
    comando_app = detectar_comando_app(mensaje)
    if comando_app:
        return comando_app

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return "No tengo configurada mi clave."

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    try:
        response = client.chat.completions.create(
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres Jarvis, un asistente personal inteligente. "
                        "Hablas en español, eres amigable, directo y útil. "
                        f"El usuario está en la zona horaria '{timezone_str}'. "
                        "Puedes usar herramientas para: hora, búsqueda en Google, o música. "
                        "Responde de forma clara y breve (máximo 3 oraciones)."
                    )
                },
                {"role": "user", "content": mensaje}
            ],
            tools=HERRAMIENTAS,
            tool_choice="auto",
        )

        respuesta = response.choices[0].message

        if respuesta.tool_calls:
            tool_call = respuesta.tool_calls[0]
            nombre_funcion = tool_call.function.name
            argumentos = tool_call.function.arguments

            resultado_herramienta = ""

            if nombre_funcion == "obtener_hora":
                resultado_herramienta = obtener_hora(timezone_str)
            elif nombre_funcion == "buscar_en_google":
                args = json.loads(argumentos)
                resultado_herramienta = buscar_en_google(args.get("query", ""))
            elif nombre_funcion == "reproducir_musica":
                args = json.loads(argumentos)
                resultado_herramienta = reproducir_musica(args.get("query", ""))

            mensajes_con_resultado = [
                {"role": "system", "content": "Eres Jarvis. Hablas español. Responde breve."},
                {"role": "user", "content": mensaje},
                respuesta,
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(resultado_herramienta),
                }
            ]

            response_final = client.chat.completions.create(
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
                messages=mensajes_con_resultado,
            )

            return response_final.choices[0].message.content

        return respuesta.content

    except Exception as e:
        return f"Error: {str(e)}"


# =========================================================
# RUTAS DE AUTENTICACIÓN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Completa todos los campos"
        else:
            user = query_db("SELECT * FROM users WHERE lower(email) = %s", (email,), one=True)

            if user is None or not check_password_hash(user["password_hash"], password):
                error = "Email o contraseña incorrecta"
            else:
                session.permanent = True
                session["user_id"] = user["id"]
                session["email"] = user["email"]
                return redirect(url_for("index"))

    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not email or not password:
            error = "Completa todos los campos"
        elif len(password) < 6:
            error = "La contraseña debe tener al menos 6 caracteres"
        elif password != password2:
            error = "Las contraseñas no coinciden"
        else:
            exists = query_db("SELECT id FROM users WHERE lower(email) = %s", (email,), one=True)

            if exists:
                error = "Ese email ya está registrado"
            else:
                execute_db("""
                    INSERT INTO users (email, password_hash, created_at)
                    VALUES (%s, %s, %s)
                """, (email, generate_password_hash(password), datetime.utcnow().isoformat()))

                return redirect(url_for("login"))

    return render_template("register.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# RUTAS PRINCIPALES
# =========================================================

@app.route("/")
@login_required
def index():
    session.permanent = True
    return render_template("index.html")


@app.route("/history")
@login_required
def history():
    session.permanent = True
    user_id = session["user_id"]
    mensajes = query_db("""
        SELECT role, message, created_at
        FROM conversations
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT 100
    """, (user_id,))
    return render_template("history.html", messages=mensajes)


@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    data = request.get_json(silent=True) or {}
    mensaje = str(data.get("message", "")).strip()
    timezone_str = str(data.get("timezone", "America/Havana")).strip()
    user_id = session["user_id"]

    if not mensaje:
        return jsonify({"ok": False, "error": "Escribe algo."}), 400

    now = datetime.utcnow().isoformat()

    execute_db("""
        INSERT INTO conversations (user_id, role, message, created_at)
        VALUES (%s, %s, %s, %s)
    """, (user_id, "user", mensaje, now))

    respuesta = preguntar_jarvis(mensaje, timezone_str)

    execute_db("""
        INSERT INTO conversations (user_id, role, message, created_at)
        VALUES (%s, %s, %s, %s)
    """, (user_id, "jarvis", respuesta, datetime.utcnow().isoformat()))

    return jsonify({"ok": True, "response": respuesta})


@app.route("/api/clear", methods=["POST"])
@login_required
def api_clear():
    user_id = session["user_id"]
    execute_db("DELETE FROM conversations WHERE user_id = %s", (user_id,))
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"status": "online"})


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
