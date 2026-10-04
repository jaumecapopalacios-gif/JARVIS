console.log("Jarvis JS cargado ✅");

const chatArea = document.getElementById("chat-area");
const inputText = document.getElementById("input-text");
const btnMic = document.getElementById("btn-mic");
const btnSend = document.getElementById("btn-send");

let escuchando = false;
let recognition = null;

const USER_TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Havana";

if ("webkitSpeechRecognition" in window || "SpeechRecognition" in window) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = "es-ES";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onresult = function (event) {
        const texto = event.results[0][0].transcript;
        inputText.value = texto;
        enviarTexto(true);
    };

    recognition.onerror = function (e) {
        console.error("Error mic:", e);
        detenerEscucha();
    };

    recognition.onend = function () {
        detenerEscucha();
    };
}

function agregarMensaje(texto, tipo) {
    const div = document.createElement("div");
    div.className = "message " + tipo;
    div.innerHTML = "<p>" + texto + "</p>";
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function procesarComandos(texto) {
    const matchAbrir = texto.match(/\[ABRIR:([^\]]+)\]/);
    if (matchAbrir) {
        const paquete = matchAbrir[1];
        const url = "intent://#Intent;package=" + paquete + ";end";
        window.location.href = url;
    }

    const matchMusica = texto.match(/\[MUSICA:([^\]]+)\]/);
    if (matchMusica) {
        const urlMusica = matchMusica[1];
        setTimeout(function () {
            window.open(urlMusica, "_blank");
        }, 1500);
    }
}

async function enviarTexto(desdeVoz) {
    const mensaje = inputText.value.trim();
    if (!mensaje) return;

    agregarMensaje(mensaje, "user");
    inputText.value = "";

    const pensando = document.createElement("div");
    pensando.className = "message jarvis";
    pensando.id = "pensando";
    pensando.innerHTML = "<p>...</p>";
    chatArea.appendChild(pensando);
    chatArea.scrollTop = chatArea.scrollHeight;

    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: mensaje,
                timezone: USER_TIMEZONE
            })
        });

        if (res.redirected) {
            window.location.href = res.url;
            return;
        }

        const data = await res.json();

        const p = document.getElementById("pensando");
        if (p) p.remove();

        if (data.ok) {
            agregarMensaje(data.response, "jarvis");

            const textoLimpio = data.response
                .replace(/\[ABRIR:[^\]]+\]/g, "")
                .replace(/\[MUSICA:[^\]]+\]/g, "")
                .trim();

            if (desdeVoz) {
                hablar(textoLimpio || data.response);
            }

            procesarComandos(data.response);

        } else {
            agregarMensaje("Error: " + (data.error || "desconocido"), "jarvis");
        }

    } catch (e) {
        const p = document.getElementById("pensando");
        if (p) p.remove();
        agregarMensaje("Error de conexión. Intenta de nuevo.", "jarvis");
    }
}

function hablar(texto) {
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();

    const utter = new SpeechSynthesisUtterance(texto);
    utter.lang = "es-ES";
    utter.rate = 1.0;
    utter.pitch = 1.0;

    const voces = window.speechSynthesis.getVoices();
    const vozEs = voces.find(v => v.lang.startsWith("es"));
    if (vozEs) utter.voice = vozEs;

    window.speechSynthesis.speak(utter);
}

function toggleVoz() {
    if (!recognition) {
        alert("Tu navegador no soporta reconocimiento de voz. Usa Chrome.");
        return;
    }

    if (escuchando) {
        recognition.stop();
        detenerEscucha();
    } else {
        try {
            recognition.start();
            escuchando = true;
            btnMic.classList.add("listening");
            btnMic.textContent = "🔴";
        } catch (e) {
            console.error(e);
        }
    }
}

function detenerEscucha() {
    escuchando = false;
    btnMic.classList.remove("listening");
    btnMic.textContent = "🎙️";
}

if (btnSend) {
    btnSend.addEventListener("click", function () {
        enviarTexto(false);
    });
}

inputText.addEventListener("keypress", function (e) {
    if (e.key === "Enter") {
        enviarTexto(false);
    }
});

if ("speechSynthesis" in window) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = function () {
        window.speechSynthesis.getVoices();
    };
}
