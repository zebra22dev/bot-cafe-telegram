"""Bot de promoções de café -> Telegram.

Fontes: Promobit, Gatry, canais públicos de promoções no Telegram
lojas Shopify (ex.: Moka Clube) e buscas no AliExpress.
Só avisa quando a oferta realmente compensa:
  - preço bem abaixo do menor preço encontrado no Buscapé, ou
  - menor preço que o bot já viu para aquele produto, ou
  - produto da sua lista de desejos abaixo do preço máximo.
Ofertas relâmpago ganham ⚡ na mensagem.
"""
import html, json, os, re, statistics, sys, time, unicodedata, urllib.parse
from datetime import date
import requests

PASTA = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(PASTA, "config.json")
ESTADO = os.path.join(PASTA, "estado.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept-Language": "pt-BR"}
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
PALAVRAS_VAZIAS = {"com", "para", "de", "da", "do", "das", "dos", "em", "e", "kit", "cor",
                   "preto", "preta", "branco", "branca", "inox", "110v", "127v", "220v", "bivolt"}


# ---------- utilidades ----------
def norm(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.lower()).strip()

def tem(texto, termo):
    return re.search(r"\b" + re.escape(norm(termo)) + r"\b", texto) is not None

def tokens(titulo, n=8):
    ts = [t for t in re.findall(r"[a-z0-9]+", norm(titulo)) if len(t) >= 2 and t not in PALAVRAS_VAZIAS]
    return ts[:n]

def chave_produto(titulo):
    return " ".join(sorted(tokens(titulo, 6)))

def preco_br(txt):
    """'R$ 1.299,90' -> 1299.9"""
    return float(txt.replace(".", "").replace(",", "."))

def get(url):
    return requests.get(url, headers=UA, timeout=30).text


# ---------- fontes ----------
def fonte_promobit(url):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', get(url), re.S)
    if not m:
        return []
    dados = json.loads(m.group(1))["props"]["pageProps"].get("serverOffers") or {}
    lista = dados.get("offers", []) if isinstance(dados, dict) else dados
    out = []
    for o in lista:
        if not o.get("offerId") or not o.get("offerPrice"):
            continue
        out.append({"id": f"promobit-{o['offerId']}", "titulo": o.get("offerTitle", ""),
                    "preco": float(o["offerPrice"]), "loja": o.get("storeName", ""),
                    "cupom": o.get("offerCoupon"), "texto": o.get("offerTitle", ""),
                    "link": f"https://www.promobit.com.br/oferta/{o['offerSlug']}/"})
    return out

def fonte_gatry(url):
    out = []
    for art in re.findall(r"<article>(.*?)</article>", get(url), re.S):
        t = re.search(r'<h3><a href="([^"]+)"[^>]*>([^<]+)</a>', art)
        p = re.search(r'class="price">\s*R\$\s*([\d.,]+)', art)
        i = re.search(r'href="/promocoes/(\d+)', art)
        if t and p and i:
            titulo = html.unescape(t.group(2)).strip()
            out.append({"id": f"gatry-{i.group(1)}", "titulo": titulo, "preco": preco_br(p.group(1)),
                        "loja": "", "cupom": None, "texto": titulo, "link": html.unescape(t.group(1))})
    return out

def fonte_telegram(canal):
    pagina = get(f"https://t.me/s/{canal}")
    out = []
    for bloco in re.split(r'<div class="tgme_widget_message_wrap', pagina)[1:]:
        post = re.search(r'data-post="([^"]+)"', bloco)
        corpo = re.search(r'tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', bloco, re.S)
        if not post or not corpo:
            continue
        texto = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", corpo.group(1))))
        precos = [preco_br(x) for x in re.findall(r"R\$\s*([\d.]+(?:,\d{1,2})?)", texto)]
        precos = [x for x in precos if x > 1]
        links = re.findall(r"https?://\S+", texto)
        if not precos:
            continue
        titulo = next((l.strip(" ➡️🔥⚡*-") for l in texto.splitlines() if len(l.strip()) > 8), "")
        out.append({"id": f"tg-{post.group(1)}", "titulo": titulo[:150], "preco": min(precos),
                    "loja": f"Telegram @{canal}", "cupom": None, "texto": texto,
                    "link": links[0] if links else f"https://t.me/{post.group(1)}"})
    return out


def fonte_shopify(loja):
    """Lê o catálogo de uma loja Shopify (ex.: Moka Clube) via /products.json."""
    base = loja["url"].rstrip("/")
    dominio = re.sub(r"^https?://(www\.)?", "", base)
    out, pagina = [], 1
    while pagina <= 10:
        r = requests.get(f"{base}/products.json?limit=250&page={pagina}", headers=UA, timeout=30)
        produtos = r.json().get("products", [])
        if not produtos:
            break
        for p in produtos:
            for v in p.get("variants", []):
                if not v.get("available"):
                    continue
                preco = float(v["price"])
                antes = float(v["compare_at_price"]) if v.get("compare_at_price") else None
                nome = p["title"] + ("" if v["title"] == "Default Title" else f" ({v['title']})")
                out.append({"id": f"loja-{dominio}-{v['id']}-{preco}-{antes}", "chave_loja": f"loja:{dominio}:{v['id']}",
                            "titulo": nome, "preco": preco, "preco_antes": antes, "loja": loja["nome"],
                            "cupom": None, "texto": nome, "desconto_minimo": loja.get("desconto_minimo", 15),
                            "link": f"{base}/products/{p['handle']}?variant={v['id']}"})
        pagina += 1
        time.sleep(1)
    return out


def fonte_aliexpress(busca, queda_min):
    """Busca no AliExpress Brasil (preços em R$). Avisa só em queda real de preço."""
    url = "https://pt.aliexpress.com/w/wholesale-" + urllib.parse.quote(busca.replace(" ", "-")) + ".html"
    r = requests.get(url, headers=UA, timeout=30,
                     cookies={"aep_usuc_f": "site=bra&c_tp=BRL&region=BR&b_locale=pt_BR"})
    m = re.search(r"_init_data_\s*=\s*\{\s*data:\s*", r.text)
    if not m:
        print("AliExpress não respondeu à busca:", busca)
        return []
    dados, _ = json.JSONDecoder().raw_decode(r.text[m.end():])

    def achar(o):
        if isinstance(o, dict):
            if isinstance(o.get("itemList"), dict) and "content" in o["itemList"]:
                return o["itemList"]["content"]
            o = list(o.values())
        if isinstance(o, list):
            for v in o:
                achou = achar(v)
                if achou:
                    return achou
        return None

    out = []
    for it in achar(dados) or []:
        pid = it.get("productId")
        venda = ((it.get("prices") or {}).get("salePrice") or {})
        if not pid or not venda.get("minPrice") or venda.get("currencyCode") not in (None, "BRL"):
            continue
        preco = float(venda["minPrice"])
        titulo = (it.get("title") or {}).get("displayTitle", "")
        out.append({"id": f"ali-{pid}-{preco}", "chave_loja": f"ali:{pid}", "titulo": titulo[:150],
                    "preco": preco, "preco_antes": None, "loja": "AliExpress", "cupom": None,
                    "texto": titulo, "desconto_minimo": 100, "queda_min": queda_min,
                    "link": f"https://pt.aliexpress.com/item/{pid}.html"})
    time.sleep(2)
    return out


# ---------- avaliação: compensa? ----------
_cache_mercado = {}

def menor_preco_mercado(titulo):
    """Busca o produto no Buscapé e devolve o menor preço de itens parecidos."""
    ts = tokens(titulo, 6)
    if len(ts) < 2:
        return None
    busca = " ".join(ts)
    if busca in _cache_mercado:
        return _cache_mercado[busca]
    resultado = None
    try:
        s = get("https://www.buscape.com.br/search?q=" + urllib.parse.quote(busca))
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
        hits = json.loads(m.group(1))["props"]["initialReduxState"]["hits"]["hits"] if m else []
        base = set(ts)
        precos = []
        for h in hits:
            parecido = len(base & set(tokens(h.get("name", ""), 20))) / len(base)
            if parecido >= 0.6 and h.get("price"):
                precos.append(float(h["price"]))
        if precos:
            mediana = statistics.median(precos)
            precos = [p for p in precos if p >= 0.5 * mediana]  # ignora acessórios/peças
            resultado = min(precos)
        time.sleep(1)
    except Exception as e:
        print("Buscapé falhou:", e)
    _cache_mercado[busca] = resultado
    return resultado

def avaliar_loja(oferta, cfg, historico):
    """Itens de loja: avisa se estiver em promoção na loja ou no menor preço já visto."""
    titulo = norm(oferta["titulo"])
    preco = oferta["preco"]
    if any(tem(titulo, x) for x in cfg["excluir"]):
        return None
    anterior = historico.get(oferta["chave_loja"])
    caiu = anterior is not None and preco < anterior["min"]
    novo_na_loja = anterior is None and "queda_min" not in oferta  # AliExpress: só avisa em queda
    for item in cfg["lista_de_desejos"]:
        if all(tem(titulo, t) for t in item["termos"]) and preco <= item["preco_max"] and (caiu or novo_na_loja):
            if not anterior or preco < anterior["min"]:
                historico[oferta["chave_loja"]] = {"min": preco, "data": date.today().isoformat()}
            return {"tipo": "alvo", "motivo": f"🎯 {item['nome']} abaixo de R$ {item['preco_max']:.0f}", "relampago": False}
    motivos = []
    antes = oferta.get("preco_antes")
    if antes and preco < antes * (1 - oferta["desconto_minimo"] / 100):
        motivos.append(f"🏷️ {(antes - preco) / antes * 100:.0f}% OFF na loja (era R$ {antes:.2f})")
    queda = oferta.get("queda_min", cfg["criterios"]["queda_historico_pct"])
    if anterior and preco < anterior["min"] * (1 - queda / 100):
        motivos.append(f"📉 menor preço já visto (antes R$ {anterior['min']:.2f})")
    if not anterior or preco < anterior["min"]:
        historico[oferta["chave_loja"]] = {"min": preco, "data": date.today().isoformat()}
    if motivos:
        return {"tipo": "geral", "motivo": "\n".join(motivos), "relampago": False}
    return None

def avaliar(oferta, cfg, historico):
    if "chave_loja" in oferta:
        return avaliar_loja(oferta, cfg, historico)
    texto = norm(oferta["titulo"] + " " + oferta["texto"][:300])
    titulo = norm(oferta["titulo"])
    preco = oferta["preco"]
    crit = cfg["criterios"]

    if any(tem(titulo, x) for x in cfg["excluir"]):
        return None

    relampago = any(tem(texto, x) for x in cfg["palavras_relampago"])

    for item in cfg["lista_de_desejos"]:
        if all(tem(titulo, t) for t in item["termos"]) and preco <= item["preco_max"]:
            return {"tipo": "alvo", "motivo": f"🎯 {item['nome']} abaixo de R$ {item['preco_max']:.0f}",
                    "relampago": relampago}

    if not any(tem(titulo, x) for x in cfg["palavras_chave"]):
        return None

    motivos = []
    chave = chave_produto(oferta["titulo"])
    anterior = historico.get(chave)
    if anterior and preco < anterior["min"] * (1 - crit["queda_historico_pct"] / 100):
        motivos.append(f"📉 menor preço já visto (antes R$ {anterior['min']:.2f})")

    mercado = menor_preco_mercado(oferta["titulo"])
    if mercado:
        dif = (mercado - preco) / mercado * 100
        if dif >= crit["abaixo_do_mercado_pct"]:
            motivos.append(f"💰 {dif:.0f}% abaixo do menor preço no Buscapé (R$ {mercado:.2f})")

    if not anterior or preco < anterior["min"]:
        historico[chave] = {"min": preco, "data": date.today().isoformat()}

    if motivos or (relampago and crit["avisar_relampago_sem_comparacao"] and mercado is None):
        return {"tipo": "geral", "motivo": "\n".join(motivos) or "⚡ relâmpago (sem preço de referência)",
                "relampago": relampago}
    return None


# ---------- envio (Telegram) ----------
def esc(t):
    return html.escape(str(t or ""), quote=False)

def formatar(o, av):
    p = f"R$ {o['preco']:.2f}".replace(".", ",")
    topo = ("⚡ <b>RELÂMPAGO</b> ⚡\n" if av["relampago"] else "") + ("☕ " if av["tipo"] == "geral" else "")
    cupom = f"\nCupom: <code>{esc(o['cupom'])}</code>" if o.get("cupom") else ""
    loja = f" – {esc(o['loja'])}" if o.get("loja") else ""
    return (f"{topo}<b>{esc(o['titulo'])}</b>\n{p}{loja}{cupom}\n{esc(av['motivo'])}\n"
            f'<a href="{esc(o["link"])}">Ver oferta</a>')

def descobrir_chat(estado):
    """Acha sozinho o seu chat: basta mandar /start para o bot uma vez."""
    if os.environ.get("TELEGRAM_CHAT_ID"):
        return os.environ["TELEGRAM_CHAT_ID"]
    if estado.get("chat_id"):
        return estado["chat_id"]
    if not TOKEN:
        return None
    try:
        r = requests.get(f"{API}/getUpdates", timeout=30).json()
        chats = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
        if chats:
            estado["chat_id"] = chats[-1]
            enviar("✅ Bot de promoções de café conectado! Vou te avisar quando aparecer algo que compensa.", chats[-1])
            return chats[-1]
        print("Nenhuma mensagem recebida ainda: mande /start para o seu bot no Telegram.")
    except Exception as e:
        print("Erro ao buscar chat:", e)
    return None

def enviar(texto, chat):
    if not TOKEN or not chat:
        print("[teste – sem Telegram configurado]\n" + texto + "\n")
        return
    r = requests.post(f"{API}/sendMessage", timeout=30,
                      data={"chat_id": chat, "text": texto, "parse_mode": "HTML"})
    print("Telegram:", r.status_code, "" if r.ok else r.text[:200])
    time.sleep(1)


def main():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    primeira = not os.path.exists(ESTADO)
    estado = {"vistos": [], "historico": {}} if primeira else json.load(open(ESTADO, encoding="utf-8"))
    ja_vi = set(estado["vistos"])
    chat = descobrir_chat(estado)

    coletadas = []
    fontes = ([(fonte_promobit, u) for u in cfg["fontes"]["promobit"]] +
              [(fonte_gatry, u) for u in cfg["fontes"]["gatry"]] +
              [(fonte_telegram, c) for c in cfg["fontes"]["telegram"]] +
              [(fonte_shopify, l) for l in cfg["fontes"].get("lojas_shopify", [])])
    ali = cfg["fontes"].get("aliexpress", {})
    fontes += [(lambda b: fonte_aliexpress(b, ali.get("queda_minima_pct", 15)), b) for b in ali.get("buscas", [])]
    for funcao, alvo in fontes:
        try:
            coletadas += funcao(alvo)
        except Exception as e:
            print("Erro em", alvo.get("nome") if isinstance(alvo, dict) else alvo, "->", e)

    mensagens, repetidas = [], set()
    for o in coletadas:
        if o["id"] in ja_vi:
            continue
        ja_vi.add(o["id"]); estado["vistos"].append(o["id"])
        av = avaliar(o, cfg, estado["historico"])
        marca = (chave_produto(o["titulo"]), round(o["preco"]))
        if av and not primeira and marca not in repetidas:
            repetidas.add(marca)
            mensagens.append((0 if av["relampago"] or av["tipo"] == "alvo" else 1, formatar(o, av)))

    mensagens.sort(key=lambda x: x[0])  # relâmpago e lista de desejos primeiro
    textos = [m for _, m in mensagens]
    for t in textos:
        enviar(t, chat)

    estado["vistos"] = estado["vistos"][-5000:]
    json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(coletadas)} coletadas, {len(textos)} enviadas" + (" (1ª execução: só aprendendo)" if primeira else ""))


if __name__ == "__main__":
    sys.exit(main())
