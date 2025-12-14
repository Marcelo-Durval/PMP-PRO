import streamlit as st
import pandas as pd
import re
import io
import time
import os
import cv2
import numpy as np
import zxingcpp
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from PIL import Image

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Sistema PMP Fluxo Contínuo", layout="wide", page_icon="🏭")

# --- BANCO DE DADOS ---
try:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sistema_local.db")
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    Base = declarative_base()
except Exception as e:
    st.error(f"❌ Erro fatal na configuração do Banco: {e}")
    st.stop()

# --- MODELOS ---
class Usuario(Base):
    __tablename__ = 'usuarios'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    senha = Column(String)
    perfil = Column(String)

class Pedido(Base):
    __tablename__ = 'pedidos'
    id = Column(Integer, primary_key=True)
    numero_pedido = Column(String)
    data_pedido = Column(String)
    status = Column(String)
    criado_em = Column(DateTime, default=datetime.now)
    data_conclusao = Column(DateTime, nullable=True)
    itens = relationship("ItemPedido", back_populates="pedido", cascade="all, delete")
    logs = relationship("LogTempo", back_populates="pedido", cascade="all, delete")

class ItemPedido(Base):
    __tablename__ = 'itens_pedido'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'))
    codigo = Column(String)
    descricao = Column(String)
    unidade = Column(String)
    qtd_solicitada = Column(Float)
    justificativa_divergencia = Column(Text, nullable=True)
    item_adicionado_manualmente = Column(Boolean, default=False)
    pedido = relationship("Pedido", back_populates="itens")
    separacoes = relationship("Separacao", back_populates="item", cascade="all, delete")

class Separacao(Base):
    __tablename__ = 'separacoes'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('itens_pedido.id'))
    rastreabilidade = Column(String)
    qtd_separada = Column(Float)
    
    # --- NOVO CAMPO: Quantidade contada pelo conferente ---
    qtd_conferida = Column(Float, nullable=True)
    
    separador_id = Column(Integer, ForeignKey('usuarios.id'))
    registrado_em = Column(DateTime, default=datetime.now)
    enviado_conferencia = Column(Boolean, default=False)
    conferido = Column(Boolean, default=False)
    motivo_rejeicao = Column(Text, nullable=True)
    enviado_sistema = Column(Boolean, default=False)
    data_envio = Column(DateTime, nullable=True)
    item = relationship("ItemPedido", back_populates="separacoes")

class LogTempo(Base):
    __tablename__ = 'logs_tempo'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'))
    usuario_id = Column(Integer, ForeignKey('usuarios.id'))
    acao = Column(String)
    timestamp = Column(DateTime, default=datetime.now)
    pedido = relationship("Pedido", back_populates="logs")

# --- CRIAÇÃO DAS TABELAS ---
try: Base.metadata.create_all(engine)
except: pass

# --- FUNÇÕES AUXILIARES ---
def get_db():
    if 'db' not in st.session_state: st.session_state.db = Session()
    return st.session_state.db

def init_users():
    s = get_db()
    try:
        if not s.query(Usuario).filter_by(username='admin').first():
            s.add(Usuario(username='admin', senha='123', perfil='ADM'))
            s.commit()
    except: pass

def encerrar_cronometros_abertos(session, pedido_id):
    logs = session.query(LogTempo).filter_by(pedido_id=pedido_id).all()
    user_logs = {}
    for log in logs:
        if log.usuario_id not in user_logs: user_logs[log.usuario_id] = []
        user_logs[log.usuario_id].append(log)
    for uid, ulogs in user_logs.items():
        ulogs.sort(key=lambda x: x.timestamp)
        if ulogs and ulogs[-1].acao == "INICIO":
            session.add(LogTempo(pedido_id=pedido_id, usuario_id=uid, acao="FIM", timestamp=datetime.now()))
    session.commit()

def calcular_tempos_reais(session, pedido_id):
    logs = session.query(LogTempo).filter_by(pedido_id=pedido_id).order_by(LogTempo.timestamp).all()
    tempos = {}
    status_atual = {}
    user_logs = {}
    for log in logs:
        if log.usuario_id not in user_logs: user_logs[log.usuario_id] = []
        user_logs[log.usuario_id].append(log)
    for uid, ulogs in user_logs.items():
        total = timedelta(0)
        inicio_periodo = None
        for log in ulogs:
            if log.acao == "INICIO":
                inicio_periodo = log.timestamp
                status_atual[uid] = 'RODANDO'
            elif (log.acao == "PAUSA" or log.acao == "FIM"):
                if inicio_periodo:
                    total += (log.timestamp - inicio_periodo)
                    inicio_periodo = None
                status_atual[uid] = 'PARADO'
        if inicio_periodo:
            total += (datetime.now() - inicio_periodo)
        tempos[uid] = total
    return tempos, status_atual

def formatar_delta(delta):
    if not delta: return "00:00:00"
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def processar_arquivo_robusto(uploaded_file):
    df_raw = None
    try: df_raw = pd.read_excel(uploaded_file, header=None, dtype=str)
    except:
        try: uploaded_file.seek(0); content = uploaded_file.getvalue().decode('latin-1')
        except: content = uploaded_file.getvalue().decode('utf-8')
        df_raw = pd.DataFrame([line.split(',') for line in content.split('\n')])

    data_ped, num_ped = "", "SEM_NUMERO"
    itens = []
    reading = False
    reg_data = re.compile(r'(\d{2}/\d{2}/\d{4})')
    reg_ped = re.compile(r'(?<!\d)(\d{5,6})(?!\d)')

    for row in df_raw.itertuples(index=False):
        row_clean = [str(x).strip() for x in row if str(x).lower() not in ['nan', 'none', '', 'nat']]
        line_str = " ".join(row_clean)
        if "Data" in line_str and not data_ped:
            m = reg_data.search(line_str)
            if m: data_ped = m.group(1)
        if "Pedido" in line_str and "SEM_NUMERO" in num_ped:
            m = reg_ped.search(line_str)
            if m: num_ped = m.group(1)
        if "TOTAIS" in line_str.replace(" ", "").upper(): reading = True; continue
        if reading and len(row_clean) >= 3:
            first = row_clean[0].replace('"', '')
            last = row_clean[-1].replace('"', '').replace(',', '.')
            if first.isdigit():
                try: itens.append({"cod": first, "desc": " ".join(row_clean[1:-1]), "und": row_clean[-2] if len(row_clean)>=4 else "UN", "qtd": float(last)})
                except: continue
    return itens, num_ped, data_ped

# --- LEITURA PODEROSA COM ZXING ---
def tentar_ler_codigo_robustamente(uploaded_image):
    try:
        file_bytes = np.asarray(bytearray(uploaded_image.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, 1)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        results = zxingcpp.read_barcodes(img_rgb)
        if results: return results[0].text

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)
        
        results_gray = zxingcpp.read_barcodes(enhanced)
        if results_gray: return results_gray[0].text

        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        results_bin = zxingcpp.read_barcodes(binary)
        if results_bin: return results_bin[0].text
            
        return None
    except Exception as e:
        print(f"Erro ZXing: {e}")
        return None

# --- TELAS ---
def login_screen():
    st.markdown("<h2 style='text-align: center;'>🏭 PMP Flow Pro V2</h2>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        with st.form("login"):
            u = st.text_input("Usuário"); p = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                s = get_db()
                try:
                    user = s.query(Usuario).filter_by(username=u, senha=p).first()
                    if user: st.session_state['user'] = user; st.rerun()
                    else: st.error("Dados inválidos")
                except Exception as e: st.error(f"Erro: {e}")

def adm_screen():
    s = get_db()
    st.title(f"Painel Gerencial (ADM: {st.session_state['user'].username})")
    
    qv = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').count()
    qa = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').count()
    
    t1, t2, t3, t4 = st.tabs(["📥 Importar", f"🛡️ Validação ({qv})", f"🏭 Gestão Contínua ({qa})", "👥 Usuários"])

    with t1:
        f = st.file_uploader("Arquivo PMP", type=["xls", "csv"])
        if f and st.button("Processar"):
            itens, num, dat = processar_arquivo_robusto(f)
            if itens:
                if s.query(Pedido).filter_by(numero_pedido=num).first(): st.error("Existe!")
                else:
                    ped = Pedido(numero_pedido=num, data_pedido=dat, status="VALIDACAO")
                    s.add(ped); s.flush()
                    for i in itens: s.add(ItemPedido(pedido_id=ped.id, codigo=i['cod'], descricao=i['desc'], unidade=i['und'], qtd_solicitada=i['qtd']))
                    s.commit(); st.success(f"Pedido {num} na Validação!")
            else: st.error("Erro leitura")

    with t2:
        validacoes = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').all()
        if not validacoes: st.caption("Vazio.")
        else:
            pid = st.selectbox("Limpar:", [p.id for p in validacoes], format_func=lambda x: next((f"{p.numero_pedido}" for p in validacoes if p.id==x), x))
            pval = s.query(Pedido).get(pid)
            dval = pd.DataFrame([{"ID": i.id, "Código": i.codigo, "Descrição": i.descricao, "Qtd": i.qtd_solicitada, "Manter?": True} for i in pval.itens])
            edf = st.data_editor(dval, num_rows="dynamic", column_config={"ID": st.column_config.NumberColumn(disabled=True), "Manter?": st.column_config.CheckboxColumn(default=True)}, hide_index=True, key="ev")
            c1, c2 = st.columns(2)
            if c1.button("🗑️ Excluir"): s.delete(pval); s.commit(); st.rerun()
            if c2.button("🚀 Liberar p/ Produção"):
                itens_banco = {i.id: i for i in pval.itens}; ids_manter = []
                for index, row in edf.iterrows():
                    if row.get("Manter?", True):
                        rid = row.get("ID")
                        if pd.isna(rid): s.add(ItemPedido(pedido_id=pval.id, codigo=str(row["Código"]), descricao=str(row["Descrição"]), unidade="UN", qtd_solicitada=float(row["Qtd"])))
                        else: ids_manter.append(int(rid))
                for db_id, db_item in itens_banco.items():
                    if db_id not in ids_manter: s.delete(db_item)
                pval.status = "EM_ANDAMENTO"; s.commit(); st.success("Liberado!"); time.sleep(1); st.rerun()

    with t3:
        peds_ativos = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').order_by(Pedido.id.desc()).all()
        peds_concluidos = s.query(Pedido).filter(Pedido.status == 'CONCLUIDO').order_by(Pedido.id.desc()).limit(5).all()
        lista_peds = peds_ativos + peds_concluidos
        if not lista_peds: st.info("Nenhum pedido.")
        pid = st.selectbox("Selecione Pedido", [p.id for p in lista_peds], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in lista_peds if p.id==x), x))
        ped = s.query(Pedido).get(pid)
        if ped:
            st.divider()
            c_head, c_btn_reopen = st.columns([4, 1])
            c_head.markdown(f"### 🏭 Pedido: {ped.numero_pedido} | Status: {ped.status}")
            if ped.status == 'CONCLUIDO':
                if c_btn_reopen.button("🔓 Reabrir", type="primary"):
                    ped.status = "EM_ANDAMENTO"; ped.data_conclusao = None; s.commit(); st.rerun()
            
            tempos_individuais, status_live = calcular_tempos_reais(s, ped.id)
            tempo_equipe_str = formatar_delta(sum(tempos_individuais.values(), timedelta(0)))
            with st.expander("⏱️ Tempos da Equipe", expanded=False):
                st.metric("Total Equipe", tempo_equipe_str)
                cols = st.columns(4); idx=0
                for uid, delta in tempos_individuais.items():
                     unome = s.query(Usuario).get(uid).username
                     cols[idx%4].text(f"{unome}: {formatar_delta(delta)}")
                     idx+=1

            if ped.status != 'CONCLUIDO':
                 with st.expander("➕ Adicionar Extra"):
                    with st.form("add_extra"):
                         c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
                         nc = c1.text_input("Cód"); nd = c2.text_input("Desc"); nq = c3.number_input("Qtd", min_value=0.1)
                         if c4.form_submit_button("Add") and nc:
                             s.add(ItemPedido(pedido_id=ped.id, codigo=nc, descricao=nd, unidade="UN", qtd_solicitada=nq, item_adicionado_manualmente=True)); s.commit(); st.rerun()

            pendencias_lancamento = 0
            pendencias_separacao = 0
            
            for it in ped.itens:
                tot_sep = sum([sep.qtd_separada for sep in it.separacoes])
                meta = it.qtd_solicitada
                
                if tot_sep == 0: style, icon = f"{it.codigo} {it.descricao}", "⬜"
                elif tot_sep < meta: style, icon = f":orange[{it.codigo} {it.descricao}]", "⏳"
                elif tot_sep == meta: style, icon = f":green[{it.codigo} {it.descricao}]", "✅"
                else: style, icon = f":red[{it.codigo} {it.descricao}]", "🚫"

                with st.expander(f"{icon} {style} ({tot_sep}/{meta})"):
                     if (tot_sep != meta) and ped.status != 'CONCLUIDO':
                         j = st.text_input("Justificativa", value=it.justificativa_divergencia or "", key=f"j_{it.id}")
                         if j != it.justificativa_divergencia: it.justificativa_divergencia = j; s.commit()
                     
                     if not it.separacoes: st.caption("Nada separado.")
                     
                     # --- CABEÇALHO DA TABELA ADM ---
                     cols_h = st.columns([3, 1, 1, 2, 2, 1])
                     cols_h[0].markdown("**Rastreabilidade**")
                     cols_h[1].markdown("**Sep.**")
                     cols_h[2].markdown("**Conf.**") # Nova coluna para ADM ver
                     cols_h[3].markdown("**Status Conf.**")
                     cols_h[4].markdown("**ERP**")

                     for sep in it.separacoes:
                         c1, c2, c3, c4, c5, c6 = st.columns([3, 1, 1, 2, 2, 1])
                         c1.text(sep.rastreabilidade)
                         c2.text(sep.qtd_separada)
                         
                         # --- COLUNA NOVA: MOSTRAR CONFERIDO ---
                         # Se houver divergencia, pinta de vermelho
                         val_conf = sep.qtd_conferida if sep.qtd_conferida is not None else 0.0
                         if sep.conferido and (val_conf != sep.qtd_separada):
                             c3.markdown(f":red[**{val_conf}**]") # Alerta visual
                         else:
                             c3.text(val_conf if sep.conferido else "-")
                         
                         if sep.motivo_rejeicao: c4.error("RECUSADO")
                         elif sep.conferido: c4.success("CONFERIDO")
                         elif sep.enviado_conferencia: c4.warning("NA CONFERÊNCIA")
                         else: c4.caption("NA SEPARAÇÃO")
                         
                         disable_erp = (ped.status == 'CONCLUIDO')
                         is_chk = c5.checkbox("Lançado", value=sep.enviado_sistema, key=f"erp_{sep.id}", disabled=disable_erp)
                         if is_chk != sep.enviado_sistema:
                             sep.enviado_sistema = is_chk; sep.data_envio = datetime.now() if is_chk else None; s.commit(); st.rerun()
                         
                         if not sep.motivo_rejeicao and not sep.enviado_sistema: 
                             pendencias_lancamento += 1

                if tot_sep < meta: pendencias_separacao += 1
            
            st.divider()
            
            if ped.status == 'CONCLUIDO':
                st.success(f"Encerrado em {ped.data_conclusao}")
                data_xls = []
                for i in ped.itens:
                    base = {"Cod": i.codigo, "Desc": i.descricao, "Meta": i.qtd_solicitada, "Justificativa": i.justificativa_divergencia}
                    if not i.separacoes:
                        base.update({"Qtd": 0, "Rastreabilidade": "-", "Conferido": "NAO"}); data_xls.append(base)
                    for sp in i.separacoes:
                        ln = base.copy(); ln.update({"Qtd": sp.qtd_separada, "Qtd Conferida": sp.qtd_conferida, "Rastreabilidade": sp.rastreabilidade, "Conferido": "SIM" if sp.conferido else "NAO", "ERP": "SIM" if sp.enviado_sistema else "NAO"}); data_xls.append(ln)
                out = io.BytesIO()
                with pd.ExcelWriter(out, engine='xlsxwriter') as w: pd.DataFrame(data_xls).to_excel(w, index=False)
                st.download_button("⬇️ Excel Final", out, f"F_{ped.numero_pedido}.xlsx")
            else:
                st.markdown(f"**Status Atual:** {pendencias_lancamento} itens pendentes de lançamento no ERP. {pendencias_separacao} itens com saldo de separação.")
                
                if pendencias_lancamento > 0:
                     st.error(f"🚫 Impossível arquivar: Existem {pendencias_lancamento} itens que ainda não foram lançados no sistema.")
                else:
                    if st.button("✅ CONCLUIR PEDIDO (ARQUIVAR)", type="primary"):
                        encerrar_cronometros_abertos(s, ped.id)
                        ped.status = 'CONCLUIDO'; ped.data_conclusao = datetime.now(); s.commit(); st.balloons(); time.sleep(1); st.rerun()

    with t4:
        with st.form("nu"):
            c1, c2, c3, c4 = st.columns(4)
            nu = c1.text_input("User"); np = c2.text_input("Pass", type="password"); nr = c3.selectbox("Perfil", ["ADM", "SEPARADOR", "CONFERENTE", "AMBOS"])
            if c4.form_submit_button("Criar"):
                try: s.add(Usuario(username=nu, senha=np, perfil=nr)); s.commit(); st.success("OK!"); st.rerun()
                except: st.error("Erro")
        st.divider()
        for u in s.query(Usuario).all(): st.text(f"{u.username} - {u.perfil}")

def op_screen():
    s = get_db()
    u = st.session_state['user']
    st.subheader(f"Operação: {u.username} ({u.perfil})")
    
    tabs = []
    if u.perfil in ['SEPARADOR', 'AMBOS']: tabs.append("📦 Separação")
    if u.perfil in ['CONFERENTE', 'AMBOS']: tabs.append("📋 Conferência")
    
    if not tabs: st.error("Sem acesso."); return
    ts = st.tabs(tabs)
    
    # --- ABA SEPARAÇÃO ---
    if "📦 Separação" in tabs:
        with ts[tabs.index("📦 Separação")]:
            peds_all = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').all()
            peds_visiveis = []
            for p in peds_all:
                mostrar = False
                for it in p.itens:
                    tot = sum([x.qtd_separada for x in it.separacoes])
                    if tot < it.qtd_solicitada: mostrar = True
                    rascunhos = [x for x in it.separacoes if (not x.enviado_conferencia) or (x.motivo_rejeicao)]
                    if rascunhos: mostrar = True
                if mostrar: peds_visiveis.append(p)

            if not peds_visiveis: st.info("Tudo em dia! Aguardando novos pedidos do ADM.")
            else:
                pid = st.selectbox("Selecione Pedido", [p.id for p in peds_visiveis], format_func=lambda x: next((f"{p.numero_pedido}" for p in peds_visiveis if p.id==x), x), key="sel_ped_sep")
                ped = s.query(Pedido).get(pid)
                
                tempos, _ = calcular_tempos_reais(s, ped.id)
                meu_tempo = tempos.get(u.id, timedelta(0))
                st.caption(f"Tempo acumulado: {formatar_delta(meu_tempo)}")
                
                log = s.query(LogTempo).filter_by(pedido_id=ped.id, usuario_id=u.id).order_by(LogTempo.timestamp.desc()).first()
                working = (log and log.acao == "INICIO")
                
                c_btn, c_mode, _ = st.columns([1, 2, 2])
                if not working:
                    if c_btn.button("▶️ INICIAR TRABALHO", type="primary"):
                        s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="INICIO")); s.commit(); st.rerun()
                else:
                    if c_btn.button("⏸️ PAUSAR"):
                        s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="PAUSA")); s.commit(); st.rerun()
                
                use_camera = c_mode.toggle("📸 Câmera (Melhorado)")

                st.divider()
                pendencias_envio = 0
                
                for it in ped.itens:
                    done = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                    meta = round(it.qtd_solicitada, 2)
                    meus_rascunhos = [x for x in it.separacoes if not x.enviado_conferencia]
                    
                    if done == 0: style, icon = f"{it.codigo} {it.descricao}", "⬜"
                    elif done < meta: style, icon = f":orange[{it.codigo} {it.descricao}]", "⏳"
                    elif done == meta: style, icon = f":green[{it.codigo} {it.descricao}]", "✅"
                    else: style, icon = f":red[{it.codigo} {it.descricao}]", "🚫"
                    
                    open_exp = (done < meta) or (len(meus_rascunhos) > 0)
                    
                    with st.expander(f"{icon} {style} ({done}/{meta})", expanded=open_exp):
                        for sep in it.separacoes:
                            c1, c2, c3 = st.columns([4, 2, 1])
                            lbl = sep.rastreabilidade
                            if sep.motivo_rejeicao: 
                                lbl += f" ❌ RECUSADO: {sep.motivo_rejeicao}"
                                c1.error(lbl)
                            elif sep.enviado_conferencia:
                                lbl += " (Enviado)"
                                c1.text(lbl)
                            else:
                                lbl += " (Rascunho)"
                                c1.markdown(f"**{lbl}**")
                            c2.text(sep.qtd_separada)
                            pode_apagar = (not sep.enviado_conferencia) or (sep.motivo_rejeicao is not None)
                            if pode_apagar:
                                if c3.button("🗑️", key=f"del_{sep.id}"):
                                    s.delete(sep); s.commit(); st.rerun()
                            if not sep.enviado_conferencia and not sep.motivo_rejeicao:
                                pendencias_envio += 1

                        if working:
                            if done < meta or meta == 0:
                                st.markdown("---")
                                with st.form(key=f"add_sep_{it.id}", clear_on_submit=True):
                                    if use_camera:
                                        img = st.camera_input("Foto do Código", key=f"cam_{it.id}")
                                        decoded_text = ""
                                        if img:
                                            decoded_text = tentar_ler_codigo_robustamente(img)
                                            if decoded_text:
                                                st.success(f"Lido: {decoded_text}")
                                            else:
                                                st.error("⚠️ Falha na leitura.")
                                        
                                        val_inicial = decoded_text if decoded_text else ""
                                        nr = st.text_input("Rastreabilidade", value=val_inicial)
                                    else:
                                        nr = st.text_input("Rastreabilidade", placeholder="Bipe aqui...")

                                    nq = st.number_input("Qtd", min_value=0.01, step=0.1)
                                    
                                    if st.form_submit_button("Salvar"):
                                        if nr and nq:
                                            s.add(Separacao(item_id=it.id, rastreabilidade=nr, qtd_separada=nq, separador_id=u.id, enviado_conferencia=False))
                                            s.commit(); st.rerun()
                                        else:
                                            st.warning("Preencha os dados.")
                        else:
                            st.caption("Inicie o trabalho para adicionar.")

                st.divider()
                if pendencias_envio > 0:
                    st.warning(f"Você tem {pendencias_envio} rastreabilidades prontas.")
                    if st.button("🚀 ENVIAR TUDO PARA CONFERÊNCIA", type="primary"):
                        rascunhos = s.query(Separacao).join(ItemPedido).filter(ItemPedido.pedido_id == ped.id, Separacao.enviado_conferencia == False).all()
                        for r in rascunhos: r.enviado_conferencia = True
                        s.commit(); st.success("Enviado!"); time.sleep(1); st.rerun()
                else:
                    if working: st.info("Adicione itens para enviar.")

    # --- ABA CONFERÊNCIA (LÓGICA NOVA DE CONTAGEM) ---
    if "📋 Conferência" in tabs:
        with ts[tabs.index("📋 Conferência")]:
            peds_conf = []
            raw_peds = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').all()
            for p in raw_peds:
                tem = False
                for it in p.itens:
                    # Pendente se: enviado pra conf E não conferido E não rejeitado
                    pendentes = [x for x in it.separacoes if x.enviado_conferencia and not x.conferido and not x.motivo_rejeicao]
                    if pendentes: tem = True; break
                if tem: peds_conf.append(p)
            
            if not peds_conf: st.info("Nada para conferir.")
            else:
                pid = st.selectbox("Selecione Pedido", [p.id for p in peds_conf], format_func=lambda x: next((f"{p.numero_pedido}" for p in peds_conf if p.id==x), x), key="sel_ped_conf")
                ped = s.query(Pedido).get(pid)
                st.divider()
                st.markdown("### Itens aguardando sua contagem")
                count_pend = 0
                for it in ped.itens:
                    to_check = [x for x in it.separacoes if x.enviado_conferencia and not x.conferido and not x.motivo_rejeicao]
                    if to_check:
                        with st.expander(f"{it.codigo} {it.descricao} ({len(to_check)} lotes)", expanded=True):
                            # Cabeçalho da tabela de conferência
                            c_h1, c_h2, c_h3, c_h4 = st.columns([3, 1, 2, 2])
                            c_h1.markdown("**Rastreabilidade**")
                            c_h2.markdown("**Qtd (Sep)**")
                            c_h3.markdown("**Sua Contagem**")
                            c_h4.markdown("**Ação**")
                            
                            for sep in to_check:
                                c1, c2, c3, c4 = st.columns([3, 1, 2, 2])
                                c1.text(sep.rastreabilidade)
                                c2.text(sep.qtd_separada)
                                
                                # Input de Contagem
                                key_in = f"in_conf_{sep.id}"
                                val_contada = c3.number_input("Qtd", key=key_in, step=0.1, label_visibility="collapsed")
                                
                                # Botão de Ação
                                if c4.button("Conferir", key=f"btn_check_{sep.id}"):
                                    if val_contada == sep.qtd_separada:
                                        # Caso ideal: Bateu!
                                        sep.qtd_conferida = val_contada
                                        sep.conferido = True
                                        sep.data_conferencia = datetime.now()
                                        s.commit()
                                        st.toast("✅ Contagem Correta! Aprovado.")
                                        time.sleep(0.5)
                                        st.rerun()
                                    else:
                                        # Divergência: Salva no estado para mostrar alerta
                                        st.session_state[f"alert_div_{sep.id}"] = True
                                
                                # Se houver alerta de divergência no estado
                                if st.session_state.get(f"alert_div_{sep.id}"):
                                    st.warning(f"⚠️ DIVERGÊNCIA! Separado: {sep.qtd_separada} | Contado: {val_contada}")
                                    cola, colb = st.columns(2)
                                    if cola.button("Aceitar Divergência", key=f"accept_{sep.id}"):
                                        sep.qtd_conferida = val_contada
                                        sep.conferido = True
                                        sep.data_conferencia = datetime.now()
                                        del st.session_state[f"alert_div_{sep.id}"] # Limpa alerta
                                        s.commit()
                                        st.rerun()
                                    
                                    if colb.button("Recusar/Devolver", key=f"reject_{sep.id}"):
                                        sep.motivo_rejeicao = f"Divergência de Qtd (Sep: {sep.qtd_separada} vs Conf: {val_contada})"
                                        sep.enviado_conferencia = False
                                        sep.conferido = False
                                        del st.session_state[f"alert_div_{sep.id}"] # Limpa alerta
                                        s.commit()
                                        st.rerun()

                                count_pend += 1
                if count_pend == 0: st.success("Tudo conferido!")

# --- MAIN ---
init_users()
if 'user' not in st.session_state: login_screen()
else:
    st.sidebar.button("Sair", on_click=lambda: st.session_state.pop('user'))
    if st.session_state['user'].perfil == 'ADM': adm_screen()
    else: op_screen()
