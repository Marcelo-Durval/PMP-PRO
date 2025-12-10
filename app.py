import streamlit as st
import pandas as pd
import re
import io
import time
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.exc import OperationalError

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Sistema PMP Pro", layout="wide", page_icon="🏭")

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
    perfil = Column(String) # ADM, SEPARADOR, CONFERENTE, AMBOS

class Pedido(Base):
    __tablename__ = 'pedidos'
    id = Column(Integer, primary_key=True)
    numero_pedido = Column(String)
    data_pedido = Column(String)
    status = Column(String) 
    
    criado_em = Column(DateTime, default=datetime.now)
    data_inicio_separacao = Column(DateTime, nullable=True)
    data_fim_separacao = Column(DateTime, nullable=True) 
    data_fim_conferencia = Column(DateTime, nullable=True) 
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
    separador_id = Column(Integer, ForeignKey('usuarios.id'))
    registrado_em = Column(DateTime, default=datetime.now)
    
    conferido = Column(Boolean, default=False) 
    data_conferencia = Column(DateTime, nullable=True)
    motivo_rejeicao = Column(Text, nullable=True)
    
    enviado_sistema = Column(Boolean, default=False) 
    data_envio = Column(DateTime, nullable=True)
    
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
try: Base.metadata.create_all(engine)
except: pass

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
    st.title(f"Painel Gerencial (ADM: {st.session_state['user'].username})")
    
    qv = s.query(Pedido).filter(Pedido.status == 'VALIDACAO').count()
    qa = s.query(Pedido).filter(Pedido.status.notin_(['VALIDACAO', 'PENDENTE'])).count()
    
    t1, t2, t3, t4 = st.tabs(["📥 Importar", f"🛡️ Validação ({qv})", f"🏭 Gestão & Input ({qa})", "👥 Usuários"])

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
            if c2.button("🚀 Liberar p/ Chão de Fábrica"):
                itens_banco = {i.id: i for i in pval.itens}; ids_manter = []
                for index, row in edf.iterrows():
                    if row.get("Manter?", True):
                        rid = row.get("ID")
                        if pd.isna(rid): s.add(ItemPedido(pedido_id=pval.id, codigo=str(row["Código"]), descricao=str(row["Descrição"]), unidade="UN", qtd_solicitada=float(row["Qtd"])))
                        else: ids_manter.append(int(rid))
                for db_id, db_item in itens_banco.items():
                    if db_id not in ids_manter: s.delete(db_item)
                pval.status = "PENDENTE"; s.commit(); st.success("Liberado!"); time.sleep(1); st.rerun()

    with t3:
        peds_ativos = s.query(Pedido).filter(Pedido.status.notin_(['VALIDACAO'])).order_by(Pedido.status, Pedido.id.desc()).all()
        if not peds_ativos: st.info("Nenhum pedido em andamento.")
        pid = st.selectbox("Selecione Pedido", [p.id for p in peds_ativos], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in peds_ativos if p.id==x), x))
        ped = s.query(Pedido).get(pid)
        if ped:
            st.divider()
            c_head, c_btn_reopen = st.columns([4, 1])
            c_head.markdown(f"### 🏭 Pedido: {ped.numero_pedido} | Status: {ped.status}")
            if ped.status == 'CONCLUIDO':
                if c_btn_reopen.button("🔓 Reabrir Pedido", type="primary"):
                    ped.status = "AGUARDANDO_INPUT"; ped.data_conclusao = None; s.commit(); st.rerun()

            tempos_individuais, status_live = calcular_tempos_reais(s, ped.id)
            tempo_equipe_str = formatar_delta(sum(tempos_individuais.values(), timedelta(0)))
            tempo_ciclo_total = "00:00:00"
            if ped.criado_em:
                fim = ped.data_conclusao if ped.data_conclusao else datetime.now()
                tempo_ciclo_total = formatar_delta(fim - ped.criado_em)
            tempo_validacao_str = "00:00:00"
            if ped.data_fim_separacao and ped.data_conclusao:
                val = ped.data_conclusao - ped.data_fim_separacao
                if val.total_seconds() > 0: tempo_validacao_str = formatar_delta(val)

            with st.expander("⏱️ Cronômetros & Performance", expanded=False):
                k1, k2, k3 = st.columns(3)
                k1.metric("👷 Tempo Operacional (Equipe)", tempo_equipe_str)
                k2.metric("🛡️ Lead Time (Total Aberto)", tempo_ciclo_total)
                k3.metric("📉 Tempo Validação ADM", tempo_validacao_str)
                st.caption("Detalhe por Operador:")
                cols = st.columns(len(tempos_individuais)) if len(tempos_individuais) > 0 else [st.container()]; idx = 0
                for uid, delta in tempos_individuais.items():
                    with cols[idx % 4] if len(tempos_individuais) > 0 else cols[0]:
                        unome = s.query(Usuario).get(uid).username; stt = status_live.get(uid, 'PARADO'); icon_stt = "🟢" if stt == 'RODANDO' else "⏸️" if stt == 'PARADO' else "🏁"
                        st.text(f"{icon_stt} {unome}: {formatar_delta(delta)}")
                    idx += 1

            if ped.status != 'CONCLUIDO':
                with st.expander("➕ Adicionar Produto Extra ao Pedido"):
                    with st.form("form_add_extra", clear_on_submit=True):
                        c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
                        nc = c1.text_input("Código"); nd = c2.text_input("Descrição"); nq = c3.number_input("Qtd Meta", min_value=0.1, value=1.0)
                        if c4.form_submit_button("Adicionar"):
                            if nc and nd: s.add(ItemPedido(pedido_id=ped.id, codigo=nc, descricao=nd, unidade="UN", qtd_solicitada=nq, item_adicionado_manualmente=True)); s.commit(); st.rerun()

            pendencias_input = 0
            for it in ped.itens:
                tot = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                meta = round(it.qtd_solicitada, 2)
                divergente = (tot != meta) or it.item_adicionado_manualmente
                
                # --- CORREÇÃO DA LÓGICA DE CORES ---
                if tot > meta: color, icon = "orange", "⚠️" # EXCEDE
                elif tot == meta: color, icon = "green", "✅" # OK
                else: color, icon = "red", "⬜" # FALTA

                with st.expander(f"{icon} :{color}[{it.codigo} {it.descricao}] ({tot}/{meta})"):
                    if divergente and ped.status != 'CONCLUIDO':
                        st.markdown("**📝 Justificativa de Divergência/Inclusão:**")
                        just = st.text_input("Motivo (Obrigatório)", value=it.justificativa_divergencia if it.justificativa_divergencia else "", key=f"just_{it.id}")
                        if just != it.justificativa_divergencia: it.justificativa_divergencia = just; s.commit()
                    elif it.justificativa_divergencia: st.info(f"Justificativa: {it.justificativa_divergencia}")

                    cols = st.columns([3, 1, 2, 2, 1])
                    cols[0].markdown("**Rastreabilidade**"); cols[1].markdown("**Qtd**"); cols[2].markdown("**Status Conf.**"); cols[3].markdown("**Input ERP**")
                    if not it.separacoes: st.caption("Aguardando separação...")
                    for sep in it.separacoes:
                        c1, c2, c3, c4, c5 = st.columns([3, 1, 2, 2, 1])
                        c1.text(sep.rastreabilidade); c2.text(sep.qtd_separada)
                        if sep.motivo_rejeicao: c3.error(f"RECUSADO: {sep.motivo_rejeicao}")
                        elif sep.conferido: c3.success("OK")
                        else: c3.warning("Pend.")
                        disabled_chk = (ped.status == 'CONCLUIDO')
                        is_checked = c4.checkbox("Lançado", value=sep.enviado_sistema, key=f"chk_adm_{sep.id}", disabled=disabled_chk)
                        if is_checked != sep.enviado_sistema: sep.enviado_sistema = is_checked; sep.data_envio = datetime.now() if is_checked else None; s.commit(); st.rerun()
                        if not sep.enviado_sistema: pendencias_input += 1

            st.divider()
            if ped.status == 'CONCLUIDO':
                 st.success(f"Pedido Concluído em {ped.data_conclusao}")
                 data = []
                 for i in ped.itens:
                     base = {"Cod": i.codigo, "Desc": i.descricao, "Meta": i.qtd_solicitada, "Justificativa": i.justificativa_divergencia}
                     if not i.separacoes:
                         base.update({"Qtd": 0, "Status": "Não Separado"}); data.append(base)
                     else:
                         for sep in i.separacoes:
                             row = base.copy(); row.update({"Qtd": sep.qtd_separada, "Rastreabilidade": sep.rastreabilidade, "Lançado ERP": "SIM" if sep.enviado_sistema else "NÃO"}); data.append(row)
                 out = io.BytesIO()
                 with pd.ExcelWriter(out, engine='xlsxwriter') as w: pd.DataFrame(data).to_excel(w, index=False)
                 st.download_button("⬇️ Baixar Excel Final", out, f"FINAL_{ped.numero_pedido}.xlsx")
            else:
                pendencias_justificativa = 0
                for it in ped.itens:
                    tot = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                    meta = round(it.qtd_solicitada, 2)
                    is_div = (tot != meta) or it.item_adicionado_manualmente
                    if is_div and (not it.justificativa_divergencia or len(it.justificativa_divergencia.strip()) < 3): pendencias_justificativa += 1
                if pendencias_input == 0:
                    if pendencias_justificativa == 0:
                        if st.button("✅ CONCLUIR PEDIDO", type="primary"):
                            encerrar_cronometros_abertos(s, ped.id); ped.status = "CONCLUIDO"; ped.data_conclusao = datetime.now(); s.commit(); st.balloons(); time.sleep(1); st.rerun()
                    else: st.error(f"🚫 Existem {pendencias_justificativa} itens divergentes SEM JUSTIFICATIVA.")
                else: st.warning(f"⚠️ Faltam lançar {pendencias_input} itens no ERP.")

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
    st.subheader(f"Área Operacional: {u.username} ({u.perfil})")
    tabs_to_show = []
    if u.perfil in ['SEPARADOR', 'AMBOS']: tabs_to_show.append("📦 Separação")
    if u.perfil in ['CONFERENTE', 'AMBOS']: tabs_to_show.append("📋 Conferência")
    if not tabs_to_show: st.error("Perfil sem acesso."); return
    tabs = st.tabs(tabs_to_show)
    
    # --- SEPARAÇÃO ---
    if "📦 Separação" in tabs_to_show:
        with tabs[tabs_to_show.index("📦 Separação")]:
            peds_sep = s.query(Pedido).filter(Pedido.status.in_(['PENDENTE', 'EM_SEPARACAO', 'CORRECAO'])).all()
            if not peds_sep: st.info("Sem pedidos pendentes.")
            else:
                pid = st.selectbox("Pedido (Separação)", [p.id for p in peds_sep], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in peds_sep if p.id==x), x))
                ped = s.query(Pedido).get(pid)
                
                tempos, _ = calcular_tempos_reais(s, ped.id)
                meu_tempo = tempos.get(u.id, timedelta(0))
                st.caption(f"⏱️ Seu tempo neste pedido: **{formatar_delta(meu_tempo)}**")
                
                meu_log = s.query(LogTempo).filter_by(pedido_id=ped.id, usuario_id=u.id).order_by(LogTempo.timestamp.desc()).first()
                estado = "PARADO"
                if meu_log and meu_log.acao == "INICIO": estado = "RODANDO"

                c_btn, _ = st.columns([1, 4])
                if estado == "PARADO":
                    if c_btn.button("▶️ TRABALHAR", type="primary"):
                        if ped.status == 'PENDENTE': ped.status = 'EM_SEPARACAO'; ped.data_inicio_separacao = datetime.now()
                        s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="INICIO")); s.commit(); st.rerun()
                else:
                    if c_btn.button("⏸️ PAUSAR"):
                        s.add(LogTempo(pedido_id=ped.id, usuario_id=u.id, acao="PAUSA")); s.commit(); st.rerun()

                st.divider()
                st.info(f"Pedido: {ped.numero_pedido}")
                if ped.status == 'CORRECAO': st.error("⚠️ ESTE PEDIDO RETORNOU DA CONFERÊNCIA! Corrija os itens em vermelho.")

                for it in ped.itens:
                    done = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                    meta = round(it.qtd_solicitada, 2)
                    
                    # --- CORREÇÃO DA LÓGICA DE CORES (SEPARADOR) ---
                    if done > meta: color, icon = "orange", "⚠️"
                    elif done == meta: color, icon = "green", "✅"
                    else: color, icon = "red", "⬜"
                    
                    with st.expander(f":{color}[{icon} {it.codigo} {it.descricao}] ({done}/{meta})"):
                        for sep in it.separacoes:
                            c1, c2, c3 = st.columns([4, 2, 1])
                            if sep.motivo_rejeicao: c1.error(f"{sep.rastreabilidade} (Recusado: {sep.motivo_rejeicao})")
                            else: c1.text(sep.rastreabilidade)
                            c2.text(sep.qtd_separada)
                            if c3.button("🗑️", key=f"d{sep.id}"): s.delete(sep); s.commit(); st.rerun()
                        
                        if estado == "RODANDO":
                            with st.form(key=f"form_sep_{it.id}", clear_on_submit=True):
                                c1, c2, c3 = st.columns([3, 2, 1])
                                nl = c1.text_input("Lote"); nq = c2.number_input("Qtd", step=0.1, min_value=0.0)
                                if c3.form_submit_button("Add"):
                                    if nl and nq > 0: s.add(Separacao(item_id=it.id, rastreabilidade=nl, qtd_separada=nq, separador_id=u.id)); s.commit(); st.rerun()
                        else: st.warning("▶️ Inicie o trabalho para editar.")
                
                st.divider()
                if ped.status in ['EM_SEPARACAO', 'CORRECAO']:
                    if st.button("🏁 ENVIAR PARA CONFERÊNCIA"):
                        ped.status = "EM_CONFERENCIA"; ped.data_fim_separacao = datetime.now(); s.commit(); st.success("Enviado!"); time.sleep(1); st.rerun()

    # --- CONFERÊNCIA ---
    if "📋 Conferência" in tabs_to_show:
        with tabs[tabs_to_show.index("📋 Conferência")]:
            peds_conf = s.query(Pedido).filter(Pedido.status.in_(['EM_CONFERENCIA', 'AGUARDANDO_INPUT'])).all()
            if not peds_conf: st.info("Sem pedidos para conferência.")
            else:
                pid = st.selectbox("Pedido (Conferência)", [p.id for p in peds_conf], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in peds_conf if p.id==x), x))
                ped = s.query(Pedido).get(pid)
                
                pendencias_conf = 0
                itens_recusados = False
                for it in ped.itens:
                    with st.expander(f"{it.codigo} {it.descricao}"):
                        cols = st.columns([3, 1, 1, 3])
                        cols[0].write("**Rastro**"); cols[1].write("**Qtd**"); cols[2].write("**OK?**"); cols[3].write("**Recusar**")
                        for sep in it.separacoes:
                            c1, c2, c3, c4 = st.columns([3, 1, 1, 3])
                            if sep.motivo_rejeicao:
                                c1.markdown(f"~~{sep.rastreabilidade}~~"); c4.error(f"{sep.motivo_rejeicao}"); itens_recusados = True
                            else:
                                c1.text(sep.rastreabilidade); c2.text(sep.qtd_separada)
                                if not sep.conferido:
                                    with c4.popover("❌ Recusar"):
                                        reason = st.text_input("Motivo", key=f"reason_{sep.id}")
                                        if st.button("Confirmar", key=f"btn_r_{sep.id}"):
                                            if reason: sep.motivo_rejeicao = reason; sep.conferido = False; s.commit(); st.rerun()

                            ic = c3.checkbox("OK", value=sep.conferido, key=f"c_{sep.id}")
                            if ic != sep.conferido: 
                                sep.conferido = ic
                                if ic: sep.motivo_rejeicao = None
                                s.commit(); st.rerun()
                            if not sep.conferido and not sep.motivo_rejeicao: pendencias_conf += 1
                
                st.divider()
                if itens_recusados:
                    st.error("⚠️ Existem itens rejeitados/recusados.")
                    if st.button("↩️ DEVOLVER PARA SEPARAÇÃO (CORREÇÃO)", type="primary"):
                        ped.status = "CORRECAO"; s.commit(); st.success("Devolvido!"); time.sleep(1); st.rerun()
                elif pendencias_conf == 0:
                    if ped.status == 'EM_CONFERENCIA':
                        if st.button("✅ APROVAR TUDO"):
                            ped.status = "AGUARDANDO_INPUT"; ped.data_fim_conferencia = datetime.now(); s.commit(); st.success("Aprovado!"); time.sleep(1); st.rerun()
                else: st.warning(f"Faltam {pendencias_conf} itens.")

# --- MAIN ---
init_users()
if 'user' not in st.session_state: login_screen()
else:
    st.sidebar.button("Sair", on_click=lambda: st.session_state.pop('user'))
    if st.session_state['user'].perfil == 'ADM': adm_screen()
    else: op_screen()
