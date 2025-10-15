import os
from psycopg2 import sql
from psycopg2.extras import execute_values

def upsert_data_to_db(cursor, table_env_var, dataframe, pk_column):
    """
    Выполняет массовую вставку/обновление (UPSERT) данных из DataFrame в таблицу.
    
    :param cursor: Активный курсор базы данных.
    :param table_env_var: Имя переменной окружения, содержащей имя таблицы.
    :param dataframe: pandas DataFrame с данными для загрузки.
    :param pk_column: Имя первичного ключа или список имен для составного ключа.
    """
    table_name = os.getenv(table_env_var)
    if not table_name:
        raise ValueError(f"Переменная окружения {table_env_var} не найдена в .env файле!")

    if dataframe.empty:
        return

    columns = dataframe.columns.tolist()
    data_tuples = [tuple(x) for x in dataframe.to_numpy()]

    # --- ИСПРАВЛЕНИЕ: Обработка как строки, так и списка для pk_column ---
    if isinstance(pk_column, list):
        # Составной ключ
        conflict_target = sql.SQL(', ').join(map(sql.Identifier, pk_column))
    else:
        # Одиночный ключ
        conflict_target = sql.Identifier(pk_column)

    update_columns = [col for col in columns if col not in (pk_column if isinstance(pk_column, list) else [pk_column])]
    
    set_clause = sql.SQL(', ').join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(col)) for col in update_columns
    )

    query = sql.SQL("INSERT INTO {table} ({cols}) VALUES %s ON CONFLICT ({pk}) DO UPDATE SET {set_clause}").format(
        table=sql.Identifier(table_name),
        cols=sql.SQL(', ').join(map(sql.Identifier, columns)),
        pk=conflict_target,
        set_clause=set_clause
    )

    execute_values(cursor, query, data_tuples, page_size=1000)