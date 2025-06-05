"""ClimateTwin - Módulo para manipulação de dados da plataforma Banco de Dados Meteorológicos do INMET"""
from datetime import datetime

import pandas as pd
import numpy as np


def ler_dados(dados: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """
    Leitura de dados do arquivo CSV do BDMEP e  extração do cabeçalho.

    :param dados: Caminho para o arquivo CSV da da base de dados BDMEP.

    :return: saida[0] = Metadados do arquivo de dados BDMEP (cidade, lat, long, alt, ..., etc), saida[1] = Dados meteorológicos base BDMEP ('data medicao', 'precipitacao total diaria (mm)', 'temperatura media diaria (°C)', 'umidade relativa ar media diaria (%)', 'velocidade vento media diaria (m/s)')
    """

    with open(dados, 'r', encoding='utf-8') as f:
        linhas = [next(f).strip() for _ in range(9)]
        cabecalho = {}
        for linha in linhas:
            if ':' in linha:
                chave, valor = linha.split(':', 1)
                chave_formatada = chave.strip().lower().replace(' ', '_')
                valor = valor.strip()
                if chave_formatada in ['latitude', 'longitude', 'altitude']:
                    valor = float(valor)
                elif chave_formatada in ['data_inicial', 'data_final']:
                    valor = datetime.strptime(valor, '%Y-%m-%d').date()
                cabecalho[chave_formatada] = valor
    df = pd.read_csv(dados, sep=";", encoding="utf-8", skiprows=9)
    df.drop(columns=['Unnamed: 5'], inplace=True, errors='ignore')
    df.columns = ['data medicao', 'precipitacao total diaria (mm)', 'temperatura media diaria (°C)', 'umidade relativa ar media diaria (%)', 'velocidade vento media diaria (m/s)']
    df['data medicao'] = pd.to_datetime(df['data medicao'], errors='coerce')
    
    return cabecalho, df


def calcular_hmax(mu, sigma, tr):
    """
    Cálculo da preciptação máxima diária em função do período de retorno.

    :param mu Média das máximas precipitações anuais (mm).
    :param sigma: Desvio padrão das máximas precipitações anuais (mm).
    :param tr: Período de retorno (anos).

    :return: Precipitação máxima diária (mm) em função do período de retorno (anos).
    """

    return mu - sigma * (0.45 + 0.7797 * np.log(np.log(tr / (tr - 1))))


def desagragacao_preciptacao_maxima_diaria_matriz_intensidade_chuva(h_max1):
    """
    Desagregação da precipitação máxima diária (mm) em função do tempo de concentração (tc) em minutos e tempo de retorno (tr) em anos para matriz de intensidade de chuva (mm/h)

    :param h_max1: Precipitação máxima diária (mm) em função do período de retorno (anos).

    :return: Matriz de intensidade de chuva (mm/h) em função do tempo de concentração (tc) em minutos e tempo de retorno (tr) em anos.
    """

    tc_list = [1440, 720, 600, 480, 360, 180, 60, 30, 25, 20, 15, 10, 5]
    tc_convert = [1.14, 0.85, 0.78, 0.72, 0.54, 0.48, 0.42, 0.74, 0.91, 0.81, 0.70, 0.54, 0.34]
    i_convert = [1/24, 1/12, 1/8, 1/6, 1/3, 1/2, 1, 1/(30/60), 1/(25/60), 1/(20/60), 1/(15/60), 1/(10/60), 1/(5/60)]
    tr = []
    tc = []
    y = []
    for index, row in h_max1.iterrows():
        y_aux = []
        for i, value in enumerate(tc_convert): 
            tr.append(row['t_r (anos)'])
            tc.append(tc_list[i])
            if i == 0:
                y_aux.append(row['h_max,1 (mm)'] * value)
            elif i > 0 and i <= 6:
                y_aux.append(y_aux[0] * value)
            elif i == 7:
                y_aux.append(y_aux[6] * value)
            else:
                y_aux.append(y_aux[7] * value)
        y_aux = [a * b for a, b in zip(y_aux, i_convert)]
        y += y_aux
    matriz_intensidade = {'t_c (min)': tc, 't_r (anos)': tr, 'y_obs (mm/h)': y}

    return pd.DataFrame(matriz_intensidade)


def calculo_precipitacoes(df: pd.DataFrame, metadados: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Processa dados de precipitação bruta para gerar preciptação máxima diária e precipitações em mm/h em diferentes períodos de retorno e tempo de concentração.

    :param df: Dados meteorológicos base BDMEP.
    :param metadados: Metadados do arquivo de dados BDMEP (cidade, lat, long, alt, ..., etc)

    :return: saida[0] = Precipitação máxima diária (mm) em função do período de retorno (anos), saida[1] = Matriz de intensidade de chuva (mm/h) em função do tempo de concentração (tc) em minutos e tempo de retorno (tr) em anos.
    """
    
    # Limpeza e formatação dos dados
    df.dropna(subset=['temperatura media diaria (°C)', 'umidade relativa ar media diaria (%)', 'velocidade vento media diaria (m/s)'], inplace=True)
    df['ano hidrologico'] = df['data medicao'].dt.year
    df['precipitacao total diaria (mm)'] = pd.to_numeric(df['precipitacao total diaria (mm)'], errors='coerce')

    # Extração da média e desvio padrão das maiores precipitações anuais
    maiores_precipitacoes_por_ano = df.groupby('ano hidrologico')['precipitacao total diaria (mm)'].max()
    media = maiores_precipitacoes_por_ano.mean()
    desvio_padrao = maiores_precipitacoes_por_ano.std()

    # Altura máxima em 1 dia para diferentes períodos de retorno
    tempo_retorno = [2, 5, 10, 15, 20, 25, 50, 100, 250, 500, 1000]
    h_max1 = [calcular_hmax(media, desvio_padrao, tr) for tr in tempo_retorno]
    df_hmax1 = pd.DataFrame({'t_r (anos)': tempo_retorno, 'h_max,1 (mm)': h_max1})

    # Desagregação da precipitação máxima diária em matriz de intensidade de chuva (mm/h)
    matriz_chuva = desagragacao_preciptacao_maxima_diaria_matriz_intensidade_chuva(df_hmax1)
    matriz_chuva['latitude'] = metadados['latitude']
    matriz_chuva['longitude'] = metadados['longitude']
    matriz_chuva['altitude'] = metadados['altitude']
    matriz_chuva['cidade'] = metadados['nome']

    return df_hmax1, matriz_chuva
