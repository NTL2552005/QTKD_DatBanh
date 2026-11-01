# Bối cảnh dự án BiBi Cake

## Mục tiêu

Xây dựng nền tảng hệ thống đặt và giao bánh BiBi Cake phục vụ đồ án môn Quản lý quy trình kinh doanh.

## Vai trò

- `CUSTOMER`: khách hàng, được cấp tự động qua đăng ký công khai.
- `STAFF`: nhân viên cửa hàng.
- `OWNER`: chủ cửa hàng.

Vai trò nghiệp vụ độc lập với `is_staff` và `is_superuser` của Django.

## Công nghệ

- Django 5.2, Django Templates, HTML/CSS/JavaScript thuần.
- SQLite để demo.
- Custom User kế thừa `AbstractUser`.
- Ngôn ngữ `vi`, múi giờ `Asia/Ho_Chi_Minh`.
- Cấu hình môi trường từ `.env` bằng `python-dotenv`.

## Đã hoàn thành

- Khung project `config` và các app `accounts`, `catalog`, `orders`.
- Trang giới thiệu, đăng ký khách hàng, đăng nhập và đăng xuất POST có CSRF.
- Ba dashboard và kiểm tra quyền backend trả 403 khi sai vai trò.
- Lệnh `seed_demo` đọc mật khẩu từ `DEMO_PASSWORD`, chạy lặp an toàn và không cấp quyền superuser cho owner.
- Template chung, điều hướng theo vai trò, thông báo tiếng Việt, responsive và trang 403.
- Kiểm thử tự động cho xác thực, phân quyền, chuyển dashboard và seed demo.
- Model `Category` và `CakeTemplate`, migration catalog, giá tham khảo nguyên VNĐ, trạng thái nhận đặt và điều kiện hiển thị công khai.
- Trang danh sách/chi tiết công khai có tìm kiếm, lọc, sắp xếp và phân trang; trang OWNER quản lý danh mục/mẫu, không có thao tác xóa.
- Upload ảnh JPEG/PNG/WebP được Pillow kiểm tra, giới hạn 5 MiB/20 megapixel, tên file ngẫu nhiên; có ảnh thay thế nội bộ và preview.
- Lệnh `seed_catalog` tạo dữ liệu minh họa idempotent, không ghi đè nội dung có sẵn.
- SQLite đã áp dụng migration catalog; các bảng tạo là `catalog_category`, `catalog_caketemplate`. Đã seed 3 danh mục và 6 mẫu bánh.
- Môi trường local đã có `.env` (không đưa giá trị vào tài liệu), SQLite đã migrate, và ba tài khoản demo đã được seed.
- Đã kiểm tra role thực tế của ba tài khoản; `owner_demo` không có `is_staff` hay `is_superuser`.
- Website catalog trước đó chạy tại `http://127.0.0.1:57884/`; cổng 8000 thuộc một tiến trình khác và được giữ nguyên.
- Trình duyệt đã xem danh sách/chi tiết; kiểm tra responsive trên viewport 390px và desktop 1440px không phát hiện tràn ngang.
- Quy trình đơn hàng: khách tạo yêu cầu cho mẫu công khai; nhân viên kiểm tra nguyên liệu/lịch, yêu cầu bổ sung hoặc từ chối, sau đó lập báo giá có phiên bản; khách yêu cầu trao đổi hoặc xác nhận báo giá hiện hành.
- Model `Order`, `Quote`, `OrderHistory` có snapshot dữ liệu, version optimistic concurrency, request key chống gửi trùng, transaction cho chuyển trạng thái và các ràng buộc giá/unique trong SQLite.
- Quyền xử lý đơn của STAFF và quyền xem/sửa đơn của CUSTOMER được kiểm tra ở backend; khách chỉ xem đơn của mình. Đăng ký công khai không thể chọn role.
- Dashboard khách/nhân viên hiển thị số liệu lấy từ database và trạng thái rỗng khi chưa có đơn cần hiển thị.
- Giao diện đơn hàng gồm form tạo/bổ sung, danh sách có lọc/phân trang, chi tiết, lịch sử, hàng đợi nhân viên và preview tổng báo giá bằng JavaScript nội bộ.
- Đã áp dụng migration `orders.0001_initial` vào database hiện có; không xóa database hoặc migration.
- Kiểm tra cuối cho mốc đơn hàng: `manage.py check` đạt; 54 test thành công; migration đã áp dụng và không có thay đổi model chưa tạo migration.
- Website đã có sẵn tại `http://127.0.0.1:57885/`; cổng 8000 đang được tiến trình khác sử dụng và không bị dừng. Trình duyệt tích hợp đã mở trang chủ; giao diện chi tiết đơn và duyệt sự cố được kiểm tra bằng Django test client.
- Mốc bổ sung tiếp nhận: STAFF có thể ghi kết quả nguyên liệu/lịch theo snapshot request revision; khi không đáp ứng có thể lập phiên bản phương án thay thế, gửi khách hoặc chờ OWNER duyệt nếu vượt quyền. Báo giá còn chờ bị thay thế ngay khi ghi nhận không đáp ứng. Khách đồng ý cập nhật request và buộc kiểm tra lại; từ chối kết thúc tiếp nhận, không xóa lịch sử.
- Bảng mới `orders_staffavailabilitycheck`, `orders_orderalternative` trong migration `orders.0008`. Chủ duyệt dùng chung hàng đợi `/owner/revision-approvals/` nhưng record duyệt tiếp nhận độc lập với phê duyệt chỉnh sửa sản xuất. OWNER duyệt không gửi khách/xác nhận đơn; không có thông báo webhook mới cho nhánh này.
- Phân loại quyền tiếp nhận: **Đơn thông thường STAFF xử lý trực tiếp. Chỉ đơn vượt quyền mới gửi OWNER duyệt.** Kết quả phân loại thủ công gắn với bản ghi kiểm tra và phiên bản yêu cầu; STAFF chọn lý do/mô tả khi cần chủ, hoặc ghi căn cứ khi trong quyền. Chưa có chính sách phân quyền STAFF được công bố trong database; không dùng AI hoặc ngưỡng tự đặt để tự phân loại.
- Đơn thông thường STAFF xử lý trực tiếp. Chỉ đơn vượt quyền mới gửi OWNER duyệt. STAFF chuyển nguyên đơn vượt quyền để OWNER tự kiểm tra khả năng đáp ứng rồi trực tiếp gửi báo giá hoặc kết thúc tiếp nhận; OWNER gửi báo giá không tự xác nhận đơn. Khách đồng ý báo giá hiện hành mới đưa đơn sang `DA_XAC_NHAN`.
- OWNER từ chối lưu lý do công khai trên đơn, chuyển `TU_CHOI`; STAFF không cần gửi thông báo thủ công hoặc gửi duyệt lại. Khi OWNER đang xử lý, STAFF không thể đổi phân loại hay gửi báo giá để né quyết định.
- Migration `orders.0009_staffavailabilitycheck_authority_basis_and_more` bổ sung phân loại và lưu dữ liệu phê duyệt lịch sử. Migration `orders.0010_order_intake_handler_role_order_rejection_reason_and_more` bổ sung người xử lý tiếp nhận, lý do từ chối công khai và bảng `orders_ownerintakecheck`. Bảng duyệt báo giá cũ được giữ để bảo toàn lịch sử; không dùng để vận hành luồng mới. Đơn legacy chờ OWNER phải được chủ nhận tiếp quản tường minh, không tự chuyển dữ liệu.
- Phân loại thiếu thông tin không lưu giả định nguyên liệu/lịch đã kiểm tra và không thể báo giá; STAFF có thể yêu cầu khách bổ sung. Thiếu nguyên liệu/lịch kín vẫn là chưa đáp ứng, không tự động chuyển OWNER. Luồng duyệt phương án thay thế vẫn tách biệt với việc OWNER trực tiếp xử lý đơn vượt quyền.

## Phạm vi chưa triển khai và tiếp theo

Đã triển khai các luồng đặt bánh/báo giá, sản xuất/QA/duyệt ảnh, giao hàng, xử lý sự cố, thu/hoàn tiền, đánh giá nội bộ, outbox webhook và lớp tư vấn AI dùng truy hồi catalog/chính sách. Chatbot tích hợp SDK Python chính thức `google-genai`; mặc định chọn Gemini `gemini-3.8-flash` với JSON structured output, nhưng AI mặc định tắt và chưa gọi API thật. Chưa triển khai giao dịch tiền tự động, xác minh API ngân hàng, vận chuyển/GPS, cấu hình workflow/email thật, callback nhà cung cấp email hoặc kiểm thử concurrency đa tiến trình trên PostgreSQL.

## Mốc đánh giá và workflow thông báo

- Migration `orders.0005` thêm `completed_at`, đánh giá và phản hồi; thời điểm đơn cũ được lấy từ lịch sử hoàn tất đáng tin. Trong lần áp dụng migration này, đơn hoàn tất hiện có được backfill từ lịch sử; không xóa database hoặc dữ liệu.
- Migration `orders.0006` tạo outbox và nhật ký yêu cầu OWNER thử lại; `orders.0007` bổ sung số lần thử trong chu kỳ để giữ tổng số lượt thử khi OWNER mở chu kỳ mới.
- Outbox chỉ được tạo bởi thao tác nghiệp vụ thật: khách chấp nhận báo giá hiện hành; STAFF gửi một phiên bản ảnh đủ điều kiện; hoặc giao hàng + thu đủ + không còn sự cố/công việc mở khiến đơn thực sự chuyển `HOAN_TAT`. Không phát từ GET/render trang.
- Payload lưu ổn định lúc tạo, chỉ gồm event metadata, mã đơn, email/tên khách nếu hợp lệ, liên kết website cấu hình, và số phiên bản/mốc phù hợp. Không đưa số điện thoại, địa chỉ, dữ liệu thanh toán, secret hoặc URL ảnh riêng tư.
- Dispatcher gọi HTTP ngoài transaction. 2xx đánh dấu “Workflow đã tiếp nhận”; 429/5xx/lỗi kết nối được retry có giới hạn; lỗi 4xx như 401/403 không tự retry. OWNER chỉ xếp retry qua POST; GET không sửa trạng thái.
- Ranh giới hợp đồng, schema và hạn chế gửi trùng được ghi tại `WORKFLOW_CONTRACT.md`. Tích hợp hiện tại chỉ sẵn sàng kết nối; không tuyên bố email thật đã gửi.
- Đã áp dụng `orders.0005`–`orders.0007` trên database hiện tại, không xóa dữ liệu; bản ghi HOAN_TAT hiện có được backfill từ lịch sử hoàn tất. `manage.py check`, `makemigrations --check --dry-run` và `migrate --check` đạt; toàn bộ 136 test đạt. HTTP workflow được mock trong test.

## Mốc giao hàng, sự cố và thanh toán

- Chỉ bắt đầu giao từ `SAN_SANG_GIAO` với ảnh hiện hành được khách chấp nhận và không còn chỉnh sửa/phê duyệt/sự cố đang mở. Mỗi lần giao lại là bản ghi `DeliveryAttempt` mới; không ghi đè lần giao cũ.
- Giao thành công không đồng nghĩa đã thu tiền. Đơn đã giao nhưng chưa thu đủ ở `DA_GIAO_CHO_THANH_TOAN`; nhận đủ tiền trước khi giao cũng không làm đơn hoàn tất.
- `Payment` và `Refund` là ledger chỉ ghi nhận khoản tiền thực tế do STAFF xác nhận. Chuyển khoản/hoàn tiền được xử lý bên ngoài và đối chiếu thủ công; cấm vượt số còn thu hoặc số đã nhận còn có thể hoàn.
- Sau khi xác nhận báo giá, CUSTOMER có thể báo đã chuyển khoản trước. Hệ thống lưu sự kiện vào `OrderHistory`, hiển thị nhắc STAFF đối chiếu, nhưng không tạo `Payment` hoặc thay đổi `payment_status`; STAFF xác nhận số tiền thực nhận bằng luồng ghi khoản thu hiện có.
- OWNER duyệt phương án sự cố; STAFF thực hiện. Giao lại cùng điều khoản được xếp lịch; sửa/đổi bánh cần QA và ảnh khách duyệt lại; giữ phương án hiện tại cần khách chủ động chấp nhận; hủy đã thu tiền chỉ hoàn tất sau khi ghi nhận hoàn đủ.
- Migration `orders.0004` giữ nguyên các bảng/dữ liệu cũ và tạo bảng lần giao, sự cố, khoản thu, khoản hoàn cùng các ràng buộc unique/check cho attempt, số tiền và mã tham chiếu.

## Mốc sản xuất và duyệt ảnh

- STAFF bắt đầu sản xuất sau khi đơn và báo giá được khách xác nhận; từng lần QA được lưu và chỉ lần đạt trong production round hiện tại mới cho phép gửi ảnh.
- Khách duyệt ảnh theo phiên bản để chuyển `SAN_SANG_GIAO`, hoặc gửi yêu cầu chỉnh sửa gắn đúng ảnh. Sửa đổi yêu cầu QA lại.
- Revision vượt phạm vi cần OWNER duyệt; đổi giá/lịch cần khách đồng ý phụ lục trước khi áp dụng. Báo giá gốc không bị chỉnh sửa.
- `ProductionCheck`, `CakeReviewVersion`, `RevisionRequest`, `ApprovalRequest`, `ChangeAgreement` được lưu trong orders. Ảnh kiểm duyệt nằm trong `PRIVATE_MEDIA_ROOT` ngoài public media và endpoint kiểm tra quyền truy cập.
- Migration `orders.0002` và `orders.0003` đã áp dụng. `manage.py check` và `makemigrations --check --dry-run` đạt; targeted production tests 20/20, toàn bộ tests 74/74.
- Luồng giao hàng, sự cố và thu/hoàn tiền đã được bổ sung bằng migration `orders.0004`, áp dụng vào database hiện có mà không xóa dữ liệu.
- Đã thêm `Order.completed_at`, `OrderReview` và `ReviewReply`. CUSTOMER chỉ đánh giá đơn hoàn tất của mình; mỗi đơn một đánh giá 1–5 sao, nhận xét tối đa 2.000 ký tự, không sửa/xóa; OWNER phản hồi một lần; nội dung chỉ nội bộ.
- Cửa sổ 24 giờ sau hoàn tất là mốc nhắc workflow, không phải hạn gửi: đơn chưa đánh giá vẫn có thể được đánh giá sau mốc. Đơn cũ chỉ được backfill từ lịch sử `ORDER_COMPLETED`; nếu không có mốc đáng tin thì yêu cầu kiểm tra.
- Outbox `WorkflowEvent` (`orders_workflowevent`) và `WorkflowRetryRequest` (`orders_workflowretryrequest`) lưu ba sự kiện `order.confirmed`, `cake.review_requested`, `order.completed`; ghi cùng transaction nghiệp vụ, chống trùng bằng `business_key` duy nhất và giữ `event_id`/payload khi retry.
- `WORKFLOW_ENABLED` mặc định tắt. Dispatcher `dispatch_workflow_events` gửi theo lô hữu hạn, lease 60 giây, timeout tối đa 30 giây, retry tăng dần tối đa 5 lần mỗi chu kỳ; OWNER theo dõi và xếp retry bằng POST ở `/owner/workflow-events/`.
- `delivered` chỉ xác nhận webhook trả 2xx, không xác nhận email được gửi/nhận. Hiện chưa cấu hình endpoint workflow hoặc nhà cung cấp email; chưa có callback email thật. Chatbot ở mốc 5.3 được mô tả riêng bên dưới và không phụ thuộc workflow.
- `DeliveryAttempt` ghi riêng từng lần giao, snapshot thông tin nhận hàng, nhân viên, kết quả, nguồn xác nhận và minh chứng riêng tư. Chỉ STAFF bắt đầu giao; CUSTOMER xác nhận hoặc báo sự cố; STAFF có thể xác nhận bàn giao hộ với ghi chú và nguồn STAFF rõ ràng.
- Các trạng thái giao mới gồm `DANG_GIAO`, `GIAO_THAT_BAI`, `CHO_XU_LY_SU_CO`, `DA_GIAO_CHO_THANH_TOAN`, `HOAN_TAT` và `DA_HUY`. `payment_status` độc lập; `Payment` và `Refund` lưu số tiền Decimal, người xác nhận, thời điểm, mã idempotency và tham chiếu.
- STAFF ghi nhận tiền đã thực nhận sau đối chiếu thủ công; hệ thống không gọi API ngân hàng/vận chuyển và không xem ảnh chuyển khoản là giao dịch. Refund chỉ ghi sau khi thực hiện bên ngoài; OWNER duyệt phương án hủy không tự đánh dấu đã hoàn.
- `OrderIncident` lưu nội dung, minh chứng riêng, đề xuất của STAFF, quyết định/lý do OWNER, kết quả thực hiện và xác nhận của khách nếu giữ phương án hiện tại. Hoàn tất cần giao thành công, thu đủ và không còn sự cố/công việc mở.
- Endpoint sự cố không sửa báo giá, địa chỉ hoặc lịch đã chốt; kế hoạch xử lý được ghi riêng. Nếu cần thay đổi điều khoản thương mại, cần phụ lục đã được OWNER duyệt và khách chủ động chấp nhận trước khi áp dụng.
- Giao diện chi tiết đơn CUSTOMER/STAFF hiển thị tiến độ, lịch sử từng lần giao, khoản thu/hoàn và hành động theo vai trò. OWNER có danh sách/chi tiết sự cố chờ quyết định; dashboard STAFF/OWNER dùng số liệu trạng thái/sự cố thực.
- Đã áp dụng migration `orders.0004_order_delivery_reschedule_note_and_more`; không xóa database, migration hay dữ liệu hiện có.
- `manage.py check` và `makemigrations --check --dry-run` đạt; targeted delivery tests 18/18; toàn bộ tests 92/92.

## Mốc tư vấn AI 5.3

- App `assistant` có hội thoại riêng theo CUSTOMER, bản nháp 7 ngày, log request và `StorePolicy` do OWNER quản lý. Provider mặc định Gemini qua SDK chính thức `google-genai`; model cấu hình mặc định `gemini-3.8-flash` có trong catalog stable chính thức (đã kiểm tra 31/10/2026), nhưng quyền của API account thực tế chưa được xác minh. AI tắt mặc định.
- Lệnh `seed_demo_policies` tạo 12 mã `CS01`–`CS12` ở trạng thái nháp, giữ nguyên các bản ghi có sẵn; chỉ OWNER có thể rà soát và công bố/ẩn qua POST/CSRF. Nội dung do người dùng cung cấp là dữ liệu đề xuất cho đồ án, chưa phải chính sách cửa hàng đã xác nhận.
- Chatbot chỉ truy hồi mẫu công khai/chính sách đang công bố, kiểm tra ID/schema/ngày/số lượng ở backend, và chỉ lưu `AIDraft`; khách tự dùng form đặt hiện có, nơi service xác minh lại mẫu/chống trùng. Gemini không có công cụ ghi đơn/nghiệp vụ. Hệ thống chặn một số tuyên bố không có thẩm quyền và local fallback dùng dữ liệu thật, nêu rõ chế độ tra cứu.
- Catalog có các trường tư vấn tùy chọn và cờ `advisory_data_verified`. Gợi ý demo chưa xác nhận hiển thị cảnh báo; lệnh `seed_demo_advisory_data` chỉ điền trường trống và không ghi đè dữ liệu OWNER. OWNER chỉ đánh dấu xác nhận sau khi rà soát; sửa dữ liệu sẽ gỡ cờ. Retrieval lọc ngân sách ở backend rồi xếp hạng theo thuộc tính, không suy đoán trường trống.
- Nội dung tư vấn cần thiết và nguồn công khai liên quan được gửi tới provider khi gọi. Backend redaction email, số điện thoại, địa chỉ/tên người nhận và giá trị bí mật có nhãn; giao diện nhắc khách không nhập thông tin này. Câu trả lời render an toàn, citations dùng source IDs đã kiểm tra và URL do backend dựng.
- Chênh lệch cần lưu ý trước khi OWNER công bố: nhắc khách sau 30 phút về ảnh chưa tự động; luồng CUSTOMER yêu cầu hủy chung trong CS09 chưa hoàn chỉnh; chuyển khoản không được xác minh ngân hàng tự động. Xem `AI_ASSISTANT.md` để biết chi tiết.
- Bộ test provider dùng mock; chưa gọi API thật. Kết quả kiểm tra gần nhất trước thay đổi này là 172 test; cần cập nhật số sau khi hoàn tất toàn bộ suite hiện tại.
