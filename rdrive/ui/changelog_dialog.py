"""Biên bản nội dung Update (Changelog & Agent Handoff Report) + Giải đáp rclone update, Local/Cloud AI, Rust/MCP."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.config import ROOT_DIR
from ..core.rclone_client import RcloneClient
from . import icons

CHANGELOG_FILE = ROOT_DIR / "CHANGELOG_AGENT.md"

REPORT_MD = """# BIÊN BẢN CẬP NHẬT & TÀI LIỆU BÀN GIAO AGENT — RDRIVE v1.2.5

Tài liệu này lưu trực tiếp tại `Rdrive/CHANGELOG_AGENT.md` và hiển thị ngay trong ứng dụng (**nút `📋 Biên bản Update`**) để người dùng và mọi AI Agent kế tiếp đọc hiểu toàn bộ kiến trúc, những gì đã làm xong, những gì cần lưu ý và định hướng mở rộng.

---

## I. NHỮNG TÍNH NĂNG ĐÃ HOÀN THÀNH (ĐÃ TEST & CHẠY THẬT)

1. **Kéo thả (Drag & Drop) & Clipboard (`Ctrl+C` / `Ctrl+X` / `Ctrl+V`) — Mục #1, #10**:
   - Kéo thả trực tiếp file/folder từ **Windows Explorer** vào Khung Trái hoặc Khung Phải của Rdrive để Upload/Copy ngay.
   - Kéo thả trực tiếp giữa **Khung Trái ↔ Khung Phải** trong Rdrive.
   - Bấm **`Ctrl+C`** trên file/folder ngoài Windows Explorer rồi bấm **`Ctrl+V`** ngay trong khung Rdrive (hoặc `Ctrl+C` / `Ctrl+X` / `Ctrl+V` giữa 2 khung trong app).
2. **Cache Cây Thư Mục Bền Vững (`data/tree_cache.json`) & Nhớ Tab/Thư Mục Khi Tắt/Mở Lại — Mục #2**:
   - Toàn bộ cây thư mục, Shared Drives và danh sách file đã mở được lưu xuống `data/tree_cache.json` (giữ qua các lần tắt/mở app, hiển thị tức thì ~0 ms).
   - Tự động lưu danh sách tất cả các tab đang mở (`left_tabs_state`, `right_tabs_state`) và đúng thư mục đang đứng (VD đang mở `folder B` ở `drive B` thì tắt app bật lại vẫn mở đúng `folder B` ở `drive B`).
   - Sau khi Upload xong 1 tác vụ, tự động làm mới đúng thư mục đích đó để thấy file mới ngay mà không xóa cache các thư mục khác.
3. **Tăng Tốc Đa Luồng Kiểu IDM (16 luồng/file) Cho Mạng 2 Gbps Load-Balance — Mục #3**:
   - Bộ điều phối hàng đợi chạy **nhiều file Upload & Download song song cùng lúc** (`Số luồng Upload cùng lúc` / `Số luồng Download cùng lúc`).
   - Tích hợp cờ đa kết nối giống IDM (`--multi-thread-streams 16 --multi-thread-cutoff 16M --multi-thread-chunk-size 32M`) **chỉ cho DOWNLOAD** (tải về máy): 1 file lớn chia 16 luồng TCP để gộp băng thông 2 đường Load-Balance.
   - **UPLOAD lên Google Drive luôn 1 luồng / file** (Google Drive nhận file theo từng đoạn nối tiếp, rclone không chia luồng được). Upload nhanh hơn là nhờ **chạy nhiều file cùng lúc** (`Số luồng Upload cùng lúc`), không phải nhờ cờ IDM.
   - Hỗ trợ **Google OAuth Client ID & Secret riêng** để không bị Google bóp băng thông.
4. **Tích Hợp NAS Xpenology / Synology — Mục #4**:
   - Hộp thoại **`+ Kết nối NAS Xpenology`** (`rdrive/ui/nas_dialog.py`): hỗ trợ **SMB/CIFS**, **SFTP/SSH (Port 22)**, **WebDAV (Port 5005/5006)**, **FTP** và mở nhanh `\\\\IP\\Share` trong mạng LAN để đồng bộ trực tiếp **NAS Xpenology ↔ Google Drive**.
5. **Menu Chuột Phải Hàng Đợi Truyền Tải + Tạm Dừng / Tiếp Tục / Chẩn Đoán Sửa Lỗi — Mục #5, #11**:
   - Chuột phải vào bất kỳ dòng nào trong `Hàng đợi truyền tải`: **`⏸ Dừng (Tạm dừng)`**, **`▶ Tiếp Tục`**, **`✖ Bãi Bỏ`**, **`↻ Chạy Lại`**, **`Xóa Hoàn Thành`**, **`Xóa Thất Bại`**, **`↑ Move Up`**, **`↓ Move Down`**, **`📅 Schedule This Task`**, và **`🔧 Chẩn đoán & Sửa lỗi`** (tự nhận diện lỗi hết hạn token để mở Đăng nhập lại rồi chạy tiếp).
   - Thanh tổng kết dưới cùng: `Total: X / Y GB [tiến độ] ⬇ Tốc độ tải xuống ⬆ Tốc độ tải lên`.
6. **Tab `Speedtest` Chính Thức (Nguồn Speedtest.net Ookla CLI + Test Đa Luồng 16 Luồng) — Mục #6**:
   - Tự động tải `speedtest.exe` chính thức từ `install.speedtest.net` vào `Rdrive/bin/speedtest.exe` để đo chuẩn Speedtest.net (Ping, Download Mbps & MB/s, Upload Mbps & MB/s, ISP, Server).
   - Có thêm nút **`⚡ Đo Đa Luồng IDM (16 luồng)`** để kiểm tra tổng băng thông gộp 2 gói mạng Load-Balance.
   - Tự động lưu mọi lần đo vào `data/speedtest_history.json` và `logs/rdrive.log` kèm cột **So với lần trước (`+/- Mbps`)**.
7. **Log Luôn Tự Lưu & Bộ Đóng Gói Bản Sạch Cho Máy Mới — Mục #7, #18**:
   - Nhật ký luôn luôn tự động ghi vào `logs/rdrive.log` và `logs/crash.log` ngay từ khi bật app (không cần vào Setting bật).
   - Có sẵn script **`Dong_goi_ban_sach_may_moi.bat`** (`tools/build_clean_installer.ps1`): xuất ra thư mục bộ cài sạch sẽ, **tự động loại bỏ 100% tài khoản, token, lịch sử, cache và log cá nhân**.
8. **Nút `★ Favourite` (Lối tắt địa chỉ ưa thích) — Mục #8**:
   - Nút **`★`** ngay trên thanh công cụ của cả 2 khung: lưu nhanh thư mục đang mở (trên máy, trên Google Drive, Shared Drive hoặc NAS Xpenology) và nhảy tới chỉ bằng 1 click.
9. **Chia Sẻ Liên Kết & Tính Chất (`Properties` / Dung Lượng Thư Mục) — Mục #9**:
   - Chuột phải vào file hoặc thư mục → **`Chia sẻ liên kết…`** (lấy & copy link chia sẻ Google Drive) hoặc **`Tính Chất (Alt+Enter)`** (`rdrive/ui/properties_dialog.py`: tự tính tổng dung lượng thật & tổng số file bên trong thư mục bằng `rclone size --json`, hiển thị ID, chủ sở hữu, thời lượng video/audio).
10. **Menu Chuột Phải Ở Cột Cây Thư Mục Nhỏ (`DirTree`) & Tự Xổ Danh Sách Chủ Tài Khoản Đã Chia Sẻ (`<tên chủ> Drive`) — Mục #12, #13**:
    - Cột cây thư mục bên trái hỗ trợ chuột phải đầy đủ: `Mở Rộng`, `Cắt`, `Sao Chép`, `Dán`, `Xóa`, `Sửa Tên`, `Thư mục Mới`, `★ Thêm vào Favourite`, `Chia sẻ liên kết…`, `Tính Chất`.
    - Tự động đọc danh sách người đã chia sẻ file/folder với bạn qua Google Drive API và gom thành các nhánh **`<tên tài khoản> Drive`** (VD: `ten-nguoi-chia-se Drive`) ngay ở cột cây thư mục nhỏ giống hệt app 2 khung.
11. **Mount Cloud Thành Ổ Đĩa Windows (`rdrive/ui/mount_dialog.py`) + Chạy Ngầm Khay Hệ Thống + Khởi Động Cùng Windows + Quyền Admin — Mục #14**:
    - Nút **`Mount Ổ Đĩa`** trên thanh công cụ: gắn bất kỳ Cloud / NAS hoặc thư mục con chỉ định thành ổ đĩa (`X:`, `Y:`, `Z:`...) trên Windows, có nút tự cài `WinFsp`, thu nhỏ xuống khay hệ thống (System Tray), tự bật khi khởi động Windows, và nút chạy lại với quyền Administrator.
12. **Chế Độ Hiển Thị (`Biểu Tượng Rất Lớn / Lớn / Trung Bình / Danh Sách / Chi Tiết`) & Hiện Thời Lượng Video/Audio — Mục #15**:
    - Nút chọn chế độ xem trên thanh công cụ mỗi khung (đổi kích thước icon / danh sách / chi tiết).
    - Thêm cột **`Chi Tiết`**: tự đọc thời lượng video/audio (`.mp4`, `.mov`, `.m4a`, `.wav`) trên máy bằng bộ đọc header Python thuần siêu nhanh (< 0.5 ms/file) và đọc `videoMediaMetadata` (thời lượng + độ phân giải `1920x1080`) trực tiếp từ Google Drive API.
13. **Tùy Chỉnh Phím Tắt (`Phím tắt…` / Custom Hotkeys) — Mục #19**:
    - Hộp thoại `rdrive/ui/keymap_dialog.py` cho phép đổi phím tắt theo ý thích (`Ctrl+C`, `Ctrl+V`, `Ctrl+X`, `F5`, `F2`, `Ctrl+Shift+N`, `Delete`, `Alt+Enter`, `Ctrl+F`, `Ctrl+T`...).

---

## II. GIẢI ĐÁP KỸ THUẬT CHI TIẾT (MỤC #16, #17, #20)

### 1. Mục #16: Khi `rclone` cập nhật bản mới thì có gây lỗi không? Cập nhật như thế nào?
- **Có gây lỗi không?** **KHÔNG.** Rdrive giao tiếp với `rclone` qua bộ giao thức chuẩn ổn định (`lsjson`, `about --json`, `config dump`, `authorize`, `--use-json-log`). Đội ngũ rclone giữ tương thích ngược (backward-compatible) cho các lệnh JSON này qua mọi phiên bản `1.x`.
- **Nếu có bản cập nhật mới thì làm thế nào?**
  - Trong hộp thoại **`Biên bản Update`** này (và trong `Tùy Chọn -> rclone & SyAu`) có nút **`Kiểm tra / Cập nhật rclone`**:
  - Hoặc chạy `winget upgrade Rclone.Rclone` / `rclone selfupdate` — Rdrive sẽ tự nhận bản mới ngay mà không mất bất kỳ tài khoản hay cấu hình nào.

### 2. Mục #17: Local AI làm được gì? Cloud AI làm được gì? Can thiệp được tới đâu?
- **Cơ chế an toàn chung:** Cả Local AI và Cloud AI đều làm việc thông qua lớp cầu nối `rdrive/core/ai_bridge.py`. Mặc định AI ở chế độ **Tư vấn & Lập kế hoạch (Read-Only Context)** — chỉ nhận bản tóm tắt tên thư mục/nhóm file (~30–200 token, **không bao giờ đọc nội dung bên trong file của bạn**) và chỉ thực thi sắp xếp khi bạn duyệt.
- **Local AI (Ollama / LM Studio chạy trên CPU/GPU máy bạn):**
  - **Ưu điểm:** Miễn phí 100%, không cần mạng, bảo mật tuyệt đối (tên file/khách hàng không gửi ra internet), chạy tự động hàng loạt 24/7 không sợ tốn tiền token.
  - **Làm được gì:** Phân loại file theo tên/ngày/loại (`az_recorder_*`, `DJI_*`, `.prproj`), phát hiện thư mục đặt sai quy chuẩn (`quay thang 11`), đề xuất cấu trúc gom thư mục, sinh quy tắc `AutoRule` tự động.
- **Cloud AI (Google Gemini / OpenAI API):**
  - **Ưu điểm:** Thông minh hơn với các yêu cầu phức tạp, không tốn RAM/VRAM máy tính khi bạn đang render Premiere/Media Encoder.
  - **Làm được gì:** Hiểu các chỉ thị phức tạp bằng tiếng Việt tự nhiên (VD: *"Tìm tất cả các buổi quay bị thiếu file WAV hoặc trùng tên giữa Drive A và NAS rồi lập lịch gom về 1 chỗ"*), tóm tắt báo cáo lịch sử backup và chẩn đoán log lỗi phức tạp.

### 3. Mục #20: Sau này có thể chuyển (build) qua Rust (`Tauri / Rust + MCP Rust`) được không?
- **Hoàn toàn ĐƯỢC và rất thuận lợi!** Kiến trúc hiện tại của Rdrive đã được tách làm 2 tầng độc lập:
  1. **Tầng Lõi (`rdrive/core/`):** Toàn bộ dữ liệu lưu ở định dạng JSON chuẩn (`data/config.json`, `data/history.json`, `data/tree_cache.json`, `data/speedtest_history.json`) và mọi thao tác đám mây đều gọi qua tiến trình `rclone.exe` + REST API.
  2. **Lộ trình chuyển sang Rust + MCP Rust:**
     - **Bước 1 (MCP Server bằng Rust):** Viết 1 binary Rust (`rdrive-mcp-rs` dùng crate `rmcp` / `tokio` / `serde_json`) đọc trực tiếp chung file `data/config.json` và điều khiển `rclone.exe` + Google Drive API. Khi đó mọi AI Agent (Claude, Cursor, Antigravity, Local LLM) có thể gọi công cụ Rdrive qua chuẩn **MCP (Model Context Protocol)** với tốc độ native Rust.
     - **Bước 2 (Giao diện Rust):** Chuyển tầng UI sang **Tauri v2 (Rust backend + WebGPU/HTML frontend)** hoặc **iced / egui (100% Pure Rust GUI)**, giữ nguyên 100% cấu trúc `data/*.json` và `rclone.conf` mà không cần đăng nhập lại tài khoản.

---

## III. GHI CHÚ CHO CÁC AI AGENT KẾ TIẾP (HANDOFF NOTES)
- **Điểm vào ứng dụng:** `Rdrive.pyw` -> `rdrive/app.py` (`MainWindow`, có `QSystemTrayIcon` & single-instance `QLocalServer`).
- **Widget trung tâm:** `rdrive/ui/main_widget.py` (`RdriveWidget`) — chạy độc lập (app riêng). KHÔNG nhúng vào SyAu: liên kết với SyAu chỉ qua cổng nội bộ (xem `QUY_TAC_AGENT.md`). Bản nhúng cũ nằm trong `SyAu-1.0.0-backup` (bản dự phòng) — không dùng nữa.
- **Quy tắc đa luồng Qt bắt buộc:** Mọi `QThread` đều phải lưu trong danh sách `self._live_workers` và chỉ xóa khi tín hiệu `finished` phát ra (tuyệt đối không ghi đè biến đơn lẻ để tránh lỗi `QThread: Destroyed while thread is still running`).
- **Phần chờ người dùng mô tả chi tiết tiếp theo:** Logic nghiệp vụ cụ thể cho **Tự động sắp xếp thư mục (`Auto / Lịch`)** theo quy tắc riêng của người dùng (sẽ lắp trực tiếp vào `rdrive/ui/auto_dialog.py` và `rdrive/core/`).

---

## IV. NHẬT KÝ SỬA LỖI (HOTFIXES)

- **v1.2.5 (2026-11-21, Claude):**
  - Cột dung lượng tài khoản: ghi rõ `Đã dùng X (NN%) · Trống Y · Tổng Z`; cột đúng % đã dùng, đổi vàng từ 80%, đỏ từ 95%.
  - NAS: đọc dung lượng thật qua Windows (ổ mạng đã gắn hoặc `\\\\host\\share`, `core/nas_usage.py`); không đọc được thì dùng ô "Dung lượng tối đa NAS" (`AccountMeta.capacity_gb`) trong Cấu hình NAS.
  - Tự cập nhật qua GitHub Releases (`core/updater.py`): Tùy Chọn → "Kho cập nhật GitHub" (chủ/kho), nút "⟳ Cập nhật", tự kiểm khi mở. Cập nhật giữ nguyên `data`, `logs`, `bin`, `config.json`.
  - Chuẩn bị đưa công khai: bỏ đường dẫn/IP cá nhân khỏi mã (`syau_dir` mặc định trống), bỏ tên ứng dụng khác khỏi giao diện, Speedtest không còn tự tải Ookla CLI (người dùng tự đặt `bin\\speedtest.exe`), `tools/make_release.py` đóng gói theo danh sách cho phép, `NOTICE.md`.
- **v1.2.4 (2026-11-21):**
  - **Thông báo Telegram bất đồng bộ** (`core/telegram_notifier.py`, `ui/settings_dialog.py`, `core/transfer_manager.py`):
    - Thêm tab **Telegram** trong `Tùy Chọn`: nhập Bot Token, bấm **`🔍 Tự lấy Chat ID`** (tự đọc `getUpdates` ở luồng nền), bấm **`📨 Gửi tin nhắn thử (Test)`**.
    - **Bảo mật chuẩn quy tắc:** Bot Token được lưu trực tiếp trong **Windows Credential Manager** (`CredWriteW` / `CredReadW`), tuyệt đối **không lưu ra `config.json`** và **không ghi vào `logs/rdrive.log`**.
    - Tự động gửi tin nhắn Telegram ở luồng nền khi: hoàn tất toàn bộ hàng đợi (tổng số file, dung lượng GB, tốc độ TB & đỉnh), có tác vụ lỗi, phát hiện nghẽn/tụt tốc đột ngột, hoặc từng file xong (tùy chọn).
  - **Đã kiểm chứng thực tế `rclone rcd` (bản 1.2.3) trên Windows với `rclone.exe` v1.76.0 thật:** Google Drive `lsjson` nhanh hơn (`1,67s` vs `2,92s`), NAS Xpenology chỉ `0,03–0,05s`, đóng app `rcd` tắt sạch (`returncode=0`).

- **v1.2.3 (2026-11-21, Claude):**
  - **Duyệt Google Drive qua 1 rclone thường trực** (`core/rclone_rcd.py`, `rclone rcd` trên `127.0.0.1`, cổng + mật khẩu ngẫu nhiên). Liệt kê folder (`lsjson`) và xem dung lượng (`about`) không còn bật 1 rclone mới mỗi lần: rclone thường trực giữ tài khoản, bảng "đường dẫn → ID folder" (1 giờ) và kết nối tới Google → mở folder sâu / mở lại folder nhanh hơn rõ.
  - Upload / download / xóa / đổi tên vẫn chạy bằng lệnh rclone riêng như cũ (giữ tiến độ, nhật ký).
  - Đường lui: không nói chuyện được với rclone thường trực → tự dùng cách cũ cho lần đó và bật lại ở lần sau; khởi động hỏng 3 lần → dùng cách cũ tới hết phiên. Lỗi thật từ Google (folder không có, token hết hạn) vẫn báo như cũ.
  - Thêm / sửa / xóa / đăng xuất tài khoản → rclone thường trực tự khởi động lại (bỏ token cũ). Tắt Rdrive → tắt theo; Windows: gắn vào Job Object nên Rdrive crash thì rclone thường trực cũng tắt.
  - Tùy Chọn: ô "Duyệt Google Drive nhanh (giữ 1 rclone chạy nền)" (`use_rcd`, mặc định bật). Nhật ký: `logs/rcd.log`.
  - Test với rclone giả lập: 18 kiểm tra; 20 lần mở folder 0,01 s so với 3,7 s. **Chưa kiểm chứng với rclone thật trên Windows.**

- **v1.2.2 (2026-11-21, Claude rà soát):**
  - **Upload không còn gắn cờ đa luồng IDM** (`core/transfer_manager.py`): `--multi-thread-streams` chỉ áp cho tác vụ download.
  - **Nhịp gọi API Google Drive về mức an toàn** (`core/rclone_client.py`): `--drive-pacer-min-sleep` 10 ms → 100 ms (chỉnh ở `drive_pacer_ms` trong `config.json`), `--drive-pacer-burst` 200 → 100. Gọi quá dày làm Google trả 403 "rate limit" → chậm hơn.
  - **Sửa treo do ống dẫn không ai đọc:** `run_transfer` đổi `stdout=PIPE` → `DEVNULL`; **Mount ổ đĩa** (`ui/mount_dialog.py`) đổi `stderr=PIPE` → ghi nhật ký vào `logs/mount_<ổ>.log` (trước đây chạy vài giờ là ổ ảo có thể treo cứng). Thêm báo lỗi nếu rclone mount dừng ngay sau khi bật.
  - **`config.json` an toàn** (`core/config.py`): mỗi lần lưu giữ bản `config.json.bak`; file hỏng thì cất ra `config.loi-<giờ>.json` và dùng bản sao lưu thay vì ghi đè cấu hình trắng (trước đây mất Favourite, Mount, thông tin tài khoản). Có khóa khi nhiều nơi cùng lưu.
  - **Hàng đợi được lưu** (`data/queue.json`): tắt app / app crash → mở lại thấy các tác vụ chưa xong ở trạng thái Tạm dừng, bấm ▶ Tiếp Tục.
  - **Kiểm tra kết nối SyAu chạy nền, lặp mỗi 20 giây** (`ui/main_widget.py`): không làm đứng giao diện khi SyAu tắt; mở SyAu sau thì nhãn tự cập nhật.
  - Phiên bản hiển thị đúng (`rdrive/__init__.py` = 1.2.2); `syau_dir` mặc định trỏ về `Desktop\\AI\\SyAu` (bản thật).

- **v1.2.1 (2026-11-20):**
  - **Sửa lỗi Mục #13 (`<tên chủ> Drive` ở cột cây thư mục nhỏ):** Sửa `PaneTabState.cache_key()`, `_tree_node_cache_key()`, `display_path()`, `rclone_spec()` và `_load_drive_current()` trong `rdrive/ui/explorer_pane.py` để khi bấm vào từng chủ sở hữu (`3cats1505 Drive`, `trong.phanduc34 Drive`...), khung bên phải hiển thị **đúng riêng các file/thư mục mà chủ sở hữu đó đã chia sẻ** (thay vì rơi về `Drive của tôi`).
  - **Sửa lỗi kết nối & hiển thị NAS Xpenology (`rdrive/ui/accounts_view.py`, `rdrive/ui/nas_dialog.py`, `rdrive/ui/icons.py`):**
    - Thêm biểu tượng NAS riêng (`icons.nas_icon`) thay cho logo Google Drive và nhãn `NAS (SMB/SFTP/WebDAV/FTP)`.
    - Sửa `_AccountProbeWorker`: NAS không dùng Google OAuth `token` nên kiểm tra kết nối trực tiếp bằng `rclone list_dir` thay vì báo nhầm `"Đã đăng xuất"`.
    - Cho phép bấm **`Cấu hình / Kết nối lại NAS`** để sửa thông tin NAS hiện có, tự động kiểm tra kết nối ngay khi lưu, và tự cảnh báo khi người dùng nhập nhầm cổng web DSM (`5000`/`5001`) cho giao thức SMB (cổng chuẩn SMB là **`445`**).
"""


def ensure_changelog_file() -> None:
    try:
        with open(CHANGELOG_FILE, "w", encoding="utf-8") as fh:
            fh.write(REPORT_MD)
    except Exception:
        pass


class ChangelogDialog(QDialog):
    """Hiển thị Biên bản nội dung Update & Tài liệu bàn giao cho AI Agent ngay trong app."""

    def __init__(self, parent, rclone: RcloneClient) -> None:
        super().__init__(parent)
        self.rclone = rclone
        ensure_changelog_file()
        self.setWindowTitle("Biên bản Cập nhật & Tài liệu Bàn giao Agent — Rdrive")
        self.setWindowIcon(icons.ribbon_icon("explorer", 24))
        self.resize(860, 620)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        head = QLabel(f"File biên bản được lưu tự động tại: {CHANGELOG_FILE}")
        head.setObjectName("Muted")
        lay.addWidget(head)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlainText(REPORT_MD)
        self.viewer.setStyleSheet("font-family: 'Segoe UI', Consolas, sans-serif; font-size: 9.5pt; line-height: 1.4;")
        lay.addWidget(self.viewer, 1)

        btns = QHBoxLayout()
        btn_rc = QPushButton("🔄 Kiểm tra phiên bản rclone")
        btn_rc.clicked.connect(self._check_rclone)
        btns.addWidget(btn_rc)
        btns.addStretch(1)
        btn_close = QPushButton("Đóng")
        btn_close.setObjectName("PrimaryBtn")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

    def _check_rclone(self) -> None:
        info = self.rclone.check_rclone_update()
        self.viewer.appendPlainText("\n--- KIỂM TRA RCLONE ---\n" + info)
