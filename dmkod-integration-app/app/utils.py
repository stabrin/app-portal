import os
import pandas as pd
from psycopg2 import sql
from psycopg2.extras import execute_values

def upsert_data_to_db(cursor, table_env_var: str, df: pd.DataFrame, pk_column: str):
    """
    Универсальная функция для UPSERT данных в любую таблицу.
    """
    table_name = os.getenv(table_env_var)
    if not table_name:
        raise ValueError(f"Переменная окружения {table_env_var} не найдена в .env файле!")
    
    columns = list(df.columns)
    update_cols_str = sql.SQL(', ').join(
        sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(col))
        for col in columns if col != pk_column
    )
    query = sql.SQL("""
        INSERT INTO {table} ({cols}) VALUES %s
        ON CONFLICT ({pk}) DO UPDATE SET {update_cols};
    """).format(
        table=sql.Identifier(table_name),
        cols=sql.SQL(', ').join(map(sql.Identifier, columns)),
        pk=sql.Identifier(pk_column),
        update_cols=update_cols_str
    )
    df_prepared = df.where(pd.notnull(df), None)
    data_tuples = [tuple(row) for row in df_prepared.itertuples(index=False)]
    execute_values(cursor, query, data_tuples, page_size=1000)