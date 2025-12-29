# 🏭 Sistema PMP Pro - Gestão de Pedidos e Produção

O **Sistema PMP Pro** é uma solução web completa para gestão de Planejamento e Controle de Produção (PCP/PCM) e Logística. Ele gerencia o ciclo de vida de pedidos desde a importação de planilhas (Excel/CSV), passando pela separação e conferência no chão de fábrica, até a exportação final para o ERP.

O sistema foca em **rastreabilidade**, **controle de tempo (H.H.)** e **flexibilidade na importação de dados**.

---

## 🚀 Funcionalidades Principais

### 1. Administração (Gerencial)

- **Importação Flexível**  
  Aceita qualquer layout de planilha (Excel/CSV). O usuário mapeia as colunas (Código, Descrição, Qtd) na hora.

- **Gestão de Lotes/Duplicatas**  
  Identificação inteligente de itens via **ID Único (Lote)** ou **Automático (Linha do Excel)**. Permite atualizar pedidos existentes sem duplicar dados.

- **Dashboard de Performance**  
  Indicadores dos últimos 30 dias, tempo médio de separação, produtividade por operador e gráfico de **H.H. (Homem-Hora)**.

- **Alertas Críticos**  
  Notificação visual imediata de divergências entre o físico e o sistêmico (ERP).

- **Controle de Usuários**  
  Criação e remoção de perfis (**ADM, Separador, Conferente**).

---

### 2. Operação (Chão de Fábrica)

#### Separação
- Cronômetro de atividade (Início / Pausa / Fim)
- Leitura de código de barras (Câmera ou Digitação)
- Correção de itens rejeitados pela conferência

#### Conferência
- Validação cega ou assistida
- Opção de aceitar divergência (com alerta) ou recusar para retrabalho

---

## 🛠️ Instalação e Configuração

### Pré-requisitos
- Docker e Docker Compose instalados na máquina servidor/local

---

### Estrutura de Arquivos

- `app.py` — Código da aplicação  
- `Dockerfile` — Configuração da imagem  
- `docker-compose.yml` — Orquestração dos containers  
- `requirements.txt` — Bibliotecas Python  

---

### Conteúdo do `requirements.txt`

```plaintext
streamlit
pandas
sqlalchemy
psycopg2-binary
opencv-python-headless
numpy
zxing-cpp
openpyxl
xlsxwriter
pillow
watchdog
```

---

### Subindo o Sistema

```bash
sudo docker-compose up -d --build
```

Acesse em: **http://localhost:8501**

---

### Primeiro Acesso

- **Usuário:** admin  
- **Senha:** 123  

---

## 🔄 Fluxo Operacional (Passo a Passo)

### Fase 1: Importação e Validação (ADM)

1. Acesse a aba **📥 Importar**
2. Carregue a planilha `.xlsx` ou `.csv`
3. Faça o mapeamento das colunas:
   - Código
   - Descrição
   - Quantidade
   - ID Único (opcional)
4. Clique em **🚀 Processar**

> Se o pedido já existir, o sistema exibirá uma tela de comparação para confirmar atualização ou inclusão de itens.

5. Vá para **🛡️ Validação** e clique em **🚀 Liberar p/ Produção**

---

### Fase 2: Separação (Perfil Separador)

1. Login do operador
2. Selecionar o pedido
3. Clique em **▶️ INICIAR SEP.**
4. Separar itens via código de barras ou digitação
5. Clique em **🚀 ENVIAR TUDO**

➡️ O cronômetro pausa automaticamente e os itens seguem para conferência.

---

### Fase 3: Conferência (Perfil Conferente)

1. Visualizar itens recebidos
2. Clique em **▶️ INICIAR CONF.**
3. Digite a quantidade física e clique em **Conf**

- Quantidade correta → ✅ Aprovado  
- Divergência → alerta do sistema:
  - **Aceitar** → assume divergência
  - **Recusar** → retorna ao separador (gera retrabalho)

---

### Fase 4: Gestão e Fechamento (ADM)

- Monitoramento em tempo real na aba **🏭 Gestão**

#### Alertas
- Divergência separado × conferido → ⚠️ Amarelo  
- Erro após lançamento no ERP → 🔴 Alerta Crítico  

#### Resolução
- Ajuste no ERP
- Clique em **Aceitar Divergência**

#### Arquivamento
- Tudo OK → **✅ CONCLUIR**
- Com pendências:
  - Marcar **Sou Gerente**
  - **⚠️ FORÇAR ARQUIVAMENTO** (justificativa obrigatória)

📄 Geração automática de **Excel final** com observações do gerente.

---

## 📊 Dashboard e Indicadores

- **H.H. por Operador**  
  Tempo em separação × conferência

- **Produtividade**  
  Itens processados por hora

- **Qualidade**  
  Divergências aceitas × retrabalho

---

## 🆘 Solução de Problemas

### Erro `PendingRollbackError`
```bash
sudo docker-compose restart
```

### Câmera não abre no celular
- Navegadores exigem HTTPS (exceto localhost)
- Use proxy reverso com SSL (ex.: Nginx)

### Código atualizado mas não refletiu
```bash
sudo docker-compose up -d --build
```

---

🏭 **Sistema PMP Pro**  
Alta performance, rastreabilidade total e controle completo do chão de fábrica.
