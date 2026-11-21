# Thông báo về thành phần bên thứ ba

Rdrive là giao diện điều khiển; nó **không đi kèm** rclone hay Python. `setup.bat` tải trực tiếp từ nguồn chính thức về máy người dùng.

| Thành phần | Giấy phép | Cách dùng |
|---|---|---|
| [rclone](https://rclone.org) | MIT | Tải từ downloads.rclone.org khi cài; Rdrive gọi như chương trình ngoài |
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) | LGPLv3 | Cài bằng pip, liên kết động (không sửa mã Qt) |
| Python | PSF License | Bản embeddable tải từ python.org khi cài |
| Speedtest CLI (Ookla) | Giấy phép riêng của Ookla (cá nhân, phi thương mại) | **Không** đi kèm và không tự tải; người dùng tự tải nếu muốn dùng |

"Google Drive" và "Google" là nhãn hiệu của Google LLC. Rdrive là ứng dụng độc lập, không liên kết với hoặc được Google chứng nhận.
"Synology", "Xpenology" nhắc đến chỉ để mô tả thiết bị tương thích.
Mỗi người dùng phải tự tạo thông tin đăng nhập Google (client ID) của mình và tuân theo Điều khoản dịch vụ Google API.
