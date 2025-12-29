import streamlit as st
import pandas as pd
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
st.set_page_config(page_title="Sistema PMP Pro - Gestão Total", layout="wide", page_icon="🏭")

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

class PreferenciaMapping(Base):
    __tablename__ = 'prefs_mapping'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('usuarios.id'), nullable=True) 
    col_id_unico = Column(String)
    col_codigo = Column(String)
    col_descricao = Column(String)
    col_qtd = Column(String)
    col_unidade = Column(String)

class Pedido(Base):
    __tablename__ = 'pedidos'
    id = Column(Integer, primary_key=True)
    numero_pedido = Column(String)
    data_pedido = Column(String)
    status = Column(String)
    criado_em = Column(DateTime, default=datetime.now)
    data_conclusao = Column(DateTime, nullable=True)
    observacao = Column(Text, nullable=True)
    itens = relationship("ItemPedido", back_populates="pedido", cascade="all, delete")
    logs = relationship("LogTempo", back_populates="pedido", cascade="all, delete")

class ItemPedido(Base):
    __tablename__ = 'itens_pedido'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'))
    id_importacao = Column(String, nullable=True) 
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
    qtd_conferida = Column(Float, nullable=True)
    separador_id = Column(Integer, ForeignKey('usuarios.id'))
    registrado_em = Column(DateTime, default=datetime.now)
    enviado_conferencia = Column(Boolean, default=False)
    conferido = Column(Boolean, default=False)
    motivo_rejeicao = Column(Text, nullable=True)
    enviado_sistema = Column(Boolean, default=False)
    data_envio = Column(DateTime, nullable=True)
    divergencia_aceita_adm = Column(Boolean, default=False)
    cnt_rejeicoes = Column(Integer, default=0)
    item = relationship("ItemPedido", back_populates="separacoes")

class LogTempo(Base):
    __tablename__ = 'logs_tempo'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'))
    usuario_id = Column(Integer, ForeignKey('usuarios.id'))
    acao = Column(String)
    tipo_atividade = Column(String) # SEPARACAO ou CONFERENCIA
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
        key = (log.usuario_id, log.tipo_atividade)
        if key not in user_logs: user_logs[key] = []
        user_logs[key].append(log)
    for (uid, tipo), ulogs in user_logs.items():
        ulogs.sort(key=lambda x: x.timestamp)
        if ulogs and ulogs[-1].acao == "INICIO":
            session.add(LogTempo(pedido_id=pedido_id, usuario_id=uid, acao="FIM", tipo_atividade=tipo, timestamp=datetime.now()))
    session.commit()

def calcular_tempos_reais(session, pedido_id=None, dias_filtro=30):
    query = session.query(LogTempo)
    if pedido_id: query = query.filter(LogTempo.pedido_id == pedido_id)
    if dias_filtro:
        dt_limit = datetime.now() - timedelta(days=dias_filtro)
        query = query.filter(LogTempo.timestamp >= dt_limit)
    logs = query.order_by(LogTempo.usuario_id, LogTempo.tipo_atividade, LogTempo.timestamp).all()
    streams = {}
    for log in logs:
        k = (log.usuario_id, log.tipo_atividade)
        if k not in streams: streams[k] = []
        streams[k].append(log)
    resultados = {}
    for (uid, tipo), lista_logs in streams.items():
        lista_logs.sort(key=lambda x: x.timestamp)
        total_seconds = 0; inicio = None
        for l in lista_logs:
            if l.acao == "INICIO":
                if inicio is None: inicio = l.timestamp
            elif l.acao in ["PAUSA", "FIM"]:
                if inicio:
                    total_seconds += (l.timestamp - inicio).total_seconds(); inicio = None
        if inicio: total_seconds += (datetime.now() - inicio).total_seconds()
        
        if uid not in resultados: resultados[uid] = {'SEPARACAO': 0, 'CONFERENCIA': 0}
        
        tipo_chave = tipo if tipo in ['SEPARACAO', 'CONFERENCIA'] else 'SEPARACAO'
        if tipo_chave not in resultados[uid]: resultados[uid][tipo_chave] = 0
        
        resultados[uid][tipo_chave] += total_seconds
        
    return resultados

def formatar_delta(seconds):
    if not seconds: return "00:00:00"
    seconds = int(seconds); hours, remainder = divmod(seconds, 3600); minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def ler_planilha_simples(uploaded_file):
    try:
        if uploaded_file.name.endswith('.csv'):
            try: return pd.read_csv(uploaded_file, sep=';', dtype=str)
            except: return pd.read_csv(uploaded_file, sep=',', dtype=str)
        else: return pd.read_excel(uploaded_file, dtype=str)
    except Exception as e:
        st.error(f"Erro ao ler arquivo: {e}")
        return None

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
    except Exception as e: return None

# --- TELAS ---
def login_screen():
    st.markdown("<h2 style='text-align: center;'>🏭 PMP Flow Pro</h2>", unsafe_allow_html=True)
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
    u_logado = st.session_state['user']
    st.title(f"Painel Gerencial (ADM: {u_logado.username})")
    if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0
    
    qv = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').count()
    qa = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').count()
    
    t1, t2, t3, t4, t5 = st.tabs(["📊 Dashboard", "📥 Importar", f"🛡️ Validação ({qv})", f"🏭 Gestão ({qa})", "👥 Usuários"])

    # --- ABA 1: DASHBOARD ---
    with t1:
        st.markdown("### Indicadores de Qualidade e Performance (30 Dias)")
        dt_limite = datetime.now() - timedelta(days=30)
        peds_30 = s.query(Pedido).filter(Pedido.criado_em >= dt_limite).all()
        if not peds_30: st.info("Sem dados recentes.")
        else:
            total_pedidos = len(peds_30); total_concluidos = len([p for p in peds_30 if p.status == 'CONCLUIDO'])
            total_separacoes = 0; div_aceitas = 0; div_corrigidas = 0
            for p in peds_30:
                for item in p.itens:
                    for sep in item.separacoes:
                        total_separacoes += 1
                        if sep.divergencia_aceita_adm: div_aceitas += 1
                        div_corrigidas += sep.cnt_rejeicoes
            
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Pedidos Concluídos", f"{total_concluidos}/{total_pedidos}")
            k2.metric("Lotes Processados", total_separacoes)
            k3.metric("Divergências Aceitas", div_aceitas)
            k4.metric("Divergências Corrigidas", div_corrigidas)
            
            st.divider()
            
            # --- GRÁFICO DE HORAS POR OPERADOR (HH) ---
            c_g1, c_g2 = st.columns(2)
            
            tempos_user = calcular_tempos_reais(s, dias_filtro=30)
            
            with c_g1:
                st.markdown("##### ⏱️ H.H. por Operador")
                data_chart = []
                if tempos_user:
                    for uid, ativs in tempos_user.items():
                        usr = s.query(Usuario).get(uid)
                        if usr:
                            h_sep = round(ativs.get('SEPARACAO', 0) / 3600, 2)
                            h_conf = round(ativs.get('CONFERENCIA', 0) / 3600, 2)
                            if h_sep > 0: data_chart.append({"Operador": usr.username, "Atividade": "Separação", "Horas": h_sep})
                            if h_conf > 0: data_chart.append({"Operador": usr.username, "Atividade": "Conferência", "Horas": h_conf})
                    
                    if data_chart:
                        st.bar_chart(pd.DataFrame(data_chart), x="Operador", y="Horas", color="Atividade", stack=True)
                    else: st.caption("Sem dados.")
                else: st.caption("Sem dados.")

            with c_g2:
                st.markdown("##### 📅 Volume Diário")
                dados = {}
                for p in peds_30:
                    d = p.criado_em.strftime("%d/%m")
                    dados[d] = dados.get(d, 0) + 1
                if dados: st.bar_chart(pd.DataFrame(list(dados.items()), columns=["Data", "Pedidos"]).set_index("Data"))

    # --- ABA 2: IMPORTAÇÃO ---
    with t2:
        if 'merge_data' in st.session_state:
            st.warning(f"⚠️ O Pedido **{st.session_state['merge_ped_num']}** já existe!")
            novos = st.session_state['merge_data']['novos']; atualizados = st.session_state['merge_data']['atualizados']
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**🆕 Novos ({len(novos)})**")
                if novos: st.dataframe(pd.DataFrame(novos)[['codigo', 'descricao', 'qtd_solicitada']], use_container_width=True, hide_index=True)
            with c2:
                st.markdown(f"**🔄 Alterados ({len(atualizados)})**")
                if atualizados: st.dataframe(pd.DataFrame(atualizados)[['codigo', 'qtd_antiga', 'qtd_nova']], use_container_width=True, hide_index=True)
            st.divider()
            ca, cb = st.columns(2)
            if ca.button("❌ Cancelar", type="secondary"):
                del st.session_state['merge_data']; del st.session_state['merge_ped_num']; st.session_state['uploader_key'] += 1; st.rerun()
            if cb.button("✅ CONFIRMAR", type="primary"):
                try:
                    ped = s.query(Pedido).filter_by(numero_pedido=st.session_state['merge_ped_num']).first()
                    
                    if novos or atualizados:
                        ped.status = 'EM_ANDAMENTO'
                        ped.data_conclusao = None

                    for n in novos: s.add(ItemPedido(pedido_id=ped.id, id_importacao=n['id_importacao'], codigo=n['codigo'], descricao=n['descricao'], unidade=n['unidade'], qtd_solicitada=n['qtd_solicitada']))
                    for u in atualizados: 
                        it = s.query(ItemPedido).filter_by(pedido_id=ped.id, id_importacao=u['id_importacao']).first()
                        if it: it.qtd_solicitada = u['qtd_nova']
                    s.commit(); st.success("Atualizado!"); del st.session_state['merge_data']; del st.session_state['merge_ped_num']; st.session_state['uploader_key'] += 1; time.sleep(1); st.rerun()
                except Exception as e: st.error(e)
        else:
            st.markdown("### 1. Carregue a Planilha")
            f = st.file_uploader("Arquivo", type=["xlsx", "xls", "csv"], key=f"uploader_{st.session_state['uploader_key']}")
            if f:
                df = ler_planilha_simples(f)
                if df is not None:
                    st.dataframe(df.head(3), use_container_width=True)
                    cols = df.columns.tolist()
                    pref = s.query(PreferenciaMapping).filter_by(user_id=u_logado.id).first()
                    def get_idx(options, value):
                        if value and value in options: return options.index(value)
                        return 0

                    with st.form("map"):
                        c1, c2 = st.columns(2)
                        nped = c1.text_input("Número Pedido")
                        dped = c2.date_input("Data", datetime.now(), format="DD/MM/YYYY")
                        col_id_opts = ["(Automático: Usar Nº da Linha)"] + cols
                        col_id = st.selectbox("ID Único", col_id_opts, index=get_idx(col_id_opts, pref.col_id_unico if pref else None))
                        st.markdown("---")
                        cc1, cc2, cc3, cc4 = st.columns(4)
                        opts = ["(Sel)"] + cols
                        ccod = cc1.selectbox("Cód *", opts, index=get_idx(opts, pref.col_codigo if pref else None))
                        cdesc = cc2.selectbox("Desc *", opts, index=get_idx(opts, pref.col_descricao if pref else None))
                        cqtd = cc3.selectbox("Qtd *", opts, index=get_idx(opts, pref.col_qtd if pref else None))
                        cund = cc4.selectbox("Und", ["(Padrão: UN)"] + cols, index=get_idx(["(Padrão: UN)"] + cols, pref.col_unidade if pref else None))
                        if st.form_submit_button("🚀 Processar"):
                            if "(Sel)" in [ccod, cdesc, cqtd]: st.error("Obrigatórios.")
                            elif not nped: st.error("Número vazio.")
                            else:
                                try:
                                    if not pref: pref = PreferenciaMapping(user_id=u_logado.id); s.add(pref)
                                    pref.col_id_unico=col_id; pref.col_codigo=ccod; pref.col_descricao=cdesc; pref.col_qtd=cqtd; pref.col_unidade=cund; s.commit()
                                except: pass
                                itens = []
                                for idx, row in df.iterrows():
                                    try:
                                        c = str(row[ccod]).strip(); q = float(str(row[cqtd]).replace(',', '.')); d = str(row[cdesc]).strip()
                                        u = "UN" if cund == "(Padrão: UN)" else str(row[cund]).strip()
                                        mid = f"LINHA_{idx+1}" if col_id == "(Automático: Usar Nº da Linha)" else str(row[col_id]).strip()
                                        if c and q > 0: itens.append({"id_importacao": mid, "codigo": c, "descricao": d, "unidade": u, "qtd_solicitada": q})
                                    except: continue
                                pex = s.query(Pedido).filter_by(numero_pedido=nped).first()
                                if not pex:
                                    npd = Pedido(numero_pedido=nped, data_pedido=dped.strftime("%d/%m/%Y"), status="VALIDACAO")
                                    s.add(npd); s.flush()
                                    for i in itens: s.add(ItemPedido(pedido_id=npd.id, **i))
                                    s.commit(); st.success("Criado!"); st.session_state['uploader_key'] += 1; time.sleep(1); st.rerun()
                                else:
                                    imap = {i.id_importacao: i for i in pex.itens if i.id_importacao}
                                    n, a = [], []
                                    for x in itens:
                                        if x['id_importacao'] in imap:
                                            if abs(x['qtd_solicitada'] - imap[x['id_importacao']].qtd_solicitada) > 0.001:
                                                x['qtd_antiga'] = imap[x['id_importacao']].qtd_solicitada; x['qtd_nova'] = x['qtd_solicitada']; a.append(x)
                                        else: n.append(x)
                                    if not n and not a: st.warning("Igual.")
                                    else: st.session_state['merge_data'] = {'novos': n, 'atualizados': a}; st.session_state['merge_ped_num'] = nped; st.rerun()

    # --- ABA 3: VALIDAÇÃO ---
    with t3:
        vals = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').all()
        if not vals: st.caption("Vazio.")
        else:
            pid = st.selectbox("Limpar:", [p.id for p in vals], format_func=lambda x: next((f"{p.numero_pedido}" for p in vals if p.id==x), x))
            pv = s.query(Pedido).get(pid)
            d = pd.DataFrame([{"ID": i.id, "Código": i.codigo, "Desc": i.descricao, "Qtd": i.qtd_solicitada, "Manter?": True} for i in pv.itens])
            edf = st.data_editor(d, hide_index=True, key="ev")
            c1, c2 = st.columns(2)
            if c1.button("🗑️ Excluir"): s.delete(pv); s.commit(); st.rerun()
            if c2.button("🚀 Liberar"):
                ib = {i.id: i for i in pv.itens}; im = []
                for _, r in edf.iterrows():
                    if r["Manter?"]:
                        rid = r["ID"]
                        if pd.isna(rid): s.add(ItemPedido(pedido_id=pv.id, codigo=str(r["Código"]), descricao=str(r["Desc"]), unidade="UN", qtd_solicitada=float(r["Qtd"]), id_importacao="MANUAL"))
                        else: im.append(int(rid)); ib[int(rid)].qtd_solicitada = float(r["Qtd"])
                for k, v in ib.items():
                    if k not in im: s.delete(v)
                pv.status = "EM_ANDAMENTO"; s.commit(); st.rerun()

    # --- ABA 4: GESTÃO ---
    with t4:
        p1 = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').order_by(Pedido.id.desc()).all()
        p2 = s.query(Pedido).filter(Pedido.status == 'CONCLUIDO').order_by(Pedido.id.desc()).limit(5).all()
        lp = p1 + p2
        if not lp: st.info("Vazio.")
        else:
            pid = st.selectbox("Pedido:", [p.id for p in lp], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in lp if p.id==x), x))
            ped = s.query(Pedido).get(pid)
            st.divider()
            c_head, c_reop = st.columns([4, 1])
            c_head.markdown(f"### 🏭 {ped.numero_pedido} | {ped.status}")
            if ped.status == 'CONCLUIDO' and c_reop.button("🔓 Reabrir"): ped.status = "EM_ANDAMENTO"; ped.data_conclusao = None; s.commit(); st.rerun()
            if ped.observacao: st.info(f"📝 **Obs:** {ped.observacao}")

            al_div, al_erp, pend_geral = [], [], 0
            for it in ped.itens:
                qtd_final = 0
                for sep in it.separacoes: qtd_final += sep.qtd_conferida if sep.conferido else sep.qtd_separada
                if qtd_final < it.qtd_solicitada: pend_geral += 1
                for sep in it.separacoes:
                    if sep.conferido and (sep.qtd_separada != sep.qtd_conferida):
                        msg = f"{it.codigo}: Sep {sep.qtd_separada} / Conf {sep.qtd_conferida}"
                        al_div.append(msg)
                        if sep.enviado_sistema and not sep.divergencia_aceita_adm: al_erp.append(msg)
            
            if al_erp: st.error(f"🚨 ERRO CRÍTICO ERP ({len(al_erp)})")
            elif al_div: st.warning(f"⚠️ Divergências ({len(al_div)})")
            
            tempos_pedido = calcular_tempos_reais(s, pedido_id=ped.id)
            total_sec_ped = sum([v['SEPARACAO'] + v['CONFERENCIA'] for v in tempos_pedido.values()])
            st.caption(f"Tempo Total: {formatar_delta(total_sec_ped)}")

            pend_lanc = 0
            for it in ped.itens:
                tot = 0
                for x in it.separacoes: tot += x.qtd_conferida if x.conferido else x.qtd_separada
                meta = it.qtd_solicitada
                ico = "✅" if tot >= meta else "⏳" if tot > 0 else "⬜"
                if any([s.enviado_sistema and s.conferido and (s.qtd_separada != s.qtd_conferida) and not s.divergencia_aceita_adm for s in it.separacoes]): ico = "🚨"
                
                with st.expander(f"{ico} {it.codigo} - {it.descricao} ({tot}/{meta})"):
                    if tot < meta and ped.status != 'CONCLUIDO':
                        j = st.text_input("Justificativa", value=it.justificativa_divergencia or "", key=f"j_{it.id}")
                        if j != it.justificativa_divergencia: it.justificativa_divergencia = j; s.commit()
                    
                    ch = st.columns([3, 1, 1, 2, 2, 2, 1])
                    ch[0].markdown("**Rast.**"); ch[1].markdown("**Sep**"); ch[2].markdown("**Conf**"); ch[3].markdown("**Status**"); ch[4].markdown("**ERP**"); ch[5].markdown("**Ação**")
                    
                    for sep in it.separacoes:
                        c = st.columns([3, 1, 1, 2, 2, 2, 1])
                        c[0].text(sep.rastreabilidade)
                        c[1].text(sep.qtd_separada)
                        vc = sep.qtd_conferida if sep.qtd_conferida is not None else 0.0
                        match = (vc == sep.qtd_separada)
                        if sep.conferido and not match: c[2].markdown(f":red[{vc}]")
                        else: c[2].text(vc if sep.conferido else "-")
                        
                        stt = "Separando"
                        if sep.motivo_rejeicao: stt = "❌ RECUSADO"
                        elif sep.conferido: stt = "✅ CONFERIDO"
                        elif sep.enviado_conferencia: stt = "👀 EM CONF."
                        if "RECUSADO" in stt: c[3].error(stt)
                        elif "CONFERIDO" in stt: c[3].success(stt)
                        elif "EM CONF" in stt: c[3].warning(stt)
                        else: c[3].caption(stt)

                        lbl_erp = "⚠️ ERRADO" if (sep.enviado_sistema and sep.conferido and not match and not sep.divergencia_aceita_adm) else "Lançado"
                        ischk = c[4].checkbox(lbl_erp, value=sep.enviado_sistema, key=f"erp_{sep.id}", disabled=(ped.status=='CONCLUIDO'))
                        if ischk != sep.enviado_sistema: sep.enviado_sistema = ischk; s.commit(); st.rerun()

                        if sep.enviado_sistema and sep.conferido and not match:
                            if sep.divergencia_aceita_adm: c[5].success("Aceito")
                            elif c[5].button("Aceitar", key=f"ac_{sep.id}"): sep.divergencia_aceita_adm = True; s.commit(); st.rerun()
                        
                        if not sep.motivo_rejeicao:
                             if not sep.enviado_sistema: pend_lanc += 1
                             elif (sep.enviado_sistema and sep.conferido and not match and not sep.divergencia_aceita_adm): pend_lanc += 1

            st.divider()
            if ped.status != 'CONCLUIDO':
                with st.expander("🗑️ Zona de Perigo"):
                    if st.button(f"Excluir {ped.numero_pedido}"): s.delete(ped); s.commit(); st.rerun()
                bloqueios = []
                if pend_geral > 0: bloqueios.append(f"{pend_geral} itens incompletos.")
                if pend_lanc > 0: bloqueios.append(f"{pend_lanc} pendências ERP.")
                if al_erp: bloqueios.append("Divergências críticas.")
                if not bloqueios:
                    if st.button("✅ CONCLUIR", type="primary"):
                        encerrar_cronometros_abertos(s, ped.id); ped.status = 'CONCLUIDO'; ped.data_conclusao = datetime.now(); s.commit(); st.balloons(); st.rerun()
                else:
                    st.error("🚫 Pendente:")
                    for b in bloqueios: st.text(f"- {b}")
                    if st.checkbox("Sou Gerente e desejo forçar"):
                        jf = st.text_area("Justificativa Obrigatória")
                        if st.button("⚠️ FORÇAR ARQUIVAMENTO"):
                            if jf.strip():
                                encerrar_cronometros_abertos(s, ped.id); ped.status = 'CONCLUIDO'; ped.data_conclusao = datetime.now(); ped.observacao = jf; s.commit(); st.rerun()
                            else: st.error("Justifique.")
            else:
                st.success(f"Concluído em {ped.data_conclusao}")
                data = []
                for i in ped.itens:
                    b = {"Cod": i.codigo, "Desc": i.descricao, "Meta": i.qtd_solicitada, "Obs Pedido": ped.observacao}
                    if not i.separacoes: data.append(b)
                    for sp in i.separacoes:
                        l = b.copy(); l.update({"Sep": sp.qtd_separada, "Conf": sp.qtd_conferida, "Status": "RECUSADO" if sp.motivo_rejeicao else "OK"}); data.append(l)
                out = io.BytesIO(); 
                with pd.ExcelWriter(out, engine='xlsxwriter') as w: pd.DataFrame(data).to_excel(w, index=False)
                st.download_button("⬇️ Excel", out, f"{ped.numero_pedido}.xlsx")

    # --- ABA 5 ---
    with t5:
        with st.form("u"):
            c1, c2, c3, c4 = st.columns(4)
            u = c1.text_input("User"); p = c2.text_input("Pass", type="password"); r = c3.selectbox("Perfil", ["ADM", "SEPARADOR", "CONFERENTE", "AMBOS"])
            if c4.form_submit_button("Criar"):
                try: 
                    if s.query(Usuario).filter_by(username=u).first(): st.error("Existe")
                    else: s.add(Usuario(username=u, senha=p, perfil=r)); s.commit(); st.success("OK"); time.sleep(1); st.rerun()
                except: s.rollback(); st.error("Erro")
        st.divider()
        for u in s.query(Usuario).all(): 
            c1, c2, c3 = st.columns([2,2,1])
            c1.text(u.username); c2.text(u.perfil)
            if u.username != 'admin' and u.username != u_logado.username:
                if c3.button("🗑️", key=f"d_u_{u.id}"): s.delete(u); s.commit(); st.rerun()

def op_screen():
    s = get_db(); u = st.session_state['user']
    st.subheader(f"Operação: {u.username} ({u.perfil})")
    ts = st.tabs(["📦 Separação", "📋 Conferência"]) if u.perfil == 'AMBOS' else st.tabs(["📦 Separação"]) if u.perfil == 'SEPARADOR' else st.tabs(["📋 Conferência"])
    
    if u.perfil in ['SEPARADOR', 'AMBOS']:
        with ts[0]:
            pall = s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').all()
            pvis = []
            for p in pall:
                pend = False
                for i in p.itens:
                    if sum([x.qtd_separada for x in i.separacoes if not x.motivo_rejeicao]) < i.qtd_solicitada: pend = True
                    if any(x.motivo_rejeicao or not x.enviado_conferencia for x in i.separacoes): pend = True
                if pend: pvis.append(p)

            if not pvis: st.info("Tudo feito."); 
            else:
                pid = st.selectbox("Pedido", [p.id for p in pvis], format_func=lambda x: next((p.numero_pedido for p in pvis if p.id==x),x))
                ped = s.query(Pedido).get(pid)
                l = s.query(LogTempo).filter_by(pedido_id=ped.id, usuario_id=u.id, tipo_atividade='SEPARACAO').order_by(LogTempo.timestamp.desc()).first(); wk = (l and l.acao=="INICIO")
                c1, c2 = st.columns([1, 4])
                if c1.button("⏸️ PAUSAR" if wk else "▶️ INICIAR", type="primary" if not wk else "secondary"):
                    s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="PAUSA" if wk else "INICIO", tipo_atividade='SEPARACAO')); s.commit(); st.rerun()
                
                cam = c2.toggle("📸 Cam")
                for it in ped.itens:
                    done = sum([x.qtd_separada for x in it.separacoes if not x.motivo_rejeicao])
                    meta = it.qtd_solicitada
                    rej = [x for x in it.separacoes if x.motivo_rejeicao]
                    
                    with st.expander(f"{it.codigo} - {it.descricao} ({done}/{meta})", expanded=(done < meta or len(rej) > 0)):
                        for sep in it.separacoes:
                            c = st.columns([4, 1, 1])
                            lbl = sep.rastreabilidade
                            if sep.motivo_rejeicao: 
                                c[0].error(f"{lbl} ❌ RECUSADO ({sep.motivo_rejeicao})")
                                with c[1]:
                                    nov_qtd = st.number_input("Nova Qtd", value=sep.qtd_separada, key=f"fix_q_{sep.id}", label_visibility="collapsed")
                                if c[2].button("🔄 Corrigir", key=f"fix_b_{sep.id}"):
                                    sep.qtd_separada = nov_qtd
                                    sep.motivo_rejeicao = None 
                                    sep.enviado_conferencia = False 
                                    s.commit(); st.rerun()
                            else:
                                c[0].text(lbl)
                                c[1].text(sep.qtd_separada)
                                if (not sep.enviado_conferencia) and c[2].button("🗑️", key=f"d{sep.id}"): s.delete(sep); s.commit(); st.rerun()
                        
                        if wk:
                            if done < meta:
                                with st.form(f"f{it.id}", clear_on_submit=True):
                                    if cam: 
                                        im = st.camera_input("Foto", key=f"i{it.id}")
                                        val = tentar_ler_codigo_robustamente(im) if im else ""; nr = st.text_input("Rast.", value=val)
                                    else: nr = st.text_input("Rast.")
                                    nq = st.number_input("Qtd", min_value=0.1)
                                    if st.form_submit_button("Add") and nr and nq: s.add(Separacao(item_id=it.id, rastreabilidade=nr, qtd_separada=nq, separador_id=u.id)); s.commit(); st.rerun()
                            else: st.success("Qtd atingida.")

                if st.button("🚀 ENVIAR TUDO"):
                    # --- CORREÇÃO: PARA O CRONÔMETRO AO ENVIAR ---
                    for r in s.query(Separacao).join(ItemPedido).filter(ItemPedido.pedido_id==ped.id, Separacao.enviado_conferencia==False, Separacao.motivo_rejeicao==None).all(): r.enviado_conferencia=True
                    
                    # Para o timer de separação
                    active_log = s.query(LogTempo).filter_by(pedido_id=ped.id, usuario_id=u.id, tipo_atividade='SEPARACAO').order_by(LogTempo.timestamp.desc()).first()
                    if active_log and active_log.acao == "INICIO":
                        s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="FIM", tipo_atividade='SEPARACAO', timestamp=datetime.now()))
                    
                    s.commit(); st.success("Enviado e Parado!"); st.rerun()

    if u.perfil in ['CONFERENTE', 'AMBOS']:
        idx = 1 if u.perfil == 'AMBOS' else 0
        with ts[idx]:
            pconf = [p for p in s.query(Pedido).filter(Pedido.status == 'EM_ANDAMENTO').all() if any(x.enviado_conferencia and not x.conferido and not x.motivo_rejeicao for i in p.itens for x in i.separacoes)]
            if not pconf: st.info("Nada.")
            else:
                pid = st.selectbox("Pedido Conf", [p.id for p in pconf], format_func=lambda x: next((p.numero_pedido for p in pconf if p.id==x),x))
                ped = s.query(Pedido).get(pid)
                l = s.query(LogTempo).filter_by(pedido_id=ped.id, usuario_id=u.id, tipo_atividade='CONFERENCIA').order_by(LogTempo.timestamp.desc()).first(); wk = (l and l.acao=="INICIO")
                if st.button("⏸️ PAUSAR CONF" if wk else "▶️ INICIAR CONF", type="primary" if not wk else "secondary"):
                    s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="PAUSA" if wk else "INICIO", tipo_atividade='CONFERENCIA')); s.commit(); st.rerun()

                for it in ped.itens:
                    pend = [x for x in it.separacoes if x.enviado_conferencia and not x.conferido and not x.motivo_rejeicao]
                    if pend:
                        with st.expander(f"{it.codigo} - {it.descricao} ({len(pend)})"):
                            for sep in pend:
                                c = st.columns([3, 1, 2, 2])
                                c[0].text(sep.rastreabilidade); c[1].text(sep.qtd_separada)
                                v = c[2].number_input("Qtd", key=f"qc{sep.id}", step=0.1)
                                if c[3].button("Conf", key=f"bc{sep.id}"):
                                    if v == sep.qtd_separada: sep.qtd_conferida=v; sep.conferido=True; s.commit(); st.rerun()
                                    else: st.session_state[f"div{sep.id}"] = True
                                
                                if st.session_state.get(f"div{sep.id}"):
                                    st.warning(f"Divergência: {v}")
                                    if st.button("Aceitar", key=f"ba{sep.id}"): sep.qtd_conferida=v; sep.conferido=True; del st.session_state[f"div{sep.id}"]; s.commit(); st.rerun()
                                    if st.button("Recusar", key=f"br{sep.id}"): 
                                        sep.motivo_rejeicao="Div"; sep.enviado_conferencia=False; sep.cnt_rejeicoes+=1; del st.session_state[f"div{sep.id}"]; s.commit(); st.rerun()

# --- MAIN ---
init_users()
if 'user' not in st.session_state: login_screen()
else:
    st.sidebar.button("Sair", on_click=lambda: st.session_state.pop('user'))
    if st.session_state['user'].perfil == 'ADM': adm_screen()
    else: op_screen()