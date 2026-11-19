"""Stage 3 models and actions, stored inside the encrypted in-memory SQLite vault.
Attachments are stored as BLOBs in the encrypted vault, never in plaintext files.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from pathlib import Path

MIME = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp',
        '.mp3':'audio/mpeg','.wav':'audio/wav','.m4a':'audio/mp4'}
MAX_ATTACHMENT = 12 * 1024 * 1024  # In-vault bytes; intentionally conservative.

def timestamp(): return datetime.now().isoformat(timespec='seconds')
def uid(): return str(uuid.uuid4())

class Organizer:
    def __init__(self, vault): self.vault=vault
    @property
    def db(self): return self.vault.conn
    def upgrade(self):
        """Idempotent, transactional schema extension; disk remains encrypted."""
        db=self.db
        statements = [
            "CREATE TABLE IF NOT EXISTS tags(id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE UNIQUE,color TEXT NOT NULL DEFAULT '#8b78a9')",
            "CREATE TABLE IF NOT EXISTS entry_tags(entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,PRIMARY KEY(entry_id,tag_id))",
            "CREATE TABLE IF NOT EXISTS categories(id TEXT PRIMARY KEY,journal_id TEXT NOT NULL REFERENCES journals(id) ON DELETE CASCADE,name TEXT NOT NULL,UNIQUE(journal_id,name))",
            "CREATE TABLE IF NOT EXISTS dream_details(entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,vividness INTEGER CHECK(vividness BETWEEN 1 AND 5),lucid INTEGER CHECK(lucid IN (0,1) OR lucid IS NULL),emotion TEXT NOT NULL DEFAULT '',characters TEXT NOT NULL DEFAULT '',places TEXT NOT NULL DEFAULT '',interpretation TEXT NOT NULL DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,name TEXT NOT NULL,mime TEXT NOT NULL,payload BLOB NOT NULL,created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,journal_id TEXT REFERENCES journals(id) ON DELETE CASCADE,name TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(journal_id,name))",
            "CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(event_at)",
            "CREATE INDEX IF NOT EXISTS idx_entries_deleted ON entries(deleted_at)",
        ]
        def migrate(c):
            cols={r[1] for r in c.execute('PRAGMA table_info(entries)')}
            for column,spec in [('deleted_at','TEXT'),('favorite','INTEGER NOT NULL DEFAULT 0'),('category_id','TEXT')]:
                if column not in cols:c.execute(f'ALTER TABLE entries ADD COLUMN {column} {spec}')
            for statement in statements: c.execute(statement)
            c.execute('PRAGMA user_version=3')
        self.vault.mutate(migrate)

    def categories(self,jid):
        return self.db.execute('SELECT id,name FROM categories WHERE journal_id=? ORDER BY name COLLATE NOCASE',(jid,)).fetchall()
    def add_category(self,jid,name):
        name=name.strip()
        if not name: raise ValueError('Укажите название категории')
        value=uid();self.vault.mutate(lambda c:c.execute('INSERT INTO categories VALUES(?,?,?)',(value,jid,name)));return value
    def delete_category(self,cid):
        def action(c):
            c.execute('UPDATE entries SET category_id=NULL WHERE category_id=?',(cid,))
            c.execute('DELETE FROM categories WHERE id=?',(cid,))
        self.vault.mutate(action)
    def set_category(self,eid,cid):
        if cid:
            valid=self.db.execute('SELECT 1 FROM entries e JOIN categories c ON e.journal_id=c.journal_id WHERE e.id=? AND c.id=?',(eid,cid)).fetchone()
            if not valid:raise ValueError('Категория принадлежит другому дневнику')
        self.vault.mutate(lambda c:c.execute('UPDATE entries SET category_id=?,updated_at=?,revision=revision+1 WHERE id=?',(cid,timestamp(),eid)))
    def tags(self): return self.db.execute('SELECT id,name,color FROM tags ORDER BY name COLLATE NOCASE').fetchall()
    def entry_tags(self,eid):return [r[0] for r in self.db.execute('SELECT tag_id FROM entry_tags WHERE entry_id=?',(eid,))]
    def add_tag(self,name):
        name=name.strip()
        if not name:raise ValueError('Укажите название тега')
        result=uid();self.vault.mutate(lambda c:c.execute('INSERT INTO tags VALUES(?,?,?)',(result,name,'#8b78a9')));return result
    def delete_tag(self,tagid):self.vault.mutate(lambda c:c.execute('DELETE FROM tags WHERE id=?',(tagid,)))
    def set_tags(self,eid,ids):
        ids=set(ids)
        def action(c):
            c.execute('DELETE FROM entry_tags WHERE entry_id=?',(eid,))
            c.executemany('INSERT INTO entry_tags VALUES(?,?)',[(eid,tagid) for tagid in ids])
            c.execute('UPDATE entries SET updated_at=?,revision=revision+1 WHERE id=?',(timestamp(),eid))
        self.vault.mutate(action)
    def put_dream(self,eid,vividness=None,lucid=None,emotion='',characters='',places='',interpretation=''):
        if vividness not in (None,1,2,3,4,5):raise ValueError('Яркость должна быть 1–5')
        if lucid not in (None,0,1):raise ValueError('Неверное значение осознанности')
        self.vault.mutate(lambda c:c.execute('''INSERT INTO dream_details VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(entry_id) DO UPDATE SET vividness=excluded.vividness,lucid=excluded.lucid,
            emotion=excluded.emotion,characters=excluded.characters,places=excluded.places,interpretation=excluded.interpretation''',
            (eid,vividness,lucid,emotion,characters,places,interpretation)))
    def dream(self,eid):return self.db.execute('SELECT vividness,lucid,emotion,characters,places,interpretation FROM dream_details WHERE entry_id=?',(eid,)).fetchone()
    def soft_delete(self,eid):self.vault.mutate(lambda c:c.execute('UPDATE entries SET deleted_at=?,updated_at=?,revision=revision+1 WHERE id=?',(timestamp(),timestamp(),eid)))
    def restore_entry(self,eid):self.vault.mutate(lambda c:c.execute('UPDATE entries SET deleted_at=NULL,revision=revision+1 WHERE id=?',(eid,)))
    def purge(self,eid):self.vault.mutate(lambda c:c.execute('DELETE FROM entries WHERE id=? AND deleted_at IS NOT NULL',(eid,)))
    def trash(self):return self.db.execute('SELECT e.id,e.title,e.event_at,j.name FROM entries e JOIN journals j ON e.journal_id=j.id WHERE e.deleted_at IS NOT NULL ORDER BY e.deleted_at DESC').fetchall()
    def toggle_favorite(self,eid):self.vault.mutate(lambda c:c.execute('UPDATE entries SET favorite=CASE WHEN favorite=1 THEN 0 ELSE 1 END,revision=revision+1 WHERE id=?',(eid,)))
    def calendar_days(self,year,month,jid=None):
        prefix=f'{year:04d}-{month:02d}-%'
        if jid:rows=self.db.execute('SELECT event_at,count(*) FROM entries WHERE deleted_at IS NULL AND journal_id=? AND event_at LIKE ? GROUP BY event_at',(jid,prefix))
        else:rows=self.db.execute('SELECT event_at,count(*) FROM entries WHERE deleted_at IS NULL AND event_at LIKE ? GROUP BY event_at',(prefix,))
        return {int(day[-2:]):count for day,count in rows}
    def at_date(self,date,jid=None):
        sql='SELECT e.id,e.journal_id,e.title,j.name FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.deleted_at IS NULL AND e.event_at=?'
        params=[date]
        if jid:sql+=' AND e.journal_id=?';params.append(jid)
        return self.db.execute(sql+' ORDER BY e.updated_at DESC',params).fetchall()
    def templates(self,jid):return self.db.execute('SELECT id,name,body FROM templates WHERE journal_id=? ORDER BY name',(jid,)).fetchall()
    def add_template(self,jid,name,body):
        if not name.strip():raise ValueError('Название шаблона обязательно')
        self.vault.mutate(lambda c:c.execute('INSERT INTO templates VALUES(?,?,?,?)',(uid(),jid,name.strip(),body)))
    def add_attachment(self,eid,path):
        p=Path(path);mime=MIME.get(p.suffix.lower())
        if mime is None:raise ValueError('Поддерживаются PNG, JPEG, WebP, MP3, WAV, M4A')
        if p.stat().st_size>MAX_ATTACHMENT:raise ValueError('Максимальный размер одного вложения — 12 МБ')
        payload=p.read_bytes()
        if len(payload)>MAX_ATTACHMENT:raise ValueError('Слишком большой файл')
        aid=uid()
        self.vault.mutate(lambda c:c.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?)',(aid,eid,p.name,mime,payload,timestamp())))
        return aid
    def attachments(self,eid):return self.db.execute('SELECT id,name,mime,length(payload) FROM attachments WHERE entry_id=? ORDER BY created_at',(eid,)).fetchall()
    def attachment(self,aid):return self.db.execute('SELECT name,mime,payload FROM attachments WHERE id=?',(aid,)).fetchone()
    def remove_attachment(self,aid):self.vault.mutate(lambda c:c.execute('DELETE FROM attachments WHERE id=?',(aid,)))
