# Báo cáo mô tả ứng dụng và đối chiếu yêu cầu kỹ thuật

**Đề tài:** Hệ thống quản lý quy trình đặt và giao bánh BiBi Cake
**Môn học:** Quản lý quy trình kinh doanh
**Ngày lập:** 02/11/2026

## 1. Mô tả ứng dụng

BiBi Cake là ứng dụng web xây dựng bằng Django, cung cấp các chức năng phục vụ quy trình từ tiếp nhận yêu cầu bánh đến báo giá, sản xuất, giao hàng và chăm sóc sau đơn. Giao diện dùng Django Templates, HTML/CSS/JavaScript thuần; dữ liệu demo được lưu bằng SQLite.

Các nhóm chức năng chính:

- Tài khoản, đăng nhập và phân quyền khách hàng, nhân viên, chủ cửa hàng.
- Quản lý danh mục và mẫu bánh.
- Khách gửi yêu cầu đặt bánh; nhân viên kiểm tra khả năng đáp ứng và lập báo giá; khách xem và phản hồi báo giá.
- Theo dõi sản xuất, kiểm tra chất lượng, gửi ảnh bánh để khách duyệt và xử lý yêu cầu chỉnh sửa.
- Điều phối giao hàng, xác nhận thanh toán, xử lý sự cố giao hàng, đánh giá đơn và phản hồi đánh giá.
- Trợ lý AI tư vấn dựa trên danh mục bánh và chính sách cửa hàng đã công bố.
- Ghi nhận sự kiện workflow vào outbox và có thể gửi webhook đến hệ thống bên ngoài khi được cấu hình.

Ứng dụng là bản demo đồ án. Một số tích hợp cần cấu hình dịch vụ/khóa riêng trước khi có thể gọi dịch vụ thật; ứng dụng không tự xác nhận thanh toán ngân hàng.

## 2. Đối chiếu sáu yêu cầu kỹ thuật tối thiểu

| # | Yêu cầu | Kết quả hiện tại | Bằng chứng và ghi chú |
|---|---|---|---|
| 1 | Xác thực với ít nhất 3 vai trò | **Đạt** | Có ba vai trò `CUSTOMER`, `STAFF`, `OWNER`; quyền được kiểm tra ở backend. Người chưa đăng nhập được chuyển tới đăng nhập, người đã đăng nhập sai vai trò nhận HTTP 403. |
| 2 | Ít nhất 3 luồng phê duyệt theo cổng XOR | **Đạt ở mức luồng nghiệp vụ có nhánh điều kiện** | Có các nhánh lựa chọn loại trừ nhau để điều hướng và quyết định: phân loại quyền xử lý đơn; phân loại phạm vi yêu cầu chỉnh sửa; phương án thay thế cần/chưa cần chủ duyệt; chủ duyệt hoặc từ chối phương án sự cố. Các nhánh được thực thi bằng trạng thái và điều kiện trong Django, không phải bằng một bộ máy BPMN riêng. |
| 3 | Lưu trữ bền vững | **Đạt** | Cấu hình SQLite tại `db.sqlite3`; dữ liệu nghiệp vụ được ánh xạ bằng Django Models và có migration được lưu trong các app. |
| 4 | Ít nhất 1 tích hợp bên ngoài | **Đã triển khai mã tích hợp; cần cấu hình để sử dụng dịch vụ thật** | Trợ lý có adapter gọi Gemini và OpenAI. Ngoài ra, workflow outbox có thể gửi webhook HTTPS. Khóa và URL lấy từ biến môi trường; webhook tắt mặc định. Bài kiểm thử dùng mock, không chứng minh kết nối thật tới nhà cung cấp. |
| 5 | Bộ kiểm thử tự động | **Đạt** | Đã chạy `manage.py test`: **239/239 kiểm thử thành công**. Có kiểm thử cho quyền, luồng đơn, các nhánh duyệt, webhook và adapter AI; lời gọi ra dịch vụ ngoài được mock trong kiểm thử tích hợp. |
| 6 | Ít nhất 15 commit Git | **Chưa đạt** | Repository đã được `git init` nhưng nhánh hiện tại chưa có commit nào (`HEAD` chưa tồn tại). Các file dự án đang ở trạng thái chưa được theo dõi. Không tạo commit trong lần lập báo cáo này. |

## 3. Mô tả các nhánh XOR trong quy trình

Trong ứng dụng, XOR được thể hiện bằng điều kiện nghiệp vụ: tại một điểm quyết định, trạng thái/điều kiện chọn một nhánh để tiếp tục. Các quyết định và chuyển trạng thái được lưu trong cơ sở dữ liệu để truy vết.

### Luồng 1 — Phân loại quyền xử lý yêu cầu đặt bánh

1. Nhân viên kiểm tra khả năng đáp ứng nguyên liệu và lịch.
2. Nhân viên phân loại yêu cầu là trong phạm vi, vượt quyền chủ cửa hàng, hoặc chưa đủ thông tin.
3. Nếu trong phạm vi, nhân viên tiếp tục xử lý trực tiếp.
4. Nếu vượt quyền, yêu cầu được chuyển cho chủ cửa hàng tiếp nhận và xử lý.
5. Nếu chưa đủ thông tin, luồng chờ bổ sung/kiểm tra lại thay vì đi tiếp như yêu cầu đã đủ điều kiện.

Đây là cổng điều hướng loại trừ nhau theo kết quả phân loại. Chủ cửa hàng xử lý yêu cầu đã được chuyển; đây không phải quy trình “nhân viên lập báo giá rồi xin chủ duyệt báo giá” trong quy trình mới.

### Luồng 2 — Phân loại phạm vi yêu cầu chỉnh sửa bánh

1. Nhân viên phân loại yêu cầu chỉnh sửa là trong hoặc ngoài phạm vi đã thống nhất.
2. **Trong phạm vi:** nhân viên có thể tiếp tục chỉnh sửa; sau đó vẫn cần kiểm tra chất lượng và gửi ảnh mới để khách duyệt.
3. **Ngoài phạm vi:** yêu cầu chuyển sang chờ chủ cửa hàng duyệt; hệ thống lưu lý do, tóm tắt và phương án đề xuất.
4. Chủ cửa hàng chọn duyệt hoặc từ chối. Nếu thay đổi giá/lịch, khách phải phản hồi phụ lục trước khi điều khoản mới được áp dụng.

### Luồng 3 — Phương án thay thế khi yêu cầu ban đầu chưa đáp ứng

1. Nhân viên lập phương án thay thế và xác định có cần chủ cửa hàng duyệt hay không.
2. **Không cần chủ duyệt:** phương án được gửi cho khách phản hồi.
3. **Cần chủ duyệt:** phương án đi vào hàng đợi chủ cửa hàng; chỉ sau khi được duyệt mới có thể gửi khách.
4. Chủ cửa hàng duyệt hoặc từ chối; phương án bị từ chối quay lại nhân viên, không được trình bày như phương án đã gửi cho khách.

### Luồng 4 — Duyệt phương án xử lý sự cố đơn hàng

1. Nhân viên đề xuất phương án xử lý, chẳng hạn giao lại, sửa bánh, làm bánh thay thế hoặc hủy/hoàn tiền theo điều kiện nghiệp vụ.
2. Sự cố chuyển sang trạng thái chờ chủ cửa hàng duyệt.
3. Chủ cửa hàng chọn chấp thuận hoặc yêu cầu phương án khác/từ chối.
4. Chỉ nhánh được chấp thuận mới chuyển sang bước thực hiện tương ứng; việc hoàn tiền còn cần ghi nhận khoản hoàn thực tế.

## 4. Bằng chứng kỹ thuật và cách kiểm tra

### Xác thực và phân quyền

- `accounts/models.py`: định nghĩa các vai trò nghiệp vụ.
- `accounts/decorators.py`: kiểm tra đăng nhập và vai trò; sai vai trò ném `PermissionDenied`.
- `config/settings.py`: khai báo `AUTH_USER_MODEL = "accounts.User"`.
- `accounts/tests.py`: kiểm thử đăng ký không thể tự nâng vai trò, băm mật khẩu, đăng nhập đúng/sai, dashboard theo vai trò, chuyển hướng người chưa đăng nhập và HTTP 403 khi sai vai trò.

### Các cổng điều kiện và phê duyệt

- `orders/services.py`: chuyển tiếp tiếp nhận đơn và phương án thay thế; điều kiện kiểm soát vai trò, phiên bản và trạng thái.
- `orders/production.py`: phân loại phạm vi chỉnh sửa, tạo yêu cầu chủ duyệt và ghi nhận quyết định.
- `orders/delivery.py`: gửi và quyết định phương án xử lý sự cố.
- `orders/models.py`: lưu trạng thái, người quyết định, lý do và thời điểm phê duyệt.
- Các bộ kiểm thử `orders/test_intake_classification.py`, `orders/test_intake_alternatives.py`, `orders/test_production.py` và `orders/test_delivery.py` kiểm chứng các nhánh chính.

### Lưu trữ

- `config/settings.py`: SQLite sử dụng `BASE_DIR / "db.sqlite3"`.
- `accounts/migrations/`, `catalog/migrations/`, `orders/migrations/`: lịch sử schema.
- Các model trong `accounts`, `catalog`, `orders` và `assistant` lưu dữ liệu tương ứng bằng ORM Django.

### Tích hợp ngoài

- `assistant/ai.py`: adapter Gemini và OpenAI Responses API.
- `config/settings.py`: cấu hình provider, model, thời hạn chờ và trạng thái bật/tắt AI qua biến môi trường.
- `orders/workflow_events.py`: giao sự kiện outbox qua webhook; `WORKFLOW_ENABLED` mặc định tắt.
- `orders/test_workflow_events.py` và `assistant/tests.py`: kiểm thử phản hồi, lỗi, retry và xử lý dữ liệu với lời gọi mạng được giả lập.

**Giới hạn của bằng chứng:** lượt kiểm tra này xác nhận mã tích hợp và kiểm thử tự động, không gửi yêu cầu thật đến Gemini, OpenAI hay webhook bên ngoài. Khi trình bày demo, cần bật/cấu hình dịch vụ phù hợp và xác nhận kết nối trong môi trường của nhóm.

### Kiểm thử tự động

Các lệnh đã chạy trong môi trường `.venv`:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

Kết quả ghi nhận: Django system check không có lỗi; **239 kiểm thử chạy thành công**.

### Git và yêu cầu 15 commit

Repository hiện đang ở nhánh `main`, nhưng chưa có commit đầu tiên. Vì vậy, báo cáo không thể xác nhận yêu cầu tối thiểu 15 commit. Không nên tạo commit giả chỉ để tăng số lượng; cần đưa các thay đổi thực tế vào lịch sử Git theo từng phần công việc có ý nghĩa và xác nhận lại bằng:

```powershell
git rev-list --count HEAD
git --no-pager log --oneline
```

## 5. Kết luận

Ứng dụng hiện đã có ba vai trò được bảo vệ ở backend, các nhánh điều kiện/phê duyệt cho nhiều quy trình nghiệp vụ, cơ sở dữ liệu SQLite với migration, mã tích hợp AI và webhook, cùng bộ kiểm thử tự động đã chạy thành công. Điểm còn thiếu rõ ràng so với sáu yêu cầu là **ít nhất 15 commit Git**; repository chưa có commit nào. Ngoài ra, tích hợp bên ngoài cần được bật/cấu hình và thử kết nối thật trước khi dùng làm bằng chứng demo trực tiếp.
