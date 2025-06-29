import mysql.connector
from .connection_credentials import BASE_DB_CONFIG


# Create and return a MySQL database connection.
def get_mysql_connection(host=None, port=None, user=None, password=None, database=None):
    # Use defaults from credentials if not provided
    config = {
        "host": host or BASE_DB_CONFIG["host"],
        "port": port or BASE_DB_CONFIG["port"],
        "user": user or BASE_DB_CONFIG["user"],
        "password": password or BASE_DB_CONFIG["password"],
        "database": database or BASE_DB_CONFIG["database"],
    }

    return mysql.connector.connect(**config)
