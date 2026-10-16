from __future__ import annotations

import json
import logging
import os
import queue
import time
import tkinter as tk
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

from .ocr import Recognizer, open_image, RECOGNITION_VERSION
from .dedup import image_digest
from .store import Store
from .session import reconcile
from .windows import Hotkey, monitors, parse_hotkey, monitor_identity, physical_pixels, apex_foreground, window_display
from .monitor import SummaryWatcher
from .pages import classify_image
from .domain import pending_reason, POLICY
from .journal import EvidenceSpool
from .services import RecognitionService
from .participation import format_rate, percentage
from .dpi import DpiController

from .theme import BG, PANEL, TEXT, MUTED, ACCENT, AMBER, FIELD, AngularPanel, ScrollBody, brand, install, section


def fit_window(window, width, height, logical=True):
    """Logical initial size, clamped to the current monitor's usable area."""
    dpi = getattr(window, 'dpi_controller', None) or DpiController(window)
    dpi.apply()
    dpi.fit(dpi.px(width) if logical else width, dpi.px(height) if logical else height)


class App:
    def __init__(self, root, data_dir):
        self.root = root
        self.store = Store(data_dir)
        self.events = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LocalOCR")
        self.page_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='EntryPageOCR')
        self.page_engine=None
        self.spool=EvidenceSpool(data_dir)
        self.processing_spool=None
        self.failed_spool=set()
        self.engine = None
        self.engine_ready = False
        self.busy = False
        self.hotkey = None
        self.watcher = None
        self.auto_job = None
        self.pending_auto = []
        self.end_requested = False
        self.close_requested = False
        self.repair_seen = set()
        self.displays = monitors()
        self.display_check=time.monotonic()
        if self.displays and not self.store.setting('monitor_device'):
            self.store.set_setting('monitor_device',monitor_identity(self.displays[min(max(0,self.store.setting('monitor',0)),len(self.displays)-1)]))
        self.selected_session = None
        self.session_lookup = {}
        self.player_labels = []
        self.player_rows = []
        self.average_labels = []
        self.participation_labels = []
        self.metric_labels = []
        self.metric_natural_width = int(root.tk.call('font','measure',('Consolas',18,'bold'),'0'))*36+100
        self.session_var = tk.StringVar()
        self.status = tk.StringVar(value="正在加载本地识别模型…")
        self.headline = tk.StringVar(value="共计：0 场游戏")
        self.caption = tk.StringVar()
        self.mode = tk.StringVar(value=f"{self.store.setting('mode', 3)} 人模式")
        self.roster_caption = tk.StringVar()
        self.automatic = tk.BooleanVar(value=self.store.setting('automatic', True))
        self.auto_status = tk.StringVar(value='自动记录：尚未开始')
        self._build()
        self.dpi = DpiController(root, self._dpi_changed)
        for session in self.store.sessions():
            # Re-evaluate saved OCR with current inference rules, including ended
            # sessions; confirmed/manual data is never overwritten by reconcile.
            self.reconcile_session(session['id'])
        self.refresh()
        fit_window(root, 1120, 860)
        if self.store.active_session():
            self._register(self.store.setting("hotkey", "Ctrl+End"))
        self.executor.submit(self._initialize)
        self.poll_id = root.after(80, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        root = self.root
        root.title("Apex 战绩记录器")
        root.geometry("1120x860")
        install(root)
        container = ScrollBody(root, padding=(24, 20)).content
        header = AngularPanel(container, padding=18)
        header.pack(fill='x', pady=(0, 16))
        title = ttk.Frame(header.content, style='Panel.TFrame')
        title.pack(side='left', fill='x', expand=True)
        brand(title)
        ttk.Label(title, text='战绩记录器', style='Panel.TLabel',
                  font=('Microsoft YaHei UI', 18, 'bold')).pack(side='left')
        ttk.Button(header.content, text='设置', command=self.settings).pack(side='right')
        toolbar = ttk.Frame(container)
        toolbar.pack(fill='x', pady=(0, 12))
        self.start_button = ttk.Button(toolbar, text='开始记录', style='Accent.TButton', command=self.start)
        self.start_button.pack(side='left')
        self.end_button = ttk.Button(toolbar, text='结束记录', command=self.end)
        self.end_button.pack(side='left', padx=8)
        self.mode_box = ttk.Combobox(toolbar, textvariable=self.mode,
                                    values=['1 人模式', '2 人模式', '3 人模式'], state='readonly', width=10)
        self.mode_box.pack(side='left', padx=(4, 16))
        self.mode_box.bind('<<ComboboxSelected>>', self.change_mode)
        self.session_box = ttk.Combobox(toolbar, textvariable=self.session_var, state='readonly', width=32)
        self.session_box.pack(side='right')
        self.session_box.bind('<<ComboboxSelected>>', self.select_session)
        self.wrapped_label(container, text='自动模式：打开小队总结页并停留约 1 秒；后台识别。也可用快捷键补录或导入截图。',
                           style='Muted.TLabel').pack(fill='x', pady=(0, 14))
        automatic_bar = ttk.Frame(container)
        automatic_bar.pack(fill='x', pady=(0, 10))
        ttk.Checkbutton(automatic_bar, text='自动捕获总结页', variable=self.automatic,
                        command=self.change_automatic).pack(side='left')
        self.auto_label = ttk.Label(automatic_bar, textvariable=self.auto_status, style='Muted.TLabel')
        self.auto_label.pack(side='left', padx=16, fill='x', expand=True)
        automatic_bar.bind('<Configure>', lambda e: self.auto_label.configure(wraplength=max(100,e.width-self.ui_pixels(190))))
        rosterbar = ttk.Frame(container)
        rosterbar.pack(fill='x', pady=(0, 8))
        self.roster_label = ttk.Label(rosterbar, textvariable=self.roster_caption, style='Muted.TLabel')
        self.roster_label.pack(side='left', fill='x', expand=True)
        self.roster_button = ttk.Button(rosterbar, text='手动确认玩家', command=self.choose_players)
        self.roster_button.pack(side='right', padx=(12, 0))
        rosterbar.bind('<Configure>', lambda e: self.roster_label.configure(wraplength=max(100,e.width-self.roster_button.winfo_reqwidth()-self.ui_pixels(20))))
        overview = section(container, '累计数据')
        ttk.Label(overview, textvariable=self.headline, font=('Microsoft YaHei UI', 12, 'bold')).pack(side='right')
        self.wrapped_label(container, textvariable=self.caption, style='Muted.TLabel').pack(fill='x', pady=(0, 10))
        self.players_panel = ttk.Frame(container)
        self.players_panel.pack(fill='x')
        for _ in range(3):
            row = AngularPanel(self.players_panel, padding=14)
            metrics = ttk.Frame(row.content, style='Panel.TFrame')
            metrics.pack(side='right', padx=(16, 0))
            values = []
            for text in ('伤害', '击杀', '助攻', '场均伤害', '参团率'):
                cell = ttk.Frame(metrics, style='Panel.TFrame')
                cell.pack(side='left', padx=(10, 10))
                ttk.Label(cell, text=text, style='MetricHeading.TLabel',
                          font=('Microsoft YaHei UI', 9),
                          foreground=AMBER if text=='场均伤害' else MUTED).pack(anchor='e')
                value = ttk.Label(cell, text='—', width=8 if text in ('伤害','场均伤害') else 6,
                                  font=('Consolas', 18, 'bold'),
                                  anchor='e', style='Average.TLabel' if text=='场均伤害' else 'Metric.TLabel')
                value.pack(anchor='e')
                values.append(value)
            label = ttk.Label(row.content, style='Panel.TLabel', font=('Microsoft YaHei UI', 12, 'bold'))
            row.widgets = (label, metrics, values)
            row.content.bind('<Configure>', lambda event, panel=row:
                             self.layout_player(panel, event.width), add=True)
            self.player_rows.append(row)
            self.player_labels.append(label)
            self.metric_labels.append(values[:3])
            self.average_labels.append(values[3])
            self.participation_labels.append(values[4])
        actionbar = section(container, '每局记录')
        actionbar.pack_configure(pady=(16, 8))
        ttk.Button(actionbar, text='导入截图', command=self.import_image).pack(side='right')
        ttk.Button(actionbar, text='查看 / 修正', command=self.review_selected).pack(side='right', padx=8)
        ttk.Button(actionbar,text='重新识别待确认',command=lambda:self.repair_history(force=True)).pack(side='right',padx=8)
        historyframe = ttk.Frame(container)
        historyframe.pack(fill='both', expand=True)
        historyframe.rowconfigure(0, weight=1)
        historyframe.columnconfigure(0, weight=1)
        columns = ('time', 'status', 'summary', 'game_mode')
        self.history = ttk.Treeview(historyframe, columns=columns, show='headings', selectmode='browse', height=5)
        for key, title, width in (('time','时间',115),('status','状态',95),('summary','本局数据 / 待确认原因',720),('game_mode','模式',75)):
            self.history.heading(key, text=title)
            self.history.column(key, width=width, minwidth=220 if key=='summary' else width,
                                anchor='center' if key=='game_mode' else 'w', stretch=key=='summary')
        self.history.grid(row=0,column=0,sticky='nsew')
        vertical=ttk.Scrollbar(historyframe,orient='vertical',command=self.history.yview)
        vertical.grid(row=0,column=1,sticky='ns')
        horizontal=ttk.Scrollbar(historyframe,orient='horizontal',command=self.history.xview)
        horizontal.grid(row=1,column=0,sticky='ew')
        self.history.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.history.tag_configure('pending',foreground=AMBER)
        self.history.tag_configure('discarded',foreground='#787A74')
        self.history.bind('<Double-1>', lambda _event:self.review_selected())
        self.wrapped_label(container,textvariable=self.status,foreground=TEXT).pack(fill='x',pady=(12,6))
        footer=ttk.Frame(container)
        footer.pack(fill='x')
        ttk.Label(footer,text='本地保存 · 本地识别 · v14-beta6',style='Muted.TLabel',font=('Microsoft YaHei UI',9)).pack(side='left')
        ttk.Label(footer,text='非官方社区工具',style='Muted.TLabel',font=('Microsoft YaHei UI',9)).pack(side='right')
        self.wrapped_label(container,text='BUG 反馈与沟通：QQ 1050315764',foreground=MUTED,
                           font=('Microsoft YaHei UI',9)).pack(fill='x',pady=(6,0))

    @staticmethod
    def wrapped_label(master, **kwargs):
        label = ttk.Label(master, **kwargs)
        label._dpi_dynamic_wrap = True
        label.bind('<Configure>', lambda e: label.configure(wraplength=max(120,e.width)))
        return label

    def layout_player(self, row, width):
        label, metrics, values = row.widgets
        dpi = getattr(self.root, 'dpi_controller', None)
        px = dpi.px if dpi else lambda n: n
        label._dpi_dynamic_wrap = True
        narrow = width < self.metric_natural_width+px(220)
        size = max(10, min(18, int(18*(width-px(100))/max(1,self.metric_natural_width-px(80))))) if narrow else 18
        for value in values:
            spec = ('Consolas',size,'bold')
            value.configure(font=dpi.font(spec) if dpi else spec)
        if narrow:
            label.pack_configure(side='top',fill='x',expand=False)
            metrics.pack_configure(side='bottom',fill='x',expand=False,padx=0)
            label.configure(wraplength=max(px(120),width))
        else:
            label.pack_configure(side='left',fill='x',expand=True)
            metrics.pack_configure(side='right',fill='none',expand=False,padx=(px(16),0))
            label.configure(wraplength=max(px(120),width-self.metric_natural_width-px(20)))

    def _dpi_changed(self):
        dpi = self.root.dpi_controller
        self.metric_natural_width = dpi.font(('Consolas',18,'bold')).measure('0')*36+dpi.px(100)
        for row in self.player_rows:
            self.layout_player(row, row.content.winfo_width())

    def ui_pixels(self, logical):
        dpi = getattr(self.root, 'dpi_controller', None)
        return dpi.px(logical) if dpi else logical

    def _initialize(self):
        try:
            self.engine = Recognizer()
            self.events.put(("ready", None))
            self.page_executor.submit(self._initialize_pages)
        except Exception as error:
            logging.exception("OCR model initialization failed")
            self.events.put(("init_error", str(error)))

    def _initialize_pages(self):
        try:self.page_engine=Recognizer()
        except Exception:logging.exception('Entry OCR unavailable; summaries remain recoverable')

    def _register(self, value):
        candidate = Hotkey(value, lambda: self.events.put(("hotkey", None)))
        try:
            candidate.start()
            self.hotkey = candidate
        except Exception as error:
            self.status.set(str(error))

    def refresh(self):
        sessions = self.store.sessions()
        self.session_lookup = {f"#{s['id']}  {s['title']}  {'进行中' if not s['ended'] else '已结束'}": s["id"]
                               for s in sessions}
        self.session_box["values"] = list(self.session_lookup)
        if self.selected_session is None and sessions:
            active = self.store.active_session()
            self.selected_session = active["id"] if active else sessions[0]["id"]
        for label, sid in self.session_lookup.items():
            if sid == self.selected_session:
                self.session_var.set(label)
        active = self.store.active_session()
        self.start_button["state"] = "disabled" if active else "normal"
        self.end_button["state"] = "normal" if active else "disabled"
        self.mode_box["state"] = "disabled" if active else "readonly"
        if active:
            self.mode.set(f"{self.store.session_mode(active['id'])} 人模式")
        state = self.store.discovery(self.selected_session) if self.selected_session else None
        self.roster_caption.set(state['message'] if state else '只需选择人数；开始记录后，程序跨局自动确定参与玩家。')
        self.roster_button["state"] = "normal" if state and not state['resolved'] and self.store.captures(self.selected_session) else "disabled"
        count, totals = self.store.totals(self.selected_session)
        mode = state['mode'] if state else int(self.mode.get()[0])
        if not state or not state['resolved']:
            totals = [{'name': '等待确定玩家', 'damage': None, 'kills': None, 'assists': None,
                       'average_damage': None, 'participation':None} for _ in range(mode)]
        self.headline.set(f"共计：{count} 场游戏")
        captures = self.store.visible_captures(self.selected_session)
        pending = sum(c["status"] == "pending" for c in captures)
        shortcut = self.store.setting("hotkey", "Ctrl+End")
        self.caption.set(f"截图快捷键：{shortcut}（{'记录中生效' if active else '开始记录后生效'}）    待确认：{pending} 张    当前统计：{len(totals)} 人")
        missing=totals[0].get('participation_missing',0) if totals else 0
        if missing:
            self.caption.set(self.caption.get()+f'；参团率缺 {missing} 局小队总击杀，请在“查看 / 修正”补充。')
        for label in self.player_labels:
            label.pack_forget()
        for row in self.player_rows:
            row.pack_forget()
        for index, (row, label, average, player) in enumerate(zip(self.player_rows, self.player_labels, self.average_labels, totals)):
            row.pack(fill="x", pady=4)
            label.pack(side="left", fill="x", expand=True, anchor='center')
            label['text'] = f"玩家：{player['name']}"
            for value, key in zip(self.metric_labels[index], ('damage','kills','assists')):
                value['text'] = f"{player[key]:,}" if player[key] is not None else '—'
            average['text'] = f"{player['average_damage']:,.1f}" if player['average_damage'] is not None else '—'
            self.participation_labels[index]['text']=format_rate(player.get('participation'))
            self.layout_player(row, row.content.winfo_width())
        selection = self.history.selection()
        self.history.delete(*self.history.get_children())
        status_names = {"pending": "待确认", "confirmed": "已记录", "discarded": "已忽略"}
        for capture in captures:
            result = json.loads(capture["result"])
            if capture["status"] == "confirmed":
                stats = self.store.capture(capture["id"])["stats"]
                names = {p["id"]: p["name"] for p in totals}
                summary = "  /  ".join(f"{names[s['player_id']]}：{s['damage']}伤害 {s['kills']}击杀 {s['assists']}助攻"
                                      f" 参团{format_rate(percentage(s['kills'],s['assists'],self.store.matches.team_kills(capture['match_id'])['team_kills']))}"
                                      for s in stats)
            else:
                summary = "；".join(result.get("warnings", [])) or ("等待跨局确定玩家，可继续截图或手动确认" if state and not state['resolved'] else "请确认玩家归属与数字")
                reason=pending_reason(result,bool(state and state['resolved']),False,self.store.setting('auto_save',True))
                if reason:summary=reason+('；'+summary if summary else '')
                if capture["duplicate_of"]:
                    summary = f"与截图 #{capture['duplicate_of']} 相同，需确认是否新的一局；" + summary
            if not Path(capture['image']).is_file(): summary+='；原图已清理或不可用'
            self.history.insert("", "end", iid=str(capture["id"]),
                                values=(capture["created"][11:19], status_names[capture["status"]], summary,
                                        '【外卡】' if self.store.matches.mode(capture['match_id'])=='wildcard' else ''), tags=(capture["status"],))
        if selection and self.history.exists(selection[0]):
            self.history.selection_set(selection[0])

    def select_session(self, _event=None):
        self.selected_session = self.session_lookup.get(self.session_var.get())
        self.refresh()
        self.repair_history()

    def change_mode(self, _event=None):
        if self.store.active_session():
            return
        count = int(self.mode.get()[0])
        self.store.set_setting("mode", count)
        self.refresh()

    def choose_players(self):
        captures = [c for c in self.store.visible_captures(self.selected_session) if c['status'] != 'discarded']
        if captures:
            Review(self, self.store.capture(captures[0]['id']))

    def reconcile_session(self, session_id):
        return reconcile(self.store, session_id, self.store.setting('auto_save', True))

    def start(self, player_ids=None):
        if self.busy:
            self.status.set('正在处理截图或优化历史，请稍候再开始。')
            return
        try:
            self.selected_session = self.store.start_session(player_ids, mode=int(self.mode.get()[0]))
            self._register(self.store.setting("hotkey", "Ctrl+End"))
            self.refresh()
            self.configure_automatic()
            self.status.set("已开始记录。单／双人跨局自动确定玩家；无法区分时继续截图或手动确认。"
                            if self.hotkey else "已开始记录，但快捷键被占用，请在设置中更换；仍可导入截图。")
        except ValueError as error:
            messagebox.showerror("无法开始", str(error), parent=self.root)

    def end(self):
        self.end_requested = True
        self._stop_automatic()
        if self.busy or self.pending_auto:
            self.status.set('已停止检测，正在保存已捕获的总结页；处理完后结束记录。')
            return
        self._end_now()

    def _end_now(self):
        active = self.store.active_session()
        if active:
            self.store.end_session(active["id"])
            if self.hotkey:
                self.hotkey.stop()
                self.hotkey = None
            self.refresh()
            self.status.set("本次记录已结束并保存。待确认截图仍可查看和修正。")
        self.end_requested = False
        self.auto_status.set('自动记录：本次记录已结束')

    def change_automatic(self):
        self.store.set_setting('automatic', self.automatic.get())
        self.configure_automatic()

    def configure_automatic(self):
        self._stop_automatic()
        active = self.store.active_session()
        if not active or self.end_requested or self.close_requested:
            self.auto_status.set('自动记录：尚未开始' if not active else '自动记录：正在结束')
            return
        if not self.automatic.get():
            self.auto_status.set('手动模式：使用快捷键或导入截图')
            return
        if not self.engine_ready:
            self.auto_status.set('自动记录：等待识别模型就绪')
            return
        if not self.displays:
            self.auto_status.set('自动记录：无法获取显示器，请手动导入')
            return
        sid = active['id']
        capture_mode=self.store.setting('capture_mode','window')
        follow = capture_mode=='window' or self.store.setting('monitor_mode', 'auto') == 'auto'
        display=self.selected_display()
        if not follow and display is None:
            self.auto_status.set('自动记录：所选显示器未连接，请在设置中重新选择')
            return
        bbox = None if follow else display['bbox']
        logging.info('Automatic monitor mode=%s bbox=%s','auto' if follow else 'manual',bbox)
        events, reference = self.events, []
        # The native capture thread must not keep the Tk App alive through a closure.
        phase = self.store.automatic_phase(sid)
        watcher = SummaryWatcher(lambda kind, value: events.put(('auto_'+kind, (reference[0](),sid,value))), bbox,
                                 initial_phase=phase,spool=self.spool,session_id=sid,
                                 capture_mode=capture_mode,hide_border=self.store.setting('hide_capture_border',True))
        reference.append(weakref.ref(watcher))
        self.watcher = watcher
        watcher.start()

    def selected_display(self):
        device=self.store.setting('monitor_device')
        return next((d for d in self.displays if monitor_identity(d)==device),None)

    def refresh_displays(self):
        current=monitors()
        if current!=self.displays:
            self.displays=current
            display=self.selected_display()
            if display:self.store.set_setting('monitor',current.index(display))
            # A following watcher resolves current metadata itself. Restarting
            # here would lose its same-summary/Alt+Tab episode lock.
            if (self.store.setting('capture_mode','window')=='screen' and self.store.setting('monitor_mode', 'auto') != 'auto') or self.watcher is None:
                self.configure_automatic()

    def _stop_automatic(self):
        watcher = self.watcher
        if not watcher:
            return
        frame = watcher.stop()
        active = self.store.active_session()
        if active:
            self.store.set_setting('automatic_phase_'+str(active['id']),watcher.phase())
        self.watcher = None
        # Drain frames already emitted before stopping, preserving other events.
        retained = []
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'auto_frame':
                self._accept_auto(*payload)
            elif kind in ('auto_phase','auto_probe'):
                pass  # Snapshot above is newer; stopped probes cannot rearm.
            elif kind=='auto_lifecycle':
                _,sid,event=payload
                self.store.matches.begin(sid,event)
            elif kind != 'auto_state':
                retained.append((kind,payload))
        for event in retained:
            self.events.put(event)
        if frame is not None:
            active = self.store.active_session()
            if active:
                self._accept_auto(watcher,active['id'],frame)

    def _accept_auto(self, watcher, sid, image):
        if isinstance(image,dict) and image.get('path'):
            if image['path']==self.processing_spool or any(
                isinstance(job[2],dict) and job[2].get('path')==image['path'] for job in self.pending_auto):return
        else:
            active = self.store.active_session()
            if not active or active['id'] != sid:
                if watcher:watcher.available.set()
                return
        if len(self.pending_auto) >= POLICY.memory_pending:
            if watcher:watcher.available.set()
            self.status.set('识别队列繁忙，原图已进入本地恢复队列，稍后处理。')
            return
        self.pending_auto.append((watcher,sid,image))
        self._next_auto()

    def _next_auto(self):
        if self.busy:
            return
        if self.auto_job:
            self.auto_job.available.set()
            self.auto_job = None
        if not self.pending_auto:
            return
        watcher,sid,image = self.pending_auto.pop(0)
        self.auto_job = watcher
        self.processing_spool=image.get('path') if isinstance(image,dict) else None
        self.busy = True
        self.status.set('自动捕获总结页，正在保存原图并后台识别…')
        self.executor.submit(self._process,sid,None,None,self.store.duplicate_index(sid),image)

    def _probe_gameplay(self, watcher, sid, request):
        try:
            started=time.perf_counter()
            evidence = classify_image(self.page_engine,request['image']) if self.page_engine else dict(kind='unknown',evidence=[])
            evidence['elapsed']=round(time.perf_counter()-started,3)
            watcher.last_observation=evidence
            now=time.monotonic()
            if evidence['kind']!=getattr(watcher,'diagnostic_kind',None) or now-getattr(watcher,'diagnostic_last',0)>=30:
                logging.info('Entry page epoch=%s kind=%s seconds=%s evidence=%s partial=%s',
                             watcher.epoch,evidence['kind'],evidence['elapsed'],evidence['evidence'],evidence.get('partial'))
                watcher.diagnostic_last=now
                watcher.diagnostic_kind=evidence['kind']
        except Exception:
            logging.exception('Entry-page probe failed; evidence capture continues')
            evidence = None
        self.events.put(('auto_probe_result',(watcher,sid,request['token'],evidence)))

    def import_image(self):
        if not self.engine_ready:
            messagebox.showinfo("模型加载中", "请等待本地识别模型加载完成。", parent=self.root)
            return
        if self.busy:
            return
        active = self.store.active_session()
        if not active:
            messagebox.showinfo("先开始记录", "请先点击“开始记录”。", parent=self.root)
            return
        path = filedialog.askopenfilename(title="选择小队总结截图", filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp")])
        if path:
            self._submit(active["id"], path)

    def capture_screen(self):
        if self.end_requested or self.close_requested:
            return
        active = self.store.active_session()
        if self.busy:
            # No queued screenshots: one keypress group cannot count as multiple matches.
            return
        if not active:
            self.status.set("尚未开始记录，请先点击“开始记录”。")
            return
        if not self.engine_ready:
            self.status.set("识别模型尚未就绪，暂未截图。")
            return
        self.refresh_displays()
        if self.store.setting('monitor_mode', 'auto') == 'auto':
            with physical_pixels():
                hwnd = apex_foreground()
                display = window_display(hwnd) if hwnd else None
            if not display:
                self.status.set('自动选屏：请让 Apex 位于前台再按截图键；也可在设置中手动选屏。')
                return
        elif not self.selected_display():
            self.status.set('所选显示器未连接，请在设置中重新选择；仍可导入图片。')
            return
        self._submit(active["id"], None)

    def _submit(self, session_id, path):
        self.busy = True
        self.status.set("正在截图并保存…" if path is None else "正在保存导入图片…")
        display=self.selected_display()
        bbox = (display['bbox'] if display else None) if self.store.setting('monitor_mode', 'auto') != 'auto' else None
        context=None
        if self.watcher:
            context=dict(self.watcher.episode_context or self.watcher.cycle.summary_context(),
                         episode_key=self.watcher.episode_key or uuid.uuid4().hex,
                         source='manual',observation_version=14)
        self.executor.submit(self._process, session_id, path, bbox, self.store.duplicate_index(session_id),context)

    def _process(self, session_id, path, bbox, duplicates, captured=None):
        started = time.perf_counter()
        context=dict(captured or {}) if isinstance(captured,dict) else {}
        automatic=context.get('source')=='automatic' or captured is not None and not context
        spool_path=context.pop('path',None)
        captured_image=context.pop('image',None)
        if not isinstance(captured,dict):captured_image=captured
        try:
            follow_capture = path is None and not automatic and bbox is None and not spool_path and captured_image is None
            with physical_pixels():
                if follow_capture:
                    # Resolve again at actual worker capture time, not at keypress:
                    # UI/worker scheduling must not capture a now-unrelated screen.
                    hwnd = apex_foreground()
                    display = window_display(hwnd) if hwnd else None
                    if display is None:
                        raise OSError('请让 Apex 位于前台再截图，或在设置中手动选屏。')
                    from .windows import apex_capture_bbox
                    bbox = apex_capture_bbox(hwnd, display['bbox'])
                image = open_image(spool_path) if spool_path else captured_image if captured_image is not None else open_image(path) if path else ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
                if follow_capture and (apex_foreground() != hwnd or window_display(hwnd) != display):
                    raise OSError('截图时游戏或屏幕已切换，请重试；本次画面未保存。')
            context.setdefault('source','automatic' if automatic else 'manual')
            if not spool_path:
                saved=self.spool.preserve(image,session_id,context)
                self.processing_spool=saved['path']
            if context.get('source')=='manual' and context.get('observation_version')==14:
                from .automatic import SummaryDetector
                if SummaryDetector().detect(image):
                    context['summary_candidate_preserved']=True
                    self.events.put(('manual_summary_preserved',(session_id,context)))
            service=RecognitionService(self.engine,self.store.screenshots,self.store.directory,
                                       lambda kind,payload:self.events.put((kind,payload)))
            service.process(session_id,image,context,duplicates[0],live=path is None and not automatic)
        except Exception as error:
            logging.exception("Screenshot/OCR failed")
            self.events.put(("error", str(error)))

    def _ack_spool(self,failed=False):
        if self.processing_spool:
            if failed:self.failed_spool.add(self.processing_spool)
            else:self.spool.acknowledge(self.processing_spool)
            self.processing_spool=None

    def _recover_spool(self):
        if not self.engine_ready or self.busy or self.pending_auto:return
        for item in self.spool.pending():
            if item['path'] in self.failed_spool:continue
            if not self.store.db.execute('SELECT 1 FROM sessions WHERE id=?',(item['session_id'],)).fetchone():continue
            self._accept_auto(None,item['session_id'],item)
            break

    def repair_history(self,force=False):
        sid=self.selected_session
        if self.busy or not self.engine_ready or not sid or (sid in self.repair_seen and not force):
            return
        self.repair_seen.add(sid)
        captures=[self.store.capture(c['id']) for c in self.store.visible_captures(sid)]
        captures=[c for c in captures if Path(c['image']).is_file() and
                  (c['result'].get('recognition_version',0)<6 or c['status']=='pending' and
                   (c['result'].get('recognition_version',0)<RECOGNITION_VERSION or force))]
        if not captures:
            return
        self.busy=True
        self.status.set(f'正在后台优化历史中的 {len(captures)} 张原图，已有手动数据会保留…')
        self.executor.submit(self._repair,sid,captures)

    def _repair(self,sid,captures):
        results=[]; errors=[]
        for capture in captures:
            try:
                result=self.engine.recognize(capture['image'])
                # A failed/blank reread must not replace previously useful OCR.
                def quality(data):
                    return (sum(c.get('damage') is not None for c in data['cards']),
                            sum(c.get('score',0)>=.94 and c.get('name_score',0)>=.85 for c in data['cards']))
                old,new=quality(capture['result']),quality(result)
                if new[0]>=old[0] and new[1]>=old[1]:
                    results.append((capture['id'],result))
            except Exception as error:
                logging.exception('History OCR failed: capture %s',capture['id'])
                errors.append(str(error))
        self.events.put(('repair_result',(sid,results,errors)))

    def _poll(self):
        try:
            if time.monotonic()-self.display_check>=2:
                self.display_check=time.monotonic()
                self.refresh_displays()
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "ready":
                    self.engine_ready = True
                    text = "模型已就绪。开始记录后，打开总结页，自动捕获；也可用快捷键补录。" if self.automatic.get() else "模型已就绪。开始记录后，打开总结页并按截图快捷键。"
                    if self.store.active_session() and self.hotkey is None:
                        text += " 当前快捷键注册失败，请在设置中更换；仍可导入截图。"
                    self.status.set(text)
                    if self.store.storage_warning:
                        self.status.set(self.store.storage_warning)
                    self.configure_automatic()
                    self.repair_history()
                elif kind == "init_error":
                    self.status.set("本地模型加载失败：" + payload)
                elif kind=='repair_result':
                    sid,results,errors=payload
                    for cid,result in results:
                        self.store.update_recognition(cid,result)
                    _,saved=self.reconcile_session(sid)
                    self.busy=False
                    self.refresh()
                    self.status.set(f'历史优化完成：复读 {len(results)} 张，补算 {len(saved)} 局；手动修正保留。'+('部分原图未能重读，可手动检查。' if errors else ''))
                elif kind=='cleanup_result':
                    result,window=payload
                    if 'error' in result:
                        self.status.set('清理失败：'+result['error'])
                    else:
                        self.store.finish_cleanup(result)
                        if result['kind']=='history': self.selected_session=None
                        self.refresh()
                        self.status.set(f"清理完成：释放 {result['freed']/1024**2:.1f} MB，{len(result['failures'])} 个文件未能清理。")
                    self.busy=False
                    if window.window.winfo_exists(): window.completed(self.status.get())
                elif kind == "hotkey":
                    self.capture_screen()
                elif kind == 'auto_state':
                    watcher,sid,state = payload
                    if watcher is self.watcher:
                        self.auto_status.set('自动记录：' + state)
                elif kind == 'auto_frame':
                    self._accept_auto(*payload)
                elif kind == 'auto_phase':
                    watcher,sid,phase = payload
                    if watcher is self.watcher:
                        self.store.set_setting('automatic_phase_'+str(sid),phase)
                elif kind=='auto_lifecycle':
                    watcher,sid,event=payload
                    if watcher is self.watcher:
                        self.store.matches.begin(sid,event)
                elif kind=='manual_summary_preserved':
                    sid,context=payload
                    if self.watcher and self.store.active_session() and self.store.active_session()['id']==sid:
                        self.watcher.preserve_manual_summary(context)
                        self.store.set_setting('automatic_phase_'+str(sid),self.watcher.phase())
                elif kind == 'auto_probe':
                    watcher,sid,request = payload
                    if watcher is self.watcher and self.engine_ready:
                        self.page_executor.submit(self._probe_gameplay,watcher,sid,request)
                    else:
                        watcher.submit_gameplay(request['token'],None)
                elif kind == 'auto_probe_result':
                    watcher,sid,token,evidence = payload
                    if watcher is self.watcher:
                        watcher.submit_gameplay(token,evidence)
                elif kind == 'auto_rejected':
                    self.busy = False
                    self._ack_spool()
                    sid,epoch = payload
                    active = self.store.active_session()
                    if self.watcher and active and active['id']==sid:
                        self.watcher.reject_candidate(epoch)
                    self.status.set('候选未通过文字校验，原图已保留（最近8张）；继续检测，可从本地候选目录导入补录。')
                elif kind=='duplicate_evidence':
                    sid,cid,context=payload
                    if self.watcher and self.store.active_session() and self.store.active_session()['id']==sid:
                        from .automatic import is_summary_result
                        if is_summary_result(self.store.capture(cid)['result']):self.watcher.accept_summary(context)
                        else:self.watcher.reject_candidate(context.get('match_key'))
                elif kind == "saved":
                    self.status.set(f"截图已保存（{payload['elapsed']:.2f}秒），可以离开总结页；正在后台识别…")
                    if payload["live"] and self.store.setting("sound", True):
                        import winsound
                        winsound.MessageBeep(winsound.MB_OK)
                elif kind == 'duplicate':
                    self.show_duplicate(*payload)
                elif kind == "result":
                    sid, path, digest, result = payload
                    duplicate = self.store.find_duplicate(sid, digest, result)
                    if duplicate:
                        # Only remove this job's new file; preserve all original images.
                        candidate = Path(path).resolve()
                        if candidate.parent == self.store.screenshots.resolve() and candidate != Path(self.store.capture(duplicate)['image']).resolve():
                            candidate.unlink(missing_ok=True)
                        self.show_duplicate(sid, duplicate, '相同截图或本局已捕获')
                        continue
                    required_count = self.store.session_mode(sid)
                    while len(result["cards"]) < required_count:
                        result["cards"].append({"card": len(result["cards"]), "name": "", "name_score": 0,
                                                "damage": None, "kills": None, "assists": None, "score": 0})
                    if len(result["cards"]) > required_count and all(not c["name"] and c["damage"] is None for c in result["cards"]):
                        result["cards"] = result["cards"][:required_count]
                    capture_id = self.store.add_capture(sid, path, digest, result)
                    from .automatic import is_summary_result
                    if self.watcher and is_summary_result(result) and self.store.active_session() and self.store.active_session()['id']==sid:
                        self.watcher.accept_summary(result)
                    self._ack_spool()
                    state, saved = self.reconcile_session(sid)
                    auto_saved = capture_id in saved
                    self.busy = False
                    self.selected_session = sid
                    self.refresh()
                    observation_id=capture_id
                    capture_id=self.store.canonical_id(capture_id)
                    self.history.selection_set(str(capture_id))
                    self.history.see(str(capture_id))
                    elapsed = result.get("elapsed", 0)
                    self.status.set(f'已归入记录 #{capture_id}，同局副本已收起，未重复累计。' if observation_id!=capture_id else
                                    f"玩家已确定，本次计入 {len(saved)} 局（识别{elapsed:.2f}秒）。" if auto_saved else
                                    f"截图 #{capture_id} 已识别（{elapsed:.2f}秒）。" + (pending_reason(self.store.capture(capture_id)['result'],state['resolved'],False) or state['message']))
                elif kind == "error":
                    self.busy = False
                    self._ack_spool(failed=True)
                    self.status.set("截图失败：" + payload)
        except queue.Empty:
            pass
        except Exception as error:
            self.busy = False
            self._ack_spool(failed=True)
            logging.exception("Result handling failed")
            self.status.set("处理失败：" + str(error))
        if not self.busy:
            if self.auto_job:
                self.auto_job.available.set()
                self.auto_job = None
            self._next_auto()
            self._recover_spool()
        if not self.busy and not self.pending_auto:
            if self.end_requested:
                self._end_now()
            if self.close_requested:
                self._close_now()
                return
        self.poll_id = self.root.after(80, self._poll)

    def show_duplicate(self, session_id, capture_id, reason, live=False):
        self._ack_spool()
        capture_id=self.store.canonical_id(capture_id)
        self.busy = False
        self.selected_session = session_id
        self.refresh()
        self.history.selection_set(str(capture_id))
        self.history.see(str(capture_id))
        status = self.store.capture(capture_id)['status']
        text = {'confirmed': '已记录，保留原有修正数据', 'pending': '待确认，请查看原记录',
                'discarded': '已忽略，可在“查看 / 修正”中恢复'}[status]
        self.status.set(f'{reason}已存在于截图 #{capture_id}：{text}；未重复计入。可以继续游戏。')
        if live and self.store.setting('sound', True):
            import winsound
            winsound.MessageBeep(winsound.MB_OK)

    def review_selected(self):
        selection = self.history.selection()
        if not selection:
            return
        capture_id = int(selection[0])
        capture = self.store.capture(capture_id)
        Review(self, capture)

    def settings(self):
        Settings(self)

    def close(self):
        self.close_requested = True
        self._stop_automatic()
        if self.busy or self.pending_auto:
            self.status.set('正在保存已捕获的截图，处理完成后关闭程序…')
            return
        self._close_now()

    def _close_now(self):
        if self.hotkey:
            self.hotkey.stop()
        self.root.after_cancel(self.poll_id)
        self.executor.shutdown(wait=True)
        self.page_executor.shutdown(wait=True)
        self.store.close()
        self.root.destroy()


class Review:
    def __init__(self, app, capture):
        self.app, self.capture = app, capture
        self.window = tk.Toplevel(app.root)
        self.window.title(f"截图 #{capture['id']} · 确认玩家与数据")
        self.window.geometry("1100x840")
        self.window.configure(bg=BG)
        frame = ScrollBody(self.window, padding=20).content
        self.required = app.store.session_player_ids(capture["session_id"])
        self.count = app.store.session_mode(capture['session_id'])
        section(frame, f"确认本局 {self.count} 位玩家的数据")
        self.match_choices={'保持当前比赛归属':capture.get('match_id'),'确认是新的一局':None}
        for match in app.store.matches.list(capture['session_id']):
            if match['id']!=capture.get('match_id'):
                self.match_choices[f"归入已有比赛 #{match['id']} · {match['created'][11:19]}"]=match['id']
        unknown=capture['result'].get('attribution')=='needs_review'
        if unknown:self.match_choices.pop('保持当前比赛归属')
        self.match_choice=tk.StringVar(value='请选择比赛归属' if unknown else '保持当前比赛归属')
        ttk.Label(frame,text='比赛归属：不同比赛数字可以相同；同一比赛的多张原图只计一次。',
                  style='Muted.TLabel',wraplength=1000).pack(anchor='w',pady=(8,4))
        ttk.Combobox(frame,textvariable=self.match_choice,values=list(self.match_choices),
                     state='readonly',width=54).pack(anchor='w',pady=(0,8))
        ttk.Label(frame, text="按名字选择参与玩家；路人选择“不统计”。空白数据请对照原图填写。",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 10))
        if Path(capture['image']).is_file():
            image = open_image(capture["image"])
            image.thumbnail((1000, 410))
            self.photo = ImageTk.PhotoImage(image)
            tk.Label(frame, image=self.photo, bg=BG).pack()
        else:
            ttk.Label(frame,text='原图已清理或不在原路径；已保存的战绩仍可查看和修正。',foreground=AMBER).pack(pady=20)
        self.players = app.store.session_players(capture['session_id']) if self.required else app.store.players()
        self.options = [f"{p['id']} · {p['name']}" for p in self.players]
        self.option_ids = {label: p['id'] for label, p in zip(self.options, self.players)}
        self.ignore = "不统计（路人 / 空卡片）"
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=12)
        for col, heading in enumerate(("卡片", "画面昵称（OCR）", "对应玩家", "伤害", "击杀", "助攻")):
            ttk.Label(grid, text=heading, style="Muted.TLabel").grid(row=0, column=col, padx=8, pady=4, sticky="w")
        self.rows = []
        previous = {row["card"]: row for row in capture["stats"]}
        for index, card in enumerate(capture["result"]["cards"]):
            row = previous.get(index, card)
            ttk.Label(grid, text=f"第{index+1}张").grid(row=index+1, column=0, padx=8, pady=7)
            ttk.Label(grid, text=card["name"] or "（未识别）", width=24, wraplength=280).grid(row=index+1, column=1, padx=8, sticky="w")
            pid = row.get("player_id") or app.store.match_player(card["name"], capture['session_id'])
            selected = next((label for label, player_id in self.option_ids.items() if player_id == pid), self.ignore)
            player = tk.StringVar(value=selected)
            ttk.Combobox(grid, textvariable=player, values=[self.ignore, *self.options], state="readonly", width=24).grid(
                row=index+1, column=2, padx=8)
            values = {}
            for col, field in enumerate(("damage", "kills", "assists"), 3):
                values[field] = tk.StringVar(value=str(row[field]) if row.get(field) is not None else "")
                ttk.Entry(grid, textvariable=values[field], width=9).grid(row=index+1, column=col, padx=8)
            self.rows.append((player, values))
        self.remember = tk.BooleanVar(value=True)
        team=app.store.matches.team_kills(capture['match_id'])
        self.team_automatic=tk.BooleanVar(value=not team['team_kills_reviewed'])
        self.team_was_automatic=not team['team_kills_reviewed']
        self.team_value=tk.StringVar(value=str(team['team_kills']) if team['team_kills'] is not None else '')
        teamrow=ttk.Frame(frame);teamrow.pack(fill='x',pady=(2,8))
        ttk.Label(teamrow,text='完整小队总击杀：').pack(side='left')
        self.team_entry=ttk.Entry(teamrow,textvariable=self.team_value,width=9)
        self.team_entry.pack(side='left',padx=(0,10))
        def team_mode():
            self.team_entry.configure(state='disabled' if self.team_automatic.get() else 'normal')
        ttk.Checkbutton(teamrow,text='自动读取（取消后可填写；未知留空）',variable=self.team_automatic,
                        command=team_mode).pack(side='left')
        team_mode()
        self.app.wrapped_label(frame,text='参团率＝个人（击杀＋助攻）÷完整小队总击杀；累计采用总和相除。分母为零或缺失时显示“—”。',
                               style='Muted.TLabel').pack(fill='x',pady=(0,8))
        ttk.Checkbutton(frame, text="记住本次画面昵称与玩家的对应关系（下次自动匹配）", variable=self.remember).pack(anchor="w")
        warning = "；".join(capture["result"].get("warnings", []))
        if capture["duplicate_of"]:
            warning = f"与已有截图 #{capture['duplicate_of']} 完全相同，保存时需确认属于新的一局。 " + warning
        ttk.Label(frame, text=warning, foreground=AMBER, wraplength=1000).pack(anchor="w", pady=7)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", side="bottom", pady=(10, 0))
        self.save_button = ttk.Button(actions, text="保存并更新统计", style="Accent.TButton", command=self.save)
        self.save_button.pack(side="right")
        if capture['status'] == 'discarded':
            self.save_button['state'] = 'disabled'
            ttk.Button(actions, text='恢复为待确认', command=self.restore).pack(side='right', padx=8)
        ttk.Button(actions, text="忽略这张截图", command=self.discard).pack(side="left")
        ttk.Button(actions, text="打开原图", command=lambda: os.startfile(capture["image"]),
                   state='normal' if Path(capture['image']).is_file() else 'disabled').pack(side="left", padx=8)
        fit_window(self.window, 1100, 840)

    def save(self):
        try:
            rows = []
            for index, (player, values) in enumerate(self.rows):
                if player.get() == self.ignore:
                    continue
                if player.get() not in self.options:
                    raise ValueError("请给参与统计的卡片选择对应玩家。")
                fields = {}
                for field, value in values.items():
                    if not value.get().strip().isascii() or not value.get().strip().isdigit():
                        raise ValueError("请填写伤害、击杀、助攻的非负整数。")
                    fields[field] = int(value.get().strip())
                    if fields[field]>999999:
                        raise ValueError('伤害、击杀、助攻最大为999999。')
                rows.append({"card": index, "player_id": self.option_ids[player.get()], **fields})
            ids = {r['player_id'] for r in rows}
            if len(rows) != self.count or len(ids) != self.count or (self.required and ids != set(self.required)):
                raise ValueError(f"请选择本次统计的 {self.count} 位不同玩家，每人一张卡片；路人无需填写。")
            team_kills='unchanged' if self.team_was_automatic else 'automatic'
            if not self.team_automatic.get():
                text=self.team_value.get().strip()
                if text and (not text.isascii() or not text.isdigit() or int(text)>999999):
                    raise ValueError('小队总击杀须为非负整数（最大999999），未知时留空。')
                team_kills=int(text) if text else None
            current=self.app.store.capture(self.capture['id'])
            if self.match_choice.get() not in self.match_choices:
                raise ValueError('未观察到开局，请先选择“确认是新的一局”或归入已有比赛。')
            if self.match_choice.get()!='保持当前比赛归属':
                self.app.store.assign_match(self.capture['id'],self.match_choices[self.match_choice.get()])
                current=self.app.store.capture(self.capture['id'])
            duplicate = current["duplicate_of"] if current["status"] != "confirmed" else None
            proven=current['merged_into']
            if not proven and duplicate and not messagebox.askyesno(
                "确认是否新的一局", f"与记录 #{duplicate} 的图像像素完全相同。\n确定这是不同的一局并计入统计吗？",
                parent=self.window):
                return
            if not proven and duplicate:
                current['result']['keep_separate']=True
                self.app.store.update_recognition(current['id'],current['result'])
            self.app.store.confirm(self.capture["id"], rows, self.remember.get(), allow_duplicate=True, manual=True,team_kills=team_kills)
            if self.app.watcher and self.app.store.active_session() and self.app.store.active_session()['id']==self.capture['session_id']:
                self.app.watcher.accept_summary(self.capture['result'])
            _, saved = self.app.reconcile_session(self.capture['session_id'])
            self.app.refresh()
            self.app.status.set(f"已保存，累计统计已更新；另补算 {len(saved)} 局。")
            self.window.destroy()
        except ValueError as error:
            messagebox.showerror("请检查填写内容", str(error), parent=self.window)

    def discard(self):
        if messagebox.askyesno("忽略截图", "将这张截图移出统计，原图仍保留。确定吗？", parent=self.window):
            self.app.store.discard(self.capture["id"])
            self.app.reconcile_session(self.capture['session_id'])
            self.app.refresh()
            self.window.destroy()

    def restore(self):
        self.app.store.restore(self.capture['id'])
        self.capture['status'] = 'pending'
        self.save_button['state'] = 'normal'
        self.app.refresh()
        self.app.status.set(f"截图 #{self.capture['id']} 已恢复为待确认，请核对后保存。")


class Settings:
    def __init__(self, app):
        self.app = app
        app.refresh_displays()
        self.window = tk.Toplevel(app.root)
        self.window.title("玩家与截图设置")
        self.window.geometry("750x580")
        self.window.configure(bg=BG)
        frame = ScrollBody(self.window, padding=24).content
        section(frame, "玩家身份绑定")
        ttk.Label(frame, text="显示名称可以写完整中文。匹配昵称填写 OCR 实际读出的文字；多个昵称用换行分隔。",
                  style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(8, 12))
        grid = ttk.Frame(frame)
        grid.pack(fill="x")
        ttk.Label(grid, text='完整显示名称', style='Muted.TLabel').grid(row=0,column=1,sticky='w',pady=4)
        ttk.Label(grid, text='画面昵称（每行一个）', style='Muted.TLabel').grid(row=0,column=2,sticky='w',pady=4)
        self.players = []
        active = app.store.active_session()
        local_names = {p['id']: p['name'] for p in app.store.session_players(active['id'])} if active else {}
        for index, player in enumerate(app.store.players()):
            ttk.Label(grid, text=f"玩家 {player['id']}").grid(row=index+1, column=0, padx=(0, 12), pady=8)
            name = tk.StringVar(value=local_names.get(player['id'], player['name']))
            ttk.Entry(grid, textvariable=name, width=22).grid(row=index+1, column=1, padx=(0, 12))
            aliases = tk.Text(grid, height=2, width=34, bg=FIELD, fg=TEXT,
                              insertbackground=TEXT, font=("Microsoft YaHei UI", 10), relief="flat")
            aliases.insert("1.0", "\n".join(app.store.aliases(player["id"])))
            aliases.grid(row=index+1, column=2, pady=8)
            self.players.append((player["id"], name, aliases))
        form = ttk.Frame(frame)
        form.pack(fill="x", pady=12)
        ttk.Label(form, text="截图快捷键").grid(row=0, column=0, sticky="w", pady=6)
        self.hotkey = tk.StringVar(value=app.store.setting("hotkey", "Ctrl+End"))
        ttk.Entry(form, textvariable=self.hotkey, width=25).grid(row=0, column=1, padx=16, sticky="w")
        ttk.Button(form, text="检查占用", command=self.check_hotkey).grid(row=0, column=2)
        ttk.Label(frame, text="可直接修改组合键，如 Ctrl+End、Ctrl+Alt+F8、Shift+Home。开始记录后才全局生效；结束后释放。\nCtrl+End 也用于跳到文末，请按自己的游戏和软件键位选择。",
                  style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=6)
        ttk.Label(form, text="游戏所在显示器").grid(row=1, column=0, sticky="w", pady=6)
        self.display_choices=list(app.displays)
        displays = ['自动跟随 Apex（推荐）'] + [f"手动：显示器 {i+1} · {d['bbox'][2]-d['bbox'][0]}×{d['bbox'][3]-d['bbox'][1]}"
                    + ("（主屏）" if d["primary"] else "") for i, d in enumerate(app.displays)]
        self.monitor = ttk.Combobox(form, values=displays, state="readonly", width=34)
        self.monitor.grid(row=1, column=1, columnspan=2, padx=16, sticky="w")
        if app.store.setting('monitor_mode', 'auto') == 'auto':
            self.monitor.current(0)
        elif app.displays:
            selected=app.selected_display()
            if selected:self.monitor.current(app.displays.index(selected)+1)
            else:self.monitor.set('原显示器未连接，请重新选择')
        ttk.Label(frame, text='窗口捕获绑定 Apex，切换其他应用时仍可检测；最小化或游戏停止渲染时会暂停。\n前台屏幕模式保留自动跟随／手选屏幕。快捷键补录仍需游戏在前台。',
                  style='Muted.TLabel', wraplength=680).pack(anchor='w', pady=6)
        self.capture_mode=tk.StringVar(value=app.store.setting('capture_mode','window'))
        ttk.Radiobutton(frame,text='捕获 Apex 窗口（推荐，支持被其他窗口遮挡）',variable=self.capture_mode,value='window').pack(anchor='w',pady=3)
        ttk.Radiobutton(frame,text='前台屏幕捕获（兼容模式，使用上方显示器设置）',variable=self.capture_mode,value='screen').pack(anchor='w',pady=3)
        self.hide_capture_border=tk.BooleanVar(value=app.store.setting('hide_capture_border',True))
        ttk.Checkbutton(frame,text='隐藏窗口捕获提示边框（系统支持时）',variable=self.hide_capture_border).pack(anchor='w',pady=5)
        ttk.Label(frame,text='仅对本程序的窗口捕获有效；Windows未允许、旧系统不支持或其他程序仍要求边框时，边框可能保留。',
                  style='Muted.TLabel',wraplength=680).pack(anchor='w',pady=3)
        self.auto_save = tk.BooleanVar(value=app.store.setting("auto_save", True))
        self.automatic = tk.BooleanVar(value=app.automatic.get())
        self.sound = tk.BooleanVar(value=app.store.setting("sound", True))
        ttk.Checkbutton(frame, text='自动捕获总结页（无需按截图键）', variable=self.automatic).pack(anchor='w', pady=5)
        ttk.Checkbutton(frame, text="身份唯一、数字识别完整且置信度达标时，自动计入统计", variable=self.auto_save).pack(anchor="w", pady=5)
        ttk.Checkbutton(frame, text="手动截图保存成功时播放提示音（自动模式静默）", variable=self.sound).pack(anchor="w", pady=5)
        ttk.Label(frame, text="昵称绑定相同会进入待确认。首次识别后，也可以直接在修正窗口选择玩家并记住对应关系。",
                  style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=8)
        section(frame,'照片存储')
        self.image_directory=tk.StringVar(value=str(app.store.screenshots))
        ttk.Label(frame,textvariable=self.image_directory,style='Muted.TLabel',wraplength=680).pack(anchor='w',fill='x',pady=8)
        storage=ttk.Frame(frame); storage.pack(fill='x',pady=6)
        ttk.Button(storage,text='选择照片位置',command=self.choose_directory).pack(side='left')
        ttk.Button(storage,text='恢复默认位置',command=lambda:self.image_directory.set(str((app.store.directory/'screenshots').absolute()))).pack(side='left',padx=8)
        ttk.Button(storage,text='清理照片与数据',command=lambda:DataManagement(app)).pack(side='left',padx=8)
        ttk.Label(frame,text='新位置仅用于今后的截图，已有原图仍从原路径读取。选择位置时建立 ApexTrackerScreenshots 专用子目录。',
                  style='Muted.TLabel',wraplength=680).pack(anchor='w',pady=6)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="保存设置", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(actions, text="打开数据目录", command=lambda: os.startfile(app.store.directory)).pack(side="left")
        fit_window(self.window, 750, 640)

    def check_hotkey(self):
        candidate = None
        try:
            value = self.hotkey.get().strip()
            flags, vk = parse_hotkey(value)
            if self.app.hotkey and (flags, vk) == parse_hotkey(self.app.hotkey.value):
                text = "当前由本程序注册，可以使用。"
            else:
                candidate = Hotkey(value, lambda: None)
                candidate.start()
                candidate.stop()

                text = "当前可以注册。此检查不检测游戏或其他软件内部的按键绑定。"
            messagebox.showinfo("快捷键检查", text, parent=self.window)
        except (ValueError, RuntimeError) as error:
            messagebox.showerror("快捷键不可用", str(error), parent=self.window)
        finally:
            if candidate:
                candidate.stop()

    def choose_directory(self):
        from .media import MANAGED_NAME
        selected=filedialog.askdirectory(parent=self.window,title='选择照片存储位置（建立专用子目录）')
        if selected:
            base=Path(selected)
            self.image_directory.set(str(base if base.name==MANAGED_NAME else base/MANAGED_NAME))

    def save(self):
        try:
            index=self.monitor.current()
            if not 0<=index<=len(self.display_choices):raise ValueError('请选择自动跟随或当前连接的游戏显示器。')
            monitor_mode = 'auto' if index == 0 else 'manual'
            old_mode = self.app.store.setting('monitor_mode', 'auto')
            old_capture_mode=self.app.store.setting('capture_mode','window')
            old_hide_border=self.app.store.setting('hide_capture_border',True)
            old_device=self.app.store.setting('monitor_device')
            latest=monitors()
            device=old_device
            selected=None
            if monitor_mode == 'manual':
                choice=self.display_choices[index-1]
                device=monitor_identity(choice)
                selected=next((d for d in latest if monitor_identity(d)==device),None)
                if selected is None:raise ValueError('这台显示器已断开，请重新打开设置选择。')
            value = self.hotkey.get().strip()
            parse_hotkey(value)
            players = [(pid, name.get().strip()) for pid, name, _ in self.players]
            if any(not name for _, name in players):
                raise ValueError("三个显示名称都需要填写。")
            requested_directory=None
            if Path(self.image_directory.get()).absolute()!=self.app.store.screenshots.absolute():
                if self.app.busy or self.app.pending_auto:
                    raise ValueError('请等待当前截图或历史优化结束后再改变照片位置。')
                from .media import prepare_directory
                requested_directory=prepare_directory(self.image_directory.get())
            old_value = self.app.store.setting("hotkey", "Ctrl+End")
            if value != old_value or self.app.hotkey is None:
                if self.app.hotkey:
                    self.app.hotkey.stop()
                    self.app.hotkey = None
                candidate = Hotkey(value, lambda: self.app.events.put(("hotkey", None)))
                try:
                    candidate.start()
                except Exception:
                    if self.app.store.active_session():
                        self.app._register(old_value)
                    raise
                self.app.hotkey = candidate
                if not self.app.store.active_session():
                    candidate.stop()
                    self.app.hotkey = None
            if requested_directory: self.app.store.set_image_directory(requested_directory)
            self.app.store.save_players(players)
            for pid, _, aliases in self.players:
                self.app.store.replace_aliases(pid, aliases.get("1.0", "end").splitlines())
            self.app.store.set_setting("hotkey", value)
            self.app.displays=latest
            self.app.store.set_setting('monitor_mode',monitor_mode)
            if selected:
                self.app.store.set_setting("monitor",latest.index(selected))
                self.app.store.set_setting('monitor_device',device)
            self.app.store.set_setting("auto_save", self.auto_save.get())
            self.app.store.set_setting("sound", self.sound.get())
            self.app.store.set_setting('capture_mode',self.capture_mode.get())
            self.app.store.set_setting('hide_capture_border',self.hide_capture_border.get())
            changed = (old_hide_border!=self.hide_capture_border.get() or old_capture_mode!=self.capture_mode.get() or self.app.automatic.get() != self.automatic.get() or old_mode!=monitor_mode or
                       monitor_mode == 'manual' and old_device!=device or
                       self.app.watcher is None and self.automatic.get() and self.app.store.active_session() or
                       monitor_mode == 'manual' and self.app.watcher is not None and self.app.watcher.bbox !=
                       selected['bbox'])
            self.app.automatic.set(self.automatic.get())
            self.app.store.set_setting('automatic',self.automatic.get())
            if changed:
                self.app.configure_automatic()
            if self.app.selected_session:
                self.app.reconcile_session(self.app.selected_session)
            self.app.refresh()
            fit_window(self.app.root, self.app.root.winfo_width(), self.app.root.winfo_height(), logical=False)
            self.app.status.set("设置已保存。")
            self.window.destroy()
        except (ValueError, RuntimeError, OSError) as error:
            messagebox.showerror("设置未保存", str(error), parent=self.window)


class DataManagement:
    def __init__(self,app):
        self.app=app
        if app.busy or app.store.active_session():
            messagebox.showinfo('请先结束记录','请先结束记录并等待截图和历史优化完成，再清理数据。',parent=app.root)
            return
        self.window=tk.Toplevel(app.root)
        self.window.title('照片与数据清理')
        self.window.configure(bg=BG)
        frame=ScrollBody(self.window,padding=24).content
        section(frame,'选择清理范围')
        self.kind=tk.StringVar(value='duplicates')
        for text,kind in [('清理重复照片与日志缓存（保留战绩和每局主图）','duplicates'),
                          ('清理全部照片与日志缓存（保留战绩与设置）','photos'),
                          ('清空全部战绩、照片与日志（保留玩家与设置）','history')]:
            ttk.Radiobutton(frame,text=text,variable=self.kind,value=kind,command=self.preview).pack(anchor='w',pady=8)
        self.summary=tk.StringVar()
        ttk.Label(frame,textvariable=self.summary,style='Muted.TLabel',wraplength=700).pack(anchor='w',pady=16)
        ttk.Label(frame,text='只删除程序生成的照片和日志，不删除所选文件夹中的其他文件或 OCR 模型。\n清理照片后仍能查看保存的数字，但无法再打开原图。删除不可撤销。',
                  wraplength=700,foreground=AMBER).pack(anchor='w',pady=10)
        self.clean_button=ttk.Button(frame,text='执行清理',style='Accent.TButton',command=self.clean)
        self.clean_button.pack(anchor='e',pady=12)
        self.preview()
        fit_window(self.window,800,550)

    def preview(self):
        self.plan=self.app.store.cleanup_plan(self.kind.get())
        p=self.plan
        self.summary.set(f"可清理 {len(p['photos'])} 张照片和日志缓存，预计释放 {p['bytes']/1024**2:.1f} MB。\n"
                         f"其中 {p['pending']} 张仍待确认。"+
                         (f"将删除 {p['records']} 条可见战绩；玩家和设置保留。" if self.kind.get()=='history' else '已保存战绩、玩家和设置保留。')+
                         (f"\n{p['skipped']} 个非程序管理的文件将保留。" if p['skipped'] else ''))

    def clean(self):
        if self.app.busy or self.app.store.active_session():
            messagebox.showinfo('暂不能清理','请结束记录并等待处理完成。',parent=self.window)
            return
        self.preview()
        if not messagebox.askyesno('确认清理',self.summary.get()+'\n\n照片删除后不能恢复。继续吗？',parent=self.window):
            return
        self.app.busy=True
        self.clean_button['state']='disabled'
        self.app.status.set('正在后台清理所选照片和日志…')
        plan=self.plan
        def work():
            from .media import execute_cleanup
            try: result=execute_cleanup(plan)
            except Exception as error: result={'error':str(error)}
            self.app.events.put(('cleanup_result',(result,self)))
        self.app.executor.submit(work)

    def completed(self,text):
        self.clean_button['state']='normal'
        self.preview()
        self.summary.set(text+'\n'+self.summary.get())
