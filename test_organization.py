import tempfile, unittest
from pathlib import Path
from security import Vault
from organization import Organizer

class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name);self.v=Vault(self.root);self.v.create('a strong master password')
        self.o=Organizer(self.v);self.o.upgrade()
        self.v.mutate(lambda c:c.execute("INSERT INTO journals(id,name,type,theme) VALUES('j1','Сны','Сновидения','Ivory')"))
        self.v.mutate(lambda c:c.execute("INSERT INTO journals(id,name,type,theme) VALUES('j2','Мысли','Обычный','Ivory')"))
        self.v.mutate(lambda c:c.execute("INSERT INTO entries(id,journal_id,title,content,event_at,created_at,updated_at,revision) VALUES('e1','j1','Полёт','<p>Облака</p>','2026-11-16','2026-11-18','2026-11-18',1)"))
    def tearDown(self):self.v.lock();self.tmp.cleanup()
    def test_safe_upgrade_and_calendar(self):
        self.o.upgrade();self.o.upgrade()
        self.assertEqual(self.o.calendar_days(2026,11,'j1'),{16:1})
        self.assertEqual(self.o.calendar_days(2026,11,'j2'),{})
        self.assertEqual(len(self.o.at_date('2026-11-16','j1')),1)
    def test_category_tags_and_recycle(self):
        cid=self.o.add_category('j1','Яркие');self.o.set_category('e1',cid)
        cid2=self.o.add_category('j2','Работа')
        with self.assertRaises(ValueError):self.o.set_category('e1',cid2)
        tag=self.o.add_tag('полёт');self.o.set_tags('e1',[tag]);self.assertEqual(self.o.entry_tags('e1'),[tag])
        self.o.delete_tag(tag);self.assertEqual(self.o.entry_tags('e1'),[])
        self.assertIsNotNone(self.v.conn.execute("SELECT id FROM entries WHERE id='e1'").fetchone())
        self.o.delete_category(cid);self.assertIsNone(self.v.conn.execute("SELECT category_id FROM entries WHERE id='e1'").fetchone()[0])
        self.o.soft_delete('e1');self.assertEqual(self.o.calendar_days(2026,11,'j1'),{})
        self.assertEqual(len(self.o.trash()),1)
        self.o.restore_entry('e1');self.assertEqual(len(self.o.trash()),0)
        self.o.soft_delete('e1');self.o.purge('e1');self.assertEqual(len(self.o.trash()),0)
    def test_dream_and_attachment_secure_backup(self):
        self.o.put_dream('e1',4,1,'Радость','Друг','Дом','Моя версия')
        file=self.root/'sample.png';file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'opaque data')
        aid=self.o.add_attachment('e1',file)
        self.assertEqual(self.o.attachment(aid)[2],file.read_bytes())
        backup=self.root/'copy.psbackup';self.v.backup(backup)
        self.assertNotIn(b'opaque data',self.v.path.read_bytes())
        self.assertNotIn('Моя версия'.encode(),backup.read_bytes())
        self.v.lock();self.v.unlock('a strong master password');self.o.upgrade()
        self.assertEqual(self.o.dream('e1'),(4,1,'Радость','Друг','Дом','Моя версия'))
        self.assertEqual(len(self.o.attachments('e1')),1)
        self.o.remove_attachment(aid);self.assertEqual(self.o.attachments('e1'),[])
        self.v.restore(backup,'a strong master password');self.o.upgrade()
        self.assertEqual(len(self.o.attachments('e1')),1)
        self.assertEqual(self.o.dream('e1')[0],4)
    def test_template_and_favorite(self):
        self.o.add_template('j1','Мой сон','Что произошло?')
        self.assertEqual(self.o.templates('j1')[0][2],'Что произошло?')
        self.o.toggle_favorite('e1')
        self.assertEqual(self.v.conn.execute("SELECT favorite FROM entries WHERE id='e1'").fetchone()[0],1)
        self.o.toggle_favorite('e1')
        self.assertEqual(self.v.conn.execute("SELECT favorite FROM entries WHERE id='e1'").fetchone()[0],0)

if __name__=='__main__':unittest.main()
