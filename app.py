"""Baixador: TikTok/Instagram, YouTube e Spotify (via YouTube), com playlists em ZIP.

Uso no seu computador:
    pip install -r requirements.txt
    python app.py
    abrir http://localhost:5000  (no celular: o endereço que aparece no terminal)
"""
import base64
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import zipfile
from collections import defaultdict, deque
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

# Garante que programas instalados junto com o Python (ex.: deno) sejam achados.
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")

from flask import Flask, jsonify, request, send_file  # noqa: E402
import yt_dlp  # noqa: E402

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Configuração (pode ser mudada por variáveis de ambiente no Render)
# --------------------------------------------------------------------------
def _int_env(nome, padrao):
    try:
        return max(1, int(os.environ.get(nome, padrao)))
    except ValueError:
        return padrao


MAX_ITENS_AUDIO = _int_env("MAX_ITENS_AUDIO", 40)   # músicas por ZIP
MAX_ITENS_VIDEO = _int_env("MAX_ITENS_VIDEO", 10)   # vídeos por ZIP
MAX_MINUTOS = _int_env("MAX_MINUTOS", 60)           # duração máxima de cada item
MAX_JOBS = _int_env("MAX_JOBS", 2)                  # downloads ao mesmo tempo
MAX_JOBS_POR_IP = _int_env("MAX_JOBS_POR_IP", 2)
VALIDADE_MIN = _int_env("VALIDADE_MIN", 20)         # quanto tempo o arquivo fica disponível

ALTURAS = (2160, 1440, 1080, 720, 480, 360, 240, 144)
BITRATES = (128, 192, 256, 320)

SPOTIFY_ID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()

# Cookies do YouTube (opcional). NUNCA coloque esse arquivo dentro do repositório.
COOKIES_SRC = next(
    (p for p in (os.environ.get("COOKIES_FILE"), "/etc/secrets/cookies.txt") if p and os.path.isfile(p)),
    None,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# O TikTok costuma recusar pedidos que não parecem vir de um navegador de verdade.
try:
    import curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget
    TEM_IMPERSONATE = True
except Exception:
    ImpersonateTarget = None
    TEM_IMPERSONATE = False


def preparar_ffmpeg():
    """Devolve a pasta onde está um programa chamado 'ffmpeg' (ou None)."""
    achado = shutil.which("ffmpeg")
    if achado:
        return os.path.dirname(achado)
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    pasta = os.path.join(tempfile.gettempdir(), "baixador_ffmpeg")
    os.makedirs(pasta, exist_ok=True)
    alvo = os.path.join(pasta, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    try:
        if os.path.lexists(alvo):
            os.remove(alvo)
        try:
            os.symlink(exe, alvo)
        except (OSError, NotImplementedError):
            shutil.copy2(exe, alvo)
    except OSError:
        return None
    return pasta


FFMPEG_DIR = preparar_ffmpeg()


class Cancelado(getattr(yt_dlp.utils, "DownloadCancelled", Exception)):
    pass


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def host_de(url):
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return p.hostname.lower()


def _dominio(host, *dominios):
    return any(host == d or host.endswith("." + d) for d in dominios)


def classificar(url):
    """Diz de qual serviço é o link: social, youtube, spotify (ou None)."""
    h = host_de(url)
    if not h:
        return None
    if _dominio(h, "tiktok.com", "instagram.com", "instagr.am"):
        return "social"
    if _dominio(h, "youtube.com") or h == "youtu.be":
        return "youtube"
    if h == "open.spotify.com":
        return "spotify"
    return None


RE_SPOTIFY = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?(track|album|playlist)/([A-Za-z0-9]{22})")


def nome_arquivo(texto, padrao="arquivo", limite=120):
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", texto or "")
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:limite].strip(" .") or padrao


def limpar_ansi(txt):
    return re.sub(r"\x1b\[[0-9;]*m", "", str(txt or "")).strip()


def traduzir_erro(bruto):
    """Devolve (mensagem em português, detalhe curto)."""
    limpo = limpar_ansi(bruto)
    linhas = [l for l in limpo.splitlines() if l.strip()]
    detalhe = (linhas[-1] if linhas else "sem detalhe")[:220]
    b = limpo.lower()
    if "sign in to confirm" in b or "not a bot" in b:
        msg = ("O YouTube bloqueou este servidor e pediu login (comum em servidores na nuvem). "
               "Veja no LEIAME como usar cookies, ou rode o site no seu computador.")
    elif "private" in b or "login required" in b or "log in" in b:
        msg = "Esse conteúdo é privado ou exige login."
    elif "confirm your age" in b or "age-restricted" in b or "age restricted" in b:
        msg = "Esse vídeo tem restrição de idade (só funciona com cookies de uma conta)."
    elif "unavailable" in b or "removed" in b or "has been terminated" in b:
        msg = "Vídeo indisponível ou removido."
    elif "ffmpeg" in b or "ffprobe" in b:
        msg = "Falta o conversor ffmpeg neste servidor."
    elif "429" in b or "too many requests" in b:
        msg = "O site limitou os pedidos deste servidor. Espere alguns minutos e tente de novo."
    elif "403" in b or "forbidden" in b:
        msg = "O site recusou o acesso deste servidor (bloqueio)."
    elif "unable to extract" in b or "unsupported url" in b or "rehydration" in b:
        msg = ("O site recusou ou mudou o formato da página. Costuma ser bloqueio de servidores "
               "na nuvem ou yt-dlp desatualizado.")
    elif "muito longo" in b:
        msg = "Item longo demais para este servidor."
    else:
        msg = "Não consegui baixar este item."
    return msg, detalhe


def http_get(url, headers=None, dados=None, timeout=15):
    cab = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
    cab.update(headers or {})
    req = Request(url, headers=cab, data=dados)
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def codigo_http(url):
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=8) as r:
            return r.status
    except HTTPError as e:
        return e.code
    except (URLError, OSError) as e:
        return f"erro: {e}"[:80]


_LIMITES = defaultdict(deque)
_LIM_LOCK = threading.Lock()


def estourou(ip, chave, maximo, janela=60):
    agora = time.time()
    with _LIM_LOCK:
        fila = _LIMITES[(ip, chave)]
        while fila and agora - fila[0] > janela:
            fila.popleft()
        if len(fila) >= maximo:
            return True
        fila.append(agora)
        return False


def ip_do_cliente():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else "") or request.remote_addr or "?"


# --------------------------------------------------------------------------
# yt-dlp
# --------------------------------------------------------------------------
def opcoes_base(pasta, extra=None):
    o = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 3,
        "windowsfilenames": True,
    }
    if FFMPEG_DIR:
        o["ffmpeg_location"] = FFMPEG_DIR
    if COOKIES_SRC and pasta:
        destino = os.path.join(pasta, "cookies.txt")
        try:
            if not os.path.exists(destino):
                shutil.copyfile(COOKIES_SRC, destino)
            o["cookiefile"] = destino
        except OSError:
            pass
    if extra:
        o.update(extra)
    return o


def opcoes_video_youtube(altura):
    if FFMPEG_DIR:
        return {
            "format": f"bv*[height<={altura}]+ba/b[height<={altura}]/bv*+ba/b",
            "format_sort": [f"res:{altura}", "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4",
        }
    return {"format": f"b[height<={altura}][ext=mp4]/b[height<={altura}]/b"}


def opcoes_audio(bitrate):
    if FFMPEG_DIR:
        return {
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(bitrate),
            }],
        }
    return {"format": "bestaudio[ext=m4a]/bestaudio/best"}


def opcoes_social():
    return {
        # Evita o formato "download" do TikTok, que é o que vem com marca d'água.
        "format": "best[ext=mp4][format_id!*=download]/best[ext=mp4]/best",
        "format_sort": ["res", "br"],
    }


def achar_arquivo(sub, motivo):
    if not os.path.isdir(sub):
        raise RuntimeError(motivo[-1] if motivo else "arquivo não gerado")
    ruins = (".part", ".ytdl", ".temp", ".json", ".jpg", ".jpeg", ".webp", ".png", ".txt")
    todos = [os.path.join(sub, f) for f in os.listdir(sub)]
    cands = [
        c for c in todos
        if os.path.isfile(c) and not c.lower().endswith(ruins) and not re.search(r"\.f\d+\.\w+$", c)
    ]
    if not cands:
        if motivo:
            raise RuntimeError(motivo[-1])
        if any(re.search(r"\.f\d+\.\w+$", c) for c in todos):
            raise RuntimeError("não consegui juntar vídeo e áudio (ffmpeg)")
        raise RuntimeError("arquivo não gerado")
    return max(cands, key=os.path.getsize)


# --------------------------------------------------------------------------
# Spotify (só descobre a lista de músicas; o áudio vem do YouTube)
# --------------------------------------------------------------------------
def _procurar(obj, pred):
    pilha = [obj]
    while pilha:
        o = pilha.pop()
        if isinstance(o, dict):
            if pred(o):
                return o
            pilha.extend(o.values())
        elif isinstance(o, list):
            pilha.extend(o)
    return None


def _capa_embed(ent):
    for chave in ("coverArt", "visualIdentity", "images"):
        v = ent.get(chave)
        if isinstance(v, dict):
            lst = v.get("sources") or v.get("image") or []
        else:
            lst = v or []
        urls = [i.get("url") for i in lst if isinstance(i, dict) and i.get("url")]
        if urls:
            return urls[0]
    return None


def spotify_embed(tipo, sid):
    html = http_get(f"https://open.spotify.com/embed/{tipo}/{sid}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("página do Spotify sem dados")
    dados = json.loads(m.group(1))
    if tipo == "track":
        ent = _procurar(dados, lambda d: d.get("type") == "track" and "name" in d) or \
              _procurar(dados, lambda d: "name" in d and "artists" in d)
        if not ent:
            raise RuntimeError("faixa não encontrada na página")
        artistas = ", ".join(a.get("name", "") for a in ent.get("artists", []) if isinstance(a, dict)) \
            or ent.get("subtitle", "")
        dur = ent.get("duration") or ent.get("duration_ms")
        return {
            "tipo": "track", "titulo": ent["name"], "capa": _capa_embed(ent),
            "faixas": [{"titulo": ent["name"], "artista": artistas,
                        "duracao": (dur / 1000.0) if dur else None}],
        }
    ent = _procurar(dados, lambda d: isinstance(d.get("trackList"), list))
    if not ent:
        raise RuntimeError("lista de faixas não encontrada na página")
    faixas = []
    for t in ent["trackList"]:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        dur = t.get("duration")
        faixas.append({"titulo": t["title"], "artista": t.get("subtitle", ""),
                       "duracao": (dur / 1000.0) if dur else None})
    return {"tipo": tipo, "titulo": ent.get("title") or ent.get("name") or tipo.capitalize(),
            "capa": _capa_embed(ent), "faixas": faixas}


_TOKEN = {"valor": None, "expira": 0}


def _spotify_token():
    if _TOKEN["valor"] and time.time() < _TOKEN["expira"] - 30:
        return _TOKEN["valor"]
    cred = base64.b64encode(f"{SPOTIFY_ID}:{SPOTIFY_SECRET}".encode()).decode()
    d = json.loads(http_get(
        "https://accounts.spotify.com/api/token",
        {"Authorization": f"Basic {cred}", "Content-Type": "application/x-www-form-urlencoded"},
        dados=b"grant_type=client_credentials",
    ))
    _TOKEN["valor"] = d["access_token"]
    _TOKEN["expira"] = time.time() + int(d.get("expires_in", 3600))
    return _TOKEN["valor"]


def _faixa_api(t, album=None):
    return {
        "titulo": t["name"],
        "artista": ", ".join(a["name"] for a in t.get("artists", [])),
        "duracao": (t["duration_ms"] / 1000.0) if t.get("duration_ms") else None,
        "album": album or (t.get("album") or {}).get("name"),
    }


def spotify_api(tipo, sid):
    cab = {"Authorization": f"Bearer {_spotify_token()}"}
    base = "https://api.spotify.com/v1"

    def get(u):
        return json.loads(http_get(u, cab))

    cap = max(MAX_ITENS_AUDIO, MAX_ITENS_VIDEO) + 1
    if tipo == "track":
        t = get(f"{base}/tracks/{sid}")
        imgs = (t.get("album") or {}).get("images") or []
        return {"tipo": "track", "titulo": t["name"], "capa": imgs[0]["url"] if imgs else None,
                "faixas": [_faixa_api(t)]}
    obj = get(f"{base}/{tipo}s/{sid}")
    faixas, pag = [], obj.get("tracks")
    while pag and len(faixas) < cap:
        for it in pag.get("items", []):
            t = it.get("track") if tipo == "playlist" else it
            if t and t.get("name"):
                faixas.append(_faixa_api(t, obj["name"] if tipo == "album" else None))
        pag = get(pag["next"]) if pag.get("next") else None
    imgs = obj.get("images") or []
    return {"tipo": tipo, "titulo": obj["name"], "capa": imgs[0]["url"] if imgs else None, "faixas": faixas}


def spotify_info(tipo, sid):
    if SPOTIFY_ID and SPOTIFY_SECRET:
        try:
            return spotify_api(tipo, sid)
        except Exception as e:  # cai para a página pública
            app.logger.warning("API do Spotify falhou (%s); usando página pública", e)
    return spotify_embed(tipo, sid)


PALAVRAS_RUINS = ("live", "ao vivo", "cover", "karaoke", "karaokê", "instrumental", "sped up",
                  "slowed", "reverb", "8d", "remix", "nightcore", "reaction", "tutorial")


def escolher_resultado(candidatos, titulo, artista, duracao_s):
    """Escolhe, entre resultados do YouTube, o que parece ser a música certa."""
    alvo = f"{titulo} {artista}".lower()
    principal = (artista or "").split(",")[0].strip().lower()
    melhor, melhor_nota = None, None
    for c in candidatos:
        t = (c.get("title") or "").lower()
        canal = (c.get("channel") or c.get("uploader") or "").lower()
        d = c.get("duration")
        nota = 0.0
        if duracao_s and d:
            nota += abs(d - duracao_s)
        elif duracao_s:
            nota += 30
        for p in PALAVRAS_RUINS:
            if p in t and p not in alvo:
                nota += 40
        if canal.endswith("- topic"):
            nota -= 15
        if principal and principal in (t + " " + canal):
            nota -= 5
        if d and d > MAX_MINUTOS * 60:
            nota += 1000
        if melhor is None or nota < melhor_nota:
            melhor, melhor_nota = c, nota
    return melhor


def achar_no_youtube(item):
    principal = (item.get("artista") or "").split(",")[0].strip()
    consulta = f"{principal} - {item['titulo']}".strip(" -")
    with tempfile.TemporaryDirectory(prefix="busca_") as tmp:
        with yt_dlp.YoutubeDL(opcoes_base(tmp, {"extract_flat": True, "skip_download": True})) as ydl:
            info = ydl.extract_info(f"ytsearch6:{consulta}", download=False)
    entradas = [e for e in ((info or {}).get("entries") or []) if e]
    if not entradas:
        raise RuntimeError("não achei essa música no YouTube")
    melhor = escolher_resultado(entradas, item["titulo"], item.get("artista", ""), item.get("duracao"))
    url = melhor.get("url") or ""
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={melhor.get('id')}"
    return url


# --------------------------------------------------------------------------
# Informações sobre o link (botão "Buscar")
# --------------------------------------------------------------------------
def _partes_youtube(url):
    p = urlparse(url)
    qs = parse_qs(p.query)
    lista = (qs.get("list") or [None])[0]
    video = (qs.get("v") or [None])[0]
    if host_de(url) == "youtu.be" and len(p.path.strip("/")) > 0:
        video = p.path.strip("/")
    elif re.match(r"^/(shorts|live|embed)/[^/]+", p.path):
        video = p.path.split("/")[2]
    return lista, video


def eh_playlist_pura(url):
    lista, video = _partes_youtube(url)
    return bool(lista) and not video


def info_youtube(url):
    lista, video = _partes_youtube(url)
    cap = max(MAX_ITENS_AUDIO, MAX_ITENS_VIDEO)
    with tempfile.TemporaryDirectory(prefix="info_") as tmp:
        if lista and not video:
            purl = f"https://www.youtube.com/playlist?list={lista}"
            opts = opcoes_base(tmp, {"skip_download": True, "extract_flat": "in_playlist",
                                     "noplaylist": False, "playlistend": cap + 1})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(purl, download=False)
            entradas = [e for e in (info.get("entries") or []) if e]
            capa = None
            if entradas and entradas[0].get("id"):
                capa = f"https://i.ytimg.com/vi/{entradas[0]['id']}/hqdefault.jpg"
            return {
                "fonte": "youtube", "tipo": "playlist", "titulo": info.get("title") or "Playlist",
                "capa": capa, "autor": info.get("uploader") or info.get("channel"),
                "itens": min(len(entradas), cap), "itens_truncado": len(entradas) > cap,
                "limite_audio": MAX_ITENS_AUDIO, "limite_video": MAX_ITENS_VIDEO,
                "alturas": list(ALTURAS), "tem_playlist": True,
            }
        opts = opcoes_base(tmp, {"skip_download": True, "noplaylist": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    alturas = sorted(
        {f.get("height") for f in (info.get("formats") or [])
         if f.get("height") and f.get("vcodec") not in (None, "none")} & set(ALTURAS),
        reverse=True,
    )
    return {
        "fonte": "youtube", "tipo": "video", "titulo": info.get("title") or "Vídeo",
        "capa": info.get("thumbnail"), "autor": info.get("uploader") or info.get("channel"),
        "duracao": info.get("duration"), "alturas": alturas or list(ALTURAS),
        "tem_playlist": bool(lista),
        "limite_audio": MAX_ITENS_AUDIO, "limite_video": MAX_ITENS_VIDEO,
    }


def info_spotify(url):
    m = RE_SPOTIFY.search(url)
    if not m:
        raise ValueError("Use o link completo de uma música, álbum ou playlist (open.spotify.com/...).")
    tipo, sid = m.group(1), m.group(2)
    sp = spotify_info(tipo, sid)
    if not sp["faixas"]:
        raise RuntimeError("nenhuma faixa encontrada")
    artistas = sp["faixas"][0]["artista"] if tipo == "track" else None
    return {
        "fonte": "spotify", "tipo": tipo, "titulo": sp["titulo"], "capa": sp.get("capa"),
        "autor": artistas, "itens": len(sp["faixas"]),
        "limite_audio": MAX_ITENS_AUDIO, "limite_video": MAX_ITENS_AUDIO,
    }


# --------------------------------------------------------------------------
# Fila de downloads
# --------------------------------------------------------------------------
JOBS = {}
JOBS_LOCK = threading.Lock()
SEM = threading.BoundedSemaphore(MAX_JOBS)
FINAIS = ("pronto", "erro", "cancelado")


class Job:
    def __init__(self, ip, params):
        self.id = secrets.token_urlsafe(16)
        self.ip = ip
        self.params = params
        self.status = "fila"
        self.atual = 0
        self.total = 0
        self.titulo = ""
        self.pct = 0.0
        self.falhas = []
        self.aviso = ""
        self.arquivo = None
        self.nome = None
        self.tamanho = 0
        self.erro = None
        self.detalhe = None
        self.cancelar = False
        self.nome_lista = None
        self.eh_lista = False
        self.criado = time.time()
        self.finalizado = None
        self.pasta = tempfile.mkdtemp(prefix="dl_")

    def to_dict(self):
        return {
            "id": self.id, "status": self.status, "atual": self.atual, "total": self.total,
            "titulo": self.titulo, "pct": round(self.pct, 1), "falhas": len(self.falhas),
            "aviso": self.aviso, "nome": self.nome, "tamanho": self.tamanho,
            "erro": self.erro, "detalhe": self.detalhe, "lista": self.eh_lista,
            "tipo": self.params["tipo"],
        }


def limite_de(params):
    if params["fonte"] == "youtube" and params["tipo"] == "video":
        return MAX_ITENS_VIDEO
    return MAX_ITENS_AUDIO


def listar_itens(job):
    p = job.params
    limite = limite_de(p)
    if p["fonte"] == "spotify":
        sp = spotify_info(*p["spotify"])
        faixas = sp["faixas"]
        job.nome_lista = sp["titulo"]
        job.eh_lista = sp["tipo"] != "track"
        if len(faixas) > limite:
            job.aviso = f"A lista tem mais de {limite} músicas; baixei só as primeiras {limite}."
        return [{"titulo": f["titulo"], "artista": f["artista"], "duracao": f.get("duracao")}
                for f in faixas[:limite]]
    if p["fonte"] == "youtube" and p["playlist"]:
        lista, _ = _partes_youtube(p["url"])
        purl = f"https://www.youtube.com/playlist?list={lista}"
        with yt_dlp.YoutubeDL(opcoes_base(job.pasta, {
                "skip_download": True, "extract_flat": "in_playlist",
                "noplaylist": False, "playlistend": limite + 1})) as ydl:
            info = ydl.extract_info(purl, download=False)
        entradas = [e for e in (info.get("entries") or []) if e]
        if len(entradas) > limite:
            job.aviso = f"A playlist tem mais de {limite} itens; baixei só os primeiros {limite}."
        job.nome_lista = info.get("title") or "playlist"
        job.eh_lista = True
        itens = []
        for e in entradas[:limite]:
            u = e.get("url") or ""
            if not u.startswith("http"):
                u = f"https://www.youtube.com/watch?v={e.get('id')}"
            itens.append({"url": u, "titulo": e.get("title") or e.get("id") or "item"})
        return itens
    return [{"url": p["url"], "titulo": "", "social": p["fonte"] == "social"}]


def rodar_ytdlp(job, url, sub, extra, impersonar=False):
    motivo = []

    def filtro(info, *, incomplete=False):
        d = info.get("duration")
        if d and d > MAX_MINUTOS * 60:
            motivo.append(f"muito longo ({int(d // 60)} min; o limite é {MAX_MINUTOS} min)")
            return motivo[-1]
        return None

    def gancho(d):
        if job.cancelar:
            raise Cancelado("cancelado")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                job.pct = max(job.pct, min(99.0, d.get("downloaded_bytes", 0) * 100.0 / total))

    os.makedirs(sub, exist_ok=True)
    opts = opcoes_base(job.pasta, {
        "outtmpl": os.path.join(sub, "%(title).120s.%(ext)s"),
        "progress_hooks": [gancho],
        "match_filter": filtro,
    })
    opts.update(extra)
    if impersonar and TEM_IMPERSONATE:
        try:
            opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            pass
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return achar_arquivo(sub, motivo)


def baixar_item(job, item, sub):
    p = job.params
    if p["fonte"] == "spotify":
        url = achar_no_youtube(item)
        nome = nome_arquivo(f"{item['artista']} - {item['titulo']}").replace("%", "%%")
        extra = opcoes_audio(p["bitrate"])
        extra["outtmpl"] = os.path.join(sub, nome + ".%(ext)s")
        return rodar_ytdlp(job, url, sub, extra)
    if p["fonte"] == "youtube":
        extra = opcoes_audio(p["bitrate"]) if p["tipo"] == "audio" else opcoes_video_youtube(p["altura"])
        return rodar_ytdlp(job, item["url"], sub, extra)
    try:
        return rodar_ytdlp(job, item["url"], sub, opcoes_social(), impersonar=True)
    except Exception as e:
        if TEM_IMPERSONATE and "mpersonat" in str(e):
            shutil.rmtree(sub, ignore_errors=True)
            return rodar_ytdlp(job, item["url"], sub, opcoes_social(), impersonar=False)
        raise


def montar_zip(job, arquivos):
    pasta_final = os.path.join(job.pasta, "final")
    os.makedirs(pasta_final, exist_ok=True)
    nome = nome_arquivo(job.nome_lista or "downloads", "downloads") + ".zip"
    destino = os.path.join(pasta_final, nome)
    usados = set()
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for i, (caminho, _) in enumerate(arquivos, 1):
            base = os.path.basename(caminho)
            entrada = f"{i:02d} - {base}"
            while entrada in usados:
                entrada = "_" + entrada
            usados.add(entrada)
            z.write(caminho, entrada)
        if job.falhas or job.aviso:
            linhas = []
            if job.aviso:
                linhas.append(job.aviso + "\n")
            if job.falhas:
                linhas.append("Não consegui baixar estes itens:\n")
                linhas += [f"- {t}: {m}" for t, m in job.falhas]
            z.writestr("FALHAS.txt", "\n".join(linhas) + "\n")
    return destino, nome


def _executar(job):
    p = job.params
    itens = listar_itens(job)
    if not itens:
        raise RuntimeError("nenhum item encontrado")
    job.total = len(itens)
    job.status = "baixando"
    ok = []
    for i, item in enumerate(itens, 1):
        if job.cancelar:
            break
        job.atual, job.titulo, job.pct = i, item.get("titulo") or "", 0.0
        sub = os.path.join(job.pasta, f"item{i:03d}")
        try:
            ok.append((baixar_item(job, item, sub), item))
        except Exception as e:
            if job.cancelar:
                break
            app.logger.error("Falha em %s: %s", item.get("titulo") or p["url"], limpar_ansi(e))
            job.falhas.append((item.get("titulo") or p["url"], traduzir_erro(e)))
    if job.cancelar:
        job.status = "cancelado"
        return
    if not ok:
        msg, det = job.falhas[0][1] if job.falhas else ("Não consegui baixar.", "")
        job.status, job.erro, job.detalhe = "erro", msg, det
        return
    job.falhas = [(t, m[0] if isinstance(m, tuple) else m) for t, m in job.falhas]
    job.pct = 100.0
    if job.total == 1 and not job.eh_lista:
        job.arquivo = ok[0][0]
        job.nome = os.path.basename(job.arquivo)
    else:
        job.status = "zipando"
        job.arquivo, job.nome = montar_zip(job, ok)
        for i in range(1, job.total + 1):
            shutil.rmtree(os.path.join(job.pasta, f"item{i:03d}"), ignore_errors=True)
    job.tamanho = os.path.getsize(job.arquivo)
    job.status = "pronto"


def executar(job):
    SEM.acquire()
    try:
        if job.cancelar:
            job.status = "cancelado"
            return
        job.status = "preparando"
        _executar(job)
    except Exception as e:
        if job.cancelar:
            job.status = "cancelado"
        else:
            app.logger.exception("Erro no download")
            job.status = "erro"
            job.erro, job.detalhe = traduzir_erro(e)
    finally:
        job.finalizado = time.time()
        SEM.release()


def faxina():
    while True:
        time.sleep(60)
        agora = time.time()
        with JOBS_LOCK:
            velhos = [j for j in JOBS.values()
                      if (j.finalizado and agora - j.finalizado > VALIDADE_MIN * 60)
                      or agora - j.criado > 3 * 3600]
            for j in velhos:
                JOBS.pop(j.id, None)
        for j in velhos:
            j.cancelar = True
            shutil.rmtree(j.pasta, ignore_errors=True)


threading.Thread(target=faxina, daemon=True).start()


# --------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "index.html"))


@app.route("/saude")
def saude():
    versao = getattr(getattr(yt_dlp, "version", None), "__version__", "?")
    r = {
        "yt_dlp": versao,
        "ffmpeg": bool(FFMPEG_DIR),
        "js_runtime": bool(shutil.which("deno") or shutil.which("node")),
        "imitar_chrome": TEM_IMPERSONATE,
        "cookies": bool(COOKIES_SRC),
        "spotify_api": bool(SPOTIFY_ID and SPOTIFY_SECRET),
        "limites": {"musicas_por_zip": MAX_ITENS_AUDIO, "videos_por_zip": MAX_ITENS_VIDEO,
                    "minutos_por_item": MAX_MINUTOS},
    }
    if request.args.get("rede"):
        r["rede"] = {
            "tiktok": codigo_http("https://www.tiktok.com/"),
            "youtube": codigo_http("https://www.youtube.com/"),
            "spotify": codigo_http("https://open.spotify.com/"),
        }
    return jsonify(r)


@app.route("/api/info", methods=["POST"])
def api_info():
    if estourou(ip_do_cliente(), "info", 15):
        return jsonify(erro="Muitas buscas seguidas. Espere um minuto."), 429
    d = request.get_json(silent=True) or {}
    url = (d.get("url") or "").strip()
    fonte = classificar(url)
    if not fonte:
        return jsonify(erro="Cole um link válido do TikTok, Instagram, YouTube ou Spotify."), 400
    esperada = d.get("fonte")
    if esperada and esperada != fonte:
        return jsonify(erro="Esse link é de outro serviço.", fonte_certa=fonte), 400
    try:
        if fonte == "youtube":
            return jsonify(info_youtube(url))
        if fonte == "spotify":
            return jsonify(info_spotify(url))
        return jsonify(fonte="social")
    except ValueError as e:
        return jsonify(erro=str(e)), 400
    except Exception as e:
        app.logger.error("Falha ao buscar %s: %s", url, limpar_ansi(e))
        if fonte == "spotify":
            msg = ("Não consegui ler esse link do Spotify. O Spotify muda isso de vez em quando; "
                   "veja no LEIAME como usar a chave gratuita da API.")
            return jsonify(erro=msg, detalhe=limpar_ansi(e)[:200]), 422
        msg, det = traduzir_erro(e)
        return jsonify(erro=msg, detalhe=det), 422


@app.route("/api/jobs", methods=["POST"])
def criar_job():
    ip = ip_do_cliente()
    if estourou(ip, "job", 10):
        return jsonify(erro="Muitos pedidos seguidos. Espere um minuto."), 429
    d = request.get_json(silent=True) or {}
    url = (d.get("url") or "").strip()
    fonte = classificar(url)
    if not fonte:
        return jsonify(erro="Cole um link válido do TikTok, Instagram, YouTube ou Spotify."), 400
    tipo = "audio" if d.get("tipo") == "audio" else "video"
    try:
        altura = int(d.get("altura") or 1080)
        bitrate = int(d.get("bitrate") or 192)
    except (TypeError, ValueError):
        altura, bitrate = 1080, 192
    if altura not in ALTURAS:
        altura = 1080
    if bitrate not in BITRATES:
        bitrate = 192
    playlist = False
    sp = None
    if fonte == "spotify":
        tipo = "audio"
        m = RE_SPOTIFY.search(url)
        if not m:
            return jsonify(erro="Use o link completo de uma música, álbum ou playlist do Spotify."), 400
        sp = (m.group(1), m.group(2))
    elif fonte == "youtube":
        playlist = bool(d.get("playlist")) or eh_playlist_pura(url)
        if playlist and not _partes_youtube(url)[0]:
            playlist = False
    with JOBS_LOCK:
        ativos = sum(1 for j in JOBS.values() if j.ip == ip and j.status not in FINAIS)
        if ativos >= MAX_JOBS_POR_IP:
            return jsonify(erro="Você já tem downloads em andamento. Espere terminar."), 429
        job = Job(ip, {"url": url, "fonte": fonte, "tipo": tipo, "altura": altura,
                       "bitrate": bitrate, "playlist": playlist, "spotify": sp})
        JOBS[job.id] = job
    threading.Thread(target=executar, args=(job,), daemon=True).start()
    return jsonify(id=job.id), 202


def _job_ou_404(jid):
    with JOBS_LOCK:
        return JOBS.get(jid)


@app.route("/api/jobs/<jid>")
def status_job(jid):
    j = _job_ou_404(jid)
    if not j:
        return jsonify(erro="Download não encontrado ou expirado."), 404
    return jsonify(j.to_dict())


@app.route("/api/jobs/<jid>/cancelar", methods=["POST"])
def cancelar_job(jid):
    j = _job_ou_404(jid)
    if not j:
        return jsonify(erro="Download não encontrado ou expirado."), 404
    j.cancelar = True
    return jsonify(ok=True)


@app.route("/api/jobs/<jid>/arquivo")
def arquivo_job(jid):
    j = _job_ou_404(jid)
    if not j or j.status != "pronto" or not j.arquivo or not os.path.exists(j.arquivo):
        return jsonify(erro="Arquivo não disponível (pode ter expirado)."), 404
    return send_file(j.arquivo, as_attachment=True, download_name=j.nome, max_age=0)


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    print("\n  Baixador no ar!")
    print(f"  No computador:  http://localhost:{porta}")
    print(f"  No celular (mesmo Wi-Fi):  http://{ip_local()}:{porta}\n")
    app.run(host="0.0.0.0", port=porta, threaded=True)
