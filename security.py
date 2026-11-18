"""Encrypted local vault. SQLite is deserialized into memory; no plaintext SQLite temp DB."""
import json
import os
import sqlite3
import struct
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b'PSVAULT2\x00'
MAX_FILE = 512 * 1024 * 1024


def derive(password: str, salt: bytes) -> bytes:
    if not password:
        raise ValueError('Пароль не может быть пустым')
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode('utf-8'))


def pack(plaintext: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = derive(password, salt)
    return MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, plaintext, MAGIC + salt)


def unpack(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC)+16+12+16:
        raise ValueError('Неверный формат защищённого хранилища')
    if len(blob) > MAX_FILE:
        raise ValueError('Файл превышает допустимый размер')
    i = len(MAGIC)
    salt, nonce, payload = blob[i:i+16], blob[i+16:i+28], blob[i+28:]
    try:
        return AESGCM(derive(password, salt)).decrypt(nonce, payload, MAGIC + salt)
    except Exception as ex:
        raise ValueError('Неверный пароль или повреждённый файл') from ex


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.vault-', suffix='.tmp', delete=False) as f:
            name = f.name
            os.chmod(name, 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


class Vault:
    def __init__(self, directory: Path):
        self.dir = directory
        self.path = directory/'vault.psv'
        self.conn = None
        self._password = None
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def exists(self):
        return self.path.exists()

    def _connect(self, payload=None):
        db = sqlite3.connect(':memory:')
        if payload is not None:
            db.deserialize(payload)
        db.execute('PRAGMA foreign_keys=ON')
        check = db.execute('PRAGMA integrity_check').fetchone()[0]
        if check != 'ok':
            db.close()
            raise ValueError('Проверка целостности базы не пройдена')
        return db

    def create(self, password):
        if self.exists:
            raise FileExistsError('Хранилище уже существует')
        self.conn = self._connect()
        self._password = password
        self.conn.executescript('''
            CREATE TABLE journals(id TEXT PRIMARY KEY, name TEXT NOT NULL,
              type TEXT NOT NULL DEFAULT 'Обычный', theme TEXT NOT NULL DEFAULT 'Ivory');
            CREATE TABLE entries(id TEXT PRIMARY KEY,
              journal_id TEXT NOT NULL REFERENCES journals(id) ON DELETE CASCADE,
              title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
              event_at TEXT NOT NULL, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX idx_entries_journal ON entries(journal_id);
        ''')
        self.persist()

    def unlock(self, password):
        if self.conn:
            self.lock()
        blob = self.path.read_bytes()
        data = unpack(blob, password)
        self.conn = self._connect(data)
        self._password = password

    def persist(self):
        if self.conn is None or self._password is None:
            raise RuntimeError('Хранилище заблокировано')
        self.conn.commit()
        blob = pack(self.conn.serialize(), self._password)
        atomic_write(self.path, blob)

    def mutate(self, func):
        if self.conn is None:
            raise RuntimeError('Хранилище заблокировано')
        try:
            with self.conn:
                result = func(self.conn)
            self.persist()
            return result
        except Exception:
            # A failed persistence must not leave UI believing changes are durable.
            if self.path.exists():
                password = self._password
                self.lock()
                self.unlock(password)
            raise

    def backup(self, path):
        self.persist()
        destination = Path(path)
        if destination.resolve() == self.path.resolve():
            raise ValueError('Выберите другой путь резервной копии')
        atomic_write(destination, self.path.read_bytes())
        if unpack(destination.read_bytes(), self._password) != self.conn.serialize():
            raise ValueError('Резервная копия не прошла проверку')

    def restore(self, path, backup_password):
        source = Path(path)
        data = unpack(source.read_bytes(), backup_password)
        test = self._connect(data)
        required = {'journals', 'entries'}
        found = {r[0] for r in test.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= found:
            test.close()
            raise ValueError('В копии нет необходимых таблиц')
        test.close()
        blob = pack(data, self._password)
        atomic_write(self.path, blob)
        self.conn.close()
        self.conn = self._connect(data)

    def change_password(self, old, new):
        # Verify old password against the current on-disk vault.
        unpack(self.path.read_bytes(), old)
        data = self.conn.serialize()
        atomic_write(self.path, pack(data, new))
        self._password = new

    def import_legacy(self, original):
        """Read old plaintext DB into RAM; keep original untouched for explicit cleanup."""
        original = Path(original)
        if not original.is_file():
            raise FileNotFoundError(original)
        src = sqlite3.connect(f'file:{original.as_posix()}?mode=ro', uri=True)
        try:
            src.backup(self.conn)
            required = {'journals','entries'}
            found = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= found:
                raise ValueError('Старая база не соответствует формату MVP')
            self.persist()
        finally:
            src.close()

    def lock(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self._password = None  # Python cannot guarantee zeroization of immutable strings.
