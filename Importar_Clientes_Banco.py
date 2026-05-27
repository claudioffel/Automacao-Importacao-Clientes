
# Importa a planilha de clientes tratada para uma tabela ponte no SQL Server.

# Este arquivo foi criado para ser executado depois do notebook, ou seja, depois que a planilha tratada ja existir.

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

import pandas as pd
import pyodbc


# ============================================================
# VARIAVEIS DE CONEXAO COM O BANCO
# ============================================================

SERVIDOR = "localhost,porta"
BANCO = "BANCO_LOJA"
USUARIO = "sa"
SENHA = "senha_do_sql"
DRIVER_ODBC = "ODBC Driver 17 for SQL Server"

# Nome da tabela ponte que recebera os dados tratados.
SCHEMA = "dbo"
TABELA = "CLIENTES_TRATADOS"


# ============================================================
# CAMINHO DA PLANILHA TRATADA
# ============================================================

CAMINHO_PLANILHA = (
    r"C:/ESTUDOS/Projetos/Importar Clientes/Planilhas/Clientes_Tratados.xlsx"
)


# ============================================================
# COLUNAS ESPERADAS
# ============================================================

COLUNAS_BANCO = [
    "CPFCNPJ",
    "NOME",
    "CIDADE",
    "RAZAO SOCIAL",
    "UF",
    "CEP",
    "TELEFONE",
    "LIMITE",
]


def conectar_sql_server() -> pyodbc.Connection:

    texto_conexao = (
        f"DRIVER={{{DRIVER_ODBC}}};"
        f"SERVER={SERVIDOR};"
        f"DATABASE={BANCO};"
        f"UID={USUARIO};"
        f"PWD={SENHA};"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(texto_conexao)

# Recebe qualquer valor e devolve somente os numeros.
def somente_numeros(valor: object) -> str:

    if pd.isna(valor):
        return ""

    texto = str(valor).strip()

    texto = re.sub(r"\.0$", "", texto)

    return re.sub(r"\D", "", texto)

# Limpa textos comuns antes do insert.
def texto_limpo(valor: object, tamanho_maximo: int) -> str:

    if pd.isna(valor):
        return ""

    texto = str(valor).strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto[:tamanho_maximo]

# Converte o limite para Decimal(18, 0).
def converter_limite(valor: object) -> Decimal:

    if pd.isna(valor):
        return Decimal("0")

    texto = str(valor).strip()
    if not texto:
        return Decimal("0")

    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        return Decimal(texto).quantize(Decimal("1"))
    except (InvalidOperation, ValueError):
        return Decimal("0")

# Le a planilha tratada e padroniza os nomes das colunas.
def carregar_planilha() -> pd.DataFrame:


    clientes = pd.read_excel(CAMINHO_PLANILHA, dtype="object")

    clientes.columns = [str(coluna).strip() for coluna in clientes.columns]

    if "RAZAOSOCIAL" in clientes.columns and "RAZAO SOCIAL" not in clientes.columns:
        clientes = clientes.rename(columns={"RAZAOSOCIAL": "RAZAO SOCIAL"})

    colunas_faltando = [
        coluna for coluna in COLUNAS_BANCO if coluna not in clientes.columns
    ]
    if colunas_faltando:
        raise ValueError(f"Colunas faltando na planilha: {colunas_faltando}")

    clientes = clientes[COLUNAS_BANCO].copy()

    # Padroniza cada coluna para respeitar os tipos e tamanhos informados.
    clientes["CPFCNPJ"] = clientes["CPFCNPJ"].apply(somente_numeros).str[:14]
    clientes["NOME"] = clientes["NOME"].apply(lambda valor: texto_limpo(valor, 150))
    clientes["CIDADE"] = clientes["CIDADE"].apply(lambda valor: texto_limpo(valor, 150))
    clientes["RAZAO SOCIAL"] = clientes["RAZAO SOCIAL"].apply(
        lambda valor: texto_limpo(valor, 150)
    )
    clientes["UF"] = clientes["UF"].apply(lambda valor: texto_limpo(valor, 2).upper())
    clientes["CEP"] = clientes["CEP"].apply(somente_numeros).str[:8]
    clientes["TELEFONE"] = clientes["TELEFONE"].apply(somente_numeros).str[:15]
    clientes["LIMITE"] = clientes["LIMITE"].apply(converter_limite)

    # Evita tentar inserir linhas sem CPF/CNPJ, porque esse campo sera usado como criterio para nao duplicar clientes na tabela ponte.
    clientes = clientes[clientes["CPFCNPJ"] != ""].copy()

    clientes = clientes.drop_duplicates(subset=["CPFCNPJ"], keep="first")

    return clientes

# Cria a tabela ponte se ela ainda nao existir.
def criar_tabela_se_nao_existir(conexao: pyodbc.Connection) -> None:

    comando_sql = f"""
    IF OBJECT_ID(N'[{SCHEMA}].[{TABELA}]', N'U') IS NULL
    BEGIN
        CREATE TABLE [{SCHEMA}].[{TABELA}] (
            [CPFCNPJ] NVARCHAR(14) NULL,
            [NOME] NVARCHAR(150) NULL,
            [CIDADE] NVARCHAR(150) NULL,
            [RAZAO SOCIAL] NVARCHAR(150) NULL,
            [UF] NVARCHAR(2) NULL,
            [CEP] NVARCHAR(8) NULL,
            [TELEFONE] NVARCHAR(15) NULL,
            [LIMITE] DECIMAL(18, 0) NULL
        );
    END
    """

    cursor = conexao.cursor()
    cursor.execute(comando_sql)
    conexao.commit()

# Busca todos os CPFs/CNPJs que ja estao na tabela.
def buscar_cpfs_cnpjs_existentes(conexao: pyodbc.Connection) -> set[str]:

    cursor = conexao.cursor()
    cursor.execute(f"SELECT [CPFCNPJ] FROM [{SCHEMA}].[{TABELA}] WHERE [CPFCNPJ] IS NOT NULL")

    existentes = set()
    for linha in cursor.fetchall():
        cpf_cnpj = somente_numeros(linha[0])
        if cpf_cnpj:
            existentes.add(cpf_cnpj)

    return existentes

# Insere no SQL Server somente os clientes que ainda nao existem.
def inserir_clientes(conexao: pyodbc.Connection, clientes: pd.DataFrame) -> int:

    cpfs_cnpjs_existentes = buscar_cpfs_cnpjs_existentes(conexao)

    clientes_para_inserir = clientes[
        ~clientes["CPFCNPJ"].isin(cpfs_cnpjs_existentes)
    ].copy()

    if clientes_para_inserir.empty:
        return 0

    comando_insert = f"""
    INSERT INTO [{SCHEMA}].[{TABELA}] (
        [CPFCNPJ],
        [NOME],
        [CIDADE],
        [RAZAO SOCIAL],
        [UF],
        [CEP],
        [TELEFONE],
        [LIMITE]
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    dados = [
        (
            linha["CPFCNPJ"],
            linha["NOME"],
            linha["CIDADE"],
            linha["RAZAO SOCIAL"],
            linha["UF"],
            linha["CEP"],
            linha["TELEFONE"],
            linha["LIMITE"],
        )
        for _, linha in clientes_para_inserir.iterrows()
    ]

    cursor = conexao.cursor()

    cursor.fast_executemany = True
    cursor.executemany(comando_insert, dados)
    conexao.commit()

    return len(dados)

# Organiza a ordem completa do processo.
def main() -> None:


    print("Lendo a planilha tratada...")
    clientes = carregar_planilha()
    print(f"Clientes validos na planilha: {len(clientes)}")

    print("Conectando ao SQL Server...")
    with conectar_sql_server() as conexao:
        print("Criando a tabela ponte, caso ela ainda nao exista...")
        criar_tabela_se_nao_existir(conexao)

        print("Inserindo apenas CPFs/CNPJs que ainda nao existem...")
        quantidade_inserida = inserir_clientes(conexao, clientes)

    print(f"Processo finalizado. Linhas inseridas: {quantidade_inserida}")


if __name__ == "__main__":
    main()