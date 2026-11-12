import os
import re
import json
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import pandas as pd
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# Força o diretório de execução para a pasta do próprio script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# =========================================================================
# CONFIGURAÇÕES GLOBAIS E CONSTANTES (EXATAMENTE COMO NO ORIGINAL)
# =========================================================================
URL_SISTEMA = "https://texcoprd.seniorcloud.com.br/siltwms"
PASTA_CACHE_CHROMIUM = os.path.join("/tmp", "Playwright_Senior_Chromium")
ARQUIVO_USUARIOS = os.path.join(BASE_DIR, "usuarios_inventario.json")
ARQUIVO_PRODUTOS_PADRAO = "Produtos.csv"

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

ITENS_INICIAIS_SUBTIPO = [
    "ITEM A CADASTRAR", "ITEM A CADASTRAR"
]


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


LISTA_USUARIOS = carregar_lista_usuarios()


# =========================================================================
# CONTROLADOR UNIFICADO (MESMA CLASSE E MÉTODOS DO ORIGINAL ADAPTADOS P/ WEB)
# =========================================================================
class PainelSiltUnificadoWeb:
    def __init__(self):
        self.robo_ativo = False
        self.parar_inv = False
        self.parar_subtipo = False
        self.parar_vincular = False

        self.status_global = "● Sistema Pronto"
        self.status_cor = "#4ade80"

        self.logs_inv = []
        self.logs_sub = []
        self.logs_vinc = []
        self.nao_encontrados_vinc = []

        self.sub_codigo_atual = 60
        self.sub_item_atual = ITENS_INICIAIS_SUBTIPO[0]
        self.sub_status = f"{len(ITENS_INICIAIS_SUBTIPO)} itens carregados. Pronto."
        self.alerta_popup = None

    def _set_status_global(self, texto, cor="#4ade80"):
        self.status_global = texto
        self.status_cor = cor

    def _notificar(self, titulo, mensagem):
        self.alerta_popup = {"titulo": titulo, "mensagem": mensagem}

    # ---------------------------------------------------------------------
    # LOGS
    # ---------------------------------------------------------------------
    def _inv_log(self, msg):
        self.logs_inv.append(f">> {msg}")

    def _sub_log(self, msg):
        self.logs_sub.append(f"{msg}")

    def _vinc_log(self, mensagem, tag=None):
        self.logs_vinc.append(f">> {mensagem}")

    def _vinc_registrar_nao_encontrado(self, numero_inicial, descricao_completa):
        bloco = (
            f"NÚMERO INICIAL: {numero_inicial}\n"
            f"DESCRIÇÃO: {descricao_completa}\n"
            + ("-" * 40)
        )
        self.nao_encontrados_vinc.append(bloco)

    # ---------------------------------------------------------------------
    # MÉTODOS AUXILIARES ABA 1: INVENTÁRIO
    # ---------------------------------------------------------------------
    def _inv_verificar_parada(self):
        if self.parar_inv:
            raise Exception("Processo interrompido pelo usuário (STOP acionado).")

    def _inv_obter_contexto_tabela(self, page):
        self._inv_verificar_parada()
        for ctx in [page] + page.frames:
            self._inv_verificar_parada()
            try:
                qtd = ctx.locator("input[id*='filter-'], input[columnid*='ID'], .slick-row, .x-grid3-row").count()
                if qtd > 0:
                    return ctx
            except Exception:
                continue
        return page

    def _inv_selecionar_endereco(self, page):
        self._inv_verificar_parada()
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

    def _inv_vincular_multiplos_usuarios(self, page, lista_usuarios):
        self._inv_verificar_parada()
        campo_busca = page.locator("#SiltTransfere_buscarText")
        campo_busca.wait_for(state="visible", timeout=60000)

        for idx, codigo_usr in enumerate(lista_usuarios, 1):
            self._inv_verificar_parada()
            if not codigo_usr:
                continue

            self._inv_log(f"Pesquisando e flegando Usuário {idx} ({codigo_usr})...")
            campo_busca.click(force=True)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            campo_busca.fill(codigo_usr)
            campo_busca.press("Enter")
            page.locator("#SiltTransfere_buscarButton").click()
            page.wait_for_timeout(1500)

            chk = page.locator(".x-grid3-row img").first
            if chk.is_visible():
                chk.click()
                self._inv_log(f"✓ Usuário {idx} ({codigo_usr}) flegado!")
                page.wait_for_timeout(800)
            else:
                self._inv_log(f"Aviso: Checkbox do usuário '{codigo_usr}' não foi encontrado.")

        self._inv_log("Fechando modal de vinculação de usuários...")
        btn_fechar = page.locator("#SiltTransfere_fecharButton, button:has-text('Fechar')").first
        btn_fechar.click()
        page.wait_for_timeout(1000)

    def _inv_reativar_selecao(self, page, id_inventario: str):
        self._inv_verificar_parada()
        self._inv_log(f"Re-selecionando inventário (ID: {id_inventario or 'filtrado'})...")

        try:
            page.locator(".ext-el-mask, .x-mask-loading").wait_for(state="hidden", timeout=10000)
        except Exception:
            pass

        alvo_frame = self._inv_obter_contexto_tabela(page)

        if id_inventario:
            try:
                campo_id = alvo_frame.locator(
                    "input#filter-IDINVENTARIO, input#filter-ID_INVENTARIO, input#filter-ID, input[columnid='IDINVENTARIO'], input[columnid='ID_INVENTARIO'], input[columnid='ID']"
                ).first
                if campo_id.is_visible():
                    self._inv_log(f"Pesquisando ID '{id_inventario}' no filtro de 'id Inventário'...")
                    campo_id.click(force=True)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.type(str(id_inventario), delay=35)
                    page.keyboard.press("Enter")
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(1500)
                    try:
                        alvo_frame.locator(".ext-el-mask, .x-mask-loading").wait_for(state="hidden", timeout=10000)
                    except Exception:
                        pass
            except Exception as e:
                self._inv_log(f"Aviso no filtro por ID: {e}")

        celula_alvo = None
        if id_inventario:
            try:
                candidato = alvo_frame.locator(
                    ".slick-cell, .x-grid3-cell, .x-grid3-col-0, [columnid='IDINVENTARIO'], [columnid='ID_INVENTARIO']"
                ).filter(has_text=str(id_inventario)).first
                if candidato.is_visible():
                    celula_alvo = candidato
            except Exception:
                pass

        if not celula_alvo:
            celula_alvo = alvo_frame.locator(
                ".slick-row .slick-cell, .x-grid3-row .x-grid3-cell, .grid-canvas .slick-row, .x-grid3-row"
            ).first

        celula_alvo.wait_for(state="visible", timeout=15000)
        celula_alvo.scroll_into_view_if_needed()
        celula_alvo.click(force=True)

        celula_alvo.evaluate("""el => {
            const linha = el.closest('.slick-row') || el.closest('.x-grid3-row') || el;
            ['mouseover', 'mousedown', 'mouseup', 'click'].forEach(evt => {
                linha.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
            });
            linha.classList.add('x-grid3-row-selected');
        }""")

        page.wait_for_timeout(800)
        self._inv_log("✓ ID clicado com sucesso! Menus liberados.")

    def _inv_filtrar_e_selecionar(self, page, nome_inventario: str) -> str:
        self._inv_verificar_parada()
        self._inv_log("Aguardando estabilização da tela e fechamento de modais...")

        try:
            modal_cad = page.locator(".x-window").filter(has_text="Cadastro").last
            if modal_cad.is_visible():
                btn_fechar = modal_cad.locator("button:has-text('Fechar'), button:has-text('Cancelar'), .x-tool-close").first
                if btn_fechar.is_visible():
                    btn_fechar.click(force=True)
        except Exception:
            pass

        try:
            page.locator(".ext-el-mask, .x-mask-loading").wait_for(state="hidden", timeout=10000)
        except Exception:
            pass

        page.wait_for_timeout(1500)
        alvo_frame = self._inv_obter_contexto_tabela(page)

        self._inv_log(f"Buscando no filtro pelo nome: {nome_inventario}...")
        campo_filtro = alvo_frame.locator("input#filter-DESCRICAO, input[columnid='DESCRICAO']").first
        campo_filtro.wait_for(state="visible", timeout=20000)
        campo_filtro.click(force=True)

        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.type(nome_inventario, delay=35)
        page.keyboard.press("Enter")
        page.keyboard.press("Tab")

        page.wait_for_timeout(2000)
        try:
            alvo_frame.locator(".ext-el-mask, .x-mask-loading").wait_for(state="hidden", timeout=15000)
        except Exception:
            pass

        self._inv_log("Selecionando o inventário localizado no grid...")
        linha = alvo_frame.locator(".grid-canvas .slick-row, .slick-row, .x-grid3-row").first
        linha.wait_for(state="visible", timeout=20000)
        linha.click(force=True)

        linha.evaluate("""el => {
            ['mouseover', 'mousedown', 'mouseup', 'click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
            });
            el.classList.add('x-grid3-row-selected');
        }""")

        id_inventario = ""
        try:
            celula_id = linha.locator(".slick-cell.l0, .x-grid3-col-0, [columnid='IDINVENTARIO'], [columnid='ID_INVENTARIO'], [columnid='ID']").first
            if celula_id.is_visible():
                txt = celula_id.inner_text()
                id_inventario = "".join(filter(str.isdigit, txt))
            if not id_inventario:
                texto_linha = linha.inner_text()
                primeiro_token = texto_linha.split()[0]
                id_inventario = "".join(filter(str.isdigit, primeiro_token))
            self._inv_log(f"✓ ID do Inventário capturado: {id_inventario}")
        except Exception:
            self._inv_log("Aviso: Mantendo referência pela linha filtrada.")

        page.wait_for_timeout(1000)
        btn_vinc_dep = page.locator("#tb-Planejamento-VincularDepositantes")
        btn_vinc_dep.wait_for(state="visible", timeout=15000)
        self._inv_log("✓ Inventário selecionado! Botões liberados.")
        page.wait_for_timeout(800)

        return id_inventario

    def _executar_automacao_inventario(self, dados):
        tipo_inv = dados["tipo_inventario"]
        self._inv_log(f"Iniciando Chromium em modo INVISÍVEL (Headless) para {tipo_inv}...")
        limpar_lockfile()

        try:
            with sync_playwright() as p:
                launch_args = [
                    "--ignore-certificate-errors",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]

                context = p.chromium.launch_persistent_context(
                    user_data_dir=PASTA_CACHE_CHROMIUM,
                    headless=True,
                    viewport={"width": 1366, "height": 768},
                    args=launch_args,
                )

                page = context.pages[0] if context.pages else context.new_page()

                try:
                    self._inv_verificar_parada()
                    self._inv_log(f"Acessando {URL_SISTEMA}...")
                    page.goto(URL_SISTEMA, wait_until="domcontentloaded", timeout=120000)

                    self._inv_verificar_parada()
                    self._inv_log("Preenchendo credenciais...")
                    campo_login = page.locator("#LoginDialog_loginText")
                    campo_login.wait_for(state="visible", timeout=120000)
                    campo_login.fill(dados["usuario"])
                    page.locator("#LoginDialog_passwordText").fill(dados["senha"])
                    page.wait_for_timeout(500)

                    self._inv_verificar_parada()
                    self._inv_log(f"Selecionando armazém: {dados['armazem']}...")
                    trigger_armazem = page.locator("#LoginDialog_armazemComboBox, .x-form-trigger-arrow").first
                    trigger_armazem.click()
                    page.wait_for_timeout(800)

                    opcao_armazem = page.locator(".x-combo-list-item").filter(has_text=dados["armazem"]).first
                    opcao_armazem.wait_for(state="visible", timeout=15000)
                    opcao_armazem.click()
                    page.wait_for_timeout(500)

                    self._inv_verificar_parada()
                    self._inv_log("Clicando em Entrar...")
                    btn_entrar = page.locator("#LoginDialog_loginButton")
                    btn_entrar.wait_for(state="visible", timeout=10000)
                    btn_entrar.click()

                    self._inv_verificar_parada()
                    self._inv_log("Aguardando tela principal...")
                    pasta_inv = page.locator(".x-tree3-node-text").filter(has_text="Inventário").first
                    pasta_inv.wait_for(state="visible", timeout=120000)
                    pasta_inv.dblclick()
                    page.wait_for_timeout(1000)

                    item_inv = page.get_by_text("Inventário", exact=True).last
                    item_inv.wait_for(state="visible", timeout=60000)
                    item_inv.dblclick()
                    page.wait_for_timeout(1500)

                    self._inv_verificar_parada()
                    data_hoje_terc = datetime.now().strftime("%m.%d.%y")
                    data_hoje_din = datetime.now().strftime("%Y%m%d")
                    alvo_frame = self._inv_obter_contexto_tabela(page)

                    nome_final = ""
                    if tipo_inv == "Inventário Terceirizado":
                        self._inv_log("Verificando se já existe REV_SERIAL1 para o dia de hoje...")
                        termo_serial1 = f"REV_SERIAL1_{data_hoje_terc}"
                        try:
                            tem_serial1 = alvo_frame.locator(".slick-row, .x-grid3-row").filter(has_text=termo_serial1).count() > 0
                        except Exception:
                            tem_serial1 = False

                        if tem_serial1:
                            nome_final = f"REV_SERIAL2_{data_hoje_terc}"
                            self._inv_log(f"Já existe inventário SERIAL1 hoje! Gerando como: {nome_final}")
                        else:
                            nome_final = termo_serial1
                            self._inv_log(f"Primeiro inventário terceirizado de hoje. Gerando como: {nome_final}")
                    else:
                        dep_clean = dados["depositante"].replace(" LTDA", "").replace(" S.A.", "").replace(" GERAL", "").replace(" S.A", "").strip()
                        palavras = dep_clean.split()
                        tag = palavras[0] if len(palavras) == 1 else f"{palavras[0]}_{palavras[1]}"
                        nome_base = f"INV_{tag}_{data_hoje_din}".upper()

                        try:
                            tem_inv1 = alvo_frame.locator(".slick-row, .x-grid3-row").filter(has_text=nome_base).count() > 0
                        except Exception:
                            tem_inv1 = False

                        if tem_inv1:
                            nome_final = f"{nome_base}_2"
                            self._inv_log(f"Já existe inventário para este depositante hoje. Gerando: {nome_final}")
                        else:
                            nome_final = nome_base
                            self._inv_log(f"Nome gerado automaticamente: {nome_final}")

                    self._inv_verificar_parada()
                    self._inv_log(f"Iniciando cadastro: {nome_final}...")
                    btn_cadastrar = page.locator("#tb-Controle-Cadastrar")
                    btn_cadastrar.wait_for(state="visible", timeout=60000)
                    btn_cadastrar.click()

                    campo_desc = page.locator("#InventarioDadosScreenDescriptor_descricao")
                    campo_desc.wait_for(state="visible", timeout=60000)
                    campo_desc.fill(nome_final)
                    page.wait_for_timeout(500)

                    try:
                        campo_tipo = page.locator("#InventarioDadosScreenDescriptor_tipoPadraoIdentificacao")
                        if campo_tipo.is_visible():
                            campo_tipo.click()
                            page.wait_for_timeout(500)
                            opcao_emb = page.locator(".x-combo-list-item").filter(has_text="Embalagem").first
                            if opcao_emb.is_visible():
                                opcao_emb.click()
                    except Exception:
                        pass

                    modal_cad = page.locator(".x-window").filter(has_text="Cadastro").last

                    self._inv_verificar_parada()
                    if tipo_inv == "Inventário Terceirizado":
                        self._inv_log("Acessando aba 'Inventário Terceirizado'...")
                        item_inv_terc = modal_cad.locator(".x-tree3-node-text").filter(has_text="Inventário Terceirizado").first
                        item_inv_terc.wait_for(state="visible", timeout=15000)
                        item_inv_terc.click()
                        page.wait_for_timeout(1000)

                        self._inv_verificar_parada()
                        self._inv_log("Marcando a opção 'Terceirizado'...")
                        lbl_terceirizado = modal_cad.get_by_text("Terceirizado", exact=True).last
                        lbl_terceirizado.wait_for(state="visible", timeout=15000)

                        try:
                            caixa_box = lbl_terceirizado.bounding_box()
                            if caixa_box:
                                page.mouse.click(caixa_box["x"] - 12, caixa_box["y"] + (caixa_box["height"] / 2))
                            else:
                                lbl_terceirizado.click(force=True)
                        except Exception:
                            lbl_terceirizado.click(force=True)

                        page.wait_for_timeout(800)

                        self._inv_verificar_parada()
                        self._inv_log("Preenchendo Empresa Terceirizada com 'TOTAL EXPRESS'...")
                        campo_empresa = modal_cad.locator("input.x-form-text:not([readonly])").last
                        if not campo_empresa.is_visible():
                            campo_empresa = modal_cad.locator("input[type='text']:not([readonly])").last

                        campo_empresa.wait_for(state="visible", timeout=15000)
                        campo_empresa.click(force=True)
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        campo_empresa.fill("TOTAL EXPRESS")
                        page.keyboard.press("Tab")
                        page.wait_for_timeout(800)

                    else:
                        self._inv_verificar_parada()
                        self._inv_log("Acessando aba Configurações...")
                        item_config = modal_cad.locator(".x-tree3-node-text").filter(has_text="Configurações").first
                        item_config.wait_for(state="visible", timeout=15000)
                        item_config.click()
                        page.wait_for_timeout(1000)

                        page.evaluate("""
                            const corpos = document.querySelectorAll('.x-window-body');
                            corpos.forEach(c => { c.scrollTop = 1200; });
                        """)
                        page.wait_for_timeout(800)

                        self._inv_verificar_parada()
                        self._inv_log("Marcando Inventário Dinâmico...")
                        lbl_dinamico = modal_cad.get_by_text("Inventário Dinâmico", exact=True).last
                        lbl_dinamico.scroll_into_view_if_needed()
                        page.wait_for_timeout(600)

                        try:
                            caixa_box = lbl_dinamico.bounding_box()
                            if caixa_box:
                                page.mouse.click(caixa_box["x"] - 12, caixa_box["y"] + (caixa_box["height"] / 2))
                            else:
                                lbl_dinamico.click(force=True)
                        except Exception:
                            lbl_dinamico.click(force=True)

                        page.wait_for_timeout(800)

                        btn_sim = page.locator("#WmsMessageBoxCONFIRM_YES_BUTTON, button:has-text('Sim'), .x-btn-text:has-text('Sim')").first
                        btn_sim.wait_for(state="visible", timeout=30000)
                        btn_sim.click(force=True)
                        page.wait_for_timeout(1000)

                        self._inv_verificar_parada()
                        self._inv_log("Acessando aba Inventário Dinâmico (Endereços)...")
                        item_inv_din = modal_cad.locator(".x-tree3-node-text").filter(has_text="Inventário Dinâmico").first
                        item_inv_din.wait_for(state="visible", timeout=15000)
                        item_inv_din.click()
                        page.wait_for_timeout(1000)

                        self._inv_log("Configurando endereço Falta...")
                        page.locator("#InventarioLocalConciliacaoScreenDescriptor_idLocalConciliacaoFalta-input").click()
                        self._inv_selecionar_endereco(page)

                        self._inv_log("Configurando endereço Sobra...")
                        page.locator("#InventarioLocalConciliacaoScreenDescriptor_idLocalConciliacaoSobra-input").click()
                        self._inv_selecionar_endereco(page)

                    self._inv_verificar_parada()
                    self._inv_log(f"Salvando cadastro do {tipo_inv}...")
                    btn_salvar = modal_cad.locator("#CadastroWindow_salvarCadastrodeInventárioButton, button:has-text('Salvar')").first
                    btn_salvar.wait_for(state="visible", timeout=30000)
                    btn_salvar.click()

                    try:
                        modal_cad.wait_for(state="hidden", timeout=25000)
                    except Exception:
                        page.wait_for_timeout(2500)

                    id_inv_capturado = self._inv_filtrar_e_selecionar(page, nome_final)

                    # Vinculação do Depositante
                    self._inv_verificar_parada()
                    self._inv_log(f"Vinculando depositante: {dados['depositante']}...")
                    btn_vinc_dep = page.locator("#tb-Planejamento-VincularDepositantes")
                    btn_vinc_dep.wait_for(state="visible", timeout=30000)
                    btn_vinc_dep.click()
                    page.wait_for_timeout(1000)

                    combo_arrow = page.locator("#SiltTransfere_buscarComboBoxComboArrow")
                    if combo_arrow.is_visible():
                        combo_arrow.click()
                        page.locator(".x-combo-list-item").filter(has_text="Depositante").first.click()

                    campo_busca_dep = page.locator("#SiltTransfere_buscarText")
                    campo_busca_dep.click()

                    dep_nome = dados["depositante"].strip()
                    if "|" in dep_nome:
                        termo_busca = f"%{dep_nome.split('|')[-1].strip()}%"
                    else:
                        termo_busca = f"%{dep_nome.split()[0]}%"

                    campo_busca_dep.fill(termo_busca)
                    campo_busca_dep.press("Enter")
                    page.locator("#SiltTransfere_buscarButton").click()
                    page.wait_for_timeout(1500)

                    linha_alvo = page.locator(".x-grid3-row").filter(has_text=dep_nome)
                    if linha_alvo.count() == 0 and "|" in dep_nome:
                        sufixo = dep_nome.split("|")[-1].strip()
                        linha_alvo = page.locator(".x-grid3-row").filter(has_text=sufixo)

                    if linha_alvo.count() > 0:
                        chk_dep = linha_alvo.first.locator("img, .x-grid3-row-checker").first
                        chk_dep.click()
                        self._inv_log(f"✓ Depositante '{dep_nome}' selecionado com sucesso!")
                    else:
                        chk_dep = page.locator(".x-grid3-row img").first
                        if chk_dep.is_visible():
                            chk_dep.click()
                        self._inv_log(f"Aviso: Linha exata para '{dep_nome}' não identificada diretamente, marcado primeiro registro.")

                    page.wait_for_timeout(800)
                    page.locator("#SiltTransfere_fecharButton").click()
                    page.wait_for_timeout(1500)

                    self._inv_reativar_selecao(page, id_inv_capturado)

                    self._inv_verificar_parada()
                    btn_vinc_usr = page.locator("#tb-Planejamento-VincularUsuarios")
                    self._inv_log("Abrindo tela de vinculação de usuários...")
                    btn_vinc_usr.wait_for(state="visible", timeout=30000)
                    btn_vinc_usr.click()
                    page.wait_for_timeout(1500)

                    usuarios_para_vincular = [u for u in [dados["usr1"], dados["usr2"]] if u]
                    self._inv_vincular_multiplos_usuarios(page, usuarios_para_vincular)

                    self._inv_log(f"✓ {tipo_inv} concluído com sucesso!")
                    self._notificar("Sucesso", f"{tipo_inv} ({nome_final}) criado, depositante e usuários vinculados!")

                except Exception as e:
                    if self.parar_inv:
                        self._inv_log("⚠️ Processo interrompido com sucesso pelo usuário (STOP).")
                        self._notificar("Informação", "Automação parada pelo usuário.")
                    else:
                        self._inv_log(f"❌ Erro crítico: {e}")
                        self._notificar("Erro", f"Ocorreu um erro durante a automação: {e}")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

        except Exception as e_outer:
            self._inv_log(f"❌ Falha ao iniciar navegador Chromium: {e_outer}")
            self._notificar("Erro", f"Falha ao abrir Chromium: {e_outer}")
        finally:
            self.robo_ativo = False
            self.parar_inv = False
            self._set_status_global("● Sistema Pronto", "#4ade80")

    # ---------------------------------------------------------------------
    # MÉTODOS AUXILIARES ABA 2: SUBTIPO PRODUTO
    # ---------------------------------------------------------------------
    def _sub_obter_contexto(self, page):
        for ctx in [page] + page.frames:
            try:
                if ctx.locator("#filter-SUBTIPO").count() > 0:
                    return ctx
            except Exception:
                continue
        try:
            for f in page.frames:
                if "gwt-uid" in f.name:
                    return f
        except Exception:
            pass
        return page

    def _sub_limpar_filtro(self, filtro_subtipo, page):
        filtro_subtipo.click()
        page.keyboard.press("ControlOrMeta+a")
        filtro_subtipo.fill("")
        filtro_subtipo.press("Enter")
        page.wait_for_timeout(700)

    def _sub_atualizar_replica_visual(self, subtipo, codigo):
        self.sub_item_atual = subtipo
        self.sub_codigo_atual = codigo

    def _executar_playwright_subtipo(self, dados, itens, codigo_inicial):
        url = dados["url"]
        usuario = dados["usuario"]
        senha = dados["senha"]
        armazem = dados["armazem"]
        tipo_esperado = dados["tipo"]

        codigo_atual = codigo_inicial
        total_sucesso = 0
        total_duplicados_grid = 0
        total_duplicados_lista = 0
        total_excluidos = 0
        subtipos_cadastrados_sessao = set()

        self._sub_log("🚀 Iniciando Chromium (viewport padrão)...")
        limpar_lockfile()

        with sync_playwright() as playwright:
            try:
                launch_args = [
                    "--ignore-certificate-errors",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]

                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=PASTA_CACHE_CHROMIUM,
                    headless=True,
                    viewport={"width": 1366, "height": 768},
                    args=launch_args,
                )

                page = context.pages[0] if context.pages else context.new_page()

                self._sub_log(f"🌐 Acessando {url}...")
                page.goto(url, wait_until="domcontentloaded", timeout=120000)

                # 1. Login
                self._sub_log("🔑 Inserindo credenciais...")
                campo_login = page.locator("#LoginDialog_loginText")
                campo_login.wait_for(state="visible", timeout=120000)
                campo_login.fill(usuario)
                page.locator("#LoginDialog_passwordText").fill(senha)
                page.wait_for_timeout(600)

                self._sub_log(f"🏢 Selecionando armazém: {armazem}...")
                trigger_armazem = page.locator("#LoginDialog_armazemComboBox, .x-form-trigger-arrow").first
                trigger_armazem.click()
                page.wait_for_timeout(900)

                opcao_armazem = page.locator(".x-combo-list-item").filter(has_text=armazem).first
                opcao_armazem.wait_for(state="visible", timeout=15000)
                opcao_armazem.click()
                page.wait_for_timeout(600)

                self._sub_log("➡️ Clicando em Entrar...")
                btn_entrar = page.locator("#LoginDialog_loginButton, button:has-text('Entrar')").first
                btn_entrar.wait_for(state="visible", timeout=10000)
                btn_entrar.click()

                self._sub_log("⏳ Aguardando tela principal do SILT carregar...")
                page.wait_for_timeout(4500)

                # 2. Pesquisa de Menu
                self._sub_log("🔍 Pesquisando menu 'SubTipo Produto' com Enter...")
                campo_busca = page.locator("#x-auto-873-input, input[name='search'], #NavigationView input").first
                campo_busca.wait_for(state="visible", timeout=30000)
                campo_busca.click()
                campo_busca.fill("SubTipo Produto")
                page.wait_for_timeout(500)
                campo_busca.press("Enter")
                page.wait_for_timeout(1400)

                item_menu = page.locator("#NavigationView_tree").get_by_text("SubTipo Produto").first
                item_menu.wait_for(state="visible", timeout=20000)
                item_menu.click()
                page.wait_for_timeout(3200)

                self._sub_log("🔍 Localizando tela de Subtipo dentro do iframe...")
                frame_silt = None
                for _ in range(15):
                    frame_silt = self._sub_obter_contexto(page)
                    if frame_silt.locator("#filter-SUBTIPO").count() > 0:
                        break
                    page.wait_for_timeout(900)

                self._sub_log("✓ Campo #filter-SUBTIPO pronto para operações!")

                # 3. Processamento dos Itens
                for idx, subtipo in enumerate(itens, 1):
                    if self.parar_subtipo:
                        self._sub_log("🛑 Operação cancelada pelo usuário.")
                        break

                    if subtipo in subtipos_cadastrados_sessao:
                        self._sub_log(f"⚠️ [DUPLICADO NA LISTA] Linha {idx}: '{subtipo}' já tratado nesta sessão.")
                        self._sub_log(f"   -> Código {codigo_atual} preservado.")
                        total_duplicados_lista += 1
                        continue

                    self._sub_atualizar_replica_visual(subtipo, codigo_atual)
                    self._sub_log(f"\n🔎 [{idx}/{len(itens)}] Consultando no SILT: '{subtipo}'...")

                    try:
                        filtro_subtipo = frame_silt.locator("#filter-SUBTIPO").first
                        filtro_subtipo.wait_for(state="visible", timeout=15000)
                        filtro_subtipo.click()
                        filtro_subtipo.fill(subtipo)
                        filtro_subtipo.press("Enter")
                        page.wait_for_timeout(1800)

                        linhas_encontradas = frame_silt.locator(".slick-row, .x-grid3-row").filter(has_text=subtipo)
                        qtd_linhas = linhas_encontradas.count()

                        if qtd_linhas > 1:
                            self._sub_log(f"⚠️ [DUPLICADO DETECTADO] Encontradas {qtd_linhas} ocorrências de '{subtipo}'!")
                            self._sub_log("🎯 Clicando em 'PNEU' na primeira linha...")
                            btn_pneu = frame_silt.get_by_text(tipo_esperado).first
                            if btn_pneu.is_visible():
                                btn_pneu.click()
                            else:
                                linhas_encontradas.first.click()

                            page.wait_for_timeout(800)

                            btn_excluir = page.get_by_role("button", name="Excluir").first
                            if not btn_excluir.is_visible():
                                btn_excluir = frame_silt.get_by_role("button", name="Excluir").first

                            precisa_clicar_codigo = False
                            try:
                                eh_desabilitado = btn_excluir.is_disabled() if btn_excluir.is_visible() else True
                                eh_sel = "x-grid3-row-selected" in (linhas_encontradas.first.get_attribute("class") or "")
                                if eh_desabilitado or not eh_sel:
                                    precisa_clicar_codigo = True
                            except Exception:
                                precisa_clicar_codigo = True

                            if precisa_clicar_codigo:
                                self._sub_log("🔄 'PNEU' não respondeu a tempo. Clicando na célula da coluna 'Código'...")
                                celula_codigo = linhas_encontradas.first.locator(".slick-cell.l2, .x-grid3-col-2, [columnid*='CODIGO']").first
                                if celula_codigo.is_visible():
                                    celula_codigo.click()
                                else:
                                    frame_silt.get_by_text("Código").first.click()
                                    linhas_encontradas.first.click()
                                page.wait_for_timeout(800)

                            self._sub_log("🗑️ Clicando em Excluir...")
                            btn_excluir.wait_for(state="visible", timeout=10000)
                            btn_excluir.click()
                            page.wait_for_timeout(1000)

                            self._sub_log("❓ Confirmando exclusão (Sim)...")
                            btn_sim = page.get_by_role("button", name="Sim").first
                            if not btn_sim.is_visible():
                                btn_sim = frame_silt.get_by_role("button", name="Sim").first

                            btn_sim.wait_for(state="visible", timeout=10000)
                            btn_sim.click()
                            self._sub_log("✅ Item duplicado mais recente excluído com sucesso!")
                            total_excluidos += 1
                            total_duplicados_grid += 1
                            subtipos_cadastrados_sessao.add(subtipo)
                            page.wait_for_timeout(2500)

                            self._sub_limpar_filtro(filtro_subtipo, page)
                            continue

                        elif qtd_linhas == 1:
                            self._sub_log(f"⚠️ [JÁ EXISTE NO SILT] Subtipo '{subtipo}' já consta cadastrado de forma única.")
                            self._sub_log(f"   -> Código {codigo_atual} preservado. Pulando para o próximo...")
                            total_duplicados_grid += 1
                            subtipos_cadastrados_sessao.add(subtipo)

                            self._sub_limpar_filtro(filtro_subtipo, page)
                            continue

                        self._sub_log(f"👉 Inédito! '{subtipo}' não encontrado no SILT.")
                        self._sub_limpar_filtro(filtro_subtipo, page)

                        self._sub_log("⏳ Aguardando 2s para prosseguir com o cadastro...")
                        page.wait_for_timeout(2000)

                        self._sub_log(f"📝 Cadastrando -> Código: {codigo_atual} | Subtipo: {subtipo}")

                        btn_cadastrar = page.get_by_role("button", name="Cadastrar").first
                        if not btn_cadastrar.is_visible():
                            btn_cadastrar = frame_silt.get_by_role("button", name="Cadastrar").first
                        btn_cadastrar.wait_for(state="visible", timeout=15000)
                        btn_cadastrar.click()

                        modal_cad = page.locator(".x-window").last
                        modal_cad.wait_for(state="visible", timeout=15000)

                        campo_cod = modal_cad.locator("#SubTipoProdutoScreenDescriptor_codigo, input[name='codigo']").first
                        campo_cod.wait_for(state="visible", timeout=15000)
                        campo_cod.click()
                        campo_cod.fill(str(codigo_atual))
                        page.wait_for_timeout(400)

                        modal_cad.get_by_role("textbox", name="Tipo:").click()
                        page.wait_for_timeout(500)
                        item_tipo = page.locator(".x-grid3-cell, a, div").get_by_text(tipo_esperado).first
                        item_tipo.dblclick()
                        page.wait_for_timeout(400)

                        campo_sub = modal_cad.locator("#SubTipoProdutoScreenDescriptor_descricao, input[name='descricao']").first
                        campo_sub.click()
                        campo_sub.fill(subtipo)
                        page.wait_for_timeout(400)

                        btn_salvar = modal_cad.get_by_role("button", name="Salvar").first
                        btn_salvar.click()

                        self._sub_log("⏳ Aguardando gravação no SILT...")
                        try:
                            modal_cad.wait_for(state="hidden", timeout=10000)
                        except Exception:
                            page.wait_for_timeout(4500)

                        page.wait_for_timeout(1000)

                        self._sub_log(f"🔎 Auditoria pós-cadastro: verificando se '{subtipo}' adentrou no SILT...")
                        filtro_subtipo.click()
                        filtro_subtipo.fill(subtipo)
                        filtro_subtipo.press("Enter")
                        page.wait_for_timeout(1800)

                        linhas_adentradas = frame_silt.locator(".slick-row, .x-grid3-row").filter(has_text=subtipo)
                        if linhas_adentradas.count() > 0:
                            self._sub_log(f"🎉 Confirmado! '{subtipo}' adentrou perfeitamente no Senior SILT! (Código: {codigo_atual})")
                            subtipos_cadastrados_sessao.add(subtipo)
                            codigo_atual += 1
                            total_sucesso += 1
                        else:
                            self._sub_log(f"⚠️ Atenção: '{subtipo}' salvo, mas não respondeu na checagem do grid.")
                            self._sub_log(f"   -> Código {codigo_atual} preservado por cautela.")

                        self._sub_limpar_filtro(filtro_subtipo, page)

                    except Exception as step_err:
                        self._sub_log(f"⚠️ Falha no item {subtipo}: {step_err}")
                        self._sub_log(f"⚠️ Mantendo código {codigo_atual}.")
                        try:
                            btn_cancelar = page.locator(".x-window button:has-text('Cancelar')").first
                            if btn_cancelar.is_visible():
                                btn_cancelar.click()
                        except Exception:
                            pass

                context.close()

            except Exception as e:
                self._sub_log(f"❌ Erro no Playwright: {e}")

        self.robo_ativo = False
        self.parar_subtipo = False
        self._set_status_global("● Sistema Pronto", "#4ade80")
        self.sub_status = "Execução Concluída!"
        self.sub_codigo_atual = codigo_atual

        resumo = (
            f"\n🏁 --- RESUMO DA AUDITORIA ---\n"
            f"✅ Cadastrados e auditados no SILT: {total_sucesso}\n"
            f"🗑️ Duplicados excluídos (topo): {total_excluidos}\n"
            f"⚠️ Já existiam no SILT: {total_duplicados_grid}\n"
            f"⚠️ Repetidos na lista TXT (pulados): {total_duplicados_lista}\n"
            f"🔢 Próximo código disponível: {codigo_atual}"
        )
        self._sub_log(resumo)
        self._notificar("Fim do Processamento", f"Processamento concluído! Novos cadastrados: {total_sucesso} | Próximo código: {codigo_atual}")

    # ---------------------------------------------------------------------
    # MÉTODOS AUXILIARES ABA 3: VINCULAR PRODUTO
    # ---------------------------------------------------------------------
    def _vinc_selecionar_armazem(self, page, armazem_selecionado):
        self._vinc_log(f"Selecionando {armazem_selecionado}...")
        trigger = page.locator("#LoginDialog_armazemComboBox, .x-form-trigger-arrow").first
        trigger.wait_for(state="visible", timeout=30000)
        trigger.click()
        page.wait_for_timeout(1000)

        opcao = page.locator(".x-combo-list-item").filter(has_text=armazem_selecionado).first
        opcao.wait_for(state="visible", timeout=15000)
        opcao.click()
        page.wait_for_timeout(800)

    def _executar_automacao_vincular(self, usuario, senha, armazem_selecionado, caminho_csv):
        itens_subtipo_nao_encontrado = []

        if not os.path.exists(caminho_csv):
            self._vinc_log(f"Arquivo '{caminho_csv}' não encontrado!", "erro")
            self.robo_ativo = False
            self._set_status_global("● Sistema Pronto", "#4ade80")
            return

        try:
            if caminho_csv.endswith(".csv"):
                df = pd.read_csv(caminho_csv)
            else:
                df = pd.read_excel(caminho_csv)

            coluna_produto = "Produto" if "Produto" in df.columns else df.columns[2]
            lista_descricoes = df[coluna_produto].dropna().astype(str).tolist()
            total_itens = len(lista_descricoes)
            self._vinc_log(f"Carregados {total_itens} produtos do arquivo.")

            with sync_playwright() as p:
                self._vinc_log("Abrindo Chromium com viewport padrão...")
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(viewport={"width": 1366, "height": 768})
                page = context.new_page()

                # 1. Login e Seleção do Armazém
                self._vinc_log("Acessando SILT WMS...")
                page.goto(URL_SISTEMA + "/", timeout=120000)

                campo_login = page.locator("#LoginDialog_loginText")
                campo_login.wait_for(state="visible", timeout=60000)
                campo_login.fill(usuario)
                page.locator("#LoginDialog_passwordText").fill(senha)
                page.wait_for_timeout(600)

                self._vinc_selecionar_armazem(page, armazem_selecionado)

                self._vinc_log("Entrando no sistema...")
                page.get_by_role("button", name="Entrar").click()

                # 2. Navegação até Produto
                self._vinc_log("Aguardando menu lateral...")
                tree = page.locator("#NavigationView_tree")
                tree.wait_for(state="visible", timeout=60000)
                page.wait_for_timeout(1000)

                self._vinc_log("1. Abrindo pasta 'Cadastro'...")
                pasta_cadastro = page.get_by_text("Cadastro", exact=True)
                pasta_cadastro.wait_for(state="visible", timeout=15000)
                pasta_cadastro.dblclick()
                page.wait_for_timeout(800)

                self._vinc_log("2. Abrindo pasta 'Produto'...")
                pasta_produto = (
                    tree.locator(".x-tree3-node:not(.x-tree3-node-leaf)")
                    .get_by_text("Produto", exact=True)
                    .first
                )
                pasta_produto.wait_for(state="visible", timeout=15000)
                pasta_produto.dblclick()
                page.wait_for_timeout(800)

                self._vinc_log("3. Clicando no ItemProduto final...")
                item_produto = (
                    page.locator("[id$='__NavigationView_tree-ItemProduto']")
                    .get_by_text("Produto", exact=True)
                    .first
                )
                item_produto.wait_for(state="visible", timeout=15000)
                item_produto.click()

                frame = page.frame_locator("iframe[name^='gwt-uid-']")

                def aguardar_mascara():
                    try:
                        frame.locator(".mask, .ext-el-mask, .x-mask-loading").wait_for(state="hidden", timeout=25000)
                    except Exception:
                        pass

                self._vinc_log("Aguardando grid de produtos estabilizar...")
                campo_filtro_produto = frame.locator("#filter-PRODUTO")
                campo_filtro_produto.wait_for(state="visible", timeout=40000)
                aguardar_mascara()
                page.wait_for_timeout(1000)

                # 3. Paginação 2000
                self._vinc_log("Definindo paginação para 2000...")
                btn_2000 = page.get_by_role("button", name="2000")
                if not btn_2000.is_visible():
                    btn_2000 = frame.get_by_role("button", name="2000")
                btn_2000.wait_for(state="visible", timeout=10000)
                btn_2000.click()
                aguardar_mascara()
                page.wait_for_timeout(1200)

                turno_clique = 0

                # 4. Loop de Atualização
                for idx, descricao in enumerate(lista_descricoes, start=1):
                    if self.parar_vincular:
                        self._vinc_log("🛑 Operação cancelada pelo usuário.", "alerta")
                        break

                    desc_limpa = descricao.strip()
                    numero_inicial = extrair_numero_inicial(desc_limpa)
                    termo_busca = f"%{desc_limpa}%"

                    self._vinc_log(f"[{idx}/{total_itens}] Filtrando: {termo_busca}")

                    campo_filtro_produto.click()
                    campo_filtro_produto.fill(termo_busca)
                    campo_filtro_produto.press("Enter")
                    aguardar_mascara()
                    page.wait_for_timeout(1200)

                    try:
                        alvo = None
                        if turno_clique % 2 == 0:
                            self._vinc_log(f"[{idx}/{total_itens}] Clicando no Código de Referência (nth=1)...")
                            loc_pneu = frame.get_by_text(re.compile(r"PNEU.*-00\d")).nth(1)
                            if loc_pneu.is_visible():
                                alvo = loc_pneu
                            else:
                                alvo = frame.get_by_text(re.compile(r"PNEU")).nth(1)
                        else:
                            self._vinc_log(f"[{idx}/{total_itens}] Clicando na Descrição do Produto...")
                            alvo = frame.get_by_text(desc_limpa, exact=False).first

                        if not alvo or not alvo.is_visible():
                            alvo = frame.locator(".x-grid3-row").first

                        alvo.wait_for(state="visible", timeout=8000)
                        alvo.click(force=True)

                        alvo.evaluate("""el => {
                            const row = el.closest('.x-grid3-row') || el;
                            ['mousedown', 'mouseup', 'click'].forEach(evt => {
                                el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true }));
                                row.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true }));
                            });
                            row.classList.add('x-grid3-row-selected');
                        }""")
                        page.wait_for_timeout(800)

                        turno_clique += 1

                        self._vinc_log(f"[{idx}/{total_itens}] Clicando em 'Alterar'...")
                        btn_alterar = page.get_by_role("button", name="Alterar")
                        btn_alterar.wait_for(state="visible", timeout=6000)
                        btn_alterar.click()

                        aguardar_mascara()
                        page.wait_for_timeout(1500)

                        modal_produto = page.locator(".x-window").last

                        # ETAPA A: TIPO DO PRODUTO (%PNEU%)
                        self._vinc_log(f"[{idx}/{total_itens}] Configurando Tipo do Produto...")
                        campo_tipo = page.get_by_role("textbox", name="Tipo do Produto:", exact=True)
                        campo_tipo.wait_for(state="visible", timeout=10000)
                        campo_tipo.click()
                        page.wait_for_timeout(800)

                        modal_busca = page.locator("#SearchTriggerWindowRemote_searchTextField")
                        modal_busca.wait_for(state="visible", timeout=10000)
                        modal_busca.fill("%PNEU%")
                        modal_busca.press("Enter")
                        page.wait_for_timeout(1200)

                        item_pneu = page.locator(".x-grid3-row, .x-tree3-node").get_by_text("PNEU", exact=True).first
                        if not item_pneu.is_visible():
                            item_pneu = page.get_by_role("link").filter(has_text="PNEU").first
                        if not item_pneu.is_visible():
                            item_pneu = page.get_by_role("link").first

                        item_pneu.wait_for(state="visible", timeout=8000)
                        item_pneu.click()
                        page.wait_for_timeout(500)

                        btn_sel_tipo = page.get_by_role("button", name="Selecionar")
                        btn_sel_tipo.wait_for(state="visible", timeout=5000)
                        btn_sel_tipo.click()
                        page.wait_for_timeout(1200)

                        # ETAPA B: SUBTIPO DO PRODUTO
                        if numero_inicial:
                            self._vinc_log(f"[{idx}/{total_itens}] Abrindo SubTipo do Produto...")
                            campo_subtipo = page.get_by_role("textbox", name="SubTipo do Produto:", exact=True)
                            campo_subtipo.wait_for(state="visible", timeout=10000)
                            campo_subtipo.click()
                            page.wait_for_timeout(800)

                            janela_busca_subtipo = page.locator(".x-window").last
                            modal_busca.wait_for(state="visible", timeout=10000)
                            modal_busca.fill(numero_inicial)
                            modal_busca.press("Enter")
                            page.wait_for_timeout(1500)

                            linhas_resultado = janela_busca_subtipo.locator(".x-grid3-row")
                            achou_subtipo = False

                            try:
                                if linhas_resultado.count() > 0:
                                    item_subtipo = linhas_resultado.first
                                    item_subtipo.wait_for(state="visible", timeout=3000)
                                    item_subtipo.click()
                                    page.wait_for_timeout(500)
                                    btn_sel_subtipo = janela_busca_subtipo.locator("button:has-text('Selecionar')").first
                                    btn_sel_subtipo.click()
                                    page.wait_for_timeout(1200)
                                    achou_subtipo = True
                            except Exception:
                                achou_subtipo = False

                            if not achou_subtipo:
                                msg_aviso = f"Item {idx}: SubTipo '{numero_inicial}' NÃO ENCONTRADO"
                                self._vinc_log(f"⚠️ {msg_aviso}", "alerta")

                                self._vinc_registrar_nao_encontrado(numero_inicial, desc_limpa)
                                itens_subtipo_nao_encontrado.append(f"Número: {numero_inicial} | Descrição: {desc_limpa}")

                                try:
                                    btn_cancelar_busca = janela_busca_subtipo.locator(
                                        "button:has-text('Cancelar'), button:has-text('Fechar'), .x-tool-close"
                                    ).first
                                    if btn_cancelar_busca.is_visible():
                                        btn_cancelar_busca.click()
                                        page.wait_for_timeout(800)
                                except Exception:
                                    pass

                                try:
                                    btn_cancelar_prod = modal_produto.locator(
                                        "button:has-text('Cancelar'), button:has-text('Fechar'), .x-tool-close"
                                    ).first
                                    if btn_cancelar_prod.is_visible():
                                        btn_cancelar_prod.click()
                                        aguardar_mascara()
                                        page.wait_for_timeout(1000)
                                except Exception:
                                    pass

                                continue

                        # ETAPA C: SALVAR ALTERAÇÕES
                        self._vinc_log(f"[{idx}/{total_itens}] Salvando alterações...")
                        btn_salvar = modal_produto.locator("button:has-text('Salvar')").first
                        if not btn_salvar.is_visible():
                            btn_salvar = page.get_by_role("button", name="Salvar")
                        btn_salvar.wait_for(state="visible", timeout=6000)
                        btn_salvar.click()

                        aguardar_mascara()
                        page.wait_for_timeout(1500)
                        self._vinc_log(f"✓ [{idx}/{total_itens}] {desc_limpa} atualizado com sucesso!", "sucesso")

                    except Exception as err_item:
                        self._vinc_log(f"⚠️ Erro no item {idx} ({desc_limpa}): {err_item}", "erro")
                        try:
                            btn_cancelar = page.get_by_role("button", name="Cancelar")
                            if btn_cancelar.is_visible():
                                btn_cancelar.click()
                                page.wait_for_timeout(800)
                        except Exception:
                            pass
                        continue

                # Relatório final
                self._vinc_log("=" * 60)
                self._vinc_log("RELATÓRIO DE SUBTIPOS NÃO LOCALIZADOS:", "alerta")
                if itens_subtipo_nao_encontrado:
                    for nao_achou in itens_subtipo_nao_encontrado:
                        self._vinc_log(f"- {nao_achou}", "erro")
                    self._vinc_log(f"Total com SubTipo ausente: {len(itens_subtipo_nao_encontrado)}", "alerta")
                else:
                    self._vinc_log("Todos os SubTipos foram localizados e atualizados!", "sucesso")
                self._vinc_log("=" * 60)

                self._notificar(
                    "Concluído",
                    f"Processo finalizado! Total processados: {total_itens} | SubTipos não encontrados: {len(itens_subtipo_nao_encontrado)}",
                )

                context.close()
                browser.close()

        except Exception as e:
            self._vinc_log(f"Erro geral: {str(e)}", "erro")
            self._notificar("Erro", f"Falha no processo: {str(e)}")

        finally:
            self.robo_ativo = False
            self.parar_vincular = False
            self._set_status_global("● Sistema Pronto", "#4ade80")


painel = PainelSiltUnificadoWeb()


# =========================================================================
# ROTAS WEB (CONECTAM O HTML À SUA CLASSE PYTHON)
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
        itens_iniciais="\n".join(ITENS_INICIAIS_SUBTIPO),
    )


@app.route("/api/estado")
def api_estado():
    alerta = painel.alerta_popup
    painel.alerta_popup = None
    return jsonify({
        "robo_ativo": painel.robo_ativo,
        "status_global": painel.status_global,
        "status_cor": painel.status_cor,
        "logs_inv": painel.logs_inv,
        "logs_sub": painel.logs_sub,
        "logs_vinc": painel.logs_vinc,
        "nao_encontrados_vinc": painel.nao_encontrados_vinc,
        "sub_codigo_atual": painel.sub_codigo_atual,
        "sub_item_atual": painel.sub_item_atual,
        "sub_status": painel.sub_status,
        "alerta": alerta,
    })


@app.route("/api/stop", methods=["POST"])
def api_stop():
    aba = request.json.get("aba")
    if not painel.robo_ativo:
        if aba == "inv":
            painel._inv_log("Nenhuma automação em execução no momento.")
        return jsonify({"ok": True})

    if aba == "inv":
        painel._inv_log("🛑 Solicitando parada imediata (STOP)...")
        painel.parar_inv = True
    elif aba == "sub":
        painel.parar_subtipo = True
        painel._sub_log("⚠️ Interrupção solicitada. Finalizando após o item atual...")
    elif aba == "vinc":
        painel.parar_vincular = True
        painel._vinc_log("⚠️ Interrupção solicitada. Finalizando após o item atual...", "alerta")
    return jsonify({"ok": True})


@app.route("/api/inv/salvar_usuario", methods=["POST"])
def api_inv_salvar_usuario():
    novo_id = request.json.get("id", "").strip()
    novo_nome = request.json.get("nome", "").strip()
    if not novo_id or not novo_nome:
        return jsonify({"ok": False, "msg": "Preencha o ID e o Nome do novo usuário!"})
    reg = f"{novo_id} - {novo_nome}"
    if reg in LISTA_USUARIOS:
        return jsonify({"ok": False, "msg": f"O usuário '{reg}' já está na lista!"})
    LISTA_USUARIOS.append(reg)
    salvar_lista_usuarios(LISTA_USUARIOS)
    painel._inv_log(f"Novo usuário salvo: {reg}")
    return jsonify({"ok": True, "usuarios": LISTA_USUARIOS, "msg": f"Usuário '{reg}' salvo com sucesso!"})


@app.route("/api/inv/iniciar", methods=["POST"])
def api_inv_iniciar():
    if painel.robo_ativo:
        return jsonify({"ok": False, "msg": "Já existe uma automação em execução em outra aba!"})

    body = request.json
    usr1 = extrair_codigo_usuario(body.get("usr1", ""))
    usr2 = extrair_codigo_usuario(body.get("usr2", ""))
    dep = body.get("depositante", "").strip()
    tipo_inv = body.get("tipo_inventario", "")
    usuario = body.get("usuario", "").strip()
    senha = body.get("senha", "").strip()
    armazem = body.get("armazem", "").strip()

    if not dep or dep == "N/A":
        return jsonify({"ok": False, "msg": "Selecione um Depositante válido!"})
    if not usr1:
        return jsonify({"ok": False, "msg": "Selecione o Usuário 1 (não pode ser N/A)!"})
    if not usuario or not senha:
        return jsonify({"ok": False, "msg": "Preencha o usuário e a senha!"})

    dados = {
        "tipo_inventario": tipo_inv,
        "usuario": usuario,
        "senha": senha,
        "armazem": armazem,
        "depositante": dep,
        "usr1": usr1,
        "usr2": usr2,
    }

    painel.robo_ativo = True
    painel.parar_inv = False
    painel.logs_inv = []
    painel._set_status_global("● Executando: Inventário (Chromium)", "#facc15")

    threading.Thread(target=painel._executar_automacao_inventario, args=(dados,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/sub/extrair", methods=["POST"])
def api_sub_extrair():
    conteudo = request.json.get("texto", "").splitlines()
    itens_filtrados = []
    duplicados = 0
    for linha in conteudo:
        medida = extrair_padrao_subtipo(linha)
        if medida:
            if medida not in itens_filtrados:
                itens_filtrados.append(medida)
            else:
                duplicados += 1
    if not itens_filtrados:
        return jsonify({"ok": False, "msg": "Nenhum formato de pneu/medida válido foi detectado no texto!"})
    painel._sub_log(f"✅ {len(itens_filtrados)} medidas únicas padronizadas. ({duplicados} repetidos eliminados)")
    return jsonify({"ok": True, "itens": itens_filtrados})


@app.route("/api/sub/iniciar", methods=["POST"])
def api_sub_iniciar():
    if painel.robo_ativo:
        return jsonify({"ok": False, "msg": "Já existe uma automação em execução em outra aba!"})

    body = request.json
    itens = [linha.strip() for linha in body.get("itens", "").splitlines() if linha.strip()]
    if not itens:
        return jsonify({"ok": False, "msg": "A caixa de itens está vazia!"})

    try:
        codigo_inicial = int(str(body.get("codigo", "60")).strip())
    except ValueError:
        return jsonify({"ok": False, "msg": "O código inicial deve ser um número inteiro (ex: 60)!"})

    dados = {
        "url": body.get("url", URL_SISTEMA).strip(),
        "usuario": body.get("usuario", "").strip(),
        "senha": body.get("senha", "").strip(),
        "armazem": body.get("armazem", "").strip(),
        "tipo": body.get("tipo", "PNEU").strip(),
    }

    painel.robo_ativo = True
    painel.parar_subtipo = False
    painel.logs_sub = []
    painel.sub_status = "Robô em execução no Chromium..."
    painel._set_status_global("● Executando: Cadastro de SubTipo (Chromium)", "#facc15")

    threading.Thread(target=painel._executar_playwright_subtipo, args=(dados, itens, codigo_inicial), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/vinc/iniciar", methods=["POST"])
def api_vinc_iniciar():
    if painel.robo_ativo:
        return jsonify({"ok": False, "msg": "Já existe uma automação em execução em outra aba!"})

    arquivo = request.files.get("arquivo")
    if not arquivo:
        return jsonify({"ok": False, "msg": "Selecione um arquivo CSV ou Excel!"})

    caminho_temp = os.path.join("/tmp", arquivo.filename)
    arquivo.save(caminho_temp)

    usuario = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "").strip()
    armazem = request.form.get("armazem", "").strip()

    painel.robo_ativo = True
    painel.parar_vincular = False
    painel.logs_vinc = []
    painel.nao_encontrados_vinc = []
    painel._set_status_global("● Executando: Vincular Produto (Chromium)", "#facc15")

    threading.Thread(target=painel._executar_automacao_vincular, args=(usuario, senha, armazem, caminho_temp), daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
