import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from security import Vault, MAGIC, pack, unpack

class SecurityTests(unittest.TestCase):
    def test_cipher_roundtrip_and_tamper(self):
        blob=pack('секретный текст'.encode(),'very long password')
        self.assertEqual(unpack(blob,'very long password'),'секретный текст'.encode())
        self.assertNotIn('секретный текст'.encode(),blob)
        with self.assertRaises(ValueError):unpack(blob,'wrong password')
        altered=bytearray(blob);altered[-1]^=1
        with self.assertRaises(ValueError):unpack(altered,'very long password')
    def test_vault_crud_backup_lock_change_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            v=Vault(Path(tmp));v.create('initial password 123')
            v.mutate(lambda c:c.execute("INSERT INTO journals(id,name) VALUES('1','Личные мысли')"))
            self.assertNotIn('Личные мысли'.encode(),v.path.read_bytes())
            backup=Path(tmp)/'saved.psbackup';v.backup(backup)
            v.lock()
            self.assertIsNone(v.conn)
            with self.assertRaises(ValueError):v.unlock('wrong')
            v.unlock('initial password 123')
            self.assertEqual(v.conn.execute('SELECT name FROM journals').fetchone()[0],'Личные мысли')
            v.change_password('initial password 123','changed password 123')
            v.lock()
            with self.assertRaises(ValueError):v.unlock('initial password 123')
            v.unlock('changed password 123')
            v.mutate(lambda c:c.execute("DELETE FROM journals WHERE id='1'"))
            v.restore(backup,'initial password 123')
            self.assertEqual(v.conn.execute('SELECT name FROM journals').fetchone()[0],'Личные мысли')
            v.lock()
            v.unlock('changed password 123')
            self.assertEqual(v.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
    def test_migrate_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'personal_space.sqlite3'
            c=sqlite3.connect(old)
            c.execute('CREATE TABLE journals(id TEXT PRIMARY KEY,name TEXT,type TEXT,theme TEXT)')
            c.execute('CREATE TABLE entries(id TEXT PRIMARY KEY,journal_id TEXT,title TEXT,content TEXT,event_at TEXT,created_at TEXT,updated_at TEXT,revision INTEGER)')
            c.execute("INSERT INTO journals VALUES('1','До защиты','Обычный','Ivory')")
            c.commit();c.close()
            v=Vault(Path(tmp));v.create('password for migration')
            v.import_legacy(old)
            self.assertTrue(old.exists())
            v.lock();v.unlock('password for migration')
            self.assertEqual(v.conn.execute('SELECT name FROM journals').fetchone()[0],'До защиты')
            self.assertNotIn('До защиты'.encode(),v.path.read_bytes())

if __name__=='__main__': unittest.main()
