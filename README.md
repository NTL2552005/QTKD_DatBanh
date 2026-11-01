# BiBi Cake

Nền tảng demo đặt và giao bánh BiBi Cake cho đồ án Quản lý quy trình kinh doanh. Phạm vi hiện tại gồm tài khoản/phân quyền, danh mục/mẫu bánh, yêu cầu đặt bánh/báo giá, sản xuất/kiểm tra chất lượng/duyệt ảnh, giao hàng, xử lý sự cố, thu/hoàn tiền, đánh giá sau đơn và outbox sự kiện workflow.

## Yêu cầu

- Windows và PowerShell
- Python trong môi trường ảo `.venv`
- Các gói trong `requirements.txt`

## Cấu hình lần đầu

Tại thư mục dự án, tạo môi trường ảo nếu chưa có và cài dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Tạo `.env` từ mẫu mà không ghi đè file đã có:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Tạo khóa bí mật bằng Python trong `.venv`:

```powershell
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Điền khóa vừa tạo vào `DJANGO_SECRET_KEY`; đặt `DJANGO_DEBUG=True`, `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1` cho môi trường local và đặt `DEMO_PASSWORD` thành mật khẩu đủ mạnh. Giữ `.env` trên máy cục bộ, không đưa lên Git. Lệnh seed không in mật khẩu.

Trong workspace hiện tại, `.env` local đã được tạo/cấu hình, database đã migrate và ba tài khoản demo đã seed. Các giá trị nhạy cảm chỉ được lưu trong `.env`, không ghi vào tài liệu.

## Khởi tạo và chạy

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py seed_catalog
.\.venv\Scripts\python.exe manage.py runserver
```

`seed_demo` tạo `customer_demo`, `staff_demo` và `owner_demo` với mật khẩu lấy từ `DEMO_PASSWORD`. Lệnh bỏ qua tài khoản đã tồn tại và không thay đổi mật khẩu hiện có. Tài khoản owner demo không phải Django superuser/staff.

Xem mật khẩu demo local trong `.env` bằng PowerShell/Notepad:

```powershell
notepad .env
```

Ba tài khoản demo có thể dùng để kiểm tra phân quyền:

| Vai trò | Tên đăng nhập | Trang sau đăng nhập |
|---|---|---|
| Khách hàng | `customer_demo` | `/customer/dashboard/` |
| Nhân viên | `staff_demo` | `/staff/dashboard/` |
| Chủ cửa hàng | `owner_demo` | `/owner/dashboard/` |

Mật khẩu demo được lấy từ `DEMO_PASSWORD` trong `.env`; lệnh `seed_demo` không đổi mật khẩu tài khoản đã tồn tại. Xem cục bộ bằng `notepad .env`, không chia sẻ hoặc đưa file này lên Git. Để chạy website local, dùng `.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000`, rồi mở `http://127.0.0.1:8000/`.

`seed_catalog` tạo dữ liệu minh họa gồm ba danh mục và sáu mẫu bánh. Lệnh chạy lặp không tạo trùng và giữ nguyên dữ liệu danh mục/mẫu đã được chỉnh sửa.

## Danh mục và mẫu bánh

- Khách chưa đăng nhập, CUSTOMER và STAFF có thể xem `/cakes/` và `/cakes/<slug>/`.
- OWNER quản lý danh mục tại `/owner/catalog/categories/` và mẫu bánh tại `/owner/catalog/cakes/`.
- Form thêm/sửa danh mục nằm dưới `/owner/catalog/categories/add/` và `/owner/catalog/categories/<id>/edit/`; form thêm/sửa mẫu bánh nằm dưới `/owner/catalog/cakes/add/` và `/owner/catalog/cakes/<id>/edit/`.
- Chỉ role `OWNER` được phép gọi URL quản lý; `is_staff`/`is_superuser` không thay thế role nghiệp vụ.
- Không có chức năng xóa trong giao diện quản lý; danh mục và mẫu được ngừng hoạt động/nhận đặt bằng POST có CSRF.
- Các mẫu công khai cần đồng thời đang nhận đặt và thuộc danh mục đang hoạt động. Ngừng danh mục sẽ ẩn mẫu thuộc danh mục đó mà không thay đổi trạng thái của từng mẫu.
- Giá là giá tham khảo nguyên VNĐ; giá cuối cùng và lịch giao được xác nhận theo yêu cầu. “Đang nhận đặt” không biểu thị tồn kho.
- Có thể thêm hoặc thay ảnh trong form OWNER. Chỉ chấp nhận JPEG, PNG hoặc WebP đọc được bởi Pillow, tối đa 5 MiB và 20 megapixel. Tệp được lưu tên ngẫu nhiên; nếu không tải ảnh mới khi sửa thì ảnh cũ được giữ lại. Trang local dùng ảnh thay thế nội bộ khi chưa có ảnh.
- SQLite lưu các bảng `catalog_category` và `catalog_caketemplate`. Dữ liệu minh họa của `seed_catalog` (không phải báo giá):
  - Bánh sinh nhật: Bánh kem dâu tươi (350.000 đ), Bánh sinh nhật hoa kem (480.000 đ).
  - Bánh kỷ niệm: Bánh kỷ niệm trái tim (520.000 đ), Bánh kỷ niệm tối giản (450.000 đ).
  - Bánh cupcake: Cupcake kem vani (45.000 đ), Cupcake chocolate (50.000 đ).
- `MEDIA_ROOT`/`MEDIA_URL` được phục vụ trực tiếp chỉ khi `DEBUG=True` để demo local; cần cấu hình lưu trữ/phục vụ media phù hợp trước khi triển khai production.

## Đơn hàng và báo giá

- Khách hàng đăng nhập chọn mẫu công khai tại `/cakes/<slug>/` rồi gửi yêu cầu tại `/orders/new/<slug>/`. Hệ thống chụp tên mẫu/giá tham khảo tại thời điểm đặt; không nhận giá hoặc chủ đơn do trình duyệt gửi.
- Khách theo dõi đơn tại `/orders/mine/`; trang chi tiết hiển thị yêu cầu, các phiên bản báo giá và lịch sử xử lý. Chủ đơn được kiểm tra ở backend; truy cập đơn của người khác trả 404.
- Nhân viên xử lý hàng đợi tại `/staff/orders/`: ghi nhận khả năng đáp ứng và phân loại quyền thủ công. Đơn thông thường STAFF xử lý trực tiếp. Chỉ đơn vượt quyền mới được chuyển cho OWNER trực tiếp kiểm tra, gửi báo giá hoặc từ chối.
- Báo giá lưu snapshot riêng, tổng tiền được backend tính bằng Decimal và ràng buộc cơ sở dữ liệu. STAFF gửi báo giá cho đơn trong quyền; OWNER gửi báo giá cho đơn đã chuyển mình và đã kiểm tra đủ điều kiện. Báo giá có phiên bản, hạn chấp nhận và lịch sử; báo giá mới thay phiên bản cũ, cần nêu lý do khi sửa.
- Khách chỉ xác nhận được báo giá hiện hành, còn hạn và chưa bị thay thế. Có thể yêu cầu trao đổi; gửi lặp hoặc gửi từ trang cũ không xác nhận/gửi lịch sử trùng.
- Trạng thái đơn: `CHO_KIEM_TRA` → `CAN_BO_SUNG` → `CHO_KIEM_TRA`; từ kiểm tra sang `CHO_KHACH_XAC_NHAN`; có thể trao đổi qua `CHO_TRAO_DOI`, hoặc chốt `DA_XAC_NHAN`/kết thúc `TU_CHOI`.
- Mỗi thao tác thay đổi dùng POST/CSRF, phân quyền backend, transaction và version check để phát hiện trang cũ/tranh chấp. Tạo đơn dùng request key để chống gửi trùng.

### Kiểm tra khả năng đáp ứng và phương án thay thế

- STAFF ghi nhận riêng việc nguyên liệu và lịch làm có đáp ứng không, nguyên nhân, ghi chú, người kiểm tra, thời điểm và snapshot phiên bản yêu cầu. Nếu thiếu thông tin, STAFF có thể yêu cầu khách bổ sung; không gửi báo giá cho đến khi rà soát lại. Đây là kiểm tra thủ công, không có kho nguyên liệu hoặc tự suy luận tồn kho.
- **Đơn thông thường STAFF xử lý trực tiếp. Chỉ đơn vượt quyền mới gửi OWNER duyệt.** STAFF phân loại thủ công trong/ngoài quyền hoặc chưa đủ thông tin; yêu cầu phải ghi căn cứ, và trường hợp vượt quyền phải chọn lý do cùng mô tả cụ thể. Thiếu nguyên liệu/lịch kín là “chưa đáp ứng”, không tự động thành đơn vượt quyền.
- Hiện chưa có chính sách quyền hạn STAFF được công bố/cấu hình; giao diện nhắc đối chiếu quy định cửa hàng. Không có ngưỡng giá, số tầng, số lượng hay thời gian do hệ thống tự suy ra. Chính sách cửa hàng ở phần trợ lý hiện không chứa chính sách phân quyền đã công bố.
- Nếu vượt quyền, STAFF chuyển nguyên đơn cùng snapshot yêu cầu và lý do đến hàng đợi `/owner/intake-orders/`. OWNER kiểm tra nguyên liệu/lịch rồi tự gửi báo giá cho khách hoặc từ chối. Trong lúc chờ OWNER, STAFF không thể đổi phân loại hay gửi báo giá; khách chỉ được xác nhận báo giá hiện hành. OWNER gửi báo giá không tự xác nhận đơn.
- OWNER từ chối thì đơn kết thúc ở `TU_CHOI`; lý do công khai được lưu ngay trên đơn và khách xem tại chi tiết đơn. Không cần STAFF thông báo lại; thao tác này chưa gửi email và không thể mở lại tiếp nhận.
- Nếu không đáp ứng, STAFF có thể lập phương án đầy đủ (mẫu, kích thước, vị, số lượng, nội dung/trang trí, lịch và giải thích khác biệt), xem lại bản nháp rồi gửi khách hoặc chuyển OWNER duyệt nếu vượt quyền. Giá nếu nhập chỉ là dự kiến, chưa phải báo giá.
- Phương án thay thế là luồng riêng với việc phân loại quyền của yêu cầu ban đầu. Phương án trong quyền STAFF có thể gửi khách; phương án cần chủ duyệt vẫn dùng hàng đợi phê duyệt phương án tại `/owner/revision-approvals/`. Không dùng cơ chế duyệt phương án để thay thế việc chuyển một yêu cầu vượt quyền cho OWNER xử lý trực tiếp.
- Khách phản hồi tại chi tiết đơn. Đồng ý chỉ cập nhật yêu cầu và đưa về `CHO_KIEM_TRA`; STAFF phải kiểm tra lại rồi mới lập báo giá. Đồng ý phương án không xác nhận đơn/giá. Từ chối kết thúc tiếp nhận với `TU_CHOI`, không xóa đơn.
- Mỗi yêu cầu có revision; kiểm tra cũ hết hiệu lực sau khi khách đổi yêu cầu hoặc đồng ý phương án. Khi STAFF ghi nhận không đáp ứng, báo giá còn chờ bị thay thế ngay để khách không thể chấp nhận nhầm trong lúc lập phương án. Mỗi đơn chỉ có một phương án đang hoạt động nhờ ràng buộc database.
- Bảng mới: `orders_staffavailabilitycheck` và `orders_orderalternative` (migration `orders.0008`). Chưa có webhook/email riêng cho nhánh này; dashboard và trang chi tiết đơn là nơi hiển thị công việc cần xử lý.
- Bảng phân loại tiếp nhận `orders_staffavailabilitycheck` lưu phạm vi quyền, lý do, căn cứ và snapshot; `orders_ownerintakecheck` lưu kết quả kiểm tra do OWNER thực hiện. Migration `orders.0010` bổ sung vai trò đang xử lý, lý do từ chối hiển thị cho khách và bảng kiểm tra OWNER. `orders_intakequoteapproval` được giữ để bảo toàn lịch sử dữ liệu cũ; endpoint duyệt báo giá theo quy trình cũ không còn thực hiện nghiệp vụ mới. Đơn cũ đang chờ cần OWNER nhận tiếp quản tường minh, không tự chuyển dữ liệu hay coi phê duyệt cũ là kết quả kiểm tra mới.

## Sản xuất, kiểm tra chất lượng và duyệt ảnh

- Chỉ đơn `DA_XAC_NHAN` có báo giá được khách chấp nhận mới bắt đầu sản xuất (`DANG_LAM`). Nhân viên ghi nhận từng lần kiểm tra chất lượng; lần không đạt phải có lý do và không cho gửi ảnh.
- Sau khi QA đạt, nhân viên gửi ảnh bánh theo phiên bản. Khách duyệt ảnh để chuyển `SAN_SANG_GIAO`, hoặc gửi yêu cầu chỉnh sửa gắn với đúng phiên bản ảnh.
- Yêu cầu chỉnh sửa trong phạm vi đã thống nhất có thể chuyển trực tiếp về sản xuất và phải QA lại. Yêu cầu vượt phạm vi cần OWNER duyệt; nếu thay đổi giá/lịch, khách phải đồng ý phụ lục trước khi áp dụng.
- Từ chối yêu cầu của OWNER hoặc phụ lục không tự thay đổi thỏa thuận cũ; khách có thể xác nhận giữ phương án hoặc tiếp tục trao đổi.
- Ảnh kiểm duyệt được lưu trong `PRIVATE_MEDIA_ROOT`, ngoài `MEDIA_ROOT`; không có URL media công khai. Chỉ tài khoản có quyền liên quan đến đơn mới đọc được ảnh.
- Dashboard STAFF/OWNER hiển thị hàng đợi công việc thực tế. Các thao tác ghi dùng POST/CSRF, kiểm tra role và quyền sở hữu ở backend, đồng thời kiểm tra version để từ chối trang cũ/gửi lặp.
- Trạng thái sản xuất gồm `DANG_LAM`, `CHO_DUYET_ANH`, `CHO_XU_LY_CHINH_SUA`, `CHO_CHU_DUYET`, `CHO_KHACH_DUYET_THAY_DOI`, `CHO_TRAO_DOI_CHINH_SUA` và `SAN_SANG_GIAO`.

## Giao hàng, sự cố và thanh toán

- STAFF chỉ bắt đầu giao đơn `SAN_SANG_GIAO` có thỏa thuận còn hiệu lực, ảnh hiện hành đã được khách duyệt và không còn yêu cầu chỉnh sửa/phê duyệt/sự cố đang mở. Mỗi lần giao có `DeliveryAttempt` riêng, snapshot người nhận/địa chỉ và kết quả được giữ trong lịch sử.
- CUSTOMER xác nhận nhận bánh hoặc báo sự cố. STAFF có thể ghi nhận bàn giao thay khách nhưng hệ thống lưu rõ nguồn xác nhận STAFF và ghi chú bắt buộc. Giao thất bại phải có lý do; giao lại cùng điều khoản được xếp lịch và tạo lần giao mới.
- OWNER là người duy nhất duyệt phương án sự cố. STAFF ghi nhận kết quả thực hiện. Sửa/đổi bánh phải qua QA và khách duyệt ảnh mới; giữ bánh hiện tại cần khách xác nhận; hủy sau khi đã thu tiền chỉ hoàn tất sau khi STAFF ghi khoản hoàn thực tế.
- Tình trạng giao và thanh toán độc lập: giao thành công nhưng chưa thu đủ chuyển `DA_GIAO_CHO_THANH_TOAN`; chỉ `HOAN_TAT` khi đã giao, thu đủ và không có công việc đang mở. Tiền được tổng hợp từ báo giá/phụ lục đã chấp nhận và ledger `Payment`/`Refund`.
- Sau khi khách xác nhận báo giá, khách có thể bấm “Tôi đã chuyển khoản trước” để báo cửa hàng đối chiếu. Đây chỉ là thông báo, không tạo khoản thu hoặc chuyển trạng thái thanh toán; STAFF chỉ ghi `Payment` sau khi xác nhận tiền thực nhận. Giao thành công cùng khoản thu đủ mới hoàn tất đơn.
- STAFF chỉ ghi tiền đã thực nhận. Chuyển khoản được đối chiếu thủ công bằng mã tham chiếu; hệ thống không xác minh ngân hàng, gọi API thanh toán hoặc tự nhận ảnh chuyển khoản là giao dịch. Hoàn tiền cũng được thực hiện bên ngoài; hệ thống chỉ lưu nhận xác nhận của nhân viên.
- Ảnh giao hàng, sự cố và hoàn tiền lưu trong `PRIVATE_MEDIA_ROOT` và chỉ được phục vụ qua endpoint kiểm tra quyền. Các thao tác ghi dùng POST/CSRF, kiểm tra role/trạng thái/version và transaction; mã idempotency cùng ràng buộc DB ngăn ghi trùng.
- Dashboard STAFF hiển thị hàng đợi theo trạng thái thật; OWNER có danh sách sự cố chờ quyết định. Chưa có GPS, API ngân hàng, đơn vị vận chuyển, thông báo tự động, AI hoặc workflow tự động.
- Kịch bản demo: mở đơn `SAN_SANG_GIAO` tại `/staff/orders/`, bắt đầu giao, xác nhận khách nhận tại trang khách, sau đó STAFF ghi khoản tiền mặt thực nhận để đơn chuyển `HOAN_TAT`. Để thử giao lại, ghi nhận giao thất bại, thống nhất lịch với khách và bắt đầu lần giao mới.

## Đánh giá sau đơn hàng

- `OrderReview` (`orders_orderreview`) lưu tối đa một đánh giá cho mỗi đơn; điểm 1–5 có ràng buộc DB, nhận xét tùy chọn tối đa 2.000 ký tự. `ReviewReply` (`orders_reviewreply`) lưu một phản hồi bất biến của OWNER.
- Chỉ CUSTOMER sở hữu đơn `HOAN_TAT` được gửi đánh giá. Nhân viên/chủ không gửi thay; khách không sửa/xóa sau khi gửi. Nhận xét chỉ hiển thị trong khu vực nội bộ, được Django autoescape.
- `Order.completed_at` được ghi một lần khi đơn thực sự hoàn tất. Migration backfill chỉ lấy lịch sử `ORDER_COMPLETED` đáng tin; đơn cũ không có mốc vẫn yêu cầu kiểm tra.
- Mốc `completed_at + ORDER_REVIEW_WINDOW_HOURS` (mặc định 24 giờ) là thời điểm workflow có thể nhắc, **không phải hạn chót**. Khách vẫn có thể đánh giá đơn hoàn tất chưa được đánh giá sau mốc này; trạng thái đơn không đổi.
- CUSTOMER xem trạng thái và gửi tại `/orders/mine/`; OWNER xem/lọc, thống kê, phản hồi tại `/owner/reviews/`. STAFF chỉ xem nhận xét trong đơn họ được quyền xử lý.

## Workflow webhook và outbox

Django vẫn là nguồn quyết định nghiệp vụ. Outbox `WorkflowEvent` (`orders_workflowevent`) được ghi cùng transaction khi khách xác nhận báo giá, STAFF gửi phiên bản ảnh duyệt, hoặc đơn chuyển `HOAN_TAT`; unique `business_key`/`event_id` chống tạo bản ghi trùng. `WorkflowRetryRequest` ghi nhận OWNER xếp thử lại.

Thiết lập các biến trong `.env` mà không đưa token vào source control:

- `WORKFLOW_ENABLED=False` mặc định; khi tắt, website hoạt động bình thường và sự kiện đủ điều kiện được giữ `pending`.
- `WORKFLOW_WEBHOOK_URL`, `WORKFLOW_WEBHOOK_TOKEN`, `WORKFLOW_TIMEOUT_SECONDS` (1–30 giây).
- `SITE_BASE_URL` là địa chỉ website tin cậy để tạo liên kết. Dùng HTTPS ở môi trường ngoài; HTTP webhook chỉ được phép tới localhost khi `DEBUG=True`.

Chạy dispatcher thủ công theo từng lô hữu hạn:

```powershell
.\.venv\Scripts\python.exe manage.py dispatch_workflow_events --limit 20
```

OWNER giám sát/lọc sự kiện và POST yêu cầu thử lại tại `/owner/workflow-events/`. Trang không hiển thị token hoặc payload. Dispatcher không chạy nền liên tục; chưa cấu hình endpoint workflow hoặc nhà cung cấp email, và không gửi email thật.

`delivered` chỉ có nghĩa webhook trả HTTP 2xx và tiếp nhận sự kiện, không chứng minh email đã tới hộp thư. Workflow phải khử trùng bền vững theo `event_id`. Nếu nhà cung cấp email không có idempotency, vẫn có cửa sổ gửi trùng khi workflow lỗi sau khi email được chấp nhận nhưng trước khi lưu kết quả. Xem [WORKFLOW_CONTRACT.md](./WORKFLOW_CONTRACT.md) để biết schema, header, retry và ranh giới trách nhiệm.

## Đường dẫn

- `/` — trang giới thiệu
- `/register/` — đăng ký khách hàng
- `/login/` — đăng nhập
- `/customer/dashboard/` — khu vực khách hàng
- `/staff/dashboard/` — khu vực nhân viên
- `/owner/dashboard/` — khu vực chủ cửa hàng
- `/orders/mine/` — đơn của khách hàng
- `/staff/orders/` — hàng đợi của nhân viên
- `/owner/revision-approvals/` — yêu cầu chỉnh sửa chờ chủ cửa hàng duyệt
- `/owner/incidents/` — sự cố chờ chủ cửa hàng quyết định
- `/owner/reviews/` — danh sách đánh giá nội bộ và phản hồi của OWNER
- `/owner/workflow-events/` — outbox, trạng thái gửi webhook và thử lại thủ công
- `/assistant/` — thông tin tư vấn AI (AI tắt nếu chưa cấu hình nhà cung cấp)
- `/owner/assistant/policies/` — OWNER quản lý nội dung chính sách truy hồi
- `/orders/review-images/<id>/` — ảnh riêng tư, cần đăng nhập và có quyền liên quan
- `/orders/evidence/<type>/<id>/` — minh chứng giao/sự cố/hoàn tiền riêng tư, cần quyền phù hợp
- `/admin/` — Django admin (chỉ dành cho tài khoản được cấp quyền Django phù hợp)

Đăng xuất chỉ chấp nhận POST và có CSRF token. Vai trò được kiểm tra trên backend; người đã đăng nhập nhưng sai vai trò nhận HTTP 403.

## Kiểm tra

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

Kết quả xác minh gần nhất: `check`, `makemigrations --check --dry-run` và `migrate --check` đều đạt; toàn bộ 172 test đạt. Các migration hiện có đã áp dụng. Test webhook và trợ lý AI dùng mock, không gửi request/email/provider thật.

## Cấu trúc

- `config/` — cấu hình và URL Django
- `accounts/` — custom User, xác thực, phân quyền và lệnh demo
- `catalog/` — danh mục, mẫu bánh công khai và trang quản lý OWNER
- `orders/` — đơn, báo giá, sản xuất/QA, lần giao, sự cố, thu/hoàn tiền, lịch sử và quy tắc chuyển trạng thái
- `assistant/` — hội thoại AI tùy chọn, truy hồi catalog/chính sách, bản nháp và adapter provider
- `templates/`, `static/` — giao diện dùng chung, CSS và JavaScript nội bộ

## Tư vấn AI — mốc 5.3

Khách hàng đăng nhập có thể mô tả nhu cầu, xem gợi ý mẫu bánh đang nhận đặt, hỏi nội dung
chính sách đã được OWNER công bố và chuẩn bị bản nháp. AI không tạo đơn, không xác nhận,
không chốt giá/lịch, không xử lý thanh toán hoặc duyệt ảnh. Khách dùng nút “Dùng thông tin
này để đặt bánh”, tự bổ sung người nhận/điện thoại/địa chỉ, kiểm tra rồi gửi form đặt bánh
hiện có. Service đơn hàng vẫn kiểm tra lại mẫu và chống gửi trùng.

AI mặc định **tắt**. Để cấu hình, mở `.env` sẵn có bằng `notepad .env`, nhập key mới vào
`GEMINI_API_KEY` và đặt `AI_ENABLED=True`. Model mặc định là `gemini-3.8-flash` (có thể
ghi rõ tại `GEMINI_MODEL`). Backend dùng SDK Python chính thức `google-genai`; key không
được gửi xuống trình duyệt. Model này có trong danh mục model stable chính thức; quyền truy
cập của API account cụ thể chưa được xác minh. Không dùng lại key từng được dán trong chat:
hãy thu hồi/đổi key tại Google AI Studio và tự nhập key mới trong `.env`; không in/chia sẻ file.

Nạp bộ nội dung chính sách đề xuất cho demo (tất cả ở trạng thái nháp, không công bố):

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo_policies
```

Lệnh yêu cầu đã có OWNER, tạo mã `CS01`–`CS12`, báo từng mã được tạo/bỏ qua và không ghi đè
nội dung hoặc trạng thái đã có. OWNER rà soát nội dung và thao tác **Công bố** có POST/CSRF tại
`/owner/assistant/policies/`. Sửa tiêu đề/nội dung đã công bố sẽ tạo phiên bản nháp mới; phải
xem lại rồi công bố lại. Đây là chính sách đề xuất, chưa được cửa hàng xác nhận; không công bố
trước khi đối chiếu quy trình thật. Trang khách xem nội dung đã công bố tại
`/assistant/policies/<code>/`.

Tư vấn sử dụng truy vấn database có giới hạn, không dùng tìm kiếm web hoặc vector database;
chỉ gửi mẫu công khai/chính sách công bố liên quan. Mẫu bánh được lọc theo ngân sách rồi xếp
hạng theo tên, danh mục và thuộc tính tư vấn OWNER đã xác nhận. Form quản lý mẫu có trường
dịp, hương vị, phong cách, khẩu phần, thành phần và thông tin dị ứng. Dữ liệu demo được seed
riêng, hiển thị nhãn chưa xác nhận, không thay thế dữ liệu OWNER kiểm chứng. OWNER chỉ đánh
dấu xác nhận sau khi rà soát; sửa thuộc tính sẽ gỡ xác nhận. Khẩu phần/thành phần/dị ứng demo
để trống. Chatbot không suy ra số khẩu phần từ tên, kích thước hoặc giá. Hội thoại, bản nháp, nguồn đã trích và
chỉ số request được lưu trong các bảng `assistant_*`. Mỗi khách chỉ đọc phiên của mình.
Trước khi gửi provider, backend lọc email, số điện thoại, địa chỉ/tên người nhận và giá trị
nhạy cảm được gắn nhãn; giao diện nhắc khách không nhập dữ liệu đó. Không cấp công cụ ghi đơn
cho Gemini. Khi provider lỗi, nguồn catalog/chính sách phù hợp được trả bằng chế độ tra cứu
cục bộ có thông báo rõ; nếu không có nguồn thì chatbot đề nghị nhân viên xác nhận/thử lại.
Không tạo order từ chat. Xem [AI_ASSISTANT.md](./AI_ASSISTANT.md) để biết phạm vi, adapter,
giới hạn, URLs, policy demo và test mock.

Để điền gợi ý demo dựa trên tên/mô tả sẵn có cho catalog mẫu (lệnh không ghi đè trường đã
nhập và không đánh dấu xác nhận), OWNER có thể rà soát tại `/owner/catalog/cakes/`:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo_advisory_data
```

Workflow webhook ở mốc 5.2 vẫn hoạt động độc lập; chatbot không đi qua workflow và không gửi
email. Test chỉ dùng provider mock, không gọi API thật. Chính sách demo có điểm chưa khớp chức
năng: nhắc khách sau 30 phút về ảnh chưa tự động; luồng CUSTOMER yêu cầu hủy đơn chung như CS09
chưa hoàn chỉnh; khoản chuyển khoản vẫn cần nhân viên ghi nhận, không xác minh ngân hàng.
Không coi nội dung nháp là quy tắc vận hành.
