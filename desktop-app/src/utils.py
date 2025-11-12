# src/utils.py
import sys
import os
import pandas as pd
from psycopg2 import sql
from psycopg2.extras import execute_values


def resource_path(relative_path):
    """
    Возвращает абсолютный путь к ресурсу. Работает как для исходников,
    так и для скомпилированного приложения (PyInstaller).
    """
    try:
        # PyInstaller создает временную папку и сохраняет путь в _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # --- ИЗМЕНЕНИЕ: Если мы не в скомпилированном приложении, базовый путь - это корень 'desktop-app' ---
        # os.path.dirname(__file__) -> .../desktop-app/src
        # os.path.join(..., '..') -> .../desktop-app
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    return os.path.join(base_path, relative_path)

def upsert_data_to_db(cursor, table_name: str, dataframe: pd.DataFrame, pk_column: str):
    """
    Универсальная функция для UPSERT данных в любую таблицу.
    Адаптировано из datamatrix-app.
    """
    # --- ИЗМЕНЕНИЕ: Проверяем, что dataframe не None и не пустой ---
    if dataframe is None or dataframe.empty:
        return

    columns = dataframe.columns.tolist()
    
    # --- ИСПРАВЛЕНИЕ: Обработка как строки, так и списка для pk_column ---
    if isinstance(pk_column, list):
        conflict_target = sql.SQL(', ').join(map(sql.Identifier, pk_column))
        pk_list = pk_column
    else:
        conflict_target = sql.Identifier(pk_column)
        pk_list = [pk_column]

    update_columns = [col for col in columns if col not in pk_list]
    
    set_clause = sql.SQL(', ').join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(col)) for col in update_columns
    )
    # --- ИЗМЕНЕНИЕ: Если нет колонок для обновления (только PK), используем DO NOTHING ---
    if not update_columns:
        action_on_conflict = sql.SQL("DO NOTHING")
    else:
        action_on_conflict = sql.SQL("DO UPDATE SET {set_clause}").format(set_clause=set_clause)
    
    query = sql.SQL("INSERT INTO {table} ({cols}) VALUES %s ON CONFLICT ({pk}) {action}").format(
        table=sql.Identifier(table_name),
        cols=sql.SQL(', ').join(map(sql.Identifier, columns)),
        pk=conflict_target,
        action=action_on_conflict
    )
    df_prepared = dataframe.where(pd.notna(dataframe), None)
    data_tuples = [tuple(x) for x in df_prepared.itertuples(index=False)]
    execute_values(cursor, query, data_tuples, page_size=1000)