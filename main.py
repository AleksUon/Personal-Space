import sys, sqlite3, uuid, json, zipfile, shutil, tempfile, re
from pathlib import Path
from datetime import datetime, date
from html import escape
from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtGui import QAction, QTextCharFormat, QFont, QTextCursor, QTextListFormat
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QListWidget,QListWidgetItem,QLineEdit,QTextEdit,QLabel,QComboBox,QDateEdit,QMessageBox,QInputDialog,QToolBar,QFileDialog,QStackedWidget,QSplitter)

ROOT=Path.home()/'PersonalSpaceData'; DB=ROOT/'personal_space.sqlite3'; SETTINGS=ROOT/'settings.json'
THEMES={'Ivory':(' #faf8f3','#ffffff','#282632','#e9e4dc','#795c9b'),'Midnight':('#181923','#232535','#f0edf7','#383a50','#b7a1ef')}
def now(): return datetime.now().isoformat(timespec='seconds')
class Store:
 def __init__(self):
  ROOT.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(DB)
  self.db.execute('PRAGMA foreign_keys=ON');self.db.execute('PRAGMA journal_mode=WAL')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS journals(id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL DEFAULT 'Обычный',theme TEXT NOT NULL DEFAULT 'Ivory');
  CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY,journal_id TEXT NOT NULL REFERENCES journals(id) ON DELETE CASCADE,title TEXT NOT NULL DEFAULT '',content TEXT NOT NULL DEFAULT '',event_at TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);CREATE INDEX IF NOT EXISTS idx_entries_journal ON entries(journal_id);''');self.db.commit()
 def journals(self):return self.db.execute('SELECT id,name,type,theme FROM journals ORDER BY rowid').fetchall()
 def entries(self,jid=None,query=''):
  sql='SELECT e.id,e.journal_id,e.title,e.content,e.event_at,j.name FROM entries e JOIN journals j ON e.journal_id=j.id WHERE 1=1';args=[]
  if jid:sql+=' AND e.journal_id=?';args.append(jid)
  if query:sql+=' AND (e.title LIKE ? OR e.content LIKE ?)';args.extend(['%'+query+'%']*2)
  return self.db.execute(sql+' ORDER BY e.event_at DESC,e.updated_at DESC',args).fetchall()
 def create_journal(self,name,kind):
  jid=str(uuid.uuid4());self.db.execute('INSERT INTO journals VALUES(?,?,?,?)',(jid,name,kind,'Ivory'));self.db.commit();return jid
 def create_entry(self,jid,title,content,event):
  eid=str(uuid.uuid4());t=now();self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,1)',(eid,jid,title,content,event,t,t));self.db.commit();return eid
 def save_entry(self,eid,title,content,event):
  with self.db:self.db.execute('UPDATE entries SET title=?,content=?,event_at=?,updated_at=?,revision=revision+1 WHERE id=?',(title,content,event,now(),eid))
 def delete_entry(self,eid):
  with self.db:self.db.execute('DELETE FROM entries WHERE id=?',(eid,))
 def delete_journal(self,jid):
  with self.db:self.db.execute('DELETE FROM journals WHERE id=?',(jid,))
 def backup(self,path):
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'database.sqlite3';dest=sqlite3.connect(target)
   self.db.backup(dest);dest.close()
   with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
    z.write(target,'database.sqlite3');z.writestr('manifest.json',json.dumps({'format':'personal-space-mvp','version':1}))
 def restore(self,path):
  with zipfile.ZipFile(path) as z:
   if set(z.namelist())!={'database.sqlite3','manifest.json'}:raise ValueError('Неожиданный состав архива')
   manifest=json.loads(z.read('manifest.json'))
   if manifest.get('format')!='personal-space-mvp' or manifest.get('version')!=1:raise ValueError('Несовместимый архив')
   with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'restore.sqlite3';p.write_bytes(z.read('database.sqlite3'))
    test=sqlite3.connect(p)
    if test.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Повреждённая база')
    test.close();self.db.close()
    try:
     shutil.copy2(DB,ROOT/'pre_restore.sqlite3')
     for suffix in ('-wal','-shm'):
      (Path(str(DB)+suffix)).unlink(missing_ok=True)
     shutil.copy2(p,DB)
    except Exception:
     shutil.copy2(ROOT/'pre_restore.sqlite3',DB);raise
    finally:self.db=sqlite3.connect(DB);self.db.execute('PRAGMA foreign_keys=ON')
class Window(QMainWindow):
 def __init__(self):
  super().__init__();self.store=Store();self.jid=None;self.eid=None;self.dirty=False;self.loading=False
  self.setWindowTitle('Personal Space — MVP');self.resize(1200,780)
  self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(900);self.timer.timeout.connect(self.save)
  self.stack=QStackedWidget();self.setCentralWidget(self.stack)
  self.home=QWidget();self.stack.addWidget(self.home);hl=QHBoxLayout(self.home)
  left=QVBoxLayout();hl.addLayout(left,1);left.addWidget(QLabel('МОИ ДНЕВНИКИ'))
  self.journals=QListWidget();left.addWidget(self.journals);self.journals.currentItemChanged.connect(self.pick_journal)
  addj=QPushButton('＋ Создать дневник');addj.clicked.connect(self.new_journal);left.addWidget(addj)
  delj=QPushButton('Удалить дневник');delj.clicked.connect(self.delete_journal);left.addWidget(delj)
  right=QVBoxLayout();hl.addLayout(right,3);bar=QHBoxLayout();right.addLayout(bar)
  self.heading=QLabel('Записи');bar.addWidget(self.heading);bar.addStretch()
  new=QPushButton('＋ Новая запись');new.clicked.connect(self.new_entry);bar.addWidget(new)
  self.search=QLineEdit();self.search.setPlaceholderText('Поиск по записям…');self.search.textChanged.connect(self.refresh_entries);right.addWidget(self.search)
  self.entries=QListWidget();self.entries.itemDoubleClicked.connect(self.open_item);right.addWidget(self.entries)
  self.editorpage=QWidget();self.stack.addWidget(self.editorpage);ev=QVBoxLayout(self.editorpage)
  top=QHBoxLayout();ev.addLayout(top);back=QPushButton('← К записям');back.clicked.connect(self.go_home);top.addWidget(back)
  self.context=QLabel('');top.addWidget(self.context);top.addStretch();self.status=QLabel('Сохранено');top.addWidget(self.status)
  self.title=QLineEdit();self.title.setPlaceholderText('Название записи');self.title.setStyleSheet('font-size:22px;padding:10px');ev.addWidget(self.title)
  row=QHBoxLayout();ev.addLayout(row);row.addWidget(QLabel('Дата события / пробуждения:'))
  self.event=QDateEdit();self.event.setCalendarPopup(True);self.event.setDisplayFormat('dd.MM.yyyy');row.addWidget(self.event);row.addStretch()
  delete=QPushButton('Удалить запись');delete.clicked.connect(self.delete_entry);row.addWidget(delete)
  tools=QHBoxLayout();ev.addLayout(tools)
  for label,method in [('B','bold'),('I','italic'),('U','underline'),('Заголовок','heading'),('Список','list'),('Цитата','quote'),('Ссылка','link'),('↶','undo'),('↷','redo')]:
   b=QPushButton(label);b.clicked.connect(lambda checked=False,m=method:self.format(m));tools.addWidget(b)
  tools.addStretch();self.body=QTextEdit();self.body.setAcceptRichText(True);ev.addWidget(self.body,1)
  self.title.textChanged.connect(self.changed);self.body.textChanged.connect(self.changed);self.event.dateChanged.connect(self.changed)
  menu=self.menuBar();file=menu.addMenu('Файл');self.action(file,'Резервная копия',self.backup);self.action(file,'Восстановить копию',self.restore)
  theme=menu.addMenu('Оформление');self.action(theme,'Ivory — светлая',lambda:self.set_theme('Ivory'));self.action(theme,'Midnight — тёмная',lambda:self.set_theme('Midnight'))
  self.action(file,'Выход',self.close)
  for keys,func in [('Ctrl+N',self.new_entry),('Ctrl+S',self.save),('Ctrl+F',lambda:(self.go_home(),self.search.setFocus()))]:
   a=QAction(self);a.setShortcut(keys);a.triggered.connect(func);self.addAction(a)
  self.settings=json.loads(SETTINGS.read_text('utf-8')) if SETTINGS.exists() else {}
  self.set_theme(self.settings.get('theme','Ivory'));self.refresh_journals()
 def action(self,menu,label,callback):a=menu.addAction(label);a.triggered.connect(callback)
 def set_theme(self,name):
  if name not in THEMES:name='Ivory'
  bg,paper,fg,border,accent=THEMES[name];app=QApplication.instance()
  app.setStyleSheet(f'''QWidget{{background:{bg};color:{fg};font-family:Segoe UI;font-size:14px}} QLineEdit,QTextEdit,QListWidget,QDateEdit{{background:{paper};border:1px solid {border};border-radius:9px;padding:7px}} QPushButton{{background:{paper};border:1px solid {border};border-radius:8px;padding:8px 12px}} QPushButton:hover{{border-color:{accent}}} QListWidget::item{{padding:9px}} QListWidget::item:selected{{background:{accent};color:white}} QMenuBar,QMenu{{background:{paper}}}''')
  self.settings={'theme':name};SETTINGS.write_text(json.dumps(self.settings),'utf-8')
 def refresh_journals(self,select=None):
  self.journals.blockSignals(True);self.journals.clear();rows=self.store.journals()
  for jid,name,kind,theme in rows:
   item=QListWidgetItem(('☾ ' if kind=='Сновидения' else '▣ ')+name);item.setData(Qt.UserRole,jid);self.journals.addItem(item)
  wanted=select or self.jid
  for i in range(self.journals.count()):
   if self.journals.item(i).data(Qt.UserRole)==wanted:self.journals.setCurrentRow(i);break
  else:
   if rows:self.journals.setCurrentRow(0)
  self.journals.blockSignals(False);self.jid=self.journals.currentItem().data(Qt.UserRole) if self.journals.currentItem() else None;self.refresh_entries()
 def pick_journal(self,current,previous):self.jid=current.data(Qt.UserRole) if current else None;self.refresh_entries()
 def refresh_entries(self):
  self.entries.clear();rows=self.store.entries(self.jid,self.search.text().strip())
  for eid,jid,title,content,event,journal in rows:
   plain=re.sub('<[^>]+>',' ',content);plain=re.sub(r'\s+',' ',plain).strip()[:110]
   item=QListWidgetItem(f'{title or "Без названия"}   ·   {event}\n{plain}');item.setData(Qt.UserRole,(eid,jid));self.entries.addItem(item)
  self.heading.setText(f'Записи · {len(rows)}')
 def new_journal(self):
  name,ok=QInputDialog.getText(self,'Новый дневник','Название дневника:')
  if not ok or not name.strip():return
  kind,ok=QInputDialog.getItem(self,'Тип дневника','Тип:', ['Обычный','Сновидения'],0,False)
  if ok:self.refresh_journals(self.store.create_journal(name.strip()[:120],kind))
 def delete_journal(self):
  if not self.jid:return
  count=len(self.store.entries(self.jid))
  if QMessageBox.question(self,'Удалить дневник',f'Безвозвратно удалить дневник и {count} записей?',QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
   self.store.delete_journal(self.jid);self.jid=None;self.refresh_journals()
 def new_entry(self):
  if not self.jid:QMessageBox.information(self,'Дневники','Сначала создайте дневник.');return
  self.eid=None;self.loading=True;self.title.clear();self.body.clear();self.event.setDate(QDate.currentDate());self.loading=False;self.dirty=False;self.status.setText('Новая запись');self.context.setText(self.journal_name());self.stack.setCurrentWidget(self.editorpage);self.title.setFocus()
 def journal_name(self):
  for jid,name,kind,theme in self.store.journals():
   if jid==self.jid:return name
  return ''
 def open_item(self,item):
  eid,jid=item.data(Qt.UserRole);row=self.store.db.execute('SELECT title,content,event_at FROM entries WHERE id=?',(eid,)).fetchone()
  if not row:return
  self.eid=eid;self.jid=jid;self.loading=True;self.title.setText(row[0]);self.body.setHtml(row[1]);self.event.setDate(QDate.fromString(row[2],'yyyy-MM-dd'));self.loading=False;self.dirty=False;self.status.setText('Сохранено');self.context.setText(self.journal_name());self.stack.setCurrentWidget(self.editorpage);self.body.setFocus()
 def changed(self,*args):
  if self.loading:return
  self.dirty=True;self.status.setText('Есть изменения');self.timer.start()
 def save(self):
  self.timer.stop()
  if not self.dirty:return True
  title=self.title.text().strip();html=self.body.toHtml();event=self.event.date().toString('yyyy-MM-dd')
  try:
   if self.eid:self.store.save_entry(self.eid,title,html,event)
   elif title or self.body.toPlainText().strip():self.eid=self.store.create_entry(self.jid,title,html,event)
   else:self.dirty=False;self.status.setText('Пустая запись');return True
   self.dirty=False;self.status.setText('Сохранено');return True
  except Exception as exc:
   self.status.setText('Не сохранено — Ctrl+S повторить');QMessageBox.warning(self,'Ошибка сохранения',str(exc));return False
 def go_home(self):
  if not self.save():return
  self.stack.setCurrentWidget(self.home);self.refresh_journals(self.jid)
 def delete_entry(self):
  if not self.eid:self.go_home();return
  if QMessageBox.question(self,'Удаление','Безвозвратно удалить эту запись?',QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
   self.timer.stop();self.dirty=False;self.store.delete_entry(self.eid);self.eid=None;self.go_home()
 def format(self,mode):
  if mode in ('undo','redo'):getattr(self.body,mode)();return
  cursor=self.body.textCursor()
  if mode in ('bold','italic','underline'):
   fmt=QTextCharFormat();current=self.body.currentCharFormat()
   if mode=='bold':fmt.setFontWeight(QFont.Normal if current.fontWeight()==QFont.Bold else QFont.Bold)
   if mode=='italic':fmt.setFontItalic(not current.fontItalic())
   if mode=='underline':fmt.setFontUnderline(not current.fontUnderline())
   cursor.mergeCharFormat(fmt);self.body.mergeCurrentCharFormat(fmt)
  elif mode=='heading':
   fmt=QTextCharFormat();fmt.setFontPointSize(20);fmt.setFontWeight(QFont.Bold);cursor.mergeCharFormat(fmt)
  elif mode=='list':cursor.createList(QTextListFormat.ListDisc)
  elif mode=='quote':
   block=cursor.blockFormat();block.setLeftMargin(24);cursor.setBlockFormat(block)
  elif mode=='link':
   url,ok=QInputDialog.getText(self,'Добавить ссылку','Адрес (https://…):')
   if ok and url.startswith(('https://','http://')):
    fmt=QTextCharFormat();fmt.setAnchor(True);fmt.setAnchorHref(url);fmt.setFontUnderline(True);cursor.mergeCharFormat(fmt)
   elif ok:QMessageBox.warning(self,'Ссылка','Введите адрес http:// или https://')
  self.body.setFocus();self.changed()
 def backup(self):
  if not self.save():return
  path,_=QFileDialog.getSaveFileName(self,'Создать резервную копию',str(Path.home()/'PersonalSpace-backup.zip'),'ZIP (*.zip)')
  if path:
   try:self.store.backup(path);QMessageBox.information(self,'Копия','Резервная копия создана. Внимание: архив не зашифрован!')
   except Exception as exc:QMessageBox.critical(self,'Ошибка',str(exc))
 def restore(self):
  if not self.save():return
  path,_=QFileDialog.getOpenFileName(self,'Восстановить копию',str(Path.home()),'ZIP (*.zip)')
  if not path:return
  if QMessageBox.question(self,'Восстановление','Текущие данные будут заменены. Продолжить?',QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
  try:self.store.restore(path);self.eid=None;self.stack.setCurrentWidget(self.home);self.refresh_journals();QMessageBox.information(self,'Готово','Копия восстановлена. Предыдущее состояние сохранено как pre_restore.sqlite3.')
  except Exception as exc:QMessageBox.critical(self,'Ошибка восстановления',str(exc))
 def closeEvent(self,event):
  if not self.save():event.ignore();return
  self.store.db.close();event.accept()
if __name__=='__main__':
 app=QApplication(sys.argv);w=Window();w.show();sys.exit(app.exec())
