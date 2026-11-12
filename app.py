import os
import re
import json
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import pandas as pd
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# =========================================================================
# CONFIGURAÇÕES GLOBAIS E CONSTANTES
# =========================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URL_SISTEMA = "https://texcoprd.seniorcloud.com.br/siltwms"
PASTA_CACHE_CHROMIUM = os.path.join("/tmp", "Playwright_Senior_Chromium")
ARQUIVO_USUARIOS = os.path.join(BASE_DIR, "usuarios_inventario.json")

DEPOSITANTES_DINAMICO_BASE = [
    "ALYMENTE BENEFÍCIOS E SIMILARES LTDA",
    "REDECARD",
    "FORMA TURISMO",
    "OFERTA ATIVA COMERCIO ELETRONICO GERAL LTDA",
    "CONECTCAR INSTITUICAO DE PAGAMENTO",
    "PLENA GESTAO EMPRESARIAL E FINANCEIRA LTDA",
    "RAIO BENEFICIOS LTDA",
    "TELEFONICA BRASIL S.A.",
    "ITAU UNIBANCO S.A",
]

DEPOSITANTES_TERCEIRIZADO = [
    "TELEFONICA BRASIL S.A.",
    "TEX COURIER S.A | DEVOLUÇÃO JDU",
    "TEX COURIER S.A | DEVOLUÇÃO ETR",
]

LISTA_GERAL_ARMAZENS = [
    "TOTAL EXPRESS - JUNDIAI",
    "TOTAL EXPRESS - EXTREMA",
    "TOTAL EXPRESS - CONTAGEM - BHZ",
    "TOTAL EXPRESS - BARUERI",
    "TOTAL EXPRESS - RIO DE JANEIRO",
    "TOTAL EXPRESS - TZX",
]

LISTA_USUARIOS_PADRAO = [
    "574534 - Melyssa",
    "549134 - Ingrid",
    "530991 - Cláudia",
    "539019 - Daniel Mbembi",
    "553383 - Elisama",
    "554600 - Renato",
    "540956 - Jhonatan",
    "ALEX.SOUTO - Alex",
    "576314 - Daniel Nasci",
]

# Estado global do servidor
ESTADO = {
    "robo_ativo": False,
    "status_texto": "● Sistema Pronto",
    "parar_inv": False,
    "parar_subtipo": False,
    "parar_vincular": False,
    "logs_inv": [],
    "logs_sub": [],
    "logs_vinc": [],
    "nao_encontrados_vinc": [],
    "proximo_codigo_sub": 60,
}

# =========================================================================
# FUNÇÕES UTILITÁRIAS COMPARTILHADAS
# =========================================================================
def carregar_lista_usuarios():
    try:
        if os.path.exists(ARQUIVO_USUARIOS):
            with open(ARQUIVO_USUARIOS, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if isinstance(dados, list) and dados:
                    return dados
    except Exception:
        pass
    salvar_lista_usuarios(LISTA_USUARIOS_PADRAO)
    return list(LISTA_USUARIOS_PADRAO)


def salvar_lista_usuarios(lista):
    try:
        with open(ARQUIVO_USUARIOS, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def extrair_codigo_usuario(texto_selecionado: str) -> str:
    if not texto_selecionado or texto_selecionado in ["N/A", "(Nenhum)", "(Vazio)", ""]:
        return ""
    if " - " in texto_selecionado:
        return texto_selecionado.split(" - ")[0].strip()
    return texto_selecionado.strip()


def limpar_lockfile():
    try:
        if os.path.exists(PASTA_CACHE_CHROMIUM):
            for item in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
                caminho = os.path.join(PASTA_CACHE_CHROMIUM, item)
                if os.path.exists(caminho):
                    os.remove(caminho)
    except Exception:
        pass


def extrair_padrao_subtipo(descricao: str) -> str:
    if not isinstance(descricao, str) or not descricao.strip():
        return ""
    texto = descricao.strip().upper()
    match_pol = re.search(r'(\d+\.\d{2}(?:-[rR]?\d{2}[A-Z]?))', texto)
    if match_pol:
        return match_pol.group(1)
    match_barra = re.search(r'(\d{2,3}/(?:\d{2,3})?(?:-[rR]|[rR]|/|\s*-\s*)?\d{2}(?:\.\d)?[A-Z]*)', texto)
    if match_barra:
        res = match_barra.group(1).replace(" ", "")
        res = re.sub(r'[VvZz][rR]', 'R', res)
        return res
    return ""


def extrair_numero_inicial(descricao: str) -> str:
    if not isinstance(descricao, str):
        return ""
    desc = descricao.strip()
    padroes = [
        r"^(\d{2,3}/\d{2,3}\s*[A-Z]{0,2}[R\-–]\s*\d{2})",
        r"^(\d{2,3}/[R\-–]\d{2})",
        r"^(\d{5,6}[R\-–]\d{2})",
        r"^(\d{2,3}/\d{4})",
        r"^(\d{1,2}\.\d{1,2}[\-–]\d{2})",
        r"^(\d{3}[\-–]\d{2})",
        r"^(\d{2}X\d{1,2}[\-–]\d{2})",
        r"^(\d{2}X\d+\.\d+)",
        r"^(\d{2}-\d+)",
        r"^(\d{2,3}/\d{2,3}\b)",
    ]
    for pat in padroes:
        match = re.match(pat, desc, re.IGNORECASE)
        if match:
            medida = match.group(1).replace(" ", "")
            if medida == "17570R14":
                return "175/70R14"
            if medida == "205/5516":
                return "205/55R16"
            if "26X2.00" in medida:
                return "26X2.00"
            if "54-622" in medida:
                return "29X2.10"
            if medida.startswith("90/90-12"):
                return "90/90-12"
            return medida
    partes = desc.split()
    return partes[0] if partes else ""


def add_log(canal, msg):
    ESTADO[canal].append(f">> {msg}")


# =========================================================================
# ROTAS WEB (FLASK)
# =========================================================================
@app.route("/")
def index():
    return render_template(
        "index.html",
        url_sistema=URL_SISTEMA,
        armazens=LISTA_GERAL_ARMAZENS,
        dep_dinamico=DEPOSITANTES_DINAMICO_BASE,
        dep_terceirizado=DEPOSITANTES_TERCEIRIZADO,
        usuarios=carregar_lista_usuarios(),
    )


@app.route("/api/status")
def api_status():
    return jsonify(ESTADO)


@app.route("/api/stop", methods=["POST"])
def api_stop():
    aba = request.json.get("aba")
    if aba == "inv":
        ESTADO["parar_inv"] = True
        add_log("logs_inv", "🛑 Solicitando parada imediata (STOP)...")
    elif aba == "sub":
        ESTADO["parar_subtipo"] = True
        add_log("logs_sub", "⚠️ Interrupção solicitada. Finalizando após o item atual...")
    elif aba == "vinc":
        ESTADO["parar_vincular"] = True
        add_log("logs_vinc", "⚠️ Interrupção solicitada. Finalizando após o item atual...")
    return jsonify({"ok": True})


@app.route("/api/usuarios", methods=["POST"])
def api_adicionar_usuario():
    novo_id = request.json.get("id", "").strip()
    novo_nome = request.json.get("nome", "").strip()
    if not novo_id or not novo_nome:
        return jsonify({"ok": False, "msg": "Preencha ID e Nome."})
    lista = carregar_lista_usuarios()
    reg = f"{novo_id} - {novo_nome}"
    if reg not in lista:
        lista.append(reg)
        salvar_lista_usuarios(lista)
    return jsonify({"ok": True, "usuarios": lista, "novo": reg})


@app.route("/api/subtipo/extrair", methods=["POST"])
def api_subtipo_extrair():
    linhas = request.json.get("texto", "").splitlines()
    filtrados = []
    duplicados = 0
    for linha in linhas:
        medida = extrair_padrao_subtipo(linha)
        if medida:
            if medida not in filtrados:
                filtrados.append(medida)
            else:
                duplicados += 1
    return jsonify({"ok": True, "itens": filtrados, "duplicados": duplicados})


# =========================================================================
# WORKER 1: INVENTÁRIO (CHROMIUM HEADLESS NO RENDER)
# =========================================================================
@app.route("/api/iniciar/inventario", methods=["POST"])
def iniciar_inventario():
    if ESTADO["robo_ativo"]:
        return jsonify({"ok": False, "msg": "Já existe uma automação em execução!"})

    dados = request.json
    dados["usr1"] = extrair_codigo_usuario(dados.get("usr1", ""))
    dados["usr2"] = extrair_codigo_usuario(dados.get("usr2", ""))

    ESTADO["robo_ativo"] = True
    ESTADO["parar_inv"] = False
    ESTADO["logs_inv"] = []
    ESTADO["status_texto"] = "● Executando: Inventário (Chromium)"

    threading.Thread(target=_executar_automacao_inventario, args=(dados,), daemon=True).start()
    return jsonify({"ok": True})


def _inv_verificar_parada():
    if ESTADO["parar_inv"]:
        raise Exception("Processo interrompido pelo usuário (STOP acionado).")


def _inv_obter_contexto_tabela(page):
    _inv_verificar_parada()
    for ctx in [page] + page.frames:
        _inv_verificar_parada()
        try:
            if ctx.locator("input[id*='filter-'], input[columnid*='ID'], .slick-row, .x-grid3-row").count() > 0:
                return ctx
        except Exception:
            continue
    return page


def _inv_selecionar_endereco(page):
    _inv_verificar_parada()
    janela_local = page.locator(".x-window").filter(has_text="Local").last
    janela_local.wait_for(state="visible", timeout=30000)
    page.wait_for_timeout(800)

    campo_busca = janela_local.locator("input.x-form-text:not([readonly])").first
    if not campo_busca.is_visible():
        campo_busca = janela_local.locator("input.x-form-text").last

    campo_busca.wait_for(state="visible", timeout=15000)
    campo_busca.click()
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)

    if janela_local.locator(".x-grid3-row").count() == 0:
        btn_refresh = janela_local.locator(".x-tbar-loading, button.x-tbar-loading").first
        if btn_refresh.is_visible():
            btn_refresh.click(force=True)
            page.wait_for_timeout(2000)

    linha_endereco = janela_local.locator(".x-grid3-row").first
    linha_endereco.wait_for(state="visible", timeout=60000)
    linha_endereco.scroll_into_view_if_needed()
    linha_endereco.click(force=True)
    page.wait_for_timeout(800)

    btn_selecionar = janela_local.locator("#SearchTriggerWindowRemote_selectButton, button:has-text('Selecionar')").first
    btn_selecionar.click()
    page.wait_for_timeout(1500)


def _executar_automacao_inventario(dados):
    tipo_inv = dados["tipo_inventario"]
    add_log("logs_inv", f"Iniciando Chromium no servidor Render para {tipo_inv}...")
    limpar_lockfile()

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=PASTA_CACHE_CHROMIUM,
                headless=True,
                viewport={"width": 1366, "height": 768},
                args=["--ignore-certificate-errors", "--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()

            try:
                _inv_verificar_parada()
                add_log("logs_inv", f"Acessando {URL_SISTEMA}...")
                page.goto(URL_SISTEMA, wait_until="domcontentloaded", timeout=120000)

                campo_login = page.locator("#LoginDialog_loginText")
                campo_login.wait_for(state="visible", timeout=120000)
                campo_login.fill(dados["usuario"])
                page.locator("#LoginDialog_passwordText").fill(dados["senha"])
                page.wait_for_timeout(500)

                add_log("logs_inv", f"Selecionando armazém: {dados['armazem']}...")
                page.locator("#LoginDialog_armazemComboBox, .x-form-trigger-arrow").first.click()
                page.wait_for_timeout(800)
                opcao_armazem = page.locator(".x-combo-list-item").filter(has_text=dados["armazem"]).first
                opcao_armazem.wait_for(state="visible", timeout=15000)
                opcao_armazem.click()
                page.wait_for_timeout(500)

                page.locator("#LoginDialog_loginButton").click()
                add_log("logs_inv", "Aguardando tela principal...")

                pasta_inv = page.locator(".x-tree3-node-text").filter(has_text="Inventário").first
                pasta_inv.wait_for(state="visible", timeout=120000)
                pasta_inv.dblclick()
                page.wait_for_timeout(1000)

                item_inv = page.get_by_text("Inventário", exact=True).last
                item_inv.wait_for(state="visible", timeout=60000)
                item_inv.dblclick()
                page.wait_for_timeout(1500)

                data_hoje_terc = datetime.now().strftime("%m.%d.%y")
                data_hoje_din = datetime.now().strftime("%Y%m%d")
                alvo_frame = _inv_obter_contexto_tabela(page)

                if tipo_inv == "Inventário Terceirizado":
                    termo_serial1 = f"REV_SERIAL1_{data_hoje_terc}"
                    tem_serial1 = alvo_frame.locator(".slick-row, .x-grid3-row").filter(has_text=termo_serial1).count() > 0
                    nome_final = f"REV_SERIAL2_{data_hoje_terc}" if tem_serial1 else termo_serial1
                else:
                    dep_clean = dados["depositante"].replace(" LTDA", "").replace(" S.A.", "").replace(" GERAL", "").replace(" S.A", "").strip()
                    palavras = dep_clean.split()
                    tag = palavras[0] if len(palavras) == 1 else f"{palavras[0]}_{palavras[1]}"
                    nome_base = f"INV_{tag}_{data_hoje_din}".upper()
                    tem_inv1 = alvo_frame.locator(".slick-row, .x-grid3-row").filter(has_text=nome_base).count() > 0
                    nome_final = f"{nome_base}_2" if tem_inv1 else nome_base

                add_log("logs_inv", f"Cadastrando: {nome_final}...")
                page.locator("#tb-Controle-Cadastrar").click()
                campo_desc = page.locator("#InventarioDadosScreenDescriptor_descricao")
                campo_desc.wait_for(state="visible", timeout=60000)
                campo_desc.fill(nome_final)
                page.wait_for_timeout(500)

                modal_cad = page.locator(".x-window").filter(has_text="Cadastro").last
                if tipo_inv == "Inventário Terceirizado":
                    modal_cad.locator(".x-tree3-node-text").filter(has_text="Inventário Terceirizado").first.click()
                    page.wait_for_timeout(1000)
                    lbl_terceirizado = modal_cad.get_by_text("Terceirizado", exact=True).last
                    caixa_box = lbl_terceirizado.bounding_box()
                    if caixa_box:
                        page.mouse.click(caixa_box["x"] - 12, caixa_box["y"] + (caixa_box["height"] / 2))
                    else:
                        lbl_terceirizado.click(force=True)
                    page.wait_for_timeout(800)
                    campo_empresa = modal_cad.locator("input.x-form-text:not([readonly])").last
                    campo_empresa.fill("TOTAL EXPRESS")
                else:
                    modal_cad.locator(".x-tree3-node-text").filter(has_text="Configurações").first.click()
                    page.wait_for_timeout(1000)
                    page.evaluate("document.querySelectorAll('.x-window-body').forEach(c => { c.scrollTop = 1200; });")
                    page.wait_for_timeout(800)
                    lbl_dinamico = modal_cad.get_by_text("Inventário Dinâmico", exact=True).last
                    caixa_box = lbl_dinamico.bounding_box()
                    if caixa_box:
                        page.mouse.click(caixa_box["x"] - 12, caixa_box["y"] + (caixa_box["height"] / 2))
                    else:
                        lbl_dinamico.click(force=True)
                    page.wait_for_timeout(800)
                    page.locator("#WmsMessageBoxCONFIRM_YES_BUTTON, button:has-text('Sim')").first.click(force=True)
                    page.wait_for_timeout(1000)
                    modal_cad.locator(".x-tree3-node-text").filter(has_text="Inventário Dinâmico").first.click()
                    page.wait_for_timeout(1000)
                    page.locator("#InventarioLocalConciliacaoScreenDescriptor_idLocalConciliacaoFalta-input").click()
                    _inv_selecionar_endereco(page)
                    page.locator("#InventarioLocalConciliacaoScreenDescriptor_idLocalConciliacaoSobra-input").click()
                    _inv_selecionar_endereco(page)

                modal_cad.locator("#CadastroWindow_salvarCadastrodeInventárioButton, button:has-text('Salvar')").first.click()
                page.wait_for_timeout(2500)

                # Filtrar e selecionar
                alvo_frame = _inv_obter_contexto_tabela(page)
                campo_filtro = alvo_frame.locator("input#filter-DESCRICAO, input[columnid='DESCRICAO']").first
                campo_filtro.click(force=True)
                campo_filtro.fill(nome_final)
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)
                linha = alvo_frame.locator(".grid-canvas .slick-row, .slick-row, .x-grid3-row").first
                linha.click(force=True)
                page.wait_for_timeout(1000)

                # Vincular Depositante
                add_log("logs_inv", f"Vinculando depositante: {dados['depositante']}...")
                page.locator("#tb-Planejamento-VincularDepositantes").click()
                page.wait_for_timeout(1000)
                dep_nome = dados["depositante"].strip()
                termo_busca = f"%{dep_nome.split('|')[-1].strip()}%" if "|" in dep_nome else f"%{dep_nome.split()[0]}%"
                campo_busca_dep = page.locator("#SiltTransfere_buscarText")
                campo_busca_dep.fill(termo_busca)
                page.locator("#SiltTransfere_buscarButton").click()
                page.wait_for_timeout(1500)
                linha_alvo = page.locator(".x-grid3-row").filter(has_text=dep_nome)
                if linha_alvo.count() > 0:
                    linha_alvo.first.locator("img, .x-grid3-row-checker").first.click()
                else:
                    page.locator(".x-grid3-row img").first.click()
                page.locator("#SiltTransfere_fecharButton").click()
                page.wait_for_timeout(1500)

                # Re-selecionar e vincular usuários
                linha.click(force=True)
                page.wait_for_timeout(800)
                page.locator("#tb-Planejamento-VincularUsuarios").click()
                page.wait_for_timeout(1500)
                campo_busca_u = page.locator("#SiltTransfere_buscarText")
                for codigo_usr in [u for u in [dados["usr1"], dados["usr2"]] if u]:
                    add_log("logs_inv", f"Vinculando usuário {codigo_usr}...")
                    campo_busca_u.fill(codigo_usr)
                    page.locator("#SiltTransfere_buscarButton").click()
                    page.wait_for_timeout(1500)
                    chk = page.locator(".x-grid3-row img").first
                    if chk.is_visible():
                        chk.click()
                        page.wait_for_timeout(800)
                page.locator("#SiltTransfere_fecharButton").click()

                add_log("logs_inv", f"✓ {tipo_inv} ({nome_final}) concluído com sucesso!")
            finally:
                context.close()
    except Exception as e:
        add_log("logs_inv", f"❌ Erro: {e}")
    finally:
        ESTADO["robo_ativo"] = False
        ESTADO["parar_inv"] = False
        ESTADO["status_texto"] = "● Sistema Pronto"


# =========================================================================
# WORKER 2: SUBTIPO PRODUTO (CHROMIUM HEADLESS NO RENDER)
# =========================================================================
@app.route("/api/iniciar/subtipo", methods=["POST"])
def iniciar_subtipo():
    if ESTADO["robo_ativo"]:
        return jsonify({"ok": False, "msg": "Já existe uma automação em execução!"})

    dados = request.json
    itens = [l.strip() for l in dados.get("itens", "").splitlines() if l.strip()]
    if not itens:
        return jsonify({"ok": False, "msg": "Lista de itens vazia!"})

    ESTADO["robo_ativo"] = True
    ESTADO["parar_subtipo"] = False
    ESTADO["logs_sub"] = []
    ESTADO["status_texto"] = "● Executando: SubTipo Produto (Chromium)"

    threading.Thread(target=_executar_playwright_subtipo, args=(dados, itens), daemon=True).start()
    return jsonify({"ok": True})


def _executar_playwright_subtipo(dados, itens):
    codigo_atual = int(dados.get("codigo", 60))
    tipo_esperado = dados.get("tipo", "PNEU").strip()
    total_sucesso = 0
    subtipos_cadastrados_sessao = set()

    add_log("logs_sub", "🚀 Iniciando Chromium no Render...")
    limpar_lockfile()

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=PASTA_CACHE_CHROMIUM,
                headless=True,
                viewport={"width": 1366, "height": 768},
                args=["--ignore-certificate-errors", "--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(dados.get("url", URL_SISTEMA), wait_until="domcontentloaded", timeout=120000)

            page.locator("#LoginDialog_loginText").fill(dados["usuario"])
            page.locator("#LoginDialog_passwordText").fill(dados["senha"])
            page.locator("#LoginDialog_armazemComboBox, .x-form-trigger-arrow").first.click()
            page.wait_for_timeout(800)
            page.locator(".x-combo-list-item").filter(has_text=dados["armazem"]).first.click()
            page.locator("#LoginDialog_loginButton, button:has-text('Entrar')").first.click()
            page.wait_for_timeout(4500)

            campo_busca = page.locator("#x-auto-873-input, input[name='search'], #NavigationView input").first
            campo_busca.fill("SubTipo Produto")
            campo_busca.press("Enter")
            page.wait_for_timeout(1400)
            page.locator("#NavigationView_tree").get_by_text("SubTipo Produto").first.click()
            page.wait_for_timeout(3200)

            frame_silt = page
            for ctx in [page] + page.frames:
                if ctx.locator("#filter-SUBTIPO").count() > 0:
                    frame_silt = ctx
                    break

            for idx, subtipo in enumerate(itens, 1):
                if ESTADO["parar_subtipo"]:
                    add_log("logs_sub", "🛑 Interrompido pelo usuário.")
                    break
                if subtipo in subtipos_cadastrados_sessao:
                    continue

                add_log("logs_sub", f"🔎 [{idx}/{len(itens)}] Verificando '{subtipo}'...")
                filtro = frame_silt.locator("#filter-SUBTIPO").first
                filtro.fill(subtipo)
                filtro.press("Enter")
                page.wait_for_timeout(1800)

                if frame_silt.locator(".slick-row, .x-grid3-row").filter(has_text=subtipo).count() > 0:
                    add_log("logs_sub", f"⚠️ '{subtipo}' já existe no SILT. Pulando...")
                    subtipos_cadastrados_sessao.add(subtipo)
                    filtro.fill("")
                    filtro.press("Enter")
                    continue

                filtro.fill("")
                filtro.press("Enter")
                page.wait_for_timeout(1200)

                btn_cad = page.get_by_role("button", name="Cadastrar").first
                if not btn_cad.is_visible():
                    btn_cad = frame_silt.get_by_role("button", name="Cadastrar").first
                btn_cad.click()

                modal_cad = page.locator(".x-window").last
                modal_cad.locator("#SubTipoProdutoScreenDescriptor_codigo, input[name='codigo']").first.fill(str(codigo_atual))
                modal_cad.get_by_role("textbox", name="Tipo:").click()
                page.wait_for_timeout(500)
                page.locator(".x-grid3-cell, a, div").get_by_text(tipo_esperado).first.dblclick()
                modal_cad.locator("#SubTipoProdutoScreenDescriptor_descricao, input[name='descricao']").first.fill(subtipo)
                modal_cad.get_by_role("button", name="Salvar").first.click()
                page.wait_for_timeout(2000)

                add_log("logs_sub", f"🎉 Cadastrado '{subtipo}' com código {codigo_atual}!")
                subtipos_cadastrados_sessao.add(subtipo)
                codigo_atual += 1
                total_sucesso += 1

            context.close()
            ESTADO["proximo_codigo_sub"] = codigo_atual
            add_log("logs_sub", f"🏁 Concluído! Total cadastrados: {total_sucesso}. Próximo código: {codigo_atual}")
    except Exception as e:
        add_log("logs_sub", f"❌ Erro: {e}")
    finally:
        ESTADO["robo_ativo"] = False
        ESTADO["parar_subtipo"] = False
        ESTADO["status_texto"] = "● Sistema Pronto"


# =========================================================================
# WORKER 3: VINCULAR PRODUTO (UPLOAD CSV/EXCEL + CHROMIUM HEADLESS)
# =========================================================================
@app.route("/api/iniciar/vincular", methods=["POST"])
def iniciar_vincular():
    if ESTADO["robo_ativo"]:
        return jsonify({"ok": False, "msg": "Já existe uma automação em execução!"})

    arquivo = request.files.get("arquivo")
    if not arquivo:
        return jsonify({"ok": False, "msg": "Selecione um arquivo CSV ou Excel!"})

    caminho_temp = os.path.join("/tmp", arquivo.filename)
    arquivo.save(caminho_temp)

    dados = {
        "usuario": request.form.get("usuario", ""),
        "senha": request.form.get("senha", ""),
        "armazem": request.form.get("armazem", ""),
        "caminho": caminho_temp,
    }

    ESTADO["robo_ativo"] = True
    ESTADO["parar_vincular"] = False
    ESTADO["logs_vinc"] = []
    ESTADO["nao_encontrados_vinc"] = []
    ESTADO["status_texto"] = "● Executando: Vincular Produto (Chromium)"

    threading.Thread(target=_executar_automacao_vincular, args=(dados,), daemon=True).start()
    return jsonify({"ok": True})


def _executar_automacao_vincular(dados):
    try:
        df = pd.read_csv(dados["caminho"]) if dados["caminho"].endswith(".csv") else pd.read_excel(dados["caminho"])
        coluna_produto = "Produto" if "Produto" in df.columns else df.columns[2]
        lista_descricoes = df[coluna_produto].dropna().astype(str).tolist()
        add_log("logs_vinc", f"Carregados {len(lista_descricoes)} produtos da planilha.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            page.goto(URL_SISTEMA + "/", timeout=120000)
            page.locator("#LoginDialog_loginText").fill(dados["usuario"])
            page.locator("#LoginDialog_passwordText").fill(dados["senha"])
            page.locator("#LoginDialog_armazemComboBox, .x-form-trigger-arrow").first.click()
            page.wait_for_timeout(800)
            page.locator(".x-combo-list-item").filter(has_text=dados["armazem"]).first.click()
            page.get_by_role("button", name="Entrar").click()

            tree = page.locator("#NavigationView_tree")
            tree.wait_for(state="visible", timeout=60000)
            page.get_by_text("Cadastro", exact=True).dblclick()
            page.wait_for_timeout(800)
            tree.locator(".x-tree3-node:not(.x-tree3-node-leaf)").get_by_text("Produto", exact=True).first.dblclick()
            page.wait_for_timeout(800)
            page.locator("[id$='__NavigationView_tree-ItemProduto']").get_by_text("Produto", exact=True).first.click()

            frame = page.frame_locator("iframe[name^='gwt-uid-']")
            campo_filtro = frame.locator("#filter-PRODUTO")
            campo_filtro.wait_for(state="visible", timeout=40000)

            for idx, descricao in enumerate(lista_descricoes, start=1):
                if ESTADO["parar_vincular"]:
                    add_log("logs_vinc", "🛑 Interrompido pelo usuário.")
                    break

                desc_limpa = descricao.strip()
                numero_inicial = extrair_numero_inicial(desc_limpa)
                add_log("logs_vinc", f"[{idx}/{len(lista_descricoes)}] Processando: {desc_limpa}")

                campo_filtro.fill(f"%{desc_limpa}%")
                campo_filtro.press("Enter")
                page.wait_for_timeout(1500)

                try:
                    alvo = frame.locator(".x-grid3-row").first
                    alvo.click(force=True)
                    page.wait_for_timeout(600)
                    page.get_by_role("button", name="Alterar").click()
                    page.wait_for_timeout(1500)

                    modal_produto = page.locator(".x-window").last
                    page.get_by_role("textbox", name="Tipo do Produto:", exact=True).click()
                    modal_busca = page.locator("#SearchTriggerWindowRemote_searchTextField")
                    modal_busca.fill("%PNEU%")
                    modal_busca.press("Enter")
                    page.wait_for_timeout(1200)
                    page.locator(".x-grid3-row, .x-tree3-node").get_by_text("PNEU", exact=True).first.click()
                    page.get_by_role("button", name="Selecionar").click()
                    page.wait_for_timeout(1000)

                    if numero_inicial:
                        page.get_by_role("textbox", name="SubTipo do Produto:", exact=True).click()
                        janela_sub = page.locator(".x-window").last
                        modal_busca.fill(numero_inicial)
                        modal_busca.press("Enter")
                        page.wait_for_timeout(1500)

                        if janela_sub.locator(".x-grid3-row").count() > 0:
                            janela_sub.locator(".x-grid3-row").first.click()
                            janela_sub.locator("button:has-text('Selecionar')").first.click()
                            page.wait_for_timeout(1000)
                        else:
                            add_log("logs_vinc", f"⚠️ SubTipo '{numero_inicial}' NÃO encontrado.")
                            ESTADO["nao_encontrados_vinc"].append(f"NÚMERO INICIAL: {numero_inicial}\nDESCRIÇÃO: {desc_limpa}")
                            janela_sub.locator("button:has-text('Cancelar'), .x-tool-close").first.click()
                            modal_produto.locator("button:has-text('Cancelar'), .x-tool-close").first.click()
                            continue

                    modal_produto.locator("button:has-text('Salvar')").first.click()
                    page.wait_for_timeout(1500)
                    add_log("logs_vinc", f"✓ [{idx}] Atualizado com sucesso!")
                except Exception as err:
                    add_log("logs_vinc", f"⚠️ Erro no item {idx}: {err}")

            browser.close()
            add_log("logs_vinc", "🏁 Processamento finalizado!")
    except Exception as e:
        add_log("logs_vinc", f"❌ Erro geral: {e}")
    finally:
        ESTADO["robo_ativo"] = False
        ESTADO["parar_vincular"] = False
        ESTADO["status_texto"] = "● Sistema Pronto"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
