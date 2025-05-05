"""
Módulo de funções hidrológicas para uso em aplicativos Streamlit
Inclui cálculo de SPI, IDF, hmax, desagregação de precipitação e ajuste de parâmetros IDF
"""
import tempfile
import numpy as np
import pandas as pd
from scipy.optimize import minimize, least_squares
from scipy.stats import gamma, norm

def calcular_hmax(media, desvio_padrao, tempo_retorno):
    """
    Determina a precipitação máxima diária para tempos de retorno específicos.
    """
    return media - desvio_padrao * (0.45 + 0.7797 * np.log(np.log(tempo_retorno / (tempo_retorno - 1))))

def desagragacao_preciptacao(h_max1):
    """
    Estima precipitações máximas para diferentes durações a partir do valor diário.
    """
    dados_hmax = {'td (min)': [1440, 720, 600, 480, 360, 180, 60, 30, 25, 20, 15, 10, 5]}
    multiplicadores = [0.85, 0.78, 0.72, 0.54, 0.48, 0.42, 0.74, 0.91, 0.81, 0.7, 0.54, 0.34]
    for i, valor in enumerate(['2', '5', '10', '15', '20', '25', '50', '100', '250', '500', '1000']):
        h_max = [0] * 13
        h_max[0] = h_max1[i] * 1.14
        for j in range(1, len(multiplicadores) + 1):
            if j <= 6:
                h_max[j] = h_max[0] * multiplicadores[j - 1]
            elif j == 7:
                h_max[j] = h_max[6] * multiplicadores[j - 1]
            else:
                h_max[j] = h_max[7] * multiplicadores[j - 1]
        dados_hmax[valor] = h_max
    return pd.DataFrame(dados_hmax)

def conversao_intensidade(preciptacao):
    """
    Converte precipitações em intensidades (mm/h).
    """
    divisores = [5/60, 10/60, 15/60, 20/60, 25/60, 30/60, 1, 2, 3, 6, 8, 12, 24]
    divisores.reverse()
    def multiply_row(row):
        return row[:-1] / divisores  # assume 'td (min)' está na última coluna
    intensidades = preciptacao.copy()
    intensidades.iloc[:, 1:-1] = intensidades.iloc[:, 1:-1].apply(lambda col: col / divisores[intensidades.columns.get_loc(col.name)-1])
    return intensidades

def calculo_precipitacoes(df_inmet):
    """
    Processa dados de precipitação diária para gerar hmax, precipitações, intensidades e tabela IDF.
    """
    df = df_inmet.copy()
    if 'PRECIPITACAO TOTAL, DIARIO (AUT)(mm)' in df.columns:
        df.rename(columns={'PRECIPITACAO TOTAL, DIARIO (AUT)(mm)': 'PRECIPITACAO TOTAL DIARIA (mm)'}, inplace=True)
    elif 'PRECIPITACAO TOTAL, DIARIO(mm)' in df.columns:
        df.rename(columns={'PRECIPITACAO TOTAL, DIARIO(mm)': 'PRECIPITACAO TOTAL DIARIA (mm)'}, inplace=True)
    elif 'PRECIPITACAO TOTAL DIARIA (mm)' not in df.columns:
        raise ValueError("Coluna de precipitação não encontrada.")

    df['Data Medicao'] = pd.to_datetime(df['Data Medicao'])
    df['ano hidrológico'] = df['Data Medicao'].dt.year
    df['PRECIPITACAO TOTAL DIARIA (mm)'] = pd.to_numeric(df['PRECIPITACAO TOTAL DIARIA (mm)'], errors='coerce')
    df.dropna(subset=['PRECIPITACAO TOTAL DIARIA (mm)'], inplace=True)

    maiores_precipitacoes_por_ano = df.groupby('ano hidrológico')['PRECIPITACAO TOTAL DIARIA (mm)'].max()
    media = maiores_precipitacoes_por_ano.mean()
    desvio_padrao = maiores_precipitacoes_por_ano.std()

    tempo_retorno = [2, 5, 10, 15, 20, 25, 50, 100, 250, 500, 1000]
    h_max1 = [calcular_hmax(media, desvio_padrao, tr) for tr in tempo_retorno]
    h_max1aux = pd.DataFrame({'tempo de retorno (anos)': tempo_retorno, 'Pmax diária (mm)': h_max1})

    preciptacao = desagragacao_preciptacao(h_max1)
    intensidade = conversao_intensidade(preciptacao)

    df_longo = intensidade.melt(id_vars='td (min)', var_name='tr', value_name='y_obs (mm/h)')
    df_longo['tr'] = df_longo['tr'].astype(float)

    return h_max1aux, preciptacao, intensidade, df_longo, media, desvio_padrao

# def problema_inverso_idf(df_long):
#     """
#     Ajusta os parâmetros a, b, c, d da equação IDF usando mínimos quadrados.
#     """
#     t_r = df_long['tr'].values
#     t_c = df_long['td (min)'].values
#     y_obs = df_long['y_obs (mm/h)'].values

#     def model_function(params, t_r, t_c):
#         a, b, c, d = params
#         return (a * t_r ** b) / (t_c + c)**d

#     def error_function(params, t_r, t_c, y_obs):
#         y_pred = model_function(params, t_r, t_c)
#         return np.mean((y_pred - y_obs) ** 2)

#     initial_guess = [1, 1, 1, 1]
#     bounds = [(1e-5, None)] * 4
#     result = minimize(error_function, initial_guess, args=(t_r, t_c, y_obs), bounds=bounds)
#     return tuple(result.x)

def problema_inverso_idf(df_longo):
    """
    Ajusta os parâmetros a, b, c, d da equação IDF com o método Levenberg-Marquardt (LM),
    com tratamento robusto de valores inválidos.
    """
    try:
        # Pré-processamento
        df_longo = df_longo.dropna(subset=['tr', 'td (min)', 'y_obs (mm/h)']).copy()

        if len(df_longo) < 5:
            raise ValueError("Poucos dados para ajuste")

        t_r = df_longo['tr'].astype(float).values
        t_c = df_longo['td (min)'].astype(str).str.replace(',', '.', regex=False).astype(float).values
        y_obs = df_longo['y_obs (mm/h)'].astype(float).values

        def residuals(params, t_r, t_c, y_obs):
            a, b, c, d = params

            # Proteção contra erros de potenciação
            t_r_safe = np.where(t_r > 0, t_r, 1e-6)
            t_c_safe = np.where((t_c + c) > 0, t_c + c, 1e-6)

            try:
                y_pred = (a * t_r_safe ** b) / (t_c_safe ** d)
            except Exception:
                y_pred = np.full_like(y_obs, 1e6)

            return np.nan_to_num(y_pred - y_obs, nan=1e6, posinf=1e6, neginf=-1e6)

        # Ajuste LM 
        result = least_squares(
            residuals,
            x0=[500, 0.1, 5, 0.3],
            args=(t_r, t_c, y_obs),
            method='lm',
            max_nfev=1000
        )

        if not np.all(np.isfinite(result.x)):
            raise ValueError("Parâmetros não numéricos")

        return result.x  
    
    except Exception as e:
        print(f"[problema_inverso_idf] Erro durante ajuste: {e}")
        return [np.nan, np.nan, np.nan, np.nan]
    

def indice_spi(df_inmet):
    """
    Calcula o SPI mensal com base nos dados diários de precipitação.
    Aplica validações e proteções para séries incompletas ou inválidas.
    """
    df = df_inmet.copy()
    if 'Unnamed: 2' in df.columns:
        df = df.drop(columns=['Unnamed: 2'])

    df.columns = ['Data Medição', 'Precipitação Total Diária (mm)']
    df['Precipitação Total Diária (mm)'] = df['Precipitação Total Diária (mm)'] \
        .astype(str).str.replace(',', '.', regex=False).astype(float)

    df['Data Medição'] = pd.to_datetime(df['Data Medição'], errors='coerce')
    df = df.dropna(subset=['Data Medição', 'Precipitação Total Diária (mm)'])

    # Verifica se há dados válidos suficientes
    if df.empty or df['Precipitação Total Diária (mm)'].le(0).all():
        raise ValueError("Série de precipitação inválida ou sem valores positivos.")

    df['AnoMes'] = df['Data Medição'].dt.to_period('M')
    precip_mensal = df.groupby('AnoMes')['Precipitação Total Diária (mm)'].sum()

    spi_mensal = []
    estatisticas = []

    for mes in range(1, 13):
        dados_mes = precip_mensal[precip_mensal.index.month == mes]

        if len(dados_mes) < 3 or dados_mes.sum() == 0:
            # Preencher com NaN se o mês tiver poucos dados
            spi_mensal.extend([np.nan] * len(dados_mes))
            estatisticas.append({
                'Mês': mes,
                'Média Mensal': np.nan,
                'q (zeros)': np.nan,
                'Alpha (shape)': np.nan,
                'Beta (scale)': np.nan
            })
            continue

        media = np.mean(dados_mes)
        zeros = (dados_mes == 0).sum()
        positivos = dados_mes[dados_mes > 0]

        if len(positivos) < 2:
            spi_mensal.extend([np.nan] * len(dados_mes))
            estatisticas.append({
                'Mês': mes,
                'Média Mensal': media,
                'q (zeros)': zeros / len(dados_mes),
                'Alpha (shape)': np.nan,
                'Beta (scale)': np.nan
            })
            continue

        prob_zeros = zeros / len(dados_mes)

        try:
            shape, loc, scale = gamma.fit(positivos, floc=0)
            cdf = gamma.cdf(dados_mes, shape, loc=loc, scale=scale)
            cdf = np.clip(cdf, 1e-10, 1 - 1e-10)  # evita ±inf
            cdf_adjusted = prob_zeros + (1 - prob_zeros) * cdf
            spi_mes = norm.ppf(cdf_adjusted)
        except Exception:
            spi_mes = [np.nan] * len(dados_mes)
            shape, scale = np.nan, np.nan

        spi_mensal.extend(spi_mes)
        estatisticas.append({
            'Mês': mes,
            'Média Mensal': media,
            'q (zeros)': prob_zeros,
            'Alpha (shape)': shape,
            'Beta (scale)': scale
        })

    spi_df = pd.DataFrame({
        'AnoMes': precip_mensal.index,
        'PrecipitaçãoMensal': precip_mensal.values,
        'SPI': spi_mensal
    })

    estatisticas_df = pd.DataFrame(estatisticas)
    return spi_df, estatisticas_df


def save_figure_temp(fig):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    fig.savefig(temp_file.name)
    temp_file.close()
    return temp_file.name