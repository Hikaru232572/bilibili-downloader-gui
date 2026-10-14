"""Current-user DPAPI storage. No plaintext credential fallback."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
from uuid import uuid4


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


class DPAPI:
    @staticmethod
    def _transform(data: bytes, encrypt: bool) -> bytes:
        if os.name != 'nt':
            raise OSError('Current-user DPAPI is only available on Windows')
        buffer = ctypes.create_string_buffer(data)
        source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = _Blob()
        crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        function = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        try:
            # CRYPTPROTECT_UI_FORBIDDEN; omit LOCAL_MACHINE so only this user
            # can decrypt through normal DPAPI access.
            if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if result.pbData:
                ctypes.memset(result.pbData, 0, result.cbData)
                kernel32.LocalFree(result.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._transform(data, True)

    def unprotect(self, data: bytes) -> bytes:
        return self._transform(data, False)


class SecretVault:
    def __init__(self, directory: Path, protector=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.protector = protector or DPAPI()

    def _path(self, reference: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{32}', reference):
            raise ValueError('Invalid secret reference')
        return self.directory / (reference + '.dpapi')

    def put(self, value: str) -> str:
        encrypted = self.protector.protect(value.encode('utf-8'))
        reference = uuid4().hex
        path = self._path(reference)
        # Exclusive creation avoids accidentally replacing an existing secret.
        with path.open('xb') as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        return reference

    def get(self, reference: str) -> str:
        return self.protector.unprotect(self._path(reference).read_bytes()).decode('utf-8')

    def delete(self, reference: str):
        self._path(reference).unlink(missing_ok=True)
