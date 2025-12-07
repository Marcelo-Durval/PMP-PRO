# 🏭 Sistema PMP Pro (Gestão de Separação Industrial)

Sistema web desenvolvido para automação do fluxo de separação de materiais, rastreabilidade de lotes e controle de produtividade da equipe de almoxarifado. Substitui planilhas manuais por um fluxo digital seguro com banco de dados.

![Status](https://img.shields.io/badge/Status-Produção-green)
![Docker](https://img.shields.io/badge/Docker-Container-blue)
![Python](https://img.shields.io/badge/Backend-Python-yellow)
![Postgres](https://img.shields.io/badge/Database-PostgreSQL-blue)

## 🚀 Funcionalidades Principais

### 🛡️ Módulo ADM (Planejamento)
* **Importação Inteligente:** Lê arquivos \`.xls\` (Crystal Reports) ou CSV, extraindo automaticamente Pedido, Data e Itens.
* **Staging Area (Validação):** Permite limpar itens "lixo" (cabeçalhos, rodapés) antes de liberar para operação.
* **Kanban de Pedidos:** Visualização clara de \`A Fazer\`, \`Em Andamento\` e \`Concluídos\`.
* **Monitoramento em Tempo Real:** Vê quais operadores estão trabalhando ou em pausa no exato momento.
* **Conferência Visual:** Indicadores de cor (🟢 OK, 🟠 Excesso, 🔴 Falta) para conferência rápida.
* **Gestão de Usuários:** Criação, reset de senha e exclusão de operadores.
* **Auditoria:** Botão para excluir pedidos (mesmo concluídos) e limpeza de banco.

### 📦 Módulo Operador (Almoxarifado)
* **Cronômetro Individual:** Registro de tempo real com funções de \`Iniciar\`, \`Pausar\` (Almoço) e \`Retomar\`.
* **Rastreabilidade N:1:** Permite bipar múltiplos lotes para atender um único item.
* **Validação na Ponta:** Alerta o operador se ele tentar separar mais do que o solicitado.
* **Interface Limpa:** Focada em agilidade e uso em tablets/celulares.

---

## 🛠️ Stack Tecnológica

O projeto foi desenhado para rodar localmente ou em servidor intranet via Docker.

* **Frontend/Backend:** Python (Streamlit)
* **Banco de Dados:** PostgreSQL 15 (Containerizado)
* **ORM:** SQLAlchemy
* **Infraestrutura:** Docker & Docker Compose

---

## ⚙️ Instalação e Execução

### Pré-requisitos
* Docker e Docker Compose instalados na máquina (Linux/Windows/Mac).

### 1. Clonar o Repositório
\`\`\`bash
git clone https://github.com/SEU_USUARIO/sistema-pmp-pro.git
cd sistema-pmp-pro
\`\`\`

### 2. Rodar a Aplicação
Execute o comando abaixo para construir as imagens e subir o banco de dados:

\`\`\`bash
sudo docker-compose up -d --build
\`\`\`

O sistema estará acessível em: \`http://localhost:8501\` (ou no IP da máquina na rede).

---

## 📚 Manual de Uso do Fluxo

### 1. Importação e Validação (ADM)
O ADM importa o arquivo \`.xls\` na aba **Importar**.

O pedido vai para o status **VALIDAÇÃO**.

Na aba Validação, o ADM remove itens desnecessários da tabela.

Clica em **🚀 Liberar**, enviando o pedido para os operadores.

### 2. Separação (Operador)
O Operador loga no sistema e vê a lista de tarefas.

Clica em **INICIAR** ou **JUNTAR-SE** (o tempo começa a contar individualmente).

Preenche **Lote** e **Quantidade** item a item.

Se precisar sair, clica em **PAUSAR**.

Ao terminar, clica em **FINALIZAR E ENVIAR**.

O sistema fecha automaticamente os tempos abertos.

### 3. Conferência e Baixa (ADM)
O ADM visualiza o pedido na coluna **Em Andamento**.

Verifica os tempos de cada operador no painel de **Performance**.

Confere se as quantidades batem (verde).

Se houver erro, clica em **Devolver para Correção**.

Se estiver tudo certo, clica em **Aprovar**.

O sistema gera o **Excel Final** formatado para importação no ERP.

---

## 🔐 Acesso Padrão (Primeiro Login)

**Usuário:** admin  
**Senha:** 123

> Recomenda-se criar novos usuários e alterar a senha do admin na aba **"Usuários"** logo após o primeiro acesso.
