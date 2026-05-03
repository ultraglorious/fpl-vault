from typing import List, Dict
import psycopg2
from dotenv import load_dotenv
import os

class DatabaseManager:
    def __init__(self):
        load_dotenv()
        self.dbname = os.getenv("DB_NAME")
        self.user = os.getenv("DB_USER")
        self.password = os.getenv("DB_PASSWORD")
        self.host = os.getenv("DB_HOST")
        self.port = os.getenv("DB_PORT")
        self.conn = None
    
    def connect(self):
        try:
            self.conn = psycopg2.connect(
                dbname=self.dbname,
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port
            )
            print("Database connection established.")
        except Exception as e:
            print(f"Error connecting to the database: {e}")
    
    def close(self):
        if self.conn:
            self.conn.close()
            print("Database connection closed.")

    def create_table(self, schema: Dict[str, any]) -> None:
        table_name = schema['table_name']
        columns = []
        
        for column in schema['columns']:
            name = column['name']
            data_type = column['data_type']
            primary_key = 'PRIMARY KEY' if column.get('primary_key', False) else ''
            auto_increment = 'AUTO_INCREMENT' if column.get('auto_increment', False) else ''
            nullable = '' if column.get('nullable', True) else 'NOT NULL'
            max_length = f"({column['max_length']})" if 'max_length' in column else ''
            default = f"DEFAULT {column['default']}" if 'default' in column else ''
            
            column_def = f"{name} {data_type}{max_length} {nullable} {primary_key} {auto_increment} {default}"
            columns.append(column_def)
        
        foreign_keys = []
        for fk in schema.get('foreign_keys', []):
            column_name = fk['column_name']
            referenced_table = fk['referenced_table']
            referenced_column = fk['referenced_column']
            
            fk_def = f"FOREIGN KEY ({column_name}) REFERENCES {referenced_table}({referenced_column})"
            foreign_keys.append(fk_def)
        
        query_parts = [f"CREATE TABLE {table_name} ("] + columns
        if foreign_keys:
            query_parts.append(", ")
            query_parts.extend(foreign_keys)
        query_parts.append(")")
        
        query = ' '.join(query_parts)
        
        print(query)
        # self.execute_query(query)

    def execute_query(self, query: str) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(query)
            self.conn.commit()
        except:
            raise(Exception(f"Failed to execute: {query}"))
        cursor.close()
        print(f"Executed query: {query}")

