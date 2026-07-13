"""Tests for filesystem retries used around OneDrive-managed paths."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils import file_retry


class FileRetryTests(unittest.TestCase):
    def test_retries_permission_error_with_exponential_backoff(self) -> None:
        chamadas = 0

        def operacao() -> str:
            nonlocal chamadas
            chamadas += 1
            if chamadas < 3:
                raise PermissionError(32, "arquivo em uso")
            return "ok"

        with patch.object(file_retry.time, "sleep") as sleep:
            resultado = file_retry.executar_com_retry_lock(operacao, descricao="teste")

        self.assertEqual(resultado, "ok")
        self.assertEqual(chamadas, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_raises_after_fourth_permission_error(self) -> None:
        with patch.object(file_retry.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                file_retry.executar_com_retry_lock(
                    lambda: (_ for _ in ()).throw(PermissionError(32, "arquivo em uso")),
                    descricao="teste",
                )

        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0, 4.0])

    def test_wrappers_delegate_to_os_operations(self) -> None:
        source = Path("origem.tmp")
        target = Path("destino.tmp")

        with (
            patch.object(file_retry.os, "unlink") as unlink,
            patch.object(file_retry.os, "replace") as replace,
            patch.object(file_retry.shutil, "rmtree") as rmtree,
        ):
            file_retry.unlink_com_retry(source)
            file_retry.replace_com_retry(source, target)
            file_retry.rmtree_com_retry(target)

        unlink.assert_called_once_with(source)
        replace.assert_called_once_with(source, target)
        rmtree.assert_called_once_with(target)


if __name__ == "__main__":
    unittest.main()
