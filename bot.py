"""Bot de promoções de café -> Telegram.

Fontes: Promobit, Gatry, canais públicos de promoções no Telegram
dezenas de lojas e torrefações (Shopify, Nuvemshop, WooCommerce, VTEX)
e buscas no AliExpress, KaBuM! e lojas VTEX (Americanas, WebContinental...).
Também manda um radar diário com o menor preço das máquinas que você está de olho.
Só avisa quando a oferta realmente compensa:
  - preço bem abaixo do menor preço encontrado no Buscapé, ou
  - menor preço que o bot já viu para aquele produto, ou
  - produto da sua lista de desejos abaixo do preço máximo.
Ofertas relâmpago ganham ⚡ na mensagem.
"""
import html, json, os, re, statistics, sys, time, unicodedata, urllib.parse
from datetime import date, datetime, timedelta, timezone
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


def fonte_vtex_busca(loja, busca):
    """Busca numa loja VTEX (Americanas, WebContinental...). Avisa só em queda real de preço."""
    base, pref = loja["url"].rstrip("/"), loja["prefixo"]
    url = f"{base}/api/catalog_system/pub/products/search?ft={urllib.parse.quote(busca)}&_from=0&_to=49"
    out = []
    for p in requests.get(url, headers=UA, timeout=30).json():
        for it in p.get("items", [])[:1]:
            vendedores = [s["commertialOffer"] for s in it.get("sellers", [])
                          if s.get("commertialOffer", {}).get("AvailableQuantity", 0) > 0
                          and s["commertialOffer"].get("Price")]
            if not vendedores:
                continue
            preco = float(min(v["Price"] for v in vendedores))
            out.append({"id": f"{pref}-{p['productId']}-{preco}", "chave_loja": f"{pref}:{p['productId']}",
                        "titulo": p.get("productName", "")[:150], "preco": preco, "preco_antes": None,
                        "loja": loja["nome"], "cupom": None, "texto": p.get("productName", ""),
                        "desconto_minimo": 100, "queda_min": loja.get("queda_minima_pct", 10),
                        "link": p.get("link", "")})
    time.sleep(1)
    return out


def fonte_kabum(busca, queda_min):
    """Busca no KaBuM! (preço no PIX). Ofertas Ninja ganham ⚡."""
    s = get("https://www.kabum.com.br/busca/" + urllib.parse.quote(busca.replace(" ", "-")))
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m:
        return []
    dados = json.loads(m.group(1))["props"]["pageProps"].get("data")
    if isinstance(dados, str):
        dados = json.loads(dados)
    out = []
    for p in ((dados or {}).get("catalogServer") or {}).get("data", []):
        preco = p.get("priceWithDiscount") or p.get("price")
        if not p.get("available") or not preco:
            continue
        preco = float(preco)
        out.append({"id": f"kabum-{p['code']}-{preco}", "chave_loja": f"kabum:{p['code']}",
                    "titulo": p.get("name", "")[:150], "preco": preco, "preco_antes": None,
                    "loja": "KaBuM! (PIX)", "cupom": None, "texto": p.get("name", ""),
                    "desconto_minimo": 100, "queda_min": queda_min, "relampago": bool(p.get("offer")),
                    "link": f"https://www.kabum.com.br/produto/{p['code']}/{p.get('friendlyName', '')}"})
    time.sleep(1)
    return out


def fonte_loja(loja):
    """Lê o catálogo inteiro de uma loja (Shopify, Nuvemshop, WooCommerce ou VTEX)."""
    funcoes = {"shopify": fonte_shopify, "nuvemshop": fonte_nuvemshop,
               "woocommerce": fonte_woocommerce, "vtex": fonte_vtex}
    return funcoes[loja.get("plataforma", "shopify")](loja)


def _item_loja(loja, pid, titulo, preco, antes, link):
    base = loja["url"].rstrip("/")
    dominio = re.sub(r"^https?://(www\.)?", "", base)
    return {"id": f"loja-{dominio}-{pid}-{preco}-{antes}", "chave_loja": f"loja:{dominio}:{pid}",
            "titulo": titulo[:150], "preco": preco, "preco_antes": antes, "loja": loja["nome"],
            "cupom": None, "texto": titulo, "desconto_minimo": loja.get("desconto_minimo", 15), "link": link}


def fonte_nuvemshop(loja):
    base = loja["url"].rstrip("/")
    out, vistos = [], set()
    for pagina in range(1, 11):
        s = get(f"{base}/produtos/" + (f"page/{pagina}/" if pagina > 1 else ""))
        novos = 0
        for b in re.split(r'(?=<div[^>]+class="[^"]*js-item-product)', s)[1:]:
            pid = re.search(r'data-product-id="(\d+)"', b)
            var = re.search(r'data-variants="([^"]+)"', b)
            if not pid or not var or pid.group(1) in vistos:
                continue
            vistos.add(pid.group(1)); novos += 1
            try:
                variantes = [v for v in json.loads(html.unescape(var.group(1)))
                             if v.get("available") and v.get("price_number")]
            except Exception:
                continue
            if not variantes:
                continue
            v = min(variantes, key=lambda x: x["price_number"])
            link = re.search(r'href="([^"]*/produtos/[^"]+)"', b)
            nome = re.search(r'title="([^"]+)"', b) or re.search(r'js-item-name[^>]*>([^<]+)<', b)
            antes = v.get("compare_at_price_number")
            out.append(_item_loja(loja, pid.group(1), html.unescape(nome.group(1)).strip() if nome else "",
                                  float(v["price_number"]), float(antes) if antes else None,
                                  html.unescape(link.group(1)) if link else base))
        if novos == 0:
            break
        time.sleep(1)
    return out


def fonte_woocommerce(loja):
    base = loja["url"].rstrip("/")
    out = []
    for pagina in range(1, 11):
        r = requests.get(f"{base}/wp-json/wc/store/v1/products?per_page=100&page={pagina}", headers=UA, timeout=30)
        produtos = r.json() if r.ok else []
        if not produtos:
            break
        for p in produtos:
            pr = p.get("prices") or {}
            if not p.get("is_in_stock") or not pr.get("price"):
                continue
            div = 10 ** int(pr.get("currency_minor_unit", 2))
            preco = int(pr["price"]) / div
            regular = int(pr["regular_price"]) / div if pr.get("regular_price") else None
            out.append(_item_loja(loja, p["id"], html.unescape(p.get("name", "")), preco,
                                  regular if regular and regular > preco else None, p.get("permalink", base)))
        time.sleep(1)
    return out


def fonte_vtex(loja):
    base = loja["url"].rstrip("/")
    out = []
    for inicio in range(0, 1000, 50):
        r = requests.get(f"{base}/api/catalog_system/pub/products/search?_from={inicio}&_to={inicio + 49}",
                         headers=UA, timeout=30)
        produtos = r.json() if r.status_code in (200, 206) else []
        if not produtos:
            break
        for p in produtos:
            ofertas = [s["commertialOffer"] for it in p.get("items", [])[:1] for s in it.get("sellers", [])
                       if s.get("commertialOffer", {}).get("AvailableQuantity", 0) > 0 and s["commertialOffer"].get("Price")]
            if not ofertas:
                continue
            o = min(ofertas, key=lambda x: x["Price"])
            antes = o.get("ListPrice") if o.get("ListPrice", 0) > o["Price"] else None
            out.append(_item_loja(loja, p["productId"], p.get("productName", ""), float(o["Price"]),
                                  float(antes) if antes else None, p.get("link", base)))
        time.sleep(1)
    return out


# ---------- avaliação: compensa? ----------
_cache_mercado = {}

def bate_desejo(item, titulo, preco):
    """O título tem todos os termos, nenhum termo excluído e o preço está na faixa?"""
    return (all(tem(titulo, t) for t in item["termos"])
            and not any(tem(titulo, x) for x in item.get("excluir", []))
            and item.get("preco_min", 0) <= preco <= item["preco_max"])

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
    """Itens de loja: avisa quando entra em promoção, cai de preço ou bate a lista de desejos.
    Na primeira vez que o bot vê uma loja, ele só aprende os preços (sem avisar)."""
    titulo = norm(oferta["titulo"])
    preco = oferta["preco"]
    chave = oferta["chave_loja"]
    anterior = historico.get(chave)
    ultimo = (anterior or {}).get("ult", (anterior or {}).get("min"))
    if not anterior or preco < anterior["min"]:
        historico[chave] = {"min": preco, "data": date.today().isoformat()}
    historico[chave]["ult"] = preco
    if any(tem(titulo, x) for x in cfg["excluir"]):
        return None
    caiu = ultimo is not None and preco < ultimo
    if "queda_min" in oferta:  # AliExpress/Americanas/KaBuM!: só quedas grandes contam
        caiu = anterior is not None and preco < anterior["min"] * (1 - oferta["queda_min"] / 100)
    novo_na_loja = anterior is None and not oferta.get("loja_nova") and "queda_min" not in oferta
    for item in cfg["lista_de_desejos"]:
        if bate_desejo(item, titulo, preco) and (caiu or novo_na_loja):
            return {"tipo": "alvo", "motivo": f"🎯 {item['nome']} abaixo de R$ {item['preco_max']:.0f}", "relampago": False}
    motivos = []
    antes = oferta.get("preco_antes")
    if caiu and antes and preco < antes * (1 - oferta["desconto_minimo"] / 100):
        motivos.append(f"🏷️ entrou em promoção: {(antes - preco) / antes * 100:.0f}% OFF (era R$ {antes:.2f})")
    queda = oferta.get("queda_min", cfg["criterios"]["queda_historico_pct"])
    if anterior and preco < anterior["min"] * (1 - queda / 100):
        motivos.append(f"📉 menor preço já visto (antes R$ {anterior['min']:.2f})")
    if motivos:
        return {"tipo": "geral", "motivo": "\n".join(motivos), "relampago": oferta.get("relampago", False)}
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
        if bate_desejo(item, titulo, preco):
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


# ---------- radar diário (Black Friday) ----------
def atualizar_radar(coletadas, cfg, estado):
    """Guarda o preço atual de cada produto que bate com os modelos do radar."""
    hoje = date.today().isoformat()
    radar = estado.setdefault("radar", {})
    for modelo in cfg.get("radar", []):
        r = radar.setdefault(modelo["nome"], {"itens": {}, "min": None})
        for o in coletadas:
            titulo = norm(o["titulo"])
            if (all(tem(titulo, t) for t in modelo["termos"])
                    and not any(tem(titulo, x) for x in modelo.get("excluir", []))
                    and o["preco"] >= modelo.get("preco_min", 0)):
                r["itens"][o.get("chave_loja") or o["id"]] = {
                    "preco": o["preco"], "titulo": o["titulo"], "loja": o.get("loja", ""),
                    "link": o["link"], "dia": hoje}
                if not r["min"] or o["preco"] < r["min"]["preco"]:
                    r["min"] = {"preco": o["preco"], "loja": o.get("loja", ""), "data": hoje}
        limite = (date.today() - timedelta(days=2)).isoformat()
        r["itens"] = {k: v for k, v in r["itens"].items() if v["dia"] >= limite}

def resumo_radar(cfg, estado):
    """Uma vez por dia (a partir das 9h de Brasília), monta o resumo do radar."""
    agora = datetime.now(timezone(timedelta(hours=-3)))
    if not cfg.get("radar") or agora.hour < 9 or estado.get("radar_enviado") == agora.date().isoformat():
        return None
    estado["radar_enviado"] = agora.date().isoformat()
    linhas = ["📊 Radar de máquinas – menor preço nas fontes do bot"]
    for modelo in cfg["radar"]:
        r = estado.get("radar", {}).get(modelo["nome"], {})
        itens = sorted(r.get("itens", {}).values(), key=lambda x: x["preco"])
        if not itens:
            linhas.append(f"\n• {modelo['nome']}: ainda não apareceu")
            continue
        m = itens[0]
        txt = f"\n• {modelo['nome']}: R$ {m['preco']:.2f} – {m['loja']}\n  {m['link']}"
        if r.get("min"):
            txt += f"\n  menor já visto: R$ {r['min']['preco']:.2f} ({r['min']['loja']}, {r['min']['data']})"
        linhas.append(txt)
    return "\n".join(linhas)


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
              [(fonte_loja, l) for l in cfg["fontes"].get("lojas", [])])
    ali = cfg["fontes"].get("aliexpress", {})
    fontes += [(lambda b: fonte_aliexpress(b, ali.get("queda_minima_pct", 15)), b) for b in ali.get("buscas", [])]
    for loja in cfg["fontes"].get("buscas_vtex", []):
        fontes += [(lambda b, loja=loja: fonte_vtex_busca(loja, b), b) for b in loja.get("buscas", [])]
    kab = cfg["fontes"].get("kabum", {})
    fontes += [(lambda b: fonte_kabum(b, kab.get("queda_minima_pct", 10)), b) for b in kab.get("buscas", [])]
    for funcao, alvo in fontes:
        try:
            coletadas += funcao(alvo)
        except Exception as e:
            print("Erro em", alvo.get("nome") if isinstance(alvo, dict) else alvo, "->", e)

    conhecidas = set(estado.setdefault("lojas_conhecidas", []))
    for o in coletadas:
        if "chave_loja" in o:
            o["loja_nova"] = o["chave_loja"].rsplit(":", 1)[0] not in conhecidas
    estado["lojas_conhecidas"] = sorted(conhecidas | {o["chave_loja"].rsplit(":", 1)[0]
                                                      for o in coletadas if "chave_loja" in o})

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

    atualizar_radar(coletadas, cfg, estado)
    resumo = None if primeira else resumo_radar(cfg, estado)

    mensagens.sort(key=lambda x: x[0])  # relâmpago e lista de desejos primeiro
    textos = [m for _, m in mensagens]
    for t in textos:
        enviar(t, chat)
    if resumo:
        enviar(html.escape(resumo, quote=False), chat)

    estado["vistos"] = estado["vistos"][-20000:]
    json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(coletadas)} coletadas, {len(textos)} enviadas" + (" (1ª execução: só aprendendo)" if primeira else ""))


if __name__ == "__main__":
    sys.exit(main())
