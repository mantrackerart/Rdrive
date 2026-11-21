# Rdrive — Quản lý & Backup Google Drive đa tài khoản (rclone)

Ứng dụng quản lý, upload, download, backup và đồng bộ Google Drive chạy trên nền **rclone**, giao diện 2 khung (trái/phải), hỗ trợ:

- **Đăng nhập nhiều tài khoản Google Drive** (OAuth trực tiếp qua trình duyệt, nhận diện sẵn các remote trong `rclone.conf`, hỗ trợ `Drive của tôi`, `Được chia sẻ với tôi`, `Shared Drives / Team Drives` và mở trực tiếp từ link folder).
- **2 khung duyệt độc lập** (Trái / Phải) có nhiều tab (`This PC` + các tài khoản Google), cây thư mục bên trái, danh sách file bên phải, nút truyền nhanh `▶` `◀` `⇄` và khóa điều hướng `🔗`.
- **Hàng đợi truyền tải nền** với tiến độ %, tốc độ MB/s, thời gian còn lại (ETA), hủy/thử lại và nhật ký `rclone` trực tiếp.
- **Đồng bộ & Backup (`Đồng bộ`)** hỗ trợ xem trước `Dry-run` trước khi chạy thật (`copy` an toàn hoặc `sync` phản chiếu).
- **Liên kết Local AI & Cloud AI (`AI Quản Lý`)**: hỗ trợ Ollama / LM Studio (local) và Gemini / OpenAI API (cloud).
- **Khung Auto / Scheduler (`Auto / Lịch`)**: sẵn sàng mở rộng các quy tắc tự động sắp xếp thư mục & auto backup.
- **Liên kết với SyAu** (app riêng, chạy song song): chuột phải file trên máy → gửi sang Converter của SyAu qua cổng nội bộ `127.0.0.1:47811`. Phần tự động upload sau render sẽ nối qua cổng trung gian (đang thiết kế). **Không** nhúng Rdrive vào SyAu và không dùng `SyAu-1.0.0-backup` (bản dự phòng cũ).

## Cách mở
- Bấm đúp `Rdrive.bat` (hoặc `Rdrive_debug.bat` để xem console).

## Quy tắc khi sửa code
Agent nào sửa Rdrive phải đọc `QUY_TAC_AGENT.md` trước.
