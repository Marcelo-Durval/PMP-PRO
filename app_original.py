import streamlit as st
import pandas as pd
import re
import io
import time
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Sistema PMP Pro", layout="wide", page_icon="🏭")

# --- BANCO DE DADOS ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sistema_local.db")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

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
    pedido = relationship("Pedido", back_populates="itens")
    separacoes = relationship("Separacao", back_populates="item", cascade="all, delete")

class Separacao(Base):
    __tablename__ = 'separacoes'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('itens_pedido.id'))
    lote = Column(String)
    qtd_separada = Column(Float)
    separador_id = Column(Integer, ForeignKey('usuarios.id'))
    registrado_em = Column(DateTime, default=datetime.now)
    item = relationship("ItemPedido", back_populates="separacoes")

class LogTempo(Base):
    __tablename__ = 'logs_tempo'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'))
    usuario_id = Column(Integer, ForeignKey('usuarios.id'))
    acao = Column(String) # INICIO, PAUSA, FIM
    timestamp = Column(DateTime, default=datetime.now)
    pedido = relationship("Pedido", back_populates="logs")

Base.metadata.create_all(engine)

# --- FUNÇÕES ---
def get_db():
    if 'db' not in st.session_state: st.session_state.db = Session()
    return st.session_state.db

def init_users():
    s = get_db()
    if not s.query(Usuario).filter_by(username='admin').first():
        s.add(Usuario(username='admin', senha='123', perfil='ADM'))
        s.commit()

def encerrar_cronometros_abertos(session, pedido_id):
    """Fecha tempos abertos ao finalizar pedido"""
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
    """
    Calcula o tempo acumulado CORRETAMENTE, somando:
    1. Intervalos fechados (Inicio -> Pausa/Fim)
    2. Intervalo aberto atual (Inicio -> Agora) se estiver rodando.
    """
    logs = session.query(LogTempo).filter_by(pedido_id=pedido_id).order_by(LogTempo.timestamp).all()
    tempos = {} # {usuario_id: timedelta}
    status_atual = {} # {usuario_id: 'RODANDO' | 'PARADO'}

    # Agrupa por usuário
    user_logs = {}
    for log in logs:
        if log.usuario_id not in user_logs: user_logs[log.usuario_id] = []
        user_logs[log.usuario_id].append(log)
    
    for uid, ulogs in user_logs.items():
        total = timedelta(0)
        inicio_periodo = None
        
        for log in ulogs:
            if log.acao == "INICIO":
                # Se já tinha um inicio sem fim (erro de clique duplo), ignora o anterior ou reseta
                inicio_periodo = log.timestamp
                status_atual[uid] = 'RODANDO'
            
            elif (log.acao == "PAUSA" or log.acao == "FIM"):
                if inicio_periodo:
                    delta = log.timestamp - inicio_periodo
                    total += delta
                    inicio_periodo = None
                status_atual[uid] = 'PARADO'
        
        # O PULO DO GATO: Se terminou o loop e ainda tem 'inicio_periodo', 
        # significa que está rodando AGORA. Soma o tempo até o presente.
        if inicio_periodo:
            total += (datetime.now() - inicio_periodo)
            
        tempos[uid] = total

    return tempos, status_atual

def formatar_delta(delta):
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

# --- TELAS ---
def login_screen():
    st.markdown("<h2 style='text-align: center;'>🏭 PMP System Login</h2>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        with st.form("login"):
            u = st.text_input("Usuário"); p = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                s = get_db()
                user = s.query(Usuario).filter_by(username=u, senha=p).first()
                if user: st.session_state['user'] = user; st.rerun()
                else: st.error("Dados inválidos")

def adm_screen():
    s = get_db()
    st.title(f"Painel Gerencial ({st.session_state['user'].username})")
    
    qv = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').count()
    qc = s.query(Pedido).filter(Pedido.status == 'EM_CONFERENCIA').count()
    t1, t2, t3, t4 = st.tabs(["📥 Importar", f"🛡️ Validação ({qv})", f"✅ Conferência ({qc})", "👥 Usuários"])

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
            st.markdown(f"**Validando: {pval.numero_pedido}**")
            edf = st.data_editor(dval, num_rows="dynamic", column_config={"ID": st.column_config.NumberColumn(disabled=True)}, hide_index=True, key="ev")
            ca, cb = st.columns(2)
            if ca.button("🗑️ Excluir Pedido"): s.delete(pval); s.commit(); st.rerun()
            if cb.button("🚀 Liberar"):
                ids = edf["ID"].tolist()
                for di in pval.itens:
                    if di.id not in ids: s.delete(di)
                pval.status = "PENDENTE"; s.commit(); st.success("Liberado!"); time.sleep(1); st.rerun()

    with t3:
        pends = s.query(Pedido).filter(Pedido.status == 'PENDENTE').all()
        andams = s.query(Pedido).filter(Pedido.status.in_(['EM_SEPARACAO', 'EM_CONFERENCIA', 'CORRECAO'])).all()
        concs = s.query(Pedido).filter(Pedido.status == 'CONCLUIDO').order_by(Pedido.id.desc()).all()

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("### 🟠 A Fazer")
            for p in pends: st.info(f"📄 **{p.numero_pedido}**\n\n📅 {p.data_pedido}")
        with c2:
            st.markdown("### 🔵 Em Andamento")
            for p in andams:
                icon = "🔥" if p.status == 'EM_SEPARACAO' else "👀" if p.status == 'EM_CONFERENCIA' else "↩️"
                if st.button(f"{icon} {p.numero_pedido} ({p.status})", key=f"bk{p.id}", use_container_width=True): st.session_state['padm'] = p.id
        with c3:
            st.markdown("### 🟢 Concluídos")
            for p in concs:
                if st.button(f"🏁 {p.numero_pedido}", key=f"bc{p.id}", use_container_width=True): st.session_state['padm'] = p.id
        
        st.divider()

        if 'padm' in st.session_state:
            ped = s.query(Pedido).get(st.session_state['padm'])
            if ped:
                st.markdown(f"### 🔎 {ped.numero_pedido}")
                
                # --- PAINEL DE PERFORMANCE ---
                if ped.status in ['EM_SEPARACAO', 'EM_CONFERENCIA', 'CONCLUIDO']:
                    with st.expander("⏱️ Performance & Status da Equipe", expanded=True):
                        tempos, status_live = calcular_tempos_reais(s, ped.id)
                        
                        cols = st.columns(len(tempos)) if len(tempos) > 0 else [st.container()]
                        idx = 0
                        for uid, delta in tempos.items():
                            with cols[idx % 4] if len(tempos) > 0 else cols[0]:
                                unome = s.query(Usuario).get(uid).username
                                stt = status_live.get(uid, 'PARADO')
                                icon_stt = "🟢" if stt == 'RODANDO' else "⏸️" if stt == 'PARADO' else "🏁"
                                st.metric(label=f"{icon_stt} {unome}", value=formatar_delta(delta))
                            idx += 1

                for it in ped.itens:
                    tot = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                    meta = round(it.qtd_solicitada, 2)
                    if tot > meta: color, icon = "orange", "⚠️ EXCEDE"
                    elif tot == meta: color, icon = "green", "✅ OK"
                    else: color, icon = "red", "⬜ FALTA"
                    with st.expander(f":{color}[{icon} - {it.codigo} {it.descricao}] ({tot} / {meta})"):
                        for sep in it.separacoes:
                            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
                            c1.text(sep.lote); c2.text(sep.qtd_separada); c3