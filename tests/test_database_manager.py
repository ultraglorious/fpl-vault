from unittest.mock import patch, MagicMock
from database_manager import DatabaseManager

@patch('database_manager.psycopg2.connect')
def test_connect_success(mock_connect):
    db = DatabaseManager()

    mock_connection = MagicMock()
    mock_connect.return_value = mock_connection

    db.connect()

    assert db.conn is mock_connection
    mock_connect.assert_called_once_with(
        dbname=db.dbname,
        user=db.user,
        password=db.password,
        host=db.host,
        port=db.port,
    )
    mock_connection.close.assert_not_called()

def test_real_database_connection():
    # Arrange
    db = DatabaseManager()
    
    # Act
    db.connect()

    # Assert
    assert db.conn.closed is 0, "Connection should be successful"
    db.close()

def test_real_database_closure():
    # Arrange
    db = DatabaseManager()
    
    # Act
    db.connect()
    db.close()

    # Assert
    assert db.conn.closed is 1, "Connection should be closed"
    db.close()
