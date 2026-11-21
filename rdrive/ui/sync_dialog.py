"""Cửa sổ Đồng bộ & Hộp thoại 'Đồng bộ - Tùy Chọn' chuẩn (Compare 2 bên + 5 chế độ + Hash + Filter)."""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppConfig, AutoRule
from ..core.rclone_client import RcloneClient, format_size
from . import icons


@dataclass
class SyncOptions:
    preset: str = "mirror"              # mirror | mirror_update | update | bidir | custom
    only_left: str = "copy_right"       # copy_right | delete_left | ignore
    only_right: str = "delete_right"    # delete_right | copy_left | ignore
    left_newer: str = "copy_right"      # copy_right | ignore
    right_newer: str = "copy_right"     # copy_right | copy_left | ignore
    compare_hash: bool = False          # --checksum
    exclude_patterns: list[str] = field(default_factory=list)

    def preset_label(self) -> str:
        return {
            "mirror": "Nhân bản",
            "mirror_update": "Cập nhật nhỏ",
            "update": "Cập Nhật",
            "bidir": "Hai Chiều",
            "custom": "Tùy Chỉnh",
        }.get(self.preset, "Tùy Chỉnh")


PRESETS: dict[str, tuple[str, str, str, str, str]] = {
    # key: (title, only_left, only_right, left_newer, right_newer)
    "mirror": ("Nhân bản bên trái sang bên phải", "copy_right", "delete_right", "copy_right", "copy_right"),
    "mirror_update": ("Cập nhật nhỏ (chỉ chép file mới & file thay đổi từ trái sang phải)", "copy_right", "ignore", "copy_right", "ignore"),
    "update": ("Cập Nhật (chép từ trái sang phải, giữ file thừa ở phải)", "copy_right", "ignore", "copy_right", "copy_right"),
    "bidir": ("Đồng bộ Hai Chiều (giữ bản mới hơn ở cả 2 bên)", "copy_right", "copy_left", "copy_right", "copy_left"),
}


class SyncOptionsDialog(QDialog):
    """Hộp thoại 'Đồng bộ - Tùy Chọn' kiểu 2 khung (5 preset bên trái + 4 quy tắc + Filter + Hash)."""

    def __init__(self, parent, opts: SyncOptions) -> None:
        super().__init__(parent)
        self.opts = SyncOptions(
            preset=opts.preset,
            only_left=opts.only_left,
            only_right=opts.only_right,
            left_newer=opts.left_newer,
            right_newer=opts.right_newer,
            compare_hash=opts.compare_hash,
            exclude_patterns=list(opts.exclude_patterns),
        )
        self.setWindowTitle("Đồng bộ - Tùy Chọn")
        self.setWindowIcon(icons.ribbon_icon("sync", 22))
        self.setMinimumWidth(620)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(12)

        # Cột trái: 5 nút Preset kiểu 2 khung
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        self._preset_btns: dict[str, QPushButton] = {}
        for key, label in [
            ("mirror", "Nhân bản"),
            ("mirror_update", "Cập nhật nhỏ"),
            ("update", "Cập Nhật"),
            ("bidir", "Hai Chiều"),
            ("custom", "Tùy Chỉnh"),
        ]:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setMinimumWidth(135)
            b.clicked.connect(lambda _=False, k=key: self._apply_preset(k))
            self._preset_btns[key] = b
            left_col.addWidget(b)
        left_col.addStretch(1)
        body.addLayout(left_col)

        # Cột phải: 4 dropdown quy tắc + Filter + Hash
        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        box = QGroupBox()
        self.lbl_desc = QLabel("Nhân bản bên trái sang bên phải")
        self.lbl_desc.setStyleSheet("font-weight: 600; color: #e6e8ec;")

        fl = QFormLayout()
        fl.setSpacing(8)
        self.cb_only_left = self._combo([("Sao chép về bên phải", "copy_right"), ("Xóa trái", "delete_left"), ("Bỏ qua", "ignore")])
        self.cb_only_right = self._combo([("Xóa phải", "delete_right"), ("Sao chép về bên trái", "copy_left"), ("Bỏ qua", "ignore")])
        self.cb_left_newer = self._combo([("Sao chép về bên phải", "copy_right"), ("Bỏ qua", "ignore")])
        self.cb_right_newer = self._combo([("Sao chép về bên phải", "copy_right"), ("Sao chép về bên trái", "copy_left"), ("Bỏ qua", "ignore")])

        for cb in (self.cb_only_left, self.cb_only_right, self.cb_left_newer, self.cb_right_newer):
            cb.activated.connect(self._on_manual_rule_change)

        fl.addRow("Phần tử này chỉ tồn tại ở bên trái:", self.cb_only_left)
        fl.addRow("Phần tử này chỉ tồn tại ở bên phải:", self.cb_only_right)
        fl.addRow("Tệp tin bên trái mới hơn:", self.cb_left_newer)
        fl.addRow("Tệp tin bên phải mới hơn:", self.cb_right_newer)

        blay = QVBoxLayout(box)
        blay.addWidget(self.lbl_desc)
        blay.addLayout(fl)
        right_col.addWidget(box)

        frow = QHBoxLayout()
        self.btn_filter = QPushButton("Filter… (Loại trừ file)")
        self.btn_filter.clicked.connect(self._edit_filter)
        self.lbl_filter = QLabel("")
        self.lbl_filter.setObjectName("Muted")
        frow.addWidget(self.btn_filter)
        frow.addWidget(self.lbl_filter, 1)
        right_col.addLayout(frow)

        self.chk_hash = QCheckBox("So sánh nội dung tệp bằng mã Hash MD5 khi có thể (--checksum)")
        self.chk_hash.setChecked(self.opts.compare_hash)
        right_col.addWidget(self.chk_hash)
        right_col.addStretch(1)

        body.addLayout(right_col, 1)
        root.addLayout(body, 1)

        # Nút OK / Hủy
        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_ok = QPushButton("OK")
        btn_ok.setObjectName("PrimaryBtn")
        btn_ok.setMinimumWidth(90)
        btn_ok.clicked.connect(self._save)
        btn_cancel = QPushButton("Bãi Bỏ")
        btn_cancel.setMinimumWidth(90)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        root.addLayout(btns)

        self._sync_ui_from_opts()

    @staticmethod
    def _combo(items: list[tuple[str, str]]) -> QComboBox:
        cb = QComboBox()
        for text, val in items:
            cb.addItem(text, val)
        return cb

    @staticmethod
    def _set_combo(cb: QComboBox, val: str) -> None:
        idx = cb.findData(val)
        if idx >= 0:
            cb.setCurrentIndex(idx)

    def _sync_ui_from_opts(self) -> None:
        for k, b in self._preset_btns.items():
            b.setChecked(k == self.opts.preset)
        if self.opts.preset in PRESETS:
            self.lbl_desc.setText(PRESETS[self.opts.preset][0])
        else:
            self.lbl_desc.setText("Quy tắc đồng bộ tùy chỉnh")
        self._set_combo(self.cb_only_left, self.opts.only_left)
        self._set_combo(self.cb_only_right, self.opts.only_right)
        self._set_combo(self.cb_left_newer, self.opts.left_newer)
        self._set_combo(self.cb_right_newer, self.opts.right_newer)
        is_custom = self.opts.preset == "custom"
        for cb in (self.cb_only_left, self.cb_only_right, self.cb_left_newer, self.cb_right_newer):
            cb.setEnabled(is_custom)
        self.lbl_filter.setText(", ".join(self.opts.exclude_patterns) if self.opts.exclude_patterns else "Không lọc loại trừ")

    def _apply_preset(self, key: str) -> None:
        self.opts.preset = key
        if key in PRESETS:
            _, ol, or_, ln, rn = PRESETS[key]
            self.opts.only_left = ol
            self.opts.only_right = or_
            self.opts.left_newer = ln
            self.opts.right_newer = rn
        self._sync_ui_from_opts()

    def _on_manual_rule_change(self) -> None:
        self.opts.preset = "custom"
        self.opts.only_left = str(self.cb_only_left.currentData())
        self.opts.only_right = str(self.cb_only_right.currentData())
        self.opts.left_newer = str(self.cb_left_newer.currentData())
        self.opts.right_newer = str(self.cb_right_newer.currentData())
        self._sync_ui_from_opts()

    def _edit_filter(self) -> None:
        cur = ", ".join(self.opts.exclude_patterns)
        txt, ok = QInputDialog.getText(
            self,
            "Bộ lọc loại trừ (Exclude Filter)",
            "Nhập mẫu tên cần bỏ qua, cách nhau bởi dấu phẩy (VD: *.tmp, *.prlock, .DS_Store):",
            text=cur,
        )
        if ok:
            self.opts.exclude_patterns = [p.strip() for p in txt.split(",") if p.strip()]
            self._sync_ui_from_opts()

    def _save(self) -> None:
        self.opts.only_left = str(self.cb_only_left.currentData())
        self.opts.only_right = str(self.cb_only_right.currentData())
        self.opts.left_newer = str(self.cb_left_newer.currentData())
        self.opts.right_newer = str(self.cb_right_newer.currentData())
        self.opts.compare_hash = self.chk_hash.isChecked()
        self.accept()


def _scan_local_files(root_dir: str, with_hash: bool, excludes: list[str]) -> dict[str, dict]:
    import fnmatch

    out: dict[str, dict] = {}
    root_p = Path(root_dir)
    if not root_p.is_dir():
        return out
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            if any(fnmatch.fnmatch(fn.lower(), pat.lower()) for pat in excludes):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            try:
                st = os.stat(full)
                md5 = ""
                if with_hash and st.st_size <= 512 * 1024 * 1024:
                    h = hashlib.md5()
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                    md5 = h.hexdigest()
                out[rel] = {
                    "path": rel,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
                    "md5": md5,
                }
            except OSError:
                continue
    return out


def _scan_remote_files(rclone: RcloneClient, spec: str, with_hash: bool, excludes: list[str]) -> dict[str, dict]:
    raw = rclone.lsjson_recursive(spec, with_hash=with_hash, exclude_patterns=excludes)
    out: dict[str, dict] = {}
    for item in raw:
        if item.get("IsDir"):
            continue
        rel = str(item.get("Path") or item.get("Name") or "").replace("\\", "/")
        mt_str = str(item.get("ModTime") or "")
        ts = 0.0
        disp = ""
        if mt_str:
            try:
                dt = datetime.fromisoformat(mt_str.replace("Z", "+00:00"))
                ts = dt.timestamp()
                disp = dt.astimezone().strftime("%d/%m/%Y %H:%M")
            except Exception:
                disp = mt_str[:16]
        hashes = item.get("Hashes") or {}
        out[rel] = {
            "path": rel,
            "size": int(item.get("Size") or 0),
            "mtime": ts,
            "mtime_str": disp,
            "md5": str(hashes.get("md5") or "").lower(),
        }
    return out


class _CompareWorker(QThread):
    done = Signal(list)
    err = Signal(str)

    def __init__(
        self,
        rclone: RcloneClient,
        left_spec: str,
        left_is_local: bool,
        right_spec: str,
        right_is_local: bool,
        opts: SyncOptions,
    ) -> None:
        super().__init__()
        self.rclone = rclone
        self.left_spec = left_spec
        self.left_is_local = left_is_local
        self.right_spec = right_spec
        self.right_is_local = right_is_local
        self.opts = opts

    def run(self) -> None:
        try:
            left_map = (
                _scan_local_files(self.left_spec, self.opts.compare_hash, self.opts.exclude_patterns)
                if self.left_is_local
                else _scan_remote_files(self.rclone, self.left_spec, self.opts.compare_hash, self.opts.exclude_patterns)
            )
            right_map = (
                _scan_local_files(self.right_spec, self.opts.compare_hash, self.opts.exclude_patterns)
                if self.right_is_local
                else _scan_remote_files(self.rclone, self.right_spec, self.opts.compare_hash, self.opts.exclude_patterns)
            )

            all_keys = sorted(set(left_map.keys()) | set(right_map.keys()), key=str.lower)
            rows: list[dict] = []
            for k in all_keys:
                lf = left_map.get(k)
                rf = right_map.get(k)
                if lf and not rf:
                    act = self.opts.only_left
                    reason = "Chỉ có ở Trái"
                elif rf and not lf:
                    act = self.opts.only_right
                    reason = "Chỉ có ở Phải"
                else:
                    assert lf and rf
                    same = False
                    if self.opts.compare_hash and lf["md5"] and rf["md5"]:
                        same = (lf["md5"] == rf["md5"])
                    else:
                        same = (lf["size"] == rf["size"] and abs(lf["mtime"] - rf["mtime"]) <= 2.0)
                    if same:
                        act = "same"
                        reason = "Giống nhau"
                    elif lf["mtime"] >= rf["mtime"]:
                        act = self.opts.left_newer
                        reason = "Bên trái mới hơn"
                    else:
                        act = self.opts.right_newer
                        reason = "Bên phải mới hơn"
                rows.append({"rel": k, "left": lf, "right": rf, "action": act, "reason": reason})
            self.done.emit(rows)
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc))


ACTION_LABEL = {
    "copy_right": "→ Chép sang Phải",
    "copy_left": "← Chép sang Trái",
    "delete_right": "✗ Xóa ở Phải",
    "delete_left": "✗ Xóa ở Trái",
    "ignore": "– Bỏ qua",
    "same": "= Giống nhau",
}


class SyncDialog(QDialog):
    """Cửa sổ Đồng bộ 2 bên chuẩn: Path bars + Compare table 8 cột + Configuration + Bắt Đầu."""

    def __init__(
        self,
        parent,
        rclone: RcloneClient,
        config: AppConfig,
        left_label: str,
        left_spec: str,
        left_is_local: bool,
        right_label: str,
        right_spec: str,
        right_is_local: bool,
    ) -> None:
        super().__init__(parent)
        self.rclone = rclone
        self.config = config
        self.left_spec = left_spec
        self.left_is_local = left_is_local
        self.right_spec = right_spec
        self.right_is_local = right_is_local
        self.opts = SyncOptions(preset="mirror")
        self._live_workers: list[QThread] = []
        self._diff_rows: list[dict] = []
        self.selected_plans: list[dict] = []

        self.setWindowTitle("Đồng bộ — Rdrive")
        self.setWindowIcon(icons.ribbon_icon("sync", 24))
        self.resize(1120, 620)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # Hàng 1: Đường dẫn Trái ⇄ Đường dẫn Phải + Browse
        prow = QHBoxLayout()
        prow.setSpacing(6)
        self.ed_left = QLineEdit(left_spec)
        self.ed_left.setToolTip(left_label)
        btn_bl = QPushButton("Browse…")
        btn_bl.setVisible(left_is_local)
        btn_bl.clicked.connect(lambda: self._browse_local(self.ed_left))

        btn_swap = QToolButton()
        btn_swap.setObjectName("CenterAction")
        btn_swap.setText("⇄")
        btn_swap.setToolTip("Đổi chiều Trái ↔ Phải")
        btn_swap.clicked.connect(self._swap_sides)

        self.ed_right = QLineEdit(right_spec)
        self.ed_right.setToolTip(right_label)
        btn_br = QPushButton("Browse…")
        btn_br.setVisible(right_is_local)
        btn_br.clicked.connect(lambda: self._browse_local(self.ed_right))

        prow.addWidget(QLabel("Trái:"))
        prow.addWidget(self.ed_left, 1)
        prow.addWidget(btn_bl)
        prow.addWidget(btn_swap)
        prow.addWidget(QLabel("Phải:"))
        prow.addWidget(self.ed_right, 1)
        prow.addWidget(btn_br)
        lay.addLayout(prow)

        # Hàng 2: Thanh công cụ kiểu 2 khung (Configuration | Lưu Trữ | Compare | Bắt Đầu | Filter | Tìm)
        tbar = QFrame()
        tbar.setObjectName("MiniToolbar")
        tlay = QHBoxLayout(tbar)
        tlay.setContentsMargins(6, 4, 6, 4)
        tlay.setSpacing(6)

        self.btn_cfg = QPushButton(f"⚙ Cấu hình — {self.opts.preset_label()}")
        self.btn_cfg.clicked.connect(self._open_options)

        self.btn_save_rule = QPushButton("💾 Lưu vào Auto")
        self.btn_save_rule.clicked.connect(self._save_as_auto_rule)

        self.btn_compare = QPushButton("🔍 So sánh (Compare)")
        self.btn_compare.setObjectName("PrimaryBtn")
        self.btn_compare.clicked.connect(self._run_compare)

        self.btn_start = QPushButton("▶ Bắt Đầu")
        self.btn_start.clicked.connect(self._accept_sync)

        self.cb_act_filter = QComboBox()
        self.cb_act_filter.addItem("Hành động: Tất cả", "all")
        self.cb_act_filter.addItem("Chỉ mục có thay đổi", "changes")
        self.cb_act_filter.addItem("→ Chép sang Phải", "copy_right")
        self.cb_act_filter.addItem("← Chép sang Trái", "copy_left")
        self.cb_act_filter.addItem("✗ Xóa", "delete")
        self.cb_act_filter.addItem("= Giống nhau", "same")
        self.cb_act_filter.currentIndexChanged.connect(self._render_table)

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Lọc đường dẫn...")
        self.ed_search.setMaximumWidth(200)
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._render_table)

        tlay.addWidget(self.btn_cfg)
        tlay.addWidget(self.btn_save_rule)
        tlay.addWidget(self.btn_compare)
        tlay.addWidget(self.btn_start)
        tlay.addStretch(1)
        tlay.addWidget(self.cb_act_filter)
        tlay.addWidget(self.ed_search)
        lay.addWidget(tbar)

        # Hàng 3: Bảng so sánh 8 cột kiểu 2 khung
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Đường Dẫn (Trái)",
            "Kích Thước",
            "Ngày",
            "Hành Động",
            "Đường Dẫn (Phải)",
            "Kích Thước",
            "Ngày",
            "Trạng Thái",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

        # Hàng 4: Trạng thái & Đóng
        bot = QHBoxLayout()
        self.lbl_stat = QLabel("Bấm 'So sánh (Compare)' để xem trước chi tiết từng file, hoặc bấm 'Bắt Đầu' để chạy ngay.")
        self.lbl_stat.setObjectName("Muted")
        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.reject)
        bot.addWidget(self.lbl_stat, 1)
        bot.addWidget(btn_close)
        lay.addLayout(bot)

    def _browse_local(self, ed: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục", ed.text())
        if d:
            ed.setText(d)

    def _swap_sides(self) -> None:
        l_txt, r_txt = self.ed_left.text(), self.ed_right.text()
        self.ed_left.setText(r_txt)
        self.ed_right.setText(l_txt)
        self.left_is_local, self.right_is_local = self.right_is_local, self.left_is_local

    def _open_options(self) -> None:
        dlg = SyncOptionsDialog(self, self.opts)
        if dlg.exec() == QDialog.Accepted:
            self.opts = dlg.opts
            self.btn_cfg.setText(f"⚙ Cấu hình — {self.opts.preset_label()}")
            if self._diff_rows:
                self._run_compare()

    def _save_as_auto_rule(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Lưu tác vụ Đồng bộ vào Auto",
            "Tên tác vụ:",
            text=f"Đồng bộ ({self.opts.preset_label()})",
        )
        if not ok or not name.strip():
            return
        rules = self.config.get_rules()
        rules.append(
            AutoRule(
                id=uuid.uuid4().hex[:8],
                name=name.strip(),
                enabled=True,
                kind="sync",
                source=self.ed_left.text().strip(),
                destination=self.ed_right.text().strip(),
                schedule="manual",
                note=f"preset={self.opts.preset}; hash={self.opts.compare_hash}",
            )
        )
        self.config.set_rules(rules)
        self.config.save()
        QMessageBox.information(self, "Đã lưu", f"Đã lưu '{name.strip()}' vào danh sách Auto / Lịch.")

    def _run_compare(self) -> None:
        self.btn_compare.setEnabled(False)
        self.lbl_stat.setText("Đang quét & so sánh hai thư mục…")
        w = _CompareWorker(
            self.rclone,
            self.ed_left.text().strip(),
            self.left_is_local,
            self.ed_right.text().strip(),
            self.right_is_local,
            self.opts,
        )
        self._live_workers.append(w)
        w.finished.connect(lambda th=w: (self._live_workers.remove(th) if th in self._live_workers else None, th.deleteLater()))
        w.done.connect(self._on_compare_done)
        w.err.connect(self._on_compare_err)
        w.start()

    def _on_compare_done(self, rows: list[dict]) -> None:
        self.btn_compare.setEnabled(True)
        self._diff_rows = rows
        self._render_table()

    def _on_compare_err(self, msg: str) -> None:
        self.btn_compare.setEnabled(True)
        self.lbl_stat.setText(f"Lỗi so sánh: {msg}")

    def _render_table(self) -> None:
        flt = str(self.cb_act_filter.currentData() or "all")
        q = self.ed_search.text().strip().lower()
        self.table.setRowCount(0)

        counts = {"copy_right": 0, "copy_left": 0, "delete_right": 0, "delete_left": 0, "same": 0, "ignore": 0}
        for r in self._diff_rows:
            act = r["action"]
            counts[act] = counts.get(act, 0) + 1
            if flt == "changes" and act in ("same", "ignore"):
                continue
            if flt == "delete" and act not in ("delete_right", "delete_left"):
                continue
            if flt not in ("all", "changes", "delete") and act != flt:
                continue
            if q and q not in r["rel"].lower():
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)
            lf, rf = r["left"], r["right"]
            self.table.setItem(row, 0, QTableWidgetItem(lf["path"] if lf else ""))
            self.table.setItem(row, 1, QTableWidgetItem(format_size(lf["size"]) if lf else ""))
            self.table.setItem(row, 2, QTableWidgetItem(lf["mtime_str"] if lf else ""))

            act_it = QTableWidgetItem(ACTION_LABEL.get(act, act))
            if act in ("copy_right", "copy_left"):
                act_it.setForeground(Qt.cyan)
            elif act in ("delete_right", "delete_left"):
                act_it.setForeground(Qt.red)
            elif act == "same":
                act_it.setForeground(Qt.green)
            self.table.setItem(row, 3, act_it)

            self.table.setItem(row, 4, QTableWidgetItem(rf["path"] if rf else ""))
            self.table.setItem(row, 5, QTableWidgetItem(format_size(rf["size"]) if rf else ""))
            self.table.setItem(row, 6, QTableWidgetItem(rf["mtime_str"] if rf else ""))
            self.table.setItem(row, 7, QTableWidgetItem(r["reason"]))

        self.lbl_stat.setText(
            f"Tổng {len(self._diff_rows)} file  ·  → Phải: {counts['copy_right']}  ·  ← Trái: {counts['copy_left']}  "
            f"·  ✗ Xóa: {counts['delete_right'] + counts['delete_left']}  ·  = Giống: {counts['same']}"
        )

    def _build_rclone_plans(self) -> list[dict]:
        left = self.ed_left.text().strip()
        right = self.ed_right.text().strip()
        common: list[str] = []
        if self.opts.compare_hash:
            common.append("--checksum")
        for pat in self.opts.exclude_patterns:
            common += ["--exclude", pat]

        p = self.opts.preset
        if p == "mirror" or (self.opts.only_left == "copy_right" and self.opts.only_right == "delete_right"):
            return [{"command": "sync", "src": left, "dst": right, "extra": common, "title": f"Đồng bộ (Nhân bản): Trái → Phải"}]
        if p == "mirror_update":
            return [{"command": "copy", "src": left, "dst": right, "extra": [*common, "--update"], "title": "Đồng bộ (Cập nhật nhỏ): Trái → Phải"}]
        if p == "update":
            return [{"command": "copy", "src": left, "dst": right, "extra": common, "title": "Đồng bộ (Cập Nhật): Trái → Phải"}]
        if p == "bidir" or (self.opts.only_left == "copy_right" and self.opts.only_right == "copy_left"):
            return [
                {"command": "copy", "src": left, "dst": right, "extra": [*common, "--update"], "title": "Đồng bộ Hai Chiều (1/2): Trái → Phải"},
                {"command": "copy", "src": right, "dst": left, "extra": [*common, "--update"], "title": "Đồng bộ Hai Chiều (2/2): Phải → Trái"},
            ]
        # Custom fallback
        plans: list[dict] = []
        if self.opts.only_left == "copy_right" or self.opts.left_newer == "copy_right":
            ex = [*common, "--update"] if self.opts.right_newer != "copy_right" else list(common)
            plans.append({"command": "copy", "src": left, "dst": right, "extra": ex, "title": "Đồng bộ (Tùy Chỉnh): Trái → Phải"})
        if self.opts.only_right == "copy_left" or self.opts.right_newer == "copy_left":
            plans.append({"command": "copy", "src": right, "dst": left, "extra": [*common, "--update"], "title": "Đồng bộ (Tùy Chỉnh): Phải → Trái"})
        return plans or [{"command": "copy", "src": left, "dst": right, "extra": [*common, "--update"], "title": "Đồng bộ: Trái → Phải"}]

    def _accept_sync(self) -> None:
        self.selected_plans = self._build_rclone_plans()
        self.accept()

    def closeEvent(self, event) -> None:
        for w in list(self._live_workers):
            if w.isRunning():
                w.wait(1200)
                if w.isRunning():
                    w.terminate()
                    w.wait(400)
        super().closeEvent(event)
