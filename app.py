import streamlit as st
import pandas as pd
import re
import io
import time
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.exc import OperationalError

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Sistema PMP Pro", layout="wide", page_icon="🏭")

# --- MENSAGEM DE CARREGAMENTO ---
placeholder = st.empty()
placeholder.info("⏳ Conectando ao Banco de Dados...")

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
    data_entrada_conferencia = Column(DateTime, nullable=True)
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
    pedido = relationship("Pedido", back_populates="itens")
    separacoes = relationship("Separacao", back_populates="item", cascade="all, delete")

class Separacao(Base):
    __tablename__ = 'separacoes'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('itens_pedido.id'))
    rastreabilidade = Column(String)
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

# --- CRIAÇÃO DAS TABELAS ---
try:
    Base.metadata.create_all(engine)
    placeholder.empty()
except OperationalError as e:
    st.error(f"❌ Não foi possível conectar ao Banco de Dados Postgres.")
    st.error(f"Detalhe: {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ Erro desconhecido: {e}")
    st.stop()

# --- FUNÇÕES ---
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

# --- TELAS ---
def login_screen():
    st.markdown("<h2 style='text-align: center;'>🏭 PMP System Login</h2>", unsafe_allow_html=True)
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
    st.title(f"Painel Gerencial ({st.session_state['user'].username})")
    
    qv = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').count()
    qc = s.query(Pedido).filter(Pedido.status == 'EM_CONFERENCIA').count()
    
    t1, t2, t3, t4 = st.tabs(["📥 Importar", f"🛡️ Validação ({qv})", f"✅ Conferência ({qc})", "👥 Usuários"])

    # 1. IMPORTAR
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

    # 2. VALIDACAO (LÓGICA CORRIGIDA)
    with t2:
        validacoes = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').all()
        if not validacoes: st.caption("Vazio.")
        else:
            pid = st.selectbox("Limpar:", [p.id for p in validacoes], format_func=lambda x: next((f"{p.numero_pedido}" for p in validacoes if p.id==x), x))
            pval = s.query(Pedido).get(pid)
            
            # Carrega dados
            dval = pd.DataFrame([{"ID": i.id, "Código": i.codigo, "Descrição": i.descricao, "Qtd": i.qtd_solicitada, "Manter?": True} for i in pval.itens])
            
            st.markdown(f"**Validando: {pval.numero_pedido}**")
            st.info("💡 Dica: Desmarque 'Manter?' para excluir. Adicione linhas no final da tabela para novos itens.")
            
            # Data Editor permite adicionar linhas (num_rows="dynamic")
            edf = st.data_editor(dval, num_rows="dynamic", column_config={
                "ID": st.column_config.NumberColumn(disabled=True),
                "Código": st.column_config.TextColumn(required=True),
                "Descrição": st.column_config.TextColumn(required=True),
                "Qtd": st.column_config.NumberColumn(required=True, min_value=0.01),
                "Manter?": st.column_config.CheckboxColumn(default=True)
            }, hide_index=True, key="ev")
            
            ca, cb = st.columns(2)
            if ca.button("🗑️ Excluir Pedido Completo"): s.delete(pval); s.commit(); st.rerun()
            
            if cb.button("🚀 Liberar"):
                # 1. Identificar IDs que existem no banco hoje
                itens_banco = {i.id: i for i in pval.itens}
                ids_para_manter = []

                # 2. Processar o DataFrame editado
                for index, row in edf.iterrows():
                    # Se desmarcou "Manter?", ignora essa linha (ela será excluída do banco depois)
                    if not row.get("Manter?", True):
                        continue

                    row_id = row.get("ID")
                    
                    # Verifica se é uma linha NOVA (ID vazio ou NaN)
                    if pd.isna(row_id) or row_id is None or str(row_id).strip() == "":
                        # CRIA NOVO ITEM
                        novo_item = ItemPedido(
                            pedido_id=pval.id,
                            codigo=str(row["Código"]),
                            descricao=str(row["Descrição"]),
                            unidade="UN", # Padrão
                            qtd_solicitada=float(row["Qtd"])
                        )
                        s.add(novo_item)
                    else:
                        # ITEM JÁ EXISTE: Atualiza e guarda o ID para não apagar
                        try:
                            id_int = int(row_id)
                            item_existente = itens_banco.get(id_int)
                            if item_existente:
                                item_existente.codigo = str(row["Código"])
                                item_existente.descricao = str(row["Descrição"])
                                item_existente.qtd_solicitada = float(row["Qtd"])
                                ids_para_manter.append(id_int)
                        except: pass

                # 3. Apagar do banco o que não está na lista de "manter"
                for db_id, db_item in itens_banco.items():
                    if db_id not in ids_para_manter:
                        s.delete(db_item)

                # 4. Finaliza
                pval.status = "PENDENTE"
                s.commit()
                st.success("Liberado com sucesso!")
                time.sleep(1)
                st.rerun()

    # 3. KANBAN
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
                
                # --- CÁLCULOS DE TEMPO PARA EXIBIÇÃO ---
                
                # 1. Tempo de Validação
                tempo_validacao_str = "00:00:00"
                if ped.data_entrada_conferencia and ped.data_conclusao:
                    delta_adm = ped.data_conclusao - ped.data_entrada_conferencia
                    if delta_adm.total_seconds() < 0: delta_adm = timedelta(0)
                    tempo_validacao_str = formatar_delta(delta_adm)
                
                # 2. Tempo Operacional
                tempos_individuais, status_live = calcular_tempos_reais(s, ped.id)
                tempo_total_equipe = sum(tempos_individuais.values(), timedelta(0))
                tempo_equipe_str = formatar_delta(tempo_total_equipe)

                # 3. Lead Time Total
                tempo_ciclo_total = "00:00:00"
                if ped.status == 'CONCLUIDO' and ped.criado_em and ped.data_conclusao:
                     delta_ciclo = ped.data_conclusao - ped.criado_em
                     tempo_ciclo_total = formatar_delta(delta_ciclo)

                if ped.status in ['EM_SEPARACAO', 'EM_CONFERENCIA', 'CONCLUIDO']:
                    with st.expander("⏱️ Métricas de Tempo", expanded=True):
                        k1, k2, k3 = st.columns(3)
                        k1.metric("👷 Tempo Operacional", tempo_equipe_str, help="Soma dos relógios de todos operadores")
                        k2.metric("🛡️ Tempo em Validação", tempo_validacao_str, help="Tempo que ficou aguardando + conferência do ADM")
                        if ped.status == 'CONCLUIDO':
                            k3.metric("🚀 Lead Time Total", tempo_ciclo_total, help="Tempo desde a importação até a conclusão")
                        
                        st.divider()
                        st.caption("Detalhe por Operador:")
                        cols = st.columns(len(tempos_individuais)) if len(tempos_individuais) > 0 else [st.container()]
                        idx = 0
                        for uid, delta in tempos_individuais.items():
                            with cols[idx % 4] if len(tempos_individuais) > 0 else cols[0]:
                                unome = s.query(Usuario).get(uid).username
                                stt = status_live.get(uid, 'PARADO')
                                icon_stt = "🟢" if stt == 'RODANDO' else "⏸️" if stt == 'PARADO' else "🏁"
                                st.text(f"{icon_stt} {unome}: {formatar_delta(delta)}")
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
                            c1.text(sep.rastreabilidade)
                            c2.text(sep.qtd_separada)
                            c3.caption(s.query(Usuario).get(sep.separador_id).username)
                            if c4.button("🗑️", key=f"da{sep.id}"): s.delete(sep); s.commit(); st.rerun()
                        
                        if ped.status != 'CONCLUIDO':
                            st.markdown("---")
                            ca, cb, cc = st.columns([2,1,1])
                            nl = ca.text_input("Rastreabilidade", key=f"al{it.id}")
                            nq = cb.number_input("Qtd", step=0.1, key=f"aq{it.id}")
                            if cc.button("Salvar", key=f"ab{it.id}"):
                                if nl and nq > 0: 
                                    s.add(Separacao(item_id=it.id, rastreabilidade=nl, qtd_separada=nq, separador_id=st.session_state['user'].id))
                                    s.commit(); st.rerun()

                st.markdown("### Ações")
                if ped.status == 'CONCLUIDO':
                    cd, cdel = st.columns([3,1])
                    
                    data = []
                    for i in ped.itens:
                        if not i.separacoes:
                            data.append({
                                "Cod": i.codigo, 
                                "Desc": i.descricao, 
                                "Meta": i.qtd_solicitada, 
                                "Qtd": 0,
                                "Rastreabilidade": "",
                                "Operador": "N/A",
                                "Tempo Operador": "00:00:00",
                                "Tempo Equipe": tempo_equipe_str,
                                "Tempo Validação": tempo_validacao_str,
                                "Lead Time Total": tempo_ciclo_total
                            })
                        else:
                            for sep in i.separacoes:
                                nome_op = "N/A"
                                tempo_op_individual = "00:00:00"
                                try:
                                    u_obj = s.query(Usuario).get(sep.separador_id)
                                    nome_op = u_obj.username
                                    tempo_op_individual = formatar_delta(tempos_individuais.get(u_obj.id, timedelta(0)))
                                except: pass
                                
                                data.append({
                                    "Cod": i.codigo, 
                                    "Desc": i.descricao, 
                                    "Meta": i.qtd_solicitada, 
                                    "Qtd": sep.qtd_separada,
                                    "Rastreabilidade": sep.rastreabilidade,
                                    "Operador": nome_op,
                                    "Tempo Operador": tempo_op_individual,
                                    "Tempo Equipe": tempo_equipe_str,
                                    "Tempo Validação": tempo_validacao_str,
                                    "Lead Time Total": tempo_ciclo_total
                                })

                    out = io.BytesIO()
                    with pd.ExcelWriter(out, engine='xlsxwriter') as w: 
                        pd.DataFrame(data).to_excel(w, index=False)
                        worksheet = w.sheets['Sheet1']
                        worksheet.set_column(0, 10, 15) 
                    
                    cd.download_button("⬇️ Excel Detalhado", out, f"BAIXA_{ped.numero_pedido}.xlsx", use_container_width=True)
                    
                    if cdel.button("🗑️ APAGAR", type="primary", use_container_width=True):
                        s.delete(ped)
                        s.commit()
                        del st.session_state['padm']
                        st.rerun()
                else:
                    c1, c2 = st.columns(2)
                    if c1.button("❌ Devolver"): ped.status = "CORRECAO"; s.commit(); st.rerun()
                    if c2.button("✅ Aprovar"): 
                        encerrar_cronometros_abertos(s, ped.id)
                        ped.status = "CONCLUIDO"
                        ped.data_conclusao = datetime.now()
                        s.commit(); st.rerun()

    # 4. USERS
    with t4:
        with st.form("nu"):
            c1, c2, c3, c4 = st.columns(4)
            nu = c1.text_input("User"); np = c2.text_input("Pass", type="password"); nr = c3.selectbox("Perfil", ["OPERADOR", "ADM"])
            if c4.form_submit_button("Criar"):
                try: s.add(Usuario(username=nu, senha=np, perfil=nr)); s.commit(); st.success("OK!"); st.rerun()
                except: st.error("Erro")
        st.divider()
        for u in s.query(Usuario).all():
            with st.container():
                c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                c1.write(f"**{u.username}**"); np = c2.text_input("Senha", key=f"p{u.id}", type="password")
                if c3.button("Atualizar", key=f"up{u.id}"):
                    if np: u.senha = np; s.commit(); st.success("OK")
                if u.username != 'admin' and u.id != st.session_state['user'].id:
                    if c4.button("🗑️", key=f"del_user_{u.id}"): s.delete(u); s.commit(); st.rerun()

def op_screen():
    s = get_db()
    u = st.session_state['user']
    st.subheader(f"Operador: {u.username}")
    peds = s.query(Pedido).filter(Pedido.status.in_(['PENDENTE', 'CORRECAO', 'EM_SEPARACAO'])).all()
    if not peds: st.info("Sem tarefas."); return

    pid = st.selectbox("Selecione Tarefa", [p.id for p in peds], format_func=lambda x: next((f"{p.numero_pedido} ({p.status})" for p in peds if p.id==x), x))
    ped = s.query(Pedido).get(pid)

    meu_log = s.query(LogTempo).filter_by(pedido_id=ped.id, usuario_id=u.id).order_by(LogTempo.timestamp.desc()).first()
    estado = "PARADO"
    if meu_log:
        if meu_log.acao == "INICIO": estado = "RODANDO"
        elif meu_log.acao == "PAUSA": estado = "PAUSADO"
        elif meu_log.acao == "FIM": estado = "FINALIZADO"

    tempos, _ = calcular_tempos_reais(s, ped.id)
    meu_tempo = tempos.get(u.id, timedelta(0))
    st.caption(f"⏱️ Seu tempo acumulado: **{formatar_delta(meu_tempo)}**")

    c_btn, c_info = st.columns([1, 3])
    if estado == "PARADO" or estado == "FINALIZADO":
        lbl = "▶️ JUNTAR-SE" if ped.status == "EM_SEPARACAO" else "▶️ INICIAR"
        if c_btn.button(lbl, type="primary"):
            ped.status = "EM_SEPARACAO"
            s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="INICIO")); s.commit(); st.rerun()
    elif estado == "RODANDO":
        c_info.success("✅ Trabalhando...")
        if c_btn.button("⏸️ PAUSAR"):
            s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="PAUSA")); s.commit(); st.rerun()
    elif estado == "PAUSADO":
        c_info.warning("⏸️ Em Pausa.")
        if c_btn.button("▶️ RETOMAR"):
            s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="INICIO")); s.commit(); st.rerun()

    if ped.status == "EM_SEPARACAO":
        st.divider()
        st.info(f"Pedido {ped.numero_pedido}")
        disabled = (estado != "RODANDO")
        if disabled: st.caption("🚫 *Retome o trabalho para editar.*")
        for it in ped.itens:
            done = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
            meta = round(it.qtd_solicitada, 2)
            prog = min(done/meta, 1.0) if meta > 0 else 0
            if done > meta: color, icon = "orange", "⚠️ PASSOU"
            elif done == meta: color, icon = "green", "✅ OK"
            else: color, icon = "red", "⬜ FALTA"
            with st.expander(f":{color}[{icon} - {it.codigo} {it.descricao}] ({done} / {meta})"):
                st.progress(prog)
                if it.separacoes:
                    st.markdown("**Registros:**")
                    for sep in it.separacoes:
                        c1, c2, c3 = st.columns([4, 2, 1])
                        c1.text(sep.rastreabilidade)
                        c2.text(sep.qtd_separada)
                        if c3.button("🗑️", key=f"del_op_{sep.id}", disabled=disabled): s.delete(sep); s.commit(); st.rerun()
                st.markdown("---")
                c1, c2, c3 = st.columns([2,1,1])
                nl = c1.text_input("Rastreabilidade", key=f"l{it.id}", disabled=disabled)
                nq = c2.number_input("Qtd", step=0.1, key=f"q{it.id}", disabled=disabled)
                if nq>0 and (done+nq>meta): st.warning(f"⚠️ Total será {done+nq:.2f}!")
                if c3.button("Add", key=f"b{it.id}", disabled=disabled):
                    if nl and nq>0: 
                        s.add(Separacao(item_id=it.id, rastreabilidade=nl, qtd_separada=nq, separador_id=u.id))
                        s.commit(); st.rerun()
        st.divider()
        if st.button("🏁 FINALIZAR E ENVIAR", type="primary"):
            encerrar_cronometros_abertos(s, ped.id)
            ped.status = "EM_CONFERENCIA"
            if not ped.data_entrada_conferencia:
                ped.data_entrada_conferencia = datetime.now()
            s.commit(); st.success("Enviado!"); time.sleep(1); st.rerun()

# --- MAIN ---
init_users()
if 'user' not in st.session_state: login_screen()
else:
    st.sidebar.button("Sair", on_click=lambda: st.session_state.pop('user'))
    if st.session_state['user'].perfil == 'ADM': adm_screen()
    else: op_screen()
