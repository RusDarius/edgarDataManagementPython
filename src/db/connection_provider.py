import mysql.connector


def get_mysql_connection(
    host="localhost", port=3306, user="root", password="", database=None
):
    return mysql.connector.connect(
        host=host, port=port, user=user, password=password, database=database
    )
