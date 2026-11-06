# Nhật ký thực hiện với AI

## Yêu cầu lần này

Tiếp tục dự án hiện có: cấu hình `.env` local an toàn, áp dụng migration còn thiếu nếu có, seed và xác minh ba tài khoản demo, chạy kiểm tra/test, xác nhận hành vi phân quyền, khởi động website để xem và cập nhật tài liệu. Không xây chức năng đặt bánh, không xóa dữ liệu và không tạo commit.

## Thay đổi đã thực hiện

- Kiểm tra README, settings, custom User, seed command, migration và database trước khi thao tác. Không tạo lại project hoặc xóa database.
- Tạo `.env` từ mẫu vì trước đó chưa có; sinh secret key bằng tiện ích Django và tạo DEMO_PASSWORD ngẫu nhiên. Chỉ cập nhật cấu hình local DEBUG/ALLOWED_HOSTS; không in secret hoặc mật khẩu, không ghi chúng vào tài liệu.
- Xác nhận `.env` và `.venv` bị Git ignore.
- `makemigrations`: không phát hiện thay đổi; `migrate`: không có migration cần áp dụng.
- Chạy `seed_demo` hai lần. Lệnh tạo ba tài khoản lần đầu và giữ nguyên cả ba ở lần chạy tiếp theo. Đã xác minh role CUSTOMER/STAFF/OWNER và owner không phải staff/superuser.
- Kiểm tra mật khẩu được cấu hình khớp hash của ba tài khoản mà không hiển thị giá trị mật khẩu.
- Khởi động Django tại `http://127.0.0.1:8000/`; trang chủ, trang đăng nhập và CSS trả HTTP 200. Đã mở và xem giao diện trang chủ bằng trình duyệt.

## Kiểm tra thực tế

- `.\.venv\Scripts\python.exe manage.py check`: đạt, không có lỗi hệ thống.
- `.\.venv\Scripts\python.exe manage.py test`: 12 test thành công, 0 thất bại.
- `makemigrations --check --dry-run`: không phát hiện thay đổi model chưa có migration.
- `migrate --check`: thành công.
- Bộ test xác nhận redirect người chưa đăng nhập, dashboard phù hợp cho mỗi role, 403 khi sai role, đăng ký không thể nâng role, logout yêu cầu POST/CSRF và seed chạy lặp không đổi mật khẩu cũ.
- Kiểm tra HTTP thực tế: `/`, `/login/` và `/static/css/site.css` trả 200.

## Lỗi gặp phải

Không gặp lỗi trong lần cấu hình/seed/kiểm tra/khởi động này. Không có lỗi kiểm thử còn lại.

## Tiếp tục dự án: danh mục và mẫu bánh

### Yêu cầu lần này

Chỉ triển khai phần đầu mốc 2: danh mục bánh, mẫu bánh tham khảo, trang xem công khai và quản lý OWNER. Không xây đơn hàng/báo giá, thanh toán, sản xuất, giao hàng, AI, workflow, kho hoặc khuyến mãi. Giữ nguyên database, migration/tài khoản/config đã có; không commit.

### Thay đổi chính

- Kiểm tra README, bối cảnh, log, settings, URL, User/decorator, app catalog, migrations và Git trước khi sửa; không có AGENTS.md. App catalog ban đầu chưa có model, migration hoặc dữ liệu.
- Tạo `Category` và `CakeTemplate`, migration `catalog.0001_initial` và `catalog.0002_caketemplate_catalog_cake_price_whole_vnd`, ràng buộc giá không âm/nguyên VNĐ, `PROTECT`, slug duy nhất ổn định, truy vấn dùng chung cho dữ liệu công khai.
- Tạo form kiểm tra tên/giá/danh mục; upload JPEG/PNG/WebP được Pillow đọc, tối đa 5 MiB/20 megapixel, lưu bằng tên UUID và giữ ảnh cũ nếu không upload ảnh thay.
- Tạo danh sách/chi tiết mẫu công khai; trang OWNER tìm kiếm/lọc/phân trang, thêm/sửa/ngừng và kích hoạt lại. Thao tác ghi dùng POST/CSRF; admin không cho xóa.
- Bổ sung điều hướng, số liệu catalog thực từ database trên dashboard OWNER, ảnh thay thế SVG nội bộ và xem trước ảnh chọn.
- Tạo `seed_catalog` với 3 danh mục và 6 mẫu minh họa. Lần seed đầu tạo 3 danh mục/6 mẫu; lần thứ hai tạo 0/0. Test xác nhận không ghi đè nội dung/trạng thái đã sửa.
- Trước migration, app catalog chưa có bảng. Sau đó áp dụng migration vào SQLite hiện có, không xóa dữ liệu; các bảng là `catalog_category` và `catalog_caketemplate`.
- Cổng 8000 đã được một tiến trình khác sử dụng khi khởi chạy; không dừng tiến trình đó. Website BiBi Cake chạy ở cổng khả dụng `57884`.

### Kiểm tra thực tế

- `manage.py check`: đạt, không có lỗi hệ thống.
- `manage.py test catalog`: lần chạy tập trung ban đầu đạt 26 test.
- `manage.py test`: lần xác minh cuối đạt 41 test, 0 thất bại (bao gồm test accounts hiện có và các test catalog bổ sung).
- `makemigrations --check --dry-run`: không có thay đổi model chưa tạo migration.
- `migrate --check`: thành công.
- Kiểm tra HTTP: `/cakes/`, trang chi tiết, CSS trả 200; URL quản lý khi chưa đăng nhập chuyển hướng đăng nhập (302).
- Browser: đã mở và xem danh sách/chi tiết; kiểm tra viewport 390px cho danh sách (nội dung 375px, không tràn ngang) và desktop 1440px (không tràn ngang).
- Dùng thư mục media tạm trong kiểm thử upload; test database tự dọn sau khi chạy.

### Lỗi thực tế và cách xử lý

- Lần chạy test catalog đầu tiên có một lỗi helper do truyền dữ liệu upload sai chữ ký Django Client và một assertion nhầm chuỗi tìm kiếm trong ô input với mẫu bị lộ; đã sửa helper và kiểm tra kết quả/URL mẫu ẩn thay vì chuỗi nhập.
- Lần chạy toàn bộ đầu tiên có một assertion cũ kỳ vọng dashboard OWNER vẫn là trạng thái rỗng; cập nhật test để xác nhận dashboard mới hiển thị các số liệu danh mục/mẫu thực. Chạy lại toàn bộ đạt 38/38.
- Lần đầu kiểm tra số dòng catalog trước khi áp dụng migration gặp `no such table`; xác nhận migration chưa áp dụng, sau đó migrate theo đúng quy trình và seed thành công. Không xóa database hoặc migration.

## Tiếp tục: yêu cầu đặt bánh và báo giá

### Yêu cầu lần này

Hoàn thiện mốc nghiệp vụ đơn hàng: khách gửi yêu cầu theo mẫu bánh; nhân viên kiểm tra thủ công và có thể yêu cầu bổ sung/từ chối; nhân viên gửi báo giá theo phiên bản; khách yêu cầu trao đổi hoặc xác nhận báo giá hiện hành. Giữ nguyên database hiện có, không mở rộng sang thanh toán/giao hàng/AI/workflow tự động và không commit.

### Thay đổi chính

- Tạo `Order`, `Quote`, `OrderHistory`, migration `orders.0001_initial`; migration đã được áp dụng vào database hiện tại, không xóa DB/migration.
- Chụp tên mẫu và giá tham khảo từ catalog ở backend; không nhận khách hàng/giá từ trình duyệt. Tạo đơn chống gửi trùng bằng request key và payload fingerprint.
- Thêm form tạo đơn, bổ sung yêu cầu, kiểm tra của nhân viên, yêu cầu trao đổi và báo giá; xác thực thời gian, thông tin người nhận, số lượng, đơn giá nguyên VNĐ.
- Thêm service giao dịch cho các chuyển trạng thái, lịch sử, snapshot báo giá, tính tổng tiền backend, thay thế phiên bản cũ, hạn chấp nhận và kiểm tra version/quote hiện hành.
- Bảo vệ URL bằng role CUSTOMER/STAFF ở backend; khách chỉ xem đơn của mình. Các hành động ghi yêu cầu POST/CSRF. OWNER không tự có quyền xử lý đơn.
- Thêm giao diện khách (tạo, danh sách, chi tiết, bổ sung, xác nhận/trao đổi báo giá), giao diện hàng đợi/chi tiết nhân viên, lịch sử và dashboard số liệu từ database/trạng thái rỗng.
- Bổ sung CSS responsive và JavaScript nội bộ để xem trước tổng báo giá; server vẫn tính và xác thực tổng tiền.
- Cập nhật README, PROJECT_CONTEXT.md và nhật ký này. Không ghi secret/mật khẩu vào tài liệu.

### Kiểm tra thực tế

- `.\.venv\Scripts\python.exe manage.py check`: đạt, không có lỗi hệ thống.
- `.\.venv\Scripts\python.exe manage.py test`: 54 test thành công, 0 thất bại.
- `.\.venv\Scripts\python.exe manage.py migrate --check`: thành công.
- `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`: không có thay đổi model chưa tạo migration.
- Test bổ sung bao gồm tạo đơn/chụp snapshot và giá không tin client, idempotency/conflict, kiểm tra role/ownership, bổ sung yêu cầu, tính tổng Decimal, báo giá hết hạn/phiên bản cũ, revision, chấp nhận một lần, luồng customer-staff qua HTTP, từ chối, render trang và CSRF.
- Trình duyệt mở danh sách mẫu và trang chi tiết tại `http://127.0.0.1:57884/`; nút đặt mẫu trỏ đúng URL. Đo nội dung ở kích thước mobile 390px và desktop 1440px, không phát hiện tràn ngang.
- Server cũ trên 57884 được xác nhận là tiến trình BiBi Cake nhưng dùng Python toàn cục; đã dừng đúng PID đó và khởi động lại từ `.venv`. Yêu cầu HTTP tới `http://127.0.0.1:57884/` trả 200.

### Lỗi trong quá trình kiểm thử

- Lần chạy test đầu sau khi thêm workflow có 1 lỗi và 2 assertion thất bại: test chờ sai loại lỗi khi gửi lại xác nhận đã đổi version, và test dashboard còn kỳ vọng nội dung cũ. Đã điều chỉnh test để kiểm tra hành vi version/empty-state đúng yêu cầu.
- Rà soát sau đó phát hiện khi thao tác của nhân viên gặp version cũ, redirect lỗi dùng route chi tiết dành riêng cho CUSTOMER; đồng thời URL tạo đơn trả 403 chưa dùng trang lỗi chung. Đã sửa và thêm regression test; lần chạy toàn bộ cuối cùng đạt 54/54. Không bỏ hoặc làm yếu kiểm thử.

## Tiếp tục: sản xuất, QA và duyệt ảnh

### Yêu cầu lần này

Triển khai phần tiếp nối sau khi khách xác nhận đơn: STAFF sản xuất và kiểm tra chất lượng, gửi ảnh riêng tư cho khách duyệt, xử lý yêu cầu chỉnh sửa, OWNER duyệt yêu cầu vượt phạm vi và yêu cầu khách xác nhận phụ lục nếu đổi giá/lịch. Không triển khai giao hàng, thanh toán, AI hoặc workflow tự động; giữ database/migration hiện có và không commit.

### Thay đổi chính

- Bổ sung trạng thái production, production round, QA, phiên bản ảnh, yêu cầu chỉnh sửa, quyết định OWNER và phụ lục thay đổi vào `orders`.
- Thêm service transactional có kiểm tra role, trạng thái, ownership, version và lịch sử; yêu cầu chỉnh sửa sau khi thay đổi phải qua QA lại. Giá phụ lục được tính phía server theo số lượng, đơn giá và phí giao; báo giá gốc được giữ nguyên.
- Ảnh bánh lưu trong `PRIVATE_MEDIA_ROOT`, không dùng public media URL; endpoint ảnh xác thực quyền CUSTOMER theo đơn, STAFF hoặc OWNER liên quan.
- Bổ sung POST/CSRF views, hàng đợi OWNER, số liệu dashboard STAFF/OWNER, giao diện yêu cầu duyệt/phụ lục, hiển thị giá/lịch hiện hành và preview ảnh.
- Thêm migration `orders.0002` và `orders.0003`; migration đã áp dụng vào database hiện tại, không xóa database hoặc migration.
- Cập nhật README và PROJECT_CONTEXT.md theo phạm vi mới. Không ghi secret/mật khẩu vào tài liệu.

### Kiểm tra thực tế

- `.\.venv\Scripts\python.exe manage.py test orders.test_production`: 20 test đạt, 0 thất bại.
- `.\.venv\Scripts\python.exe manage.py test`: 74 test đạt, 0 thất bại.
- `.\.venv\Scripts\python.exe manage.py check`: đạt, không có lỗi hệ thống.
- `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`: không phát hiện thay đổi model chưa có migration.
- `.\.venv\Scripts\python.exe manage.py migrate`: áp dụng thành công `orders.0002_productioncheck_revisionrequest_and_more` và `orders.0003_remove_changeagreement_orders_change_agreement_money_consistent_and_more`; `showmigrations orders` xác nhận cả hai đã áp dụng.

### Lỗi gặp phải và xử lý

- Lần chạy test đầu khi model phụ lục mới được cập nhật thất bại do migration chưa chứa trường snapshot số lượng; tạo migration đồng bộ và chạy lại.
- Các lượt test sau tìm thấy lỗi thay ảnh khi ảnh cũ còn trạng thái chờ duyệt (vi phạm unique pending review) và một assertion dashboard bị ngắt bởi thẻ `<strong>`; đổi thứ tự thao tác để đóng phiên bản cũ trước khi tạo phiên bản mới và kiểm tra count qua context.
- Bổ sung bù trừ file ảnh riêng nếu transaction database thất bại; test xác nhận file được dọn cùng với rollback. Chạy lại targeted và toàn bộ đều đạt.

## Tiếp tục: giao hàng, sự cố và ghi nhận thanh toán

### Yêu cầu lần này

Triển khai tiếp từ `SAN_SANG_GIAO`: từng lần giao hàng, xác nhận kết quả, giao lại, sự cố và quyết định OWNER; tách giao thành công khỏi thanh toán; STAFF chỉ ghi khoản tiền đã thực nhận; theo dõi hoàn tiền thực hiện bên ngoài. Không tích hợp ngân hàng, vận chuyển, GPS, email, AI hoặc workflow tự động; không xóa dữ liệu và không commit.

### Thay đổi chính

- Bổ sung migration `orders.0004_order_delivery_reschedule_note_and_more` cho `DeliveryAttempt`, `OrderIncident`, `Payment`, `Refund`, `payment_status` và các trạng thái giao/kết thúc/hủy. Migration đã áp dụng vào database hiện có; không xóa DB/migration.
- Tạo `orders/delivery.py` tập trung chuyển trạng thái trong transaction, kiểm tra role/version/trạng thái và ghi lịch sử cùng transaction. Các lần giao có số thứ tự riêng, snapshot người nhận/địa chỉ, nguồn xác nhận, lý do thất bại và minh chứng riêng tư.
- CUSTOMER xác nhận nhận bánh hoặc báo sự cố; STAFF có thể xác nhận bàn giao thay khách với ghi chú và nguồn STAFF rõ ràng. Giao thất bại không tự hủy đơn; giao lại cùng điều khoản tạo lần mới và lưu lịch.
- OWNER duyệt/từ chối phương án sự cố có lý do; STAFF thực hiện và ghi kết quả. Sửa/đổi bánh phải QA lại và được khách duyệt ảnh mới; giữ bánh hiện tại yêu cầu khách xác nhận; ảnh bánh/giao/sự cố/hoàn tiền được kiểm tra quyền qua endpoint riêng tư.
- Ghi khoản thu và hoàn bằng Decimal, kiểm tra giới hạn tiền, mã tham chiếu và mã idempotency; khoản thu/hoàn là bất biến. Chuyển khoản được đối chiếu thủ công, không gọi API; phê duyệt hủy chưa được xem là hoàn tiền đã thực hiện.
- Bổ sung chi tiết giao/tiền/sự cố cho giao diện CUSTOMER/STAFF, màn hình OWNER duyệt và xem lịch sử; hàng đợi dashboard dùng trạng thái/sự cố thật. Cập nhật README và PROJECT_CONTEXT.md.

### Kiểm tra thực tế

- `.\.venv\Scripts\python.exe manage.py check`: đạt, không có lỗi hệ thống.
- `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`: không phát hiện thay đổi model chưa có migration.
- `.\.venv\Scripts\python.exe manage.py migrate`: áp dụng thành công migration mới `orders.0004_order_delivery_reschedule_note_and_more`.
- `.\.venv\Scripts\python.exe manage.py test orders.test_delivery`: 18 test đạt, 0 thất bại.
- `.\.venv\Scripts\python.exe manage.py test`: 92 test đạt, 0 thất bại (đã chạy sau khi hoàn tất thay đổi).
- Trình duyệt tích hợp mở trang chủ tại server có sẵn `http://127.0.0.1:57885/`; trang chủ hiển thị. Chi tiết đơn CUSTOMER/STAFF và màn hình OWNER được render/kiểm tra qua Django test client. Không thay đổi hoặc dừng tiến trình đang chiếm cổng 8000.

### Lỗi gặp phải và xử lý

- Lần chạy test giao hàng đầu tiên có một lỗi ở fixture: test yêu cầu chỉnh sửa sau khi đã khách chấp nhận ảnh, trái với quy tắc hiện hành. Sửa fixture để tạo yêu cầu chỉnh sửa đang mở và kiểm tra điều kiện chặn giao đúng mục tiêu; không nới lỏng nghiệp vụ.
- Rà soát phát hiện đường OWNER quyết định sự cố trước đó chuyển hướng về trang STAFF nên OWNER gặp 403. Đã đổi về chi tiết sự cố OWNER, cho phép xem lại lịch sử nhưng ẩn form quyết định khi sự cố không còn chờ duyệt và bổ sung kiểm thử.
- Không còn lỗi kiểm tra hoặc kiểm thử tại thời điểm ghi nhật ký. Không ghi secret key hoặc mật khẩu vào tài liệu.

## Sửa lỗi nhập số tiền thanh toán

### Nguyên nhân

Ảnh người dùng gửi cho thấy trình duyệt báo “Please enter a valid value” với các giá trị hợp lệ gần nhất là `14.01` và `15.01` khi nhập `15`. Hai form thu và hoàn đều đặt `min_value=0.01` trong khi `decimal_places=0`; HTML được Django render với `step="1"` nhưng `min="0.01"`. Theo kiểm tra constraint HTML, các bước hợp lệ vì vậy lệch khỏi số nguyên VNĐ, nên trình duyệt chặn POST trước khi backend nhận yêu cầu.

### Sửa và xác minh

- Đặt mức tối thiểu của cả khoản thu và khoản hoàn thành `1` VNĐ, đồng thời khai báo `step="1"` rõ ràng. Backend vẫn xác thực số nguyên dương; không nới lỏng điều kiện tiền.
- Thêm test cho HTML form có `min="1"`/`step="1"`, chấp nhận số nguyên và từ chối phần thập phân.
- Hai test liên quan đạt; `manage.py check` đạt.
- Render trang STAFF với `HTTP_HOST=127.0.0.1` xác nhận trang chi tiết đơn trả 200 và ô tiền được render thành `<input type="number" name="amount" step="1" min="1" ...>`.
- Server local được khởi động lại tại `http://127.0.0.1:57885/`; URL chi tiết đơn khi chưa đăng nhập trả 302 về đăng nhập. Không dừng tiến trình khác trên cổng 8000.
- Lần kiểm tra trang đầu bằng Django test client mặc định dùng host `testserver` không nằm trong `ALLOWED_HOSTS` local và trả 400; lặp lại với host hợp lệ của website cho kết quả 200. Không phải lỗi ứng dụng hay thay đổi cấu hình.

## Mốc 5.1 hoàn thiện và mốc 5.2 (Django outbox workflow)

### Yêu cầu

Hoàn thiện đánh giá sau đơn và thêm outbox/webhook cho `order.confirmed`, `cake.review_requested`, `order.completed`, trang giám sát OWNER và dispatcher hữu hạn. Workflow chỉ được gửi thông báo; chưa cấu hình workflow/email thật và không triển khai chatbot/AI. Điều chỉnh mốc 24 giờ thành điểm nhắc, không phải hạn chót đánh giá.

### Thay đổi và xác minh hiện trạng

- Đọc README, bối cảnh, log và mã xử lý báo giá, ảnh duyệt, hoàn tất, đánh giá. Phát hiện migration mốc 5.1 có sẵn nhưng chưa áp dụng; hoàn tất việc áp dụng mà không xóa database/dữ liệu.
- Backfill `Order.completed_at` chỉ từ lịch sử `ORDER_COMPLETED`. Sau migrate, database hiện có 1 đơn HOAN_TAT có mốc hoàn tất và 0 đơn HOAN_TAT thiếu mốc; không dùng thời điểm migration để mở cửa sổ đánh giá.
- Hoàn thiện `OrderReview`, `ReviewReply`, form, dashboard và trang OWNER từ thay đổi mốc 5.1 sẵn có. Khách sở hữu đơn HOAN_TAT có thể đánh giá sau mốc 24 giờ; đánh giá tùy chọn, một lần/đơn; OWNER phản hồi một lần.
- Thêm `WorkflowEvent` outbox với UUID/event id, event type, order, unique business key, payload version/JSON, pending/processing/delivered/failed, tổng lần thử và lần thử trong chu kỳ, lịch retry, lease/token, HTTP status, lỗi đã làm sạch và thời điểm gửi. Thêm `WorkflowRetryRequest` lưu OWNER đã xếp retry.
- Ba service ghi event trong cùng transaction với việc xác nhận báo giá, gửi từng phiên bản ảnh hợp lệ và chuyển đơn sang HOAN_TAT. Không phát HTTP trong request nghiệp vụ hoặc GET.
- Payload được đóng băng khi tạo; chỉ gồm thông tin nhận email tối thiểu và liên kết từ `SITE_BASE_URL`, không có số điện thoại/địa chỉ/thanh toán/secret/URL ảnh riêng tư. Thiếu email hợp lệ tạo sự kiện failed với lý do và không gửi webhook.
- Thêm `dispatch_workflow_events --limit` dùng HTTPS cho endpoint bên ngoài, token Bearer, timeout giới hạn, batch tối đa 100, lease 60 giây, backoff/retry 429/5xx/timeout tối đa 5 lần mỗi chu kỳ và thu hồi processing hết lease. Tắt tích hợp mặc định; không cấu hình hoặc gửi webhook/email thật.
- OWNER lọc/giám sát tại `/owner/workflow-events/`; POST/CSRF chỉ xếp retry, giữ event_id/payload và ghi người yêu cầu. Chỉ hiển thị “Workflow đã tiếp nhận” khi webhook trả 2xx; không gọi đó là email đã gửi.
- Tạo `WORKFLOW_CONTRACT.md` mô tả schema, header, xử lý idempotency bền vững và giới hạn gửi trùng phía nhà cung cấp. Cập nhật README, PROJECT_CONTEXT và `.env.example`; không đọc/ghi đè hoặc đưa nội dung `.env` vào tài liệu.

### Kết quả kiểm tra

- Đã áp dụng các migration `orders.0005`, `orders.0006`, `orders.0007`.
- `.\.venv\Scripts\python.exe manage.py check`: đạt, không có lỗi hệ thống.
- `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`: không có thay đổi model chưa tạo migration.
- `.\.venv\Scripts\python.exe manage.py test`: 136 test đạt, 0 thất bại. Trong đó 23 test tập trung đánh giá và 17 test outbox đạt; HTTP chỉ dùng mock.
- `.\.venv\Scripts\python.exe manage.py dispatch_workflow_events --limit 5`: xác nhận tích hợp mặc định tắt và pending không bị thay đổi.
- Chưa xác minh trực quan qua browser; các route/render/permissions được kiểm tra bằng Django test client.

### Lỗi gặp phải và xử lý

- Bộ test tập trung ban đầu bắt được fixture ảnh duyệt chạy QA lần hai khi đơn đã ở trạng thái chờ duyệt và assertion dùng object ORM chưa refresh; sửa fixture/refresh theo đúng trạng thái, không thay đổi nghiệp vụ.
- Lần chạy command disabled đầu tiên gặp `UnicodeEncodeError` do terminal Windows dùng code page không biểu diễn một số ký tự tiếng Việt. Đổi output command sang ASCII và chạy lại thành công; không có request nào được gửi.
- Sửa route dashboard OWNER sai tên trong template danh sách đánh giá, để trang quay lại đúng `/owner/dashboard/`.

### Hoàn tất rà soát mốc 5.1

- Bổ sung kiểm thử gửi đánh giá muộn 30 ngày sau mốc nhắc 24 giờ, đồng thời gửi giả `customer_id`/`order_id` để xác nhận backend luôn lấy chủ đơn và người gửi từ phiên đăng nhập.
- Bổ sung kiểm thử CUSTOMER không thể phản hồi đánh giá, GET endpoint phản hồi không làm thay đổi dữ liệu, và nội dung HTML/script trong phản hồi OWNER được escape.
- Chạy lại `manage.py test orders.test_reviews`: 23/23 đạt; chạy toàn bộ suite: 136/136 đạt. `manage.py check`, `makemigrations --check --dry-run` và `migrate --check` đều đạt.
- Xác minh database hiện tại có 1 đơn HOAN_TAT với `completed_at` và 0 đơn HOAN_TAT thiếu mốc.

## Mốc tư vấn AI 5.3

### Thay đổi và xác minh

- Thêm app `assistant` với hội thoại riêng theo CUSTOMER, bản nháp gắn với form đặt bánh hiện có, truy hồi mẫu bánh công khai/chính sách OWNER công bố và quản lý chính sách riêng cho OWNER.
- Thêm adapter OpenAI Responses API có cấu hình; AI mặc định tắt. Backend giới hạn truy hồi, kiểm tra đầu ra, xử lý yêu cầu cần nhân viên và lưu log request. Không tạo đơn hay gửi webhook/email từ chat.
- Tạo migration `assistant.0001`–`assistant.0002` và áp dụng. Không xóa database/dữ liệu, không sửa `.env`, không dùng provider/API key thật.
- Kiểm tra trang `/assistant/` và CSS nội bộ bằng server tạm tại `127.0.0.1:8001`; trang hiển thị rõ AI chưa cấu hình, tài nguyên giao diện trả HTTP 200. Server tạm đã dừng; không tác động server khác.
- `.\.venv\Scripts\python.exe manage.py check`: đạt, không có lỗi.
- `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`: đạt, không có thay đổi migration chưa tạo.
- `.\.venv\Scripts\python.exe manage.py migrate --check`: đạt.
- `.\.venv\Scripts\python.exe manage.py test`: 172 test đạt, 0 thất bại. Provider được mock; không gọi API AI thật.

### Lỗi gặp phải

- Lần chạy test chatbot đầu tiên có ba lỗi do fixture/assertion trong kiểm thử mới. Đã sửa fixture/assertion theo hành vi hiện tại và chạy lại toàn bộ suite thành công; không bỏ hoặc làm yếu test.

### Giới hạn và việc tiếp theo

- AI chưa khả dụng cho tới khi cấu hình provider, API key và model trong `.env`; chưa xác minh kết quả của model thật.
- Chưa có chính sách cửa hàng được công bố cho chatbot demo. OWNER cần bổ sung nội dung đã được cửa hàng duyệt.
- Tiếp theo: người quản lý cấu hình AI khi sẵn sàng; chạy bộ tình huống kiểm thử với môi trường/provider được phê duyệt trước khi bật cho khách.

## Tích hợp Gemini cho chatbot

### Thay đổi

- Thêm dependency chính thức `google-genai==2.34.0`; thêm adapter Gemini `generate_content` với `GEMINI_API_KEY`, `GEMINI_MODEL`, giới hạn timeout và JSON structured output. Gemini được chọn mặc định; adapter OpenAI cũ vẫn chỉ dùng khi cấu hình rõ `AI_PROVIDER=openai`.
- Model mặc định được chọn là `gemini-3.8-flash`, được Google liệt kê là stable và hỗ trợ structured outputs. Giữ nguyên truy hồi catalog/chính sách công bố, xác thực schema và nguồn, giới hạn chatbot, bản nháp, phân quyền cùng luồng đặt bánh.
- Giữ `AI_ENABLED=False` mặc định; Gemini thiếu key hoặc provider/schema lỗi dùng thông báo an toàn và luồng đặt form thông thường. Key chỉ đọc trong backend; lỗi provider và nội dung phản hồi thô không ghi vào log.
- Bổ sung `GEMINI_API_KEY=` và `GEMINI_MODEL=` vào `.env` hiện có chỉ khi thiếu, giữ nguyên các trường hiện có. `.env.example` chứa key trống và model mẫu; xác nhận `.env` đang bị Git ignore. Không thực hiện request AI thật.
- Cập nhật hướng dẫn README, AI_ASSISTANT.md và PROJECT_CONTEXT.md.

### Kiểm tra thực tế

- `manage.py test assistant`: 42/42 đạt. Các request Gemini đều được mock; không có API call thật.
- `manage.py check`: đạt, không có lỗi hệ thống.
- `git diff --check`: đạt. Xác nhận `.env` bị Git ignore và không được track.

### Lỗi gặp phải và xử lý

- Test luồng Gemini thành công phát hiện log vẫn lưu model từ cấu hình OpenAI cũ. Đã thêm chọn model theo provider để Gemini log đúng `GEMINI_MODEL`; chạy lại toàn bộ 42 test đạt.

## Bổ sung phương án thay thế tại giai đoạn tiếp nhận

### Thay đổi và xác minh

- Thêm snapshot kết quả kiểm tra STAFF theo phiên bản yêu cầu và các phương án thay thế bất biến, có phiên bản, so sánh yêu cầu trước/sau và yêu cầu OWNER duyệt khi vượt quyền.
- CUSTOMER chỉ phản hồi phương án mới nhất của đơn mình. Đồng ý cập nhật yêu cầu và buộc STAFF kiểm tra lại; từ chối kết thúc tiếp nhận mà vẫn giữ đơn và lịch sử. OWNER duyệt không tự gửi phương án hoặc xác nhận đơn.
- Báo giá còn chờ được thay thế ngay khi STAFF ghi nhận yêu cầu không đáp ứng; không thể chấp nhận nhầm trong lúc lập phương án. Có thể từ chối tiếp nhận khi không có phương án khả thi.
- Thêm kiểm thử cho quyền truy cập, CSRF, trạng thái cũ, phiên bản phương án, đường đi HTTP từ STAFF qua CUSTOMER, OWNER duyệt, báo giá và phản hồi cuối. Chạy `orders.test_intake_alternatives`: 20/20 đạt.
- `manage.py check` và `makemigrations --check --dry-run` đạt. Đã áp dụng migration `orders.0008_orderalternative_staffavailabilitycheck_and_more` vào database hiện có; không xóa database hoặc dữ liệu.
- Toàn bộ `manage.py test`: 192/192 đạt. Mở trang chủ trên trình duyệt tích hợp qua server cục bộ cổng 8001; luồng STAFF/CUSTOMER được kiểm tra bằng Django HTTP test client, không thực hiện trực quan toàn bộ thao tác đăng nhập trên trình duyệt.

### Lỗi gặp phải và xử lý

- Lần chạy toàn bộ suite đầu tiên có một assertion mới dùng `history.latest("occurred_at")`; các mốc lịch sử có thể trùng thời điểm, nên có lúc assertion đọc nhầm bản ghi. Đổi test sang truy vấn đúng action `STAFF_CHECKED`; chạy lại test mục tiêu và toàn bộ suite đều đạt. Không thay đổi hoặc bỏ kiểm tra nghiệp vụ.

## Phân loại quyền tiếp nhận và duyệt báo giá

### Thay đổi và xác minh

- Tách kết quả kiểm tra khả năng đáp ứng khỏi phân loại quyền. STAFF lưu phân loại thủ công `IN_SCOPE`, `OWNER_REQUIRED` hoặc `NEEDS_INFO`, gắn với bản ghi kiểm tra và phiên bản yêu cầu; trường DB mặc định cho dữ liệu cũ là `UNCLASSIFIED`, không được gửi báo giá khi chưa rà soát.
- Tình trạng thiếu nguyên liệu/phụ kiện hoặc lịch kín không tự động tạo yêu cầu OWNER. Luồng phương án thay thế giữ nguyên và độc lập với việc phân loại quyền.
- Đơn thường được STAFF gửi báo giá trực tiếp, không tạo `ApprovalRequest`. Đơn vượt quyền gửi OWNER duyệt snapshot yêu cầu cùng giá, phí giao, lịch, hạn và ghi chú; OWNER duyệt không tự gửi báo giá/xác nhận đơn. STAFF chỉ gửi được đúng snapshot đã duyệt; thay giá/lịch cần tạo lần duyệt mới.
- OWNER từ chối chuyển đơn sang `TU_CHOI`, không thể gửi duyệt lại cùng yêu cầu. STAFF ghi nhận thông báo trong website, dashboard đưa việc cần thông báo vào hàng đợi; đây là thông báo nội bộ, không gửi email.
- Database hiện không có chính sách quyền hạn STAFF nào được công bố. `StorePolicy` phục vụ nội dung cửa hàng/trợ lý AI không được xem là chính sách phân quyền; UI yêu cầu STAFF đối chiếu quy định thực tế, không tự đặt ngưỡng hoặc dùng AI.
- Migration `orders.0009_staffavailabilitycheck_authority_basis_and_more` đã áp dụng thành công vào SQLite hiện có. Không xóa database, migration, tài khoản hay dữ liệu.
- `manage.py check`: đạt; `makemigrations --check --dry-run`: không có migration thiếu; toàn bộ `manage.py test`: **203/203 đạt**. Bao gồm luồng STAFF trực tiếp gửi báo giá/khách xác nhận, OWNER duyệt đúng snapshot, OWNER từ chối và STAFF thông báo khách. Không gọi email/webhook thật.

### Lỗi gặp phải và xử lý

- Lần chạy test mới đầu tiên phát hiện một tham số `quantity` được truyền nhầm vào hàm kiểm tra giá, gây `UnboundLocalError` trong luồng gửi OWNER duyệt. Đã chuyển tham số về helper tạo snapshot điều khoản, chạy lại test phân loại thành công rồi chạy toàn bộ suite 203/203 đạt.

### Hoàn thiện form gửi báo giá sau khi OWNER duyệt

- STAFF được điền sẵn giá, phí giao, lịch, hạn và ghi chú đúng theo snapshot OWNER đã duyệt; kiểm tra backend vẫn chặn thay đổi điều khoản nếu chưa gửi duyệt lại.
- Chuẩn hóa thời điểm báo giá đến độ chính xác phút mà form `datetime-local` hiện hỗ trợ, để giá trị hiển thị và gửi lại khớp snapshot.
- Kiểm tra lại: nhóm test phân loại 11/11 đạt; toàn bộ suite 203/203 đạt; `manage.py check` và `makemigrations --check --dry-run` đạt, không có migration mới.

## Thay đổi quy trình tiếp nhận: OWNER xử lý đơn vượt quyền

### Yêu cầu và thay đổi

- Đơn thông thường STAFF xử lý trực tiếp: kiểm tra và phân loại trong quyền, gửi báo giá; không tạo `IntakeQuoteApproval`.
- Với đơn vượt quyền, STAFF ghi lý do và chuyển nguyên đơn sang hàng đợi `/owner/intake-orders/`. OWNER nhận/kiểm tra khả năng đáp ứng, tự gửi báo giá hoặc từ chối tiếp nhận. OWNER gửi báo giá không xác nhận đơn; khách đồng ý báo giá hiện hành mới xác nhận.
- Không đủ dữ liệu để phân loại lưu kết quả `NEEDS_INFO` với nguyên liệu/lịch để trống; STAFF có thể yêu cầu khách bổ sung. Thiếu nguyên liệu hoặc lịch kín vẫn là trở ngại khả năng đáp ứng, không tự chuyển OWNER.
- Lý do OWNER từ chối được lưu công khai trên đơn, chuyển `TU_CHOI`; không cần nhân viên thông báo lại. Route và service duyệt báo giá theo quy trình cũ không còn thực hiện nghiệp vụ mới.
- OWNER phải nhận tiếp quản tường minh đối với một số đơn legacy; không tự chuyển trạng thái hay biến quyết định cũ thành kết quả kiểm tra mới. Giữ bảng/lịch sử `IntakeQuoteApproval` để bảo toàn dữ liệu.
- Thêm migration `orders.0010_order_intake_handler_role_order_rejection_reason_and_more`; đã áp dụng thành công vào SQLite hiện có. Không xóa database, migration hoặc dữ liệu.
- Cập nhật form kiểm tra STAFF, dashboard/điều hướng OWNER, hàng đợi và chi tiết OWNER, phần hiển thị khách hàng, JavaScript nội bộ, README và bối cảnh dự án.
- Test phân loại/OWNER/phương án: 32/32 đạt; toàn bộ `manage.py test`: **204/204 đạt**. `manage.py check` và `makemigrations --check --dry-run` đều đạt.

### Kiểm tra thực tế

- `manage.py check`: đạt; `makemigrations --check --dry-run`: không phát hiện thay đổi model chưa có migration.
- Test phân loại/OWNER/phương án: 32/32 đạt; toàn bộ `manage.py test`: **204/204 đạt**.
- Không xác minh trực quan qua browser, không gọi email/webhook/API ngoài.

### Lỗi gặp phải và xử lý

- Test render chi tiết OWNER phát hiện `.distinct()` được gọi sau khi `get_object_or_404` đã trả về model `Order`. Đã chuyển `.distinct()` lên queryset; test render và phân quyền OWNER chạy lại đạt.
- Rà soát nhánh OWNER từ chối cho thấy redirect không thể quay lại chi tiết đơn đã rời hàng đợi; chuyển về danh sách tiếp nhận và thêm test hồi quy, sau đó chạy lại toàn bộ suite 204/204 đạt.

## Khách báo đã chuyển khoản trước

### Yêu cầu và thay đổi

- Sau khi báo giá được khách xác nhận, CUSTOMER có thể POST “Tôi đã chuyển khoản trước” trên chi tiết đơn. Hành động chỉ lưu `OrderHistory` cùng phiên bản báo giá và số dư tại thời điểm báo; không tạo `Payment`, không đổi `payment_status` và không xác nhận giao dịch.
- STAFF xem lời báo trong chi tiết đơn, đối chiếu tiền thực nhận rồi dùng form ghi khoản thu hiện có. Đơn chỉ `HOAN_TAT` sau khi giao thành công, thu đủ và không còn công việc mở.
- Endpoint giới hạn CUSTOMER sở hữu đơn, chỉ nhận POST có CSRF, kiểm tra trạng thái/phiên bản; thao tác lặp không tạo lịch sử trùng. CUSTOMER và STAFF đều thấy thông tin phù hợp, không hiển thị lời báo như khoản thu đã xác nhận.
- Thêm lựa chọn form, service, URL và action lịch sử; migration `orders.0011_alter_orderhistory_action` đã áp dụng vào database hiện có. Cập nhật README và PROJECT_CONTEXT.md. Không tích hợp cổng thanh toán hoặc API ngân hàng.

### Kiểm tra thực tế

- `manage.py check`: đạt, không có lỗi hệ thống.
- `manage.py makemigrations --check --dry-run`: không phát hiện thay đổi model chưa có migration.
- `manage.py test orders.test_delivery`: 22/22 đạt.
- `manage.py test`: 208/208 đạt.
- `manage.py migrate orders`: migration `0011` áp dụng thành công; `showmigrations orders` xác nhận mọi migration đến `0011` đã áp dụng.
- `git diff --check`: không phát hiện lỗi whitespace.
- Chưa kiểm tra giao diện bằng browser.

### Lỗi gặp phải

- Một lần chạy test lặp POST sau khi cùng test client đã đăng nhập STAFF, nên endpoint CUSTOMER trả 403 như thiết kế. Test được sửa để đăng nhập lại CUSTOMER trước lần gửi lặp; các lần chạy kiểm tra sau đó đều đạt.

## Sửa chatbot tư vấn theo ngân sách

### Thay đổi

- Nhận diện ngân sách dạng `200k`, `200.000đ` và `200 nghìn`; khi có ngân sách, chỉ truy hồi mẫu công khai, đang nhận đặt và có giá tham khảo không vượt quá ngân sách.
- Khi provider lỗi ở câu hỏi tư vấn theo ngân sách, chatbot trả lời dự phòng dựa trên danh mục thật, hiển thị thẻ mẫu; nếu catalog không có mẫu phù hợp thì nói rõ. Không tạo đơn/bản nháp, không đoán khẩu phần; yêu cầu nhân viên xác nhận khi khách nêu số người.
- Ghi trạng thái `fallback` riêng và giữ lại nhóm lỗi provider; cập nhật giao diện hiển thị thẻ cho trạng thái này.
- Đổi tên context hội thoại để không đè Django messages; tránh hiện chuỗi `ChatMessage object` trên trang chat. Cập nhật hướng dẫn AI và nhật ký này.

### Kiểm tra thực tế

- `manage.py test assistant`: 45/45 đạt; kiểm thử parser, lọc giá, fallback provider, không tạo đơn/bản nháp và hiển thị trang chat.
- Sau chỉnh sửa context giao diện: test fallback/render mục tiêu đạt 1/1.
- `manage.py check`, `makemigrations --check --dry-run` và `git diff --check`: đạt.
- Mở hội thoại bằng Chrome/Playwright và gửi yêu cầu bánh sinh nhật cho 1 người, ngân sách 200.000đ. Provider ghi nhận `provider_unavailable` sau khoảng 9,2 giây; chatbot dự phòng hiển thị hai mẫu thật: Cupcake kem vani 45.000đ và Cupcake chocolate 50.000đ. Trang nêu rõ giá tham khảo, thiếu dữ liệu khẩu phần và cần nhân viên xác nhận. Không tạo đơn hoặc bản nháp.

### Lỗi gặp phải và xử lý

- Lần chạy test đầu phát hiện parser nhận nhầm số người là ngân sách trong một số câu có số tiền theo dạng `200.000đ`/`200 nghìn`; đã giới hạn ngữ cảnh nhận diện ngân sách sát cụm từ chỉ ngân sách và chạy lại toàn bộ test chatbot đạt.
- Kiểm tra trình duyệt phát hiện context tên `messages` bị trùng với Django messages; đã đổi sang `chat_messages`, kiểm thử render lại đạt.

## Sửa phản hồi dự phòng khi Gemini lỗi

### Thay đổi

- Khi câu nhắn mới chỉ là lời chào, fallback ưu tiên phản hồi chào hỏi và thông báo rõ AI chưa phản hồi; không dùng các câu hỏi cũ trong hội thoại để tự ý đưa danh sách bánh.
- Giữ nguyên tra cứu catalog/chính sách khi khách hỏi cụ thể; không tạo đơn hoặc bản nháp từ fallback.

### Kiểm tra thực tế

- Ba test mục tiêu cho lời chào, tra cứu mẫu khi provider lỗi và chế độ AI tắt: đạt.
- `manage.py check` và `git diff --check`: đạt.
- Log thực tế trước đó ghi `provider_unavailable` với model `gemini-3.8-flash`; log không lưu status HTTP cụ thể hoặc nội dung phản hồi của Google nên chưa xác định được nguyên nhân upstream chi tiết.

## Triển khai tư vấn theo thuộc tính catalog

### Thay đổi

- Bổ sung trường tùy chọn cho dịp, hương vị, phong cách, khẩu phần, thành phần và thông tin dị ứng; tạo migration bổ sung, giữ mặc định trống cho dữ liệu cũ.
- OWNER có thể cập nhật các thuộc tính trên form mẫu bánh; trang chi tiết công khai chỉ hiện trường có dữ liệu và nhắc khách chuyển nhân viên xác nhận nội dung dị ứng.
- Backend lọc ngân sách trước, sau đó xếp hạng theo tên, danh mục, thuộc tính tư vấn và mô tả. Chỉ đưa tối đa số mẫu giới hạn hiện có vào Gemini; system prompt yêu cầu lý do gợi ý dựa trên thuộc tính được cung cấp và không suy diễn khẩu phần.
- Ngữ cảnh Gemini chứa thuộc tính catalog có giới hạn độ dài. Fallback ngân sách hiển thị rõ thông tin khẩu phần nếu cửa hàng nhập, vẫn yêu cầu nhân viên xác nhận phù hợp với số người.
- Thêm kiểm thử lưu trường OWNER, hiển thị chi tiết, xếp hạng hương vị/dịp, lọc ngân sách và ngữ cảnh provider.

### Kiểm tra thực tế

- Năm test mục tiêu catalog/assistant đạt tại lần kiểm tra đầu.
- `makemigrations --check --dry-run` và `git diff --check` đạt.
- Chưa gọi Gemini thật; quyền truy cập API/model và quota chưa được xác minh.

## Điền gợi ý catalog demo có nhãn chưa xác nhận

### Thay đổi

- Thêm migration `catalog.0004` với cờ OWNER xác nhận dữ liệu tư vấn; mặc định chưa xác nhận. Khi thay đổi trường tư vấn đã xác nhận, form tự gỡ cờ và thông báo OWNER cần rà soát lại.
- Thêm lệnh `seed_demo_advisory_data`, chỉ lấp các trường trống theo tên/mô tả hiện có, không ghi đè nội dung OWNER, bỏ qua mẫu đã xác nhận và không điền khẩu phần/thành phần/dị ứng.
- Trang mẫu công khai phân biệt nội dung đã xác nhận với gợi ý demo. Gemini nhận trạng thái xác nhận; backend buộc thêm cảnh báo nếu phản hồi gợi ý mẫu chưa xác nhận.
- Cập nhật test và hướng dẫn OWNER chạy seed/rà soát trong README.
- Đã seed 6 mẫu hiện có theo tên/mô tả, tất cả vẫn chưa xác nhận; chạy lại cập nhật 0, bỏ qua 6. Giữ trống khẩu phần, thành phần và dị ứng.

### Kiểm tra thực tế

- `manage.py test assistant catalog`: 95/95 đạt.
- Toàn bộ `manage.py test`: 238/238 đạt.
- `manage.py check`, `makemigrations --check --dry-run`, `migrate --check` và `git diff --check`: đạt.
- Migration `catalog.0004` đã áp dụng thành công vào database hiện tại; không xóa dữ liệu.
- Không gọi Gemini/API thật.

## 2026-11-06 — Loại bỏ chatbot

### Yêu cầu

- Gỡ chatbot tư vấn và các phần chỉ phục vụ hội thoại, bản nháp, nhật ký gọi AI.
- Giữ danh mục/chính sách, đặt bánh trực tiếp, nghiệp vụ đơn hàng và workflow webhook.
- Tạo migration mới nhưng không áp dụng lên database dự án; không xây chức năng phân loại đánh giá bằng AI.

### Thay đổi

- Xóa adapter AI, form/view/URL chat, template, CSS/JavaScript chat và tài liệu triển khai chatbot; giữ app `assistant` để quản lý `StorePolicy`.
- Bỏ nhánh nạp/claim bản nháp trong form tạo đơn, giữ nguyên biểu mẫu đặt bánh trực tiếp.
- Thêm trang danh sách chính sách công khai; cập nhật điều hướng và trang OWNER.
- Tạo `assistant.0004` để xóa `AIRequestLog`, `AIDraft`, `ChatMessage`, `ChatSession`; giữ nguyên migration cũ.
- Xóa các cài đặt AI khỏi `config/settings.py`, `.env.example` và `.env` local; gỡ `google-genai` khỏi `requirements.txt` và `.venv`.
- Cập nhật README, PROJECT_CONTEXT và tài liệu thiết kế/kiểm thử liên quan. Không sửa workflow outbox/email hoặc thêm AI phân loại đánh giá.

### Kiểm tra thực tế

- `manage.py check`: đạt, không có lỗi.
- `manage.py makemigrations --check --dry-run`: đạt, không còn thay đổi model chưa có migration.
- `manage.py test`: 189/189 đạt trên test database tạm; migration được chạy trên test DB và test DB được hủy khi hoàn tất.
- `git diff --check`: đạt.
- Lần chạy test đầu phát hiện migration tự sinh không tương thích với thứ tự dựng lại bảng SQLite; sửa migration mới thành xóa model trực tiếp theo quan hệ phụ thuộc và chạy lại thành công.
- Không chạy `migrate` trên database dự án; dữ liệu chat cũ chỉ bị xóa nếu người dùng chủ động áp dụng migration `assistant.0004`.
