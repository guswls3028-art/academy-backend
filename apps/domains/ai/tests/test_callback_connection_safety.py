from unittest import mock

from apps.domains.ai import callbacks


def test_callback_connection_cleanup_skips_active_transaction():
    with (
        mock.patch.object(callbacks, "connection") as db_connection,
        mock.patch.object(callbacks, "close_old_connections") as close_connections,
    ):
        db_connection.in_atomic_block = True

        callbacks._close_old_connections_if_safe()

    close_connections.assert_not_called()


def test_callback_connection_cleanup_runs_outside_transaction():
    with (
        mock.patch.object(callbacks, "connection") as db_connection,
        mock.patch.object(callbacks, "close_old_connections") as close_connections,
    ):
        db_connection.in_atomic_block = False

        callbacks._close_old_connections_if_safe()

    close_connections.assert_called_once_with()
