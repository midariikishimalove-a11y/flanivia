"""Baixador de vídeos do TikTok e Instagram (HD, sem marca d'água quando possível).

Uso:
    pip install -r requirements.txt
    python app.py
    abrir http://localhost:5000
"""
import os
import re
import shutil
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_file, after_this_request
import yt_dlp

# O TikTok costuma recusar pedidos que não parecem vir de um navegador de verdade.
# O curl_cffi permite ao yt-dlp "imitar" o Chrome. Se não estiver instalado,
# o site continua funcionando, só sem essa ajuda.
try:
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget
    TEM_IMPERSONATE = True
except Exception:
    ImpersonateTarget = None
    TEM_IMPERSONATE = False

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Só aceitamos links desses domínios (evita uso do servidor para outros fins)
ALLOWED_HOSTS = (
    "tiktok.com",
    "instagram.com",
    "instagr.am",
)


def host_permitido(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def nome_seguro(texto: str) -> str:
    texto = re.sub(r"[^\w\- ]+", "", texto or "video", flags=re.UNICODE).strip()
    return (texto[:60] or "video").replace(" ", "_")


def montar_opcoes(pasta: str, impersonar: bool) -> dict:
    opcoes = {
        "outtmpl": os.path.join(pasta, "%(id)s.%(ext)s"),
        # Melhor qualidade em mp4 já com áudio, evitando o formato "download"
        # do TikTok, que é o que vem com marca d'água.
        "format": "best[ext=mp4][format_id!*=download]/best[ext=mp4]/best",
        "format_sort": ["res", "br"],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 3,
    }
    if impersonar and TEM_IMPERSONATE:
        try:
            opcoes["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            app.logger.warning("Não consegui ativar o modo 'imitar Chrome'.")
    return opcoes


def baixar_com_ytdlp(url: str, pasta: str, impersonar: bool):
    with yt_dlp.YoutubeDL(montar_opcoes(pasta, impersonar)) as ydl:
        info = ydl.extract_info(url, download=True)
        return info, ydl.prepare_filename(info)


@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "index.html"))


@app.route("/saude")
def saude():
    """Página de diagnóstico: mostra versões e se o servidor consegue falar com o TikTok."""
    versao = getattr(getattr(yt_dlp, "version", None), "__version__", "?")
    resultado = {"yt_dlp": versao, "imitar_chrome_disponivel": TEM_IMPERSONATE}
    try:
        req = Request(
            "https://www.tiktok.com/",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )
        with urlopen(req, timeout=10) as r:
            resultado["tiktok_http"] = r.status
    except HTTPError as e:
        resultado["tiktok_http"] = e.code
    except (URLError, OSError) as e:
        resultado["tiktok_http"] = f"erro: {e}"
    return jsonify(resultado)


@app.route("/api/baixar", methods=["POST"])
def baixar():
    dados = request.get_json(silent=True) or {}
    url = (dados.get("url") or "").strip()

    if not host_permitido(url):
        return jsonify(erro="Cole um link válido do TikTok ou do Instagram."), 400

    pasta = tempfile.mkdtemp(prefix="dl_")

    @after_this_request
    def limpar(resposta):
        shutil.rmtree(pasta, ignore_errors=True)
        return resposta

    try:
        try:
            info, caminho = baixar_com_ytdlp(url, pasta, True)
        except yt_dlp.utils.DownloadError as e:
            # Se o problema foi o modo "imitar Chrome", tenta de novo sem ele
            if TEM_IMPERSONATE and "mpersonat" in str(e):
                app.logger.warning("Falha com impersonate, tentando sem: %s", e)
                info, caminho = baixar_com_ytdlp(url, pasta, False)
            else:
                raise
    except yt_dlp.utils.DownloadError as e:
        bruto = str(e)
        # Registra o motivo real nos logs (aba Logs do Render)
        app.logger.error("Falha ao baixar %s: %s", url, bruto)
        # Detalhe curto para mostrar na página (sem códigos de cor do terminal)
        limpo = re.sub(r"\x1b\[[0-9;]*m", "", bruto).strip()
        detalhe = (limpo.splitlines() or ["sem detalhe"])[-1][:200]
        if "login" in bruto.lower() or "private" in bruto.lower():
            msg = "Esse vídeo é privado ou exige login. Só funciona com posts públicos."
        else:
            msg = "Não consegui baixar esse vídeo. Confira se o link está correto e é público."
        return jsonify(erro=msg, detalhe=detalhe), 422
    except Exception as e:
        app.logger.exception("Erro inesperado ao baixar %s", url)
        return jsonify(erro="Erro inesperado ao baixar o vídeo.", detalhe=str(e)[:200]), 500

    if not os.path.exists(caminho):
        return jsonify(erro="O arquivo não foi gerado."), 500

    titulo = nome_seguro(info.get("title") or info.get("id"))
    ext = os.path.splitext(caminho)[1] or ".mp4"
    return send_file(caminho, as_attachment=True, download_name=f"{titulo}{ext}")


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
