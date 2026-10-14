import os
from pathlib import Path
import tempfile
import unittest

from bili.secrets import DPAPI, SecretVault


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI integration')
class SecretTests(unittest.TestCase):
    def test_current_user_roundtrip_and_plaintext_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = SecretVault(Path(directory))
            text = 'https://example.com/work?xsec_token=private-token-测试'
            reference = vault.put(text)
            self.assertEqual(vault.get(reference), text)
            self.assertNotIn(b'private-token', vault._path(reference).read_bytes())
            vault.delete(reference)
            self.assertFalse(vault._path(reference).exists())

    def test_corrupt_data_is_rejected_without_plaintext_fallback(self):
        protector = DPAPI()
        encrypted = bytearray(protector.protect(b'private-cookie'))
        encrypted[-1] ^= 1
        with self.assertRaises(OSError):
            protector.unprotect(bytes(encrypted))

    def test_reference_cannot_escape_vault(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = SecretVault(Path(directory))
            with self.assertRaises(ValueError):
                vault.get('../cookies.txt')
