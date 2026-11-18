import sys, uuid, re, json
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QDate, QEvent
from PySide6.QtGui import QAction, QTextCharFormat, QFont, QTextListFormat
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,
    QListWidget,QListWidgetItem,QLineEdit,QTextEdit,QLabel,QDateEdit,QMessageBox,QInputDialog,
    QFileDialog,QStackedWidget,QDialog,QDialogButtonBox,QFormLayout,QSpinBox)
from security import Vault

ROOT=Path.home()/'PersonalSpaceData'
SETTINGS=ROOT/'settings.json'
LEGACY=ROOT/'personal_space.sqlite3'
THEMES={'Ivory':('#faf8f3','#ffffff','#282632','#e9e4dc','#795c9b'),
        'Midnight':('#181923','#232535','#f0edf7','#383a50','#b7a1ef')}

def now(): return datetime.now().isoformat(timespec='seconds')

class Store:
    def __init__(self): self.vault=Vault(ROOT)
    @property
    def db(self):
        if self.vault.conn is None: raise RuntimeError('Хранилище заблокировано')
        return self.vault.conn
    def journals(self): return self.db.execute('SELECT id,name,type,theme FROM journals ORDER BY rowid').fetchall()
    def entries(self,jid=None,query=''):
        sql='SELECT e.id,e.journal_id,e.title,e.content,e.event_at,j.name FROM entries e JOIN journals j ON e.journal_id=j.id WHERE 1=1';args=[]
        if jid: sql+=' AND e.journal_id=?';args.append(jid)
        if query:
            # casefold-based filtering supports Cyrillic rather than ASCII-only SQLite LIKE
            rows=self.db.execute(sql+' ORDER BY e.event_at DESC,e.updated_at DESC',args).fetchall()
            q=query.casefold()
            return [r for r in rows if q in (r[2]+' '+re.sub('<[^>]+>',' ',r[3])).casefold()]
        return self.db.execute(sql+' ORDER BY e.event_at DESC,e.updated_at DESC',args).fetchall()
    def create_journal(self,name,kind):
        jid=str(uuid.uuid4())
        self.vault.mutate(lambda db: db.execute('INSERT INTO journals VALUES(?,?,?,?)',(jid,name,kind,'Ivory')))
        return jid
    def create_entry(self,jid,title,content,event):
        eid=str(uuid.uuid4());t=now()
        self.vault.mutate(lambda db: db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,1)',(eid,jid,title,content,event,t,t)))
        return eid
    def save_entry(self,eid,title,content,event):
        self.vault.mutate(lambda db: db.execute('UPDATE entries SET title=?,content=?,event_at=?,updated_at=?,revision=revision+1 WHERE id=?',(title,content,event,now(),eid)))
    def delete_entry(self,eid): self.vault.mutate(lambda db: db.execute('DELETE FROM entries WHERE id=?',(eid,)))
    def delete_journal(self,jid): self.vault.mutate(lambda db: db.execute('DELETE FROM journals WHERE id=?',(jid,)))
    def backup(self,path): self.vault.backup(path)
    def restore(self,path,password): self.vault.restore(path,password)

class PasswordDialog(QDialog):
    def __init__(self,mode='unlock',parent=None):
        super().__init__(parent)
        self.setWindowTitle({'create':'Создание мастер-пароля','unlock':'Разблокировать Personal Space',
                             'change':'Изменить мастер-пароль','restore':'Пароль резервной копии'}[mode])
        self.setMinimumWidth(420)
        form=QFormLayout(self)
        if mode=='create': form.addRow(QLabel('Придумайте длинный уникальный пароль. Без него данные не восстановить.'))
        self.old=QLineEdit();self.old.setEchoMode(QLineEdit.Password)
        self.new=QLineEdit();self.new.setEchoMode(QLineEdit.Password)
        self.confirm=QLineEdit();self.confirm.setEchoMode(QLineEdit.Password)
        if mode in ('unlock','change','restore'):
            form.addRow('Мастер-пароль:' if mode!='change' else 'Старый пароль:',self.old)
        if mode in ('create','change'):
            form.addRow('Новый пароль:',self.new)
            form.addRow('Повторите пароль:',self.confirm)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject)
        form.addRow(buttons)
    def validate(self):
        if self.new.isVisible() and self.new.parentWidget() and self.new.text(): pass
        if self.new.text() or self.confirm.text():
            if len(self.new.text())<12:
                QMessageBox.warning(self,'Пароль','Минимум 12 символов.');return
            if self.new.text()!=self.confirm.text():
                QMessageBox.warning(self,'Пароль','Подтверждение не совпадает.');return
        elif self.windowTitle() in ('Создание мастер-пароля','Изменить мастер-пароль'):
            QMessageBox.warning(self,'Пароль','Укажите новый пароль.');return
        if self.windowTitle()!='Создание мастер-пароля' and not self.old.text():
            QMessageBox.warning(self,'Пароль','Введите пароль.');return
        self.accept()

class Window(QMainWindow):
    def __init__(self,store):
        super().__init__();self.store=store;self.jid=None;self.eid=None;self.dirty=False;self.loading=False;self.locked=False
        self.setWindowTitle('Personal Space — защищённый локальный дневник');self.resize(1200,780)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(900);self.timer.timeout.connect(self.save)
        self.idle=QTimer(self);self.idle.setSingleShot(True);self.idle.timeout.connect(self.lock)
        self.stack=QStackedWidget();self.setCentralWidget(self.stack)
        self.home=QWidget();self.stack.addWidget(self.home);hl=QHBoxLayout(self.home)
        left=QVBoxLayout();hl.addLayout(left,1);left.addWidget(QLabel('МОИ ДНЕВНИКИ'))
        self.journals=QListWidget();left.addWidget(self.journals);self.journals.currentItemChanged.connect(self.pick_journal)
        b=QPushButton('＋ Создать дневник');b.clicked.connect(self.new_journal);left.addWidget(b)
        b=QPushButton('Удалить дневник');b.clicked.connect(self.delete_journal);left.addWidget(b)
        right=QVBoxLayout();hl.addLayout(right,3);bar=QHBoxLayout();right.addLayout(bar)
        self.heading=QLabel('Записи');bar.addWidget(self.heading);bar.addStretch()
        b=QPushButton('＋ Новая запись');b.clicked.connect(self.new_entry);bar.addWidget(b)
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск по записям…');self.search.textChanged.connect(self.refresh_entries);right.addWidget(self.search)
        self.entries=QListWidget();self.entries.itemDoubleClicked.connect(self.open_item);right.addWidget(self.entries)
        self.editorpage=QWidget();self.stack.addWidget(self.editorpage);ev=QVBoxLayout(self.editorpage)
        top=QHBoxLayout();ev.addLayout(top);b=QPushButton('← К записям');b.clicked.connect(self.go_home);top.addWidget(b)
        self.context=QLabel('');top.addWidget(self.context);top.addStretch();self.status=QLabel('Сохранено');top.addWidget(self.status)
        self.title=QLineEdit();self.title.setPlaceholderText('Название записи');self.title.setStyleSheet('font-size:22px;padding:10px');ev.addWidget(self.title)
        row=QHBoxLayout();ev.addLayout(row);row.addWidget(QLabel('Дата события / пробуждения:'))
        self.event=QDateEdit();self.event.setCalendarPopup(True);self.event.setDisplayFormat('dd.MM.yyyy');row.addWidget(self.event);row.addStretch()
        b=QPushButton('Удалить запись');b.clicked.connect(self.delete_entry);row.addWidget(b)
        tools=QHBoxLayout();ev.addLayout(tools)
        for label,method in [('B','bold'),('I','italic'),('U','underline'),('Заголовок','heading'),('Список','list'),('Цитата','quote'),('Ссылка','link'),('↶','undo'),('↷','redo')]:
            b=QPushButton(label);b.clicked.connect(lambda checked=False,m=method:self.format(m));tools.addWidget(b)
        tools.addStretch();self.body=QTextEdit();self.body.setAcceptRichText(True);ev.addWidget(self.body,1)
        self.title.textChanged.connect(self.changed);self.body.textChanged.connect(self.changed);self.event.dateChanged.connect(self.changed)
        menu=self.menuBar();file=menu.addMenu('Файл')
        self.action(file,'Создать зашифрованную копию',self.backup)
        self.action(file,'Восстановить зашифрованную копию',self.restore)
        self.action(file,'Импортировать старую базу MVP',self.import_old)
        self.action(file,'Заблокировать',self.lock)
        self.action(file,'Выход',self.close)
        security=menu.addMenu('Безопасность')
        self.action(security,'Изменить мастер-пароль',self.change_password)
        self.action(security,'Настроить автоблокировку',self.set_idle_time)
        theme=menu.addMenu('Оформление')
        self.action(theme,'Ivory — светлая',lambda:self.set_theme('Ivory'))
        self.action(theme,'Midnight — тёмная',lambda:self.set_theme('Midnight'))
        for keys,func in [('Ctrl+N',self.new_entry),('Ctrl+S',self.save),('Ctrl+F',lambda:(self.go_home(),self.search.setFocus())),('Ctrl+L',self.lock)]:
            a=QAction(self);a.setShortcut(keys);a.triggered.connect(func);self.addAction(a)
        try: self.settings=json.loads(SETTINGS.read_text('utf-8')) if SETTINGS.exists() else {}
        except (OSError,ValueError): self.settings={}
        self.set_theme(self.settings.get('theme','Ivory'))
        self.refresh_journals();QApplication.instance().installEventFilter(self)
        self.reset_idle()
    def eventFilter(self,obj,event):
        if not self.locked and event.type() in (QEvent.MouseButtonPress,QEvent.KeyPress,QEvent.Wheel): self.reset_idle()
        return super().eventFilter(obj,event)
    def reset_idle(self): self.idle.start(int(self.settings.get('idle_minutes',5))*60000)
    def action(self,menu,label,callback): a=menu.addAction(label);a.triggered.connect(callback)
    def set_theme(self,name):
        if name not in THEMES:name='Ivory'
        bg,paper,fg,border,accent=THEMES[name]
        QApplication.instance().setStyleSheet(f'''QWidget{{background:{bg};color:{fg};font-family:Segoe UI;font-size:14px}} QLineEdit,QTextEdit,QListWidget,QDateEdit{{background:{paper};border:1px solid {border};border-radius:9px;padding:7px}} QPushButton{{background:{paper};border:1px solid {border};border-radius:8px;padding:8px 12px}} QPushButton:hover{{border-color:{accent}}} QListWidget::item{{padding:9px}} QListWidget::item:selected{{background:{accent};color:white}} QMenuBar,QMenu{{background:{paper}}}''')
        self.settings['theme']=name;SETTINGS.write_text(json.dumps(self.settings),'utf-8')
    def refresh_journals(self,select=None):
        self.journals.blockSignals(True);self.journals.clear();rows=self.store.journals()
        for jid,name,kind,theme in rows:
            item=QListWidgetItem(('☾ ' if kind=='Сновидения' else '▣ ')+name);item.setData(Qt.UserRole,jid);self.journals.addItem(item)
        wanted=select or self.jid
        for i in range(self.journals.count()):
            if self.journals.item(i).data(Qt.UserRole)==wanted:self.journals.setCurrentRow(i);break
        else:
            if rows:self.journals.setCurrentRow(0)
        self.journals.blockSignals(False)
        self.jid=self.journals.currentItem().data(Qt.UserRole) if self.journals.currentItem() else None
        self.refresh_entries()
    def pick_journal(self,current,previous): self.jid=current.data(Qt.UserRole) if current else None;self.refresh_entries()
    def refresh_entries(self):
        self.entries.clear();rows=self.store.entries(self.jid,self.search.text().strip())
        for eid,jid,title,content,event,journal in rows:
            plain=re.sub(r'\s+',' ',re.sub('<[^>]+>',' ',content)).strip()[:110]
            item=QListWidgetItem(f'{title or "Без названия"}   ·   {event}\n{plain}');item.setData(Qt.UserRole,(eid,jid));self.entries.addItem(item)
        self.heading.setText(f'Записи · {len(rows)}')
    def new_journal(self):
        name,ok=QInputDialog.getText(self,'Новый дневник','Название дневника:')
        if not ok or not name.strip():return
        kind,ok=QInputDialog.getItem(self,'Тип дневника','Тип:', ['Обычный','Сновидения'],0,False)
        if ok:
            try:self.refresh_journals(self.store.create_journal(name.strip()[:120],kind))
            except Exception as ex:QMessageBox.critical(self,'Не сохранено',str(ex))
    def delete_journal(self):
        if not self.jid:return
        count=len(self.store.entries(self.jid))
        if QMessageBox.question(self,'Удалить дневник',f'Безвозвратно удалить дневник и {count} записей?',QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
            try:self.store.delete_journal(self.jid);self.jid=None;self.refresh_journals()
            except Exception as ex:QMessageBox.critical(self,'Ошибка',str(ex))
    def new_entry(self):
        if not self.jid: QMessageBox.information(self,'Дневники','Сначала создайте дневник.');return
        if not self.save():return
        self.eid=None;self.loading=True;self.title.clear();self.body.clear();self.event.setDate(QDate.currentDate());self.loading=False
        self.dirty=False;self.status.setText('Новая запись');self.context.setText(self.journal_name());self.stack.setCurrentWidget(self.editorpage);self.title.setFocus()
    def journal_name(self):
        for jid,name,kind,theme in self.store.journals():
            if jid==self.jid:return name
        return ''
    def open_item(self,item):
        if not self.save():return
        eid,jid=item.data(Qt.UserRole)
        row=self.store.db.execute('SELECT title,content,event_at FROM entries WHERE id=?',(eid,)).fetchone()
        if not row:return
        self.eid=eid;self.jid=jid;self.loading=True;self.title.setText(row[0]);self.body.setHtml(row[1]);self.event.setDate(QDate.fromString(row[2],'yyyy-MM-dd'))
        self.loading=False;self.dirty=False;self.status.setText('Сохранено');self.context.setText(self.journal_name());self.stack.setCurrentWidget(self.editorpage);self.body.setFocus()
    def changed(self,*args):
        if self.loading or self.locked:return
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
        except Exception as ex:
            self.status.setText('Не сохранено — Ctrl+S повторить');QMessageBox.warning(self,'Ошибка сохранения',str(ex));return False
    def go_home(self):
        if not self.save():return
        self.stack.setCurrentWidget(self.home);self.refresh_journals(self.jid)
    def delete_entry(self):
        if not self.eid:self.go_home();return
        if QMessageBox.question(self,'Удаление','Безвозвратно удалить эту запись?',QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:
            try:
                self.timer.stop();self.store.delete_entry(self.eid);self.dirty=False;self.eid=None;self.go_home()
            except Exception as ex:QMessageBox.critical(self,'Ошибка',str(ex))
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
        path,_=QFileDialog.getSaveFileName(self,'Создать защищённую копию',str(Path.home()/'PersonalSpace-backup.psbackup'),'Personal Space (*.psbackup)')
        if path:
            try:self.store.backup(path);QMessageBox.information(self,'Копия','Зашифрованная резервная копия создана. Для её восстановления нужен мастер-пароль, действовавший при создании копии.')
            except Exception as ex:QMessageBox.critical(self,'Ошибка',str(ex))
    def restore(self):
        if not self.save():return
        path,_=QFileDialog.getOpenFileName(self,'Восстановить защищённую копию',str(Path.home()),'Personal Space (*.psbackup *.psv)')
        if not path:return
        dialog=PasswordDialog('restore',self)
        if dialog.exec()!=QDialog.Accepted:return
        if QMessageBox.question(self,'Восстановление','Текущие записи будут заменены данными из копии. Рекомендуется предварительно сохранить отдельную резервную копию. Продолжить?',QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
        try:
            self.store.restore(path,dialog.old.text());self.eid=None;self.stack.setCurrentWidget(self.home);self.refresh_journals()
            QMessageBox.information(self,'Готово','Зашифрованная копия успешно восстановлена.')
        except Exception as ex:QMessageBox.critical(self,'Ошибка восстановления',str(ex))
    def change_password(self):
        if not self.save():return
        d=PasswordDialog('change',self)
        if d.exec()!=QDialog.Accepted:return
        try:self.store.vault.change_password(d.old.text(),d.new.text());QMessageBox.information(self,'Безопасность','Мастер-пароль изменён. Старые резервные копии открываются прежним паролем.')
        except Exception as ex:QMessageBox.critical(self,'Ошибка изменения пароля',str(ex))
    def set_idle_time(self):
        value,ok=QInputDialog.getInt(self,'Автоблокировка','Минут бездействия:',int(self.settings.get('idle_minutes',5)),1,120)
        if ok:self.settings['idle_minutes']=value;SETTINGS.write_text(json.dumps(self.settings),'utf-8');self.reset_idle()
    def import_old(self):
        if not LEGACY.exists():QMessageBox.information(self,'Импорт','Старая база personal_space.sqlite3 не найдена.');return
        if self.store.journals():QMessageBox.warning(self,'Импорт','Импорт возможен только в пустое защищённое хранилище.');return
        if QMessageBox.question(self,'Импорт старых записей','Импортировать незашифрованную базу первой версии? Оригинальный файл останется на диске для самостоятельного удаления после проверки переноса.',QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
        try:self.store.vault.import_legacy(LEGACY);self.refresh_journals();QMessageBox.information(self,'Импорт','Записи перенесены в зашифрованное хранилище. ВАЖНО: старая база и ранее созданные ZIP-копии НЕ зашифрованы — проверьте перенос и отдельно удалите старые файлы. Удаление файлов не гарантирует стирание предыдущих копий с SSD и резервных носителей.')
        except Exception as ex:QMessageBox.critical(self,'Ошибка импорта',str(ex))
    def lock(self):
        if self.locked:return
        if not self.save():return
        self.locked=True;self.idle.stop();self.timer.stop();self.loading=True
        self.title.clear();self.body.clear();self.entries.clear();self.journals.clear();self.search.clear();self.context.setText('');self.eid=None;self.jid=None;self.stack.setCurrentWidget(self.home)
        self.loading=False;self.store.vault.lock();self.hide()
        while True:
            d=PasswordDialog('unlock')
            if d.exec()!=QDialog.Accepted:
                self.close();return
            try:self.store.vault.unlock(d.old.text());break
            except Exception as ex:QMessageBox.warning(None,'Не удалось разблокировать',str(ex))
        self.locked=False;self.refresh_journals();self.show();self.reset_idle()
    def closeEvent(self,event):
        if not self.locked and not self.save():event.ignore();return
        self.idle.stop();self.store.vault.lock();event.accept()


def main():
    app=QApplication(sys.argv)
    store=Store()
    if not store.vault.exists:
        d=PasswordDialog('create')
        if d.exec()!=QDialog.Accepted:return 0
        try:store.vault.create(d.new.text())
        except Exception as ex:QMessageBox.critical(None,'Не удалось создать хранилище',str(ex));return 1
    else:
        while True:
            d=PasswordDialog('unlock')
            if d.exec()!=QDialog.Accepted:return 0
            try:store.vault.unlock(d.old.text());break
            except Exception as ex:QMessageBox.warning(None,'Не удалось разблокировать',str(ex))
    w=Window(store);w.show()
    if LEGACY.exists and not store.journals():
        QTimer.singleShot(200,lambda:QMessageBox.information(w,'Старая база обнаружена','Найдена незашифрованная база MVP. Чтобы перенести дневники, откройте Файл → Импортировать старую базу MVP. Старый файл не удаляется автоматически.'))
    return app.exec()

if __name__=='__main__':sys.exit(main())
