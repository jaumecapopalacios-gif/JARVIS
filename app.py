import os
import json
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    g
)

from openai import OpenAI
from googlesearch import search


DATABASE_URL = os.getenv("DATABASE_URL", "")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))


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
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    cur.close()


# =========================================================
# HERRAMIENTAS (TOOLS) QUE JARVIS PUEDE USAR
# =========================================================

def obtener_hora():
    """Devuelve la hora actual de Cuba."""
    ahora = datetime.now(ZoneInfo("America/Havana"))
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    dia_semana = dias[ahora.weekday()]
    mes = meses[ahora.month - 1]
    return (
        f"En Cuba son las {ahora.strftime('%H:%M')} "
        f"del {dia_semana} {ahora.day} de {mes} de {ahora.year}."
    )


def buscar_en_google(query):
    """Busca en Google y devuelve los 3 primeros resultados."""
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


HERRAMIENTAS = [
    {
        "type": "function",
        "function": {
            "name": "obtener_hora",
            "description": "Útil para saber la hora y fecha actuales en Cuba.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_google",
            "description": "Busca información actual en Google. Úsalo cuando el usuario pida buscar algo, noticias o información que no sepas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "El término de búsqueda, ej: 'noticias de tecnología'",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# =========================================================
# LÓGICA DE JARVIS CON TOOL CALLING
# =========================================================

def preguntar_jarvis(mensaje):
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        return "No tengo configurada mi clave de OpenRouter."

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
                        "Puedes usar herramientas para obtener la hora exacta de Cuba o buscar en Google. "
                        "Cuando te pregunten la hora, SIEMPRE usa la herramienta obtener_hora. "
                        "Cuando necesites información actual, usa buscar_en_google. "
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
                resultado_herramienta = obtener_hora()
            elif nombre_funcion == "buscar_en_google":
                args = json.loads(argumentos)
                resultado_herramienta = buscar_en_google(args.get("query", ""))

            mensajes_con_resultado = [
                {
                    "role": "system",
                    "content": (
                        "Eres Jarvis, un asistente personal inteligente. "
                        "Hablas en español. Cuando tengas el resultado de una herramienta, "
                        "responde al usuario de forma natural y breve."
                    )
                },
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
# RUTAS DE FLASK
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/history")
def history():
    mensajes = query_db("""
        SELECT role, message, created_at
        FROM conversations
        ORDER BY id DESC
        LIMIT 100
    """)
    return render_template("history.html", messages=mensajes)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    mensaje = str(data.get("message", "")).strip()

    if not mensaje:
        return jsonify({"ok": False, "error": "Escribe algo."}), 400

    now = datetime.utcnow().isoformat()

    execute_db("""
        INSERT INTO conversations (role, message, created_at)
        VALUES (%s, %s, %s)
    """, ("user", mensaje, now))

    respuesta = preguntar_jarvis(mensaje)

    execute_db("""
        INSERT INTO conversations (role, message, created_at)
        VALUES (%s, %s, %s)
    """, ("jarvis", respuesta, datetime.utcnow().isoformat()))

    return jsonify({"ok": True, "response": respuesta})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    execute_db("DELETE FROM conversations")
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"status": "online"})


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
