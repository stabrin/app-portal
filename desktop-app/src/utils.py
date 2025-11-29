import sys
import os
import pandas as pd
from psycopg2 import sql
from psycopg2.extras import execute_values

def project_root_path(relative_path):
    """
    ГЛАВНАЯ ФУНКЦИЯ ПУТЕЙ (SMART VERSION).
    Возвращает абсолютный путь к ресурсу.
    """
    if getattr(sys, 'frozen', False):
        # --- РЕЖИМ СБОРКИ (EXE) ---
        # В собранном виде все файлы (и .env, и secrets) лежат в одной куче (в корне)
        
        # 1. PyInstaller
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        # 2. Nuitka
        else:
            base_path = os.path.dirname(sys.executable)
            
        return os.path.join(base_path, relative_path)
            
    else:
        # --- РЕЖИМ РАЗРАБОТКИ (IDE) ---
        # Здесь файлы могут быть разбросаны. Ищем их "умно".
        
        # utils.py лежит в .../desktop-app/src
        src_dir = os.path.dirname(__file__)
        
        # Вариант 1: Ищем внутри desktop-app (../)
        # Пример: d:/Projects/app-portal/desktop-app/.env
        path_in_desktop_app = os.path.abspath(os.path.join(src_dir, '..', relative_path))
        
        # Вариант 2: Ищем в корне проекта (../../)
        # Пример: d:/Projects/app-portal/secrets
        path_in_project_root = os.path.abspath(os.path.join(src_dir, '..', '..', relative_path))

        # Логика проверки:
        if os.path.exists(path_in_desktop_app):
            return path_in_desktop_app
        elif os.path.exists(path_in_project_root):
            return path_in_project_root
        else:
            # Если нигде не нашли, возвращаем путь в desktop-app (чтобы ошибка в логе была понятной)
            return path_in_desktop_app

def resource_path(relative_path):
    """Обертка для совместимости."""
    return project_root_path(relative_path)

def upsert_data_to_db(cursor, table_name: str, dataframe: pd.DataFrame, pk_column: str):
    """
    Универсальная функция для UPSERT данных.
    """
    if dataframe is None or dataframe.empty:
        return

    columns = dataframe.columns.tolist()
    
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