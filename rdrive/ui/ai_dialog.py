"""Cửa sổ AI Quản Lý — Liên kết Local AI (Ollama / LM Studio) hoặc Cloud AI (Gemini / OpenAI) để quản lý & sắp xếp thư mục."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import ai_bridge
from ..core.config import AIConfig, AppConfig
from . import icons


class _AIWorker(QThread):
    done = Signal(str)
    err = Signal(str)

    def __init__(self, action: str, cfg: AIConfig, prompt: str = "", context: str = "") -> None:
        super().__init__()
        self.action = action
        self.cfg = cfg
        self.prompt = prompt
        self.context = context

    def run(self) -> None:
        try:
            if self.action == "test":
                self.done.emit(ai_bridge.test_connection(self.cfg))
            elif self.action == "models":
                models = ai_bridge.list_local_models(self.cfg)
                self.done.emit("\n".join(models))
            else:
                self.done.emit(ai_bridge.ask_ai(self.cfg, self.prompt, self.context))
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc))


class AIDialog(QDialog):
    """Cấu hình & Trợ lý AI (Local AI hoặc Cloud AI) gắn với ngữ cảnh thư mục hiện tại của Rdrive."""

    def __init__(self, parent, config: AppConfig, folder_context: str = "", full_context: str = "") -> None:
        super().__init__(parent)
        self.config = config
        self.folder_context = folder_context
        self.full_context = full_context or folder_context
        self._live_workers: list[QThread] = []
        self._worker: _AIWorker | None = None

        self.setWindowTitle("AI Quản Lý — Local AI & Cloud AI (Tiết kiệm Token)")
        self.setWindowIcon(icons.ribbon_icon("ai", 24))
        self.resize(740, 560)

        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        ai_cfg = config.get_ai()

        # --- Tab 1: Trợ lý AI phân tích & đề xuất sắp xếp ---
        chat_tab = QWidget()
        clay = QVBoxLayout(chat_tab)

        self.lbl_ai_state = QLabel("")
        self.lbl_ai_state.setWordWrap(True)
        self.lbl_ai_state.setStyleSheet(
            "background: #1a202c; border: 1px solid #2d3748; border-radius: 6px; "
            "padding: 6px 10px; color: #cbd5e1; font-size: 8.8pt;"
        )
        clay.addWidget(self.lbl_ai_state)

        tok_bar = QHBoxLayout()
        self.chk_send_ctx = QCheckBox("Gửi kèm ngữ cảnh 2 khung")
        self.chk_send_ctx.setChecked(True)
        self.chk_compact = QCheckBox("Nén gom nhóm file (tiết kiệm ~80% token)")
        self.chk_compact.setChecked(ai_cfg.compact_mode)
        self.lbl_tok = QLabel("")
        self.lbl_tok.setObjectName("BadgeGood")
        tok_bar.addWidget(self.chk_send_ctx)
        tok_bar.addWidget(self.chk_compact)
        tok_bar.addStretch(1)
        tok_bar.addWidget(self.lbl_tok)
        clay.addLayout(tok_bar)
        self.chk_send_ctx.toggled.connect(self._update_token_badge)
        self.chk_compact.toggled.connect(self._update_token_badge)

        self.chat_out = QPlainTextEdit()
        self.chat_out.setReadOnly(True)
        self.chat_out.setPlaceholderText(
            "Ví dụ câu hỏi:\n"
            "• Đề xuất cách sắp xếp các file quay video & audio trong thư mục hiện tại theo ngày/tháng\n"
            "• Kiểm tra xem file nào chưa có trên Google Drive khung phải\n"
            "• Tạo quy tắc tự động phân loại file dự án Premiere"
        )
        clay.addWidget(self.chat_out, 1)

        quick = QHBoxLayout()
        for label, q in [
            ("Phân tích thư mục hiện tại", "Hãy phân tích danh sách file ở 2 khung hiện tại và đề xuất cách sắp xếp gọn gàng."),
            ("Gợi ý cấu trúc Backup", "Đề xuất cấu trúc thư mục backup Google Drive an toàn cho các file trong khung hiện tại."),
        ]:
            b = QPushButton(label)
            b.setStyleSheet("padding: 4px 8px; font-size: 8.5pt;")
            b.clicked.connect(lambda _=False, q=q: self._send_prompt(q))
            quick.addWidget(b)
        quick.addStretch(1)
        clay.addLayout(quick)

        in_row = QHBoxLayout()
        self.ed_prompt = QLineEdit()
        self.ed_prompt.setPlaceholderText("Nhập yêu cầu cho AI quản lý...")
        self.ed_prompt.returnPressed.connect(lambda: self._send_prompt(self.ed_prompt.text()))
        self.btn_send = QPushButton("Gửi AI")
        self.btn_send.setObjectName("PrimaryBtn")
        self.btn_send.clicked.connect(lambda: self._send_prompt(self.ed_prompt.text()))
        in_row.addWidget(self.ed_prompt, 1)
        in_row.addWidget(self.btn_send)
        clay.addLayout(in_row)

        # --- Tab 2: Cấu hình Local AI / Cloud AI ---
        cfg_tab = QWidget()
        glay = QVBoxLayout(cfg_tab)

        mode_row = QHBoxLayout()
        self.rb_local = QRadioButton("Dùng Local AI (Ollama / LM Studio — miễn phí, chạy trên máy)")
        self.rb_cloud = QRadioButton("Dùng Cloud AI (Gemini / OpenAI / API tương thích)")
        self.rb_local.setChecked(ai_cfg.mode != "cloud")
        self.rb_cloud.setChecked(ai_cfg.mode == "cloud")
        mode_row.addWidget(self.rb_local)
        mode_row.addWidget(self.rb_cloud)
        glay.addLayout(mode_row)

        # Box Local AI
        grp_local = QGroupBox("Cấu hình Local AI")
        fl = QFormLayout(grp_local)
        self.cb_local_prov = QComboBox()
        self.cb_local_prov.addItem("Ollama (mặc định http://127.0.0.1:11434)", "ollama")
        self.cb_local_prov.addItem("LM Studio / OpenAI-compatible (http://127.0.0.1:1234/v1)", "lmstudio")
        idx = self.cb_local_prov.findData(ai_cfg.local_provider)
        self.cb_local_prov.setCurrentIndex(max(0, idx))
        self.ed_local_url = QLineEdit(ai_cfg.local_url)
        self.ed_local_model = QLineEdit(ai_cfg.local_model)
        self.btn_fetch_models = QPushButton("Lấy danh sách model")
        self.btn_fetch_models.clicked.connect(self._fetch_models)
        mrow = QHBoxLayout()
        mrow.addWidget(self.ed_local_model, 1)
        mrow.addWidget(self.btn_fetch_models)
        fl.addRow("Nền tảng:", self.cb_local_prov)
        fl.addRow("Địa chỉ URL:", self.ed_local_url)
        fl.addRow("Tên Model:", mrow)
        glay.addWidget(grp_local)

        # Box Cloud AI
        grp_cloud = QGroupBox("Cấu hình Cloud AI")
        fc = QFormLayout(grp_cloud)
        self.cb_cloud_prov = QComboBox()
        self.cb_cloud_prov.addItem("Google Gemini API", "gemini")
        self.cb_cloud_prov.addItem("OpenAI API", "openai")
        self.cb_cloud_prov.addItem("Custom OpenAI-compatible Endpoint", "custom")
        cidx = self.cb_cloud_prov.findData(ai_cfg.cloud_provider)
        self.cb_cloud_prov.setCurrentIndex(max(0, cidx))
        self.ed_cloud_url = QLineEdit(ai_cfg.cloud_url)
        self.ed_cloud_url.setPlaceholderText("Để trống nếu dùng mặc định (Gemini / OpenAI)")
        self.ed_cloud_model = QLineEdit(ai_cfg.cloud_model)
        self.ed_cloud_key = QLineEdit(ai_cfg.cloud_api_key)
        self.ed_cloud_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.ed_cloud_key.setPlaceholderText("Dán API Key của bạn vào đây")
        fc.addRow("Nhà cung cấp:", self.cb_cloud_prov)
        fc.addRow("Endpoint tùy chỉnh:", self.ed_cloud_url)
        fc.addRow("Model:", self.ed_cloud_model)
        fc.addRow("API Key:", self.ed_cloud_key)
        glay.addWidget(grp_cloud)

        # Giới hạn token đầu ra
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Giới hạn token phản hồi tối đa:"))
        self.sp_max_tok = QSpinBox()
        self.sp_max_tok.setRange(64, 4096)
        self.sp_max_tok.setSingleStep(64)
        self.sp_max_tok.setValue(max(64, int(ai_cfg.max_output_tokens or 512)))
        opt_row.addWidget(self.sp_max_tok)
        opt_row.addStretch(1)
        glay.addLayout(opt_row)

        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        self.lbl_test.setObjectName("Muted")
        glay.addWidget(self.lbl_test)

        crow = QHBoxLayout()
        self.btn_test = QPushButton("Kiểm tra kết nối AI")
        self.btn_test.clicked.connect(self._test_ai)
        self.btn_save = QPushButton("Lưu cấu hình AI")
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.clicked.connect(self._save_cfg)
        crow.addWidget(self.btn_test)
        crow.addStretch(1)
        crow.addWidget(self.btn_save)
        glay.addLayout(crow)
        glay.addStretch(1)

        tabs.addTab(chat_tab, "Trợ lý AI Quản Lý")
        tabs.addTab(cfg_tab, "Cấu hình Local / Cloud AI")
        lay.addWidget(tabs)
        self._update_token_badge()
        self._update_ai_state_banner()

    def _update_ai_state_banner(self) -> None:
        cfg = self._collect_cfg()
        if cfg.mode == "local":
            self.lbl_ai_state.setText(
                f"AI đang chọn: LOCAL ({cfg.local_provider.upper()} · {cfg.local_model} tại {cfg.local_url}) — "
                "Miễn phí, chạy trên máy bạn. Chỉ gọi AI khi bạn bấm 'Gửi AI'."
            )
        else:
            has_key = bool(cfg.cloud_api_key.strip())
            key_txt = "đã có API Key" if has_key else "CHƯA nhập API Key (vào tab 'Cấu hình Local / Cloud AI' để nhập)"
            self.lbl_ai_state.setText(
                f"AI đang chọn: CLOUD ({cfg.cloud_provider.upper()} · {cfg.cloud_model}) — {key_txt}. "
                "Số token bên phải chỉ là ước lượng trước, chưa gọi AI."
            )

    def _active_context(self) -> str:
        if not self.chk_send_ctx.isChecked():
            return ""
        return self.folder_context if self.chk_compact.isChecked() else self.full_context

    def _update_token_badge(self) -> None:
        ctx = self._active_context()
        toks = ai_bridge.estimate_tokens(ctx)
        self.lbl_tok.setText(f"Ước lượng gửi: ~{toks} token")

    def _collect_cfg(self) -> AIConfig:
        return AIConfig(
            mode="cloud" if self.rb_cloud.isChecked() else "local",
            local_provider=str(self.cb_local_prov.currentData() or "ollama"),
            local_url=self.ed_local_url.text().strip() or "http://127.0.0.1:11434",
            local_model=self.ed_local_model.text().strip() or "qwen2.5:7b",
            cloud_provider=str(self.cb_cloud_prov.currentData() or "gemini"),
            cloud_url=self.ed_cloud_url.text().strip(),
            cloud_model=self.ed_cloud_model.text().strip() or "gemini-2.5-flash",
            cloud_api_key=self.ed_cloud_key.text().strip(),
            compact_mode=self.chk_compact.isChecked(),
            max_output_tokens=self.sp_max_tok.value(),
        )

    def _save_cfg(self) -> None:
        self.config.set_ai(self._collect_cfg())
        self.config.save()
        self._update_ai_state_banner()
        self.lbl_test.setText("Đã lưu cấu hình AI.")

    def _start_worker(self, w: _AIWorker) -> None:
        self._worker = w
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.start()

    def closeEvent(self, event) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.wait(1200)
                if w.isRunning():
                    w.terminate()
                    w.wait(400)
        super().closeEvent(event)

    def _test_ai(self) -> None:
        cfg = self._collect_cfg()
        self.config.set_ai(cfg)
        self.config.save()
        self.btn_test.setEnabled(False)
        self.lbl_test.setText("Đang kiểm tra kết nối...")
        w = _AIWorker("test", cfg)
        w.done.connect(lambda msg: (self.btn_test.setEnabled(True), self.lbl_test.setText(msg)))
        w.err.connect(lambda msg: (self.btn_test.setEnabled(True), self.lbl_test.setText(f"Lỗi: {msg}")))
        self._start_worker(w)

    def _fetch_models(self) -> None:
        cfg = self._collect_cfg()
        self.btn_fetch_models.setEnabled(False)
        self.lbl_test.setText("Đang lấy danh sách model từ Local AI...")
        w = _AIWorker("models", cfg)

        def _ok(txt: str) -> None:
            self.btn_fetch_models.setEnabled(True)
            models = [m for m in txt.splitlines() if m.strip()]
            if models:
                self.ed_local_model.setText(models[0])
                self.lbl_test.setText(f"Tìm thấy {len(models)} model: {', '.join(models[:8])}")
            else:
                self.lbl_test.setText("Không tìm thấy model nào trên Local AI.")

        w.done.connect(_ok)
        w.err.connect(lambda msg: (self.btn_fetch_models.setEnabled(True), self.lbl_test.setText(f"Lỗi: {msg}")))
        self._start_worker(w)

    def _send_prompt(self, text: str) -> None:
        prompt = text.strip()
        if not prompt:
            return
        cfg = self._collect_cfg()
        self.ed_prompt.clear()
        self.btn_send.setEnabled(False)
        self.chat_out.appendPlainText(f"\nBạn: {prompt}\nAI ({cfg.mode.upper()}): Đang suy nghĩ...")
        ctx = self._active_context()
        w = _AIWorker("ask", cfg, prompt=prompt, context=ctx)

        def _ok(reply: str) -> None:
            self.btn_send.setEnabled(True)
            self.chat_out.appendPlainText(f"{reply}\n" + "─" * 56)

        def _err(msg: str) -> None:
            self.btn_send.setEnabled(True)
            self.chat_out.appendPlainText(f"[Lỗi AI]: {msg}\n" + "─" * 56)

        w.done.connect(_ok)
        w.err.connect(_err)
        self._start_worker(w)
