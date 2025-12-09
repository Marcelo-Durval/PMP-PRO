import streamlit as st
import pandas as pd
import re
import io
import time
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Boolean
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
    
    # AGORA O ADM VÊ TUDO QUE JÁ PASSOU DA VALIDAÇÃO NA ABA DE INPUT
    # Não importa se está EM_SEPARACAO, EM_CONFERENCIA, etc.
    qa = s.query(Pedido).filter(Pedido.status.notin_(['VALIDACAO', 'PENDENTE'])).count()
    
    t1, t2, t3, t4 = st.tabs(["📥 Importar", f"🛡️ Validação ({qv})", f"🏭 Gestão & Input ERP ({qa})", "👥 Usuários"])

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

    # 2. VALIDACAO
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
                # Lógica simplificada de atualização de itens
                itens_banco = {i.id: i for i in pval.itens}; ids_manter = []
                for index, row in edf.iterrows():
                    if row.get("Manter?", True):
                        rid = row.get("ID")
                        if pd.isna(rid): s.add(ItemPedido(pedido_id=pval.id, codigo=str(row["Código"]), descricao=str(row["Descrição"]), unidade="UN", qtd_solicitada=float(row["Qtd"])))
                        else: ids_manter.append(int(rid))
                for db_id, db_item in itens_banco.items():
                    if db_id not in ids_manter: s.delete(db_item)
                pval.status = "PENDENTE"; s.commit(); st.success("Liberado!"); time.sleep(1); st.rerun()

    # 3. GESTÃO E INPUT (SUPER TELA DO ADM)
    with t3:
        # Pega qualquer pedido que já saiu da "Validação" e "Pendente" (ou seja, já começou a vida útil)
        peds_ativos = s.query(Pedido).filter(Pedido.status.notin_(['VALIDACAO'])).order_by(Pedido.status, Pedido.id.desc()).all()
        
        if not peds_ativos: st.info("Nenhum pedido em andamento.")
        
        # Selectbox com indicador de status visual
        pid = st.selectbox("Selecione Pedido", [p.id for p in peds_ativos], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in peds_ativos if p.id==x), x))
        ped = s.query(Pedido).get(pid)
        
        if ped:
            st.divider()
            # Barra de status
            st.markdown(f"### 🏭 Pedido: {ped.numero_pedido}")
            st.caption(f"Status Atual: **{ped.status}**")
            
            pendencias_input = 0
            pendencias_separacao = 0
            pendencias_conferencia = 0
            
            for it in ped.itens:
                tot = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                meta = round(it.qtd_solicitada, 2)
                
                if tot < meta: pendencias_separacao += 1
                
                # Visual
                color = "green" if tot >= meta else "red"
                icon = "✅" if tot >= meta else "🏗️"
                
                with st.expander(f"{icon} :{color}[{it.codigo} {it.descricao}] ({tot}/{meta})"):
                    cols = st.columns([3, 1, 2, 2, 1])
                    cols[0].markdown("**Rastreabilidade**")
                    cols[1].markdown("**Qtd**")
                    cols[2].markdown("**Status Conf.**")
                    cols[3].markdown("**Input ERP**")
                    
                    if not it.separacoes:
                        st.caption("Aguardando separação...")
                    
                    for sep in it.separacoes:
                        c1, c2, c3, c4, c5 = st.columns([3, 1, 2, 2, 1])
                        c1.text(sep.rastreabilidade)
                        c2.text(sep.qtd_separada)
                        
                        # Status Conferencia (Apenas visual para o ADM saber)
                        if sep.conferido: c3.success("OK")
                        else: 
                            c3.warning("Pend.")
                            pendencias_conferencia += 1
                        
                        # Checkbox de Input do ADM (SEMPRE HABILITADO SE O PEDIDO NÃO ESTIVER CONCLUIDO)
                        disabled_chk = (ped.status == 'CONCLUIDO')
                        is_checked = c4.checkbox("Lançado", value=sep.enviado_sistema, key=f"chk_adm_{sep.id}", disabled=disabled_chk)
                        
                        if is_checked != sep.enviado_sistema:
                            sep.enviado_sistema = is_checked
                            sep.data_envio = datetime.now() if is_checked else None
                            s.commit(); st.rerun()
                        
                        if not sep.enviado_sistema: pendencias_input += 1

            st.divider()
            
            if ped.status == 'CONCLUIDO':
                 st.success(f"Pedido Concluído em {ped.data_conclusao}")
                 
                 # Export Excel
                 data = []
                 for i in ped.itens:
                     if not i.separacoes: data.append({"Cod": i.codigo, "Status": "Não Separado"})
                     else:
                         for sep in i.separacoes:
                             data.append({
                                 "Cod": i.codigo, "Desc": i.descricao, "Qtd": sep.qtd_separada,
                                 "Rastreabilidade": sep.rastreabilidade,
                                 "Conferido": "SIM" if sep.conferido else "NÃO",
                                 "Lançado ERP": "SIM" if sep.enviado_sistema else "NÃO"
                             })
                 out = io.BytesIO()
                 with pd.ExcelWriter(out, engine='xlsxwriter') as w: pd.DataFrame(data).to_excel(w, index=False)
                 st.download_button("⬇️ Baixar Excel Final", out, f"FINAL_{ped.numero_pedido}.xlsx")
                 
                 if st.button("Reabrir Pedido"):
                     ped.status = "AGUARDANDO_INPUT"
                     ped.data_conclusao = None
                     s.commit(); st.rerun()
                     
            else:
                # PAINEL DE AÇÃO DO ADM
                c_info, c_action = st.columns([2, 1])
                
                with c_info:
                    st.markdown("**Resumo de Pendências:**")
                    if pendencias_separacao > 0: st.error(f"❌ Separação: Faltam atingir meta de {pendencias_separacao} itens.")
                    else: st.success("✅ Separação Completa")
                    
                    if pendencias_conferencia > 0: st.warning(f"⚠️ Conferência: {pendencias_conferencia} itens não foram conferidos (Conferente).")
                    else: st.success("✅ Conferência Completa")
                    
                    if pendencias_input > 0: st.info(f"📥 ERP: Faltam lançar {pendencias_input} itens.")
                    else: st.success("✅ Tudo Lançado no ERP")

                with c_action:
                    # LÓGICA DE CONCLUSÃO FLEXÍVEL
                    # O ADM pode concluir se tudo foi separado E tudo foi inputado.
                    # A conferência é opcional (apenas avisa).
                    
                    pode_concluir = (pendencias_separacao == 0) and (pendencias_input == 0)
                    
                    if pode_concluir:
                        msg_botao = "✅ CONCLUIR PEDIDO"
                        if pendencias_conferencia > 0:
                            st.warning("Atenção: Existem itens sem conferência. Ao concluir, você assume a validação.")
                            msg_botao = "✅ CONCLUIR (SEM CONFERÊNCIA)"
                            
                        if st.button(msg_botao, type="primary"):
                            encerrar_cronometros_abertos(s, ped.id)
                            ped.status = "CONCLUIDO"
                            ped.data_conclusao = datetime.now()
                            s.commit(); st.balloons(); time.sleep(1); st.rerun()
                    else:
                        st.caption("🚫 *Conclusão bloqueada: Finalize a Separação e o Input no ERP.*")

    # 4. USERS
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
            peds_sep = s.query(Pedido).filter(Pedido.status.in_(['PENDENTE', 'EM_SEPARACAO', 'EM_CONFERENCIA', 'AGUARDANDO_INPUT'])).all()
            # Mostramos mais status aqui para permitir que o separador corrija algo mesmo se já avançou,
            # desde que não esteja CONCLUIDO.
            
            if not peds_sep: st.info("Sem pedidos.")
            else:
                pid = st.selectbox("Pedido (Separação)", [p.id for p in peds_sep], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in peds_sep if p.id==x), x))
                ped = s.query(Pedido).get(pid)
                
                if ped.status == 'PENDENTE':
                    if st.button("▶️ INICIAR"): ped.status = 'EM_SEPARACAO'; ped.data_inicio_separacao = datetime.now(); s.commit(); st.rerun()
                
                else:
                    st.info(f"Pedido: {ped.numero_pedido}")
                    itens_pendentes = []
                    for it in ped.itens:
                        done = round(sum([sep.qtd_separada for sep in it.separacoes]), 2)
                        meta = round(it.qtd_solicitada, 2)
                        if done < meta: itens_pendentes.append(it.codigo)
                        
                        color = "green" if done >= meta else "red"
                        with st.expander(f":{color}[{it.codigo} {it.descricao}] ({done}/{meta})"):
                            for sep in it.separacoes:
                                c1, c2, c3 = st.columns([4, 2, 1])
                                c1.text(sep.rastreabilidade); c2.text(sep.qtd_separada)
                                if c3.button("🗑️", key=f"d{sep.id}"): s.delete(sep); s.commit(); st.rerun()
                            c1, c2, c3 = st.columns([3, 2, 1])
                            nl = c1.text_input("Lote", key=f"ls{it.id}"); nq = c2.number_input("Qtd", step=0.1, key=f"qs{it.id}")
                            if c3.button("Add", key=f"as{it.id}"):
                                if nl and nq > 0: s.add(Separacao(item_id=it.id, rastreabilidade=nl, qtd_separada=nq, separador_id=u.id)); s.commit(); st.rerun()
                    
                    st.divider()
                    if not itens_pendentes:
                        # Se ainda não avançou status, permite avançar
                        if ped.status == 'EM_SEPARACAO':
                            if st.button("🏁 ENVIAR PARA CONFERÊNCIA"):
                                ped.status = "EM_CONFERENCIA"; ped.data_fim_separacao = datetime.now(); s.commit(); st.success("Enviado!"); time.sleep(1); st.rerun()
                    else: st.warning(f"Pendentes: {', '.join(itens_pendentes)}")

    # --- CONFERÊNCIA ---
    if "📋 Conferência" in tabs_to_show:
        with tabs[tabs_to_show.index("📋 Conferência")]:
            peds_conf = s.query(Pedido).filter(Pedido.status.in_(['EM_CONFERENCIA', 'AGUARDANDO_INPUT'])).all()
            if not peds_conf: st.info("Sem pedidos para conferência.")
            else:
                pid = st.selectbox("Pedido (Conferência)", [p.id for p in peds_conf], format_func=lambda x: next((f"{p.numero_pedido} [{p.status}]" for p in peds_conf if p.id==x), x))
                ped = s.query(Pedido).get(pid)
                
                pendencias_conf = 0
                for it in ped.itens:
                    with st.expander(f"{it.codigo} {it.descricao}"):
                        cols = st.columns([3, 2, 2, 2])
                        cols[0].write("**Rastro**"); cols[1].write("**Qtd**"); cols[3].write("**OK?**")
                        for sep in it.separacoes:
                            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
                            c1.text(sep.rastreabilidade); c2.text(sep.qtd_separada)
                            ic = c4.checkbox("Visto", value=sep.conferido, key=f"c_{sep.id}")
                            if ic != sep.conferido: sep.conferido = ic; s.commit(); st.rerun()
                            if not sep.conferido: pendencias_conf += 1
                
                st.divider()
                if pendencias_conf == 0:
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
