# 🏭 Sistema PMP Pro (Streamlit + Docker)

**Sistema de Gestão e Controle de Separação de Pedidos Industriais (PMP)**

Este projeto é uma aplicação web desenvolvida para otimizar, digitalizar e rastrear o processo de separação de materiais em ambientes industriais ou de manutenção. Ele substitui pranchetas de papel por tablets/computadores, oferecendo cronometragem automática, controle de rastreabilidade e indicadores de produtividade em tempo real.

---

## 🎯 Objetivo e Funcionalidades

O **Sistema PMP Pro** resolve o problema da falta de visibilidade no "chão de fábrica" durante a separação de almoxarifado/expedição.

### Principais Recursos:
* **👥 Controle de Acesso:** Perfis distintos para **Administrador** (Gestão) e **Operador** (Execução).
* **📥 Importação Inteligente:** Processamento robusto de arquivos Excel/CSV (lê dados brutos e identifica pedidos automaticamente).
* **⏱️ Cronometragem Automática:** Registro de tempo (Início/Pausa/Fim) por operador e por pedido.
* **🔍 Rastreabilidade:** Campo obrigatório para informar lote/série ou local do item separado.
* **🚦 Kanban em Tempo Real:** Visualização do status dos pedidos (Pendente, Em Separação, Conferência, Concluído).
* **📊 Indicadores Visuais:** Barras de progresso e alertas coloridos para itens faltantes ou excedentes.
* **📤 Exportação:** Gera relatórios finais em Excel com todas as anotações de separação.

---

## 🛠️ Tecnologias Utilizadas

* **Frontend/Backend:** [Streamlit](https://streamlit.io/) (Python 3.9)
* **Banco de Dados:** PostgreSQL 15 (Containerizado)
* **Manipulação de Dados:** Pandas, SQLAlchemy, XlsxWriter
* **Infraestrutura:** Docker & Docker Compose

---

## 🚀 Instalação e Execução

O sistema foi projetado para rodar isolado via Docker, eliminando problemas de dependências locais.

### Pré-requisitos
* [Docker](https://www.docker.com/) e Docker Compose instalados.
* Git instalado.

### Passo a Passo

1.  **Clone o repositório:**
    ```bash
    git clone [https://github.com/Marcelo-Durval/PMP-PRO.git](https://github.com/Marcelo-Durval/PMP-PRO.git)
    cd PMP-PRO
    ```

2.  **Inicie o sistema:**
    Este comando irá construir a imagem, baixar o PostgreSQL e iniciar os serviços.
    ```bash
    sudo docker-compose up -d --build
    ```

3.  **Acesse a aplicação:**
    Abra o navegador e acesse:
    👉 **http://localhost:8501**

> **Nota:** O usuário administrador padrão é criado automaticamente no primeiro boot.
> * **Usuário:** `admin`
> * **Senha:** `123`

---

## 📖 Fluxo de Utilização (Manual do Usuário)

O fluxo do sistema é dividido em duas perspectivas: **Gerencial (ADM)** e **Operacional (Chão de Fábrica)**.

### 1️⃣ Visão do Administrador (PCM/Gestor)

O Administrador é responsável por alimentar o sistema e validar o trabalho.

1.  **Login:** Acesse com as credenciais de ADM.
2.  **Painel Gerencial:**
    * **Aba "Importar":** Faça o upload do arquivo `.xlsx` ou `.csv` contendo a lista de materiais. O sistema identifica automaticamente o número do pedido e os itens.
    * **Aba "Validação":** Os pedidos importados caem aqui primeiro. Você pode revisar os itens, excluir linhas desnecessárias e clicar em **"🚀 Liberar"**. Isso envia o pedido para a equipe operacional.
    * **Aba "Conferência":** (Kanban) Aqui você vê o status real de todos os pedidos:
        * 🟠 **A Fazer:** Pedidos liberados aguardando operador.
        * 🔵 **Em Andamento:** Pedidos sendo separados no momento.
        * 🟢 **Concluídos:** Pedidos finalizados.
3.  **Gestão de Usuários:** Na última aba, crie os logins para seus operadores (Ex: `joao`, `maria`) com o perfil "OPERADOR".

### 2️⃣ Visão do Operador (Separador)

O Operador utiliza o sistema (preferencialmente em tablet) para executar a tarefa.

1.  **Seleção de Tarefa:** Ao logar, ele vê uma lista de pedidos pendentes ("A Fazer").
2.  **Cronômetro (Início):**
    * Ao clicar em **"▶️ INICIAR"**, o pedido muda para "Em Separação".
    * O sistema começa a contar o tempo desse operador naquele pedido.
3.  **Separação dos Itens:**
    * O operador vê a lista de itens.
    * **Ação:** Ele digita a **Rastreabilidade** (Lote/Série) e a **Quantidade** separada.
    * Ao clicar em "Add", o sistema valida a quantidade.
        * ✅ **Verde:** Quantidade atingida.
        * ⚠️ **Laranja:** Quantidade excedida (aviso).
        * 🔴 **Vermelho:** Item pendente/parcial.
4.  **Pausas:** Se for almoçar ou parar, o operador clica em "⏸️ PAUSAR". O tempo para de contar.
5.  **Finalização:** Quando terminar todos os itens, clica em **"🏁 FINALIZAR E ENVIAR"**. O pedido vai para Conferência do ADM.

### 3️⃣ Fechamento (Retorno ao ADM)

1.  O Administrador acessa o pedido que está em status "EM CONFERÊNCIA".
2.  Ele visualiza os **Tempos Reais** (quanto tempo cada operador gastou).
3.  Ele confere as quantidades e rastreabilidades.
4.  **Ações Finais:**
    * **❌ Devolver:** Se houver erro, devolve para o operador (status Correção).
    * **✅ Aprovar:** Finaliza o processo.
5.  **Exportação:** No pedido "CONCLUÍDO", o botão **"⬇️ Excel"** gera um relatório completo para dar baixa no ERP ou arquivar.

---

## ❓ Solução de Problemas Comuns

### Tela Preta ("Black Screen") ao carregar
Se o navegador ficar carregando infinitamente:
1.  Limpe o cache do navegador (**CTRL + SHIFT + R**).
2.  Se estiver em rede corporativa, verifique se WebSockets são permitidos.
3.  O `docker-compose.yml` já contém as flags necessárias (`--server.enableWebsocketCompression=false`) para evitar isso.

### Erro de "Coluna não existe" (Database Error)
Se você atualizou o código (ex: mudou de "Lote" para "Rastreabilidade") e o banco de dados antigo ainda existe:
Execute o comando abaixo para resetar o banco de dados:
```bash
sudo docker-compose down -v
sudo docker-compose up -d