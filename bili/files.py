"""File identities and no-overwrite commits shared by every platform."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
import unicodedata


RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
            *(f'LPT{i}' for i in range(1, 10)), 'CONIN$', 'CONOUT$'}


def clean_name(text: str, fallback='untitled') -> str:
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text).strip(' .')
    if not text:
        text = fallback
    if text.split('.')[0].upper() in RESERVED:
        text = '_' + text
    return text


def utf16_length(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def name_stem(directory: Path, platform: str, title: str, work_id: str, index: int) -> str:
    directory = Path(directory).resolve()
    prefix = clean_name(platform)
    identity = clean_name(work_id)
    suffix = f'_{identity}_{index:03d}'
    # Reserve room for resource type, extension and collision suffix.
    budget = 240 - utf16_length(str(directory)) - utf16_length(prefix + '_' + suffix) - 32
    if budget < 1:
        raise ValueError('Output path is too long; choose a shorter directory')
    fragment = clean_name(title, fallback=identity)
    while utf16_length(fragment) > budget:
        fragment = fragment[:-1]
    return prefix + '_' + fragment.rstrip(' .') + suffix


@dataclass(frozen=True)
class Reservation:
    directory: Path
    stem: str
    paths: tuple[tuple[str, Path], ...]

    def path(self, component: str) -> Path:
        return dict(self.paths)[component]


class OutputFiles:
    def __init__(self):
        self._lock = threading.Lock()
        self._reserved: set[str] = set()

    @staticmethod
    def _key(path: Path):
        return str(path.resolve()).casefold()

    def reserve(self, directory: Path, stem: str, components: dict[str, str]) -> Reservation:
        directory = Path(directory).resolve()
        if directory.is_symlink():
            raise ValueError('Unexpected output directory link')
        directory.mkdir(parents=True, exist_ok=True)
        if stem != clean_name(stem):
            raise ValueError('Unsafe output stem')
        for component, extension in components.items():
            if not re.fullmatch(r'[a-z]+', component) or not re.fullmatch(r'\.[a-zA-Z0-9]{1,8}', extension):
                raise ValueError('Invalid output type or extension')
        with self._lock:
            # Windows-equivalent collision checks also work in offline tests
            # on case-sensitive hosts.
            occupied = {p.name.casefold() for p in directory.iterdir()}
            for suffix in range(10000):
                name = stem + (f' ({suffix})' if suffix else '')
                paths = tuple((c, directory / (name + '_' + c + ext)) for c, ext in components.items())
                if all(p.name.casefold() not in occupied and self._key(p) not in self._reserved for _, p in paths):
                    self._reserved.update(self._key(p) for _, p in paths)
                    return Reservation(directory, name, paths)
        raise FileExistsError('Could not reserve an output name')

    @staticmethod
    def temporary(directory: Path, output_id: str) -> Path:
        if not re.fullmatch(r'[0-9a-f-]{36}', output_id):
            raise ValueError('Invalid output identity')
        return Path(directory).resolve() / ('.bili-' + output_id + '.part')

    @staticmethod
    def commit(temporary: Path, destination: Path):
        temporary, destination = Path(temporary), Path(destination)
        if temporary.resolve().parent != destination.resolve().parent:
            raise ValueError('Temporary file must be on the destination directory/volume')
        if temporary.is_symlink() or not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError('Invalid temporary media')
        # Windows rename fails if the destination exists. os.replace would
        # violate the product's no-overwrite rule. Hard-link commit provides
        # the same exclusive destination creation on POSIX test hosts.
        if os.name == 'nt':
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
            temporary.unlink()

    @staticmethod
    def remove_temporary(path: Path, directory: Path, output_id: str):
        expected = OutputFiles.temporary(directory, output_id)
        if Path(path) != expected or expected.is_symlink():
            raise ValueError('Refusing to remove an unregistered temporary path')
        expected.unlink(missing_ok=True)
