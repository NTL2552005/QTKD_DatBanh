# Kế hoạch chi tiết xây dựng chatbot tư vấn bánh BiBi Cake

## 1. Mục tiêu

Xây dựng trợ lý tiếng Việt giúp khách mô tả nhu cầu bằng câu tự nhiên, tìm và so sánh
các mẫu bánh phù hợp từ catalog BiBi Cake, giải thích vì sao mẫu được gợi ý, hỏi lại khi
thiếu thông tin và hỗ trợ chuẩn bị bản nháp yêu cầu đặt bánh.

Chatbot là công cụ tư vấn, không thay thế nhân viên hoặc quy trình đặt hàng. Mọi giá,
khả năng đáp ứng, lịch giao và chính sách phải có nguồn dữ liệu được cửa hàng xác nhận.
Khách vẫn xem lại thông tin và tự gửi yêu cầu qua form đặt bánh hiện có.

## 2. Nguyên tắc thiết kế

1. **Dữ liệu cửa hàng là nguồn sự thật:** Gemini diễn đạt và hỗ trợ hội thoại; backend mới
   quyết định mẫu nào đủ điều kiện được gợi ý.
2. **Lọc điều kiện bắt buộc trước khi xếp hạng:** ví dụ không vượt ngân sách, công khai,
   đang nhận đặt và các yêu cầu bắt buộc khác.
3. **Không suy đoán dữ liệu thiếu:** không tự kết luận khẩu phần, thành phần gây dị ứng,
   tồn kho, khả năng giao, giá cuối cùng hoặc thời gian hoàn thành.
4. **Tách tư vấn khỏi giao dịch:** chat không tạo đơn, không xác nhận thanh toán, không
   chốt giá và không giữ lịch giao.
5. **Gemini không được quyền ghi nghiệp vụ:** backend xác thực và lưu bản nháp; khách tự
   kiểm tra và gửi form đặt bánh.
6. **Fallback phải trung thực:** khi Gemini không gọi được, hệ thống chỉ tra cứu catalog/
   chính sách phù hợp và nói rõ đang ở chế độ dự phòng.
7. **Bảo vệ dữ liệu:** không gửi thông tin liên hệ hoặc bí mật không cần thiết tới provider;
   không ghi API key, prompt thô hoặc dữ liệu cá nhân vào log.

## 3. Hiện trạng và phạm vi

### Đã có

- Django app `assistant`, hội thoại riêng theo khách hàng, lưu lịch sử và bản nháp có thời hạn.
- Provider Gemini qua SDK `google-genai`; model cấu hình mặc định hiện là
  `gemini-3.8-flash`.
- Tìm kiếm catalog mẫu bánh công khai và đang nhận đặt; có xử lý ngân sách ở fallback.
- Truy hồi chính sách chỉ từ các nội dung OWNER đã công bố; có nguồn trích dẫn và quản lý
  chính sách phía OWNER.
- Kiểm tra schema/ID đầu ra, một số giới hạn nghiệp vụ, che dữ liệu liên hệ và fallback
  khi provider lỗi.
- Chatbot không có công cụ ghi đơn; khách tiếp tục qua form hiện có.
- Lời chào trong chế độ fallback đã được tách khỏi truy hồi chủ đề cũ.

### Cần giải quyết trước khi mở rộng tư vấn

- Các lần gọi gần đây ghi `provider_unavailable`. Cần xác minh quyền truy cập model, quota,
  trạng thái API và kết nối của môi trường chạy. Mock test không chứng minh API thật hoạt động.
- Catalog hiện chưa bảo đảm có trường dữ liệu chuẩn hóa về khẩu phần, kích thước, thành phần,
  dị ứng, phong cách hoặc thời gian chuẩn bị. Không được tư vấn các tiêu chí đó như sự thật
  cho đến khi dữ liệu được cửa hàng xác nhận.
- Cần có bộ tình huống đánh giá chất lượng tư vấn và quy trình cập nhật khi kết quả sai.

### Ngoài phạm vi giai đoạn này

- Fine-tuning/huấn luyện mô hình nền tảng.
- Tự xác nhận đơn, thanh toán, hoàn tiền, tồn kho, lịch giao hoặc quyền giảm giá.
- Tích hợp ngân hàng, đơn vị giao hàng, email hoặc công cụ web search.
- Tự động công bố chính sách hoặc tự thay đổi catalog bằng nội dung do Gemini tạo.

## 4. Kiến trúc dự kiến

```text
Khách hàng
    │ câu hỏi / tiêu chí
    ▼
Django assistant view
    │ xác thực CUSTOMER, giới hạn tốc độ, kiểm tra đầu vào
    ▼
Backend tư vấn
    ├── phân tích tiêu chí → bộ lọc bắt buộc → xếp hạng catalog
    ├── truy hồi chính sách đã công bố
    ├── kiểm tra dữ liệu thiếu / yêu cầu cần nhân viên
    └── tạo ngữ cảnh tối thiểu và đã lọc dữ liệu cá nhân
            │
            ├── Gemini: tạo lời giải thích/câu hỏi tiếp theo
            └── fallback: trả kết quả tra cứu có cấu trúc
    ▼
Backend xác thực kết quả, mẫu bánh, nguồn và giới hạn nghiệp vụ
    │
    ├── lưu câu trả lời, nguồn, telemetry đã làm sạch
    └── lưu bản nháp hợp lệ nếu phù hợp
    ▼
Giao diện hội thoại, thẻ mẫu bánh, lý do gợi ý, câu hỏi bổ sung
    │
    └── khách chủ động mở form đặt bánh hiện có và gửi yêu cầu
```

Gemini chỉ nên nhận: câu hỏi đã làm sạch, tiêu chí đã trích xuất, một nhóm nhỏ các mẫu
đủ điều kiện, trích đoạn chính sách đã công bố liên quan và hướng dẫn trả lời. Backend
không chấp nhận kết quả tự tạo ID mẫu hoặc nguồn chính sách ngoài danh sách đã truy hồi.

## 5. Kế hoạch theo giai đoạn

### Giai đoạn 0 — Xác nhận provider và đường cơ sở

**Công việc**

- Xác nhận `AI_ENABLED`, provider, model và key có mặt mà không in hoặc đọc giá trị key.
- Gửi một yêu cầu thử có kiểm soát từ môi trường local, chỉ sau khi chủ dự án đã đồng ý
  việc sử dụng API/key và dữ liệu thử không chứa thông tin cá nhân.
- Phân biệt lỗi cấu hình, xác thực, hạn mức, model không hỗ trợ, kết nối/timeout và lỗi
  nội dung/schema. Không ghi response thô hoặc credential.
- Lưu một bộ test baseline mock để phát triển không phụ thuộc API thật.

**Đầu ra**

- Provider trả lời thành công ít nhất một câu hỏi thử hoặc có mã lỗi đủ cụ thể để xử lý.
- Có hướng dẫn cách xem trạng thái provider mà không tiết lộ key.

**Điều kiện hoàn tất**

- Một lần gọi thành công được ghi `succeeded`, hoặc nguyên nhân lỗi được xác định từ mã
  trạng thái đã làm sạch.
- Không có API key, header xác thực hay response chứa dữ liệu nhạy cảm trong log.

### Giai đoạn 1 — Chuẩn hóa dữ liệu sản phẩm

**Công việc**

- Cùng cửa hàng xác định các thuộc tính tư vấn cần quản lý. Đề xuất ban đầu:
  - loại dịp/phong cách;
  - hương vị;
  - kích thước và khẩu phần đã xác nhận;
  - thành phần đã biết và cảnh báo dị ứng đã được xác nhận;
  - thời gian chuẩn bị tối thiểu nếu quy trình thật có quy định;
  - trạng thái nhận đặt và giá tham khảo hiện hành.
- Chốt trường nào là bắt buộc, trường nào được phép để trống. Không nhập dữ liệu giả để
  lấp chỗ trống.
- OWNER cập nhật qua giao diện quản lý catalog; chỉ dữ liệu đã lưu và còn hiệu lực mới
  được đưa vào tư vấn.
- Nếu thêm trường model, tạo migration an toàn; giữ nguyên catalog/database hiện có và
  kiểm thử dữ liệu cũ.

**Đầu ra**

- Bộ từ điển dữ liệu và hướng dẫn OWNER điền dữ liệu.
- Catalog có dữ liệu đã xác nhận cho một tập mẫu dùng làm demo.

**Điều kiện hoàn tất**

- Mỗi thuộc tính có nguồn/người chịu trách nhiệm cập nhật rõ ràng.
- Dữ liệu còn thiếu được biểu diễn là “chưa có thông tin”, không thành giá trị suy đoán.
- Form quản lý và trang chi tiết hiển thị nhất quán; migration không làm mất dữ liệu.

### Giai đoạn 2 — Trích xuất yêu cầu và hỏi làm rõ

**Công việc**

- Xác định cấu trúc tiêu chí nội bộ, ví dụ:
  - `occasion`, `budget_max_vnd`, `people_count`, `flavor`,
    `style`, `size`, `dietary_constraints`, `requested_date`.
- Phân biệt:
  - điều kiện cứng khách yêu cầu;
  - sở thích dùng để xếp hạng;
  - thông tin chưa rõ;
  - yêu cầu cần nhân viên xác nhận.
- Chuẩn hóa cách hiểu ngân sách (ví dụ `200k`, `200.000đ`) và số người; kiểm tra giới
  hạn kiểu dữ liệu, giá trị âm/quá lớn và nội dung không hợp lệ phía backend.
- Lưu tiêu chí hội thoại ở dạng có cấu trúc nếu cần tiếp nối giữa các lượt; cho phép khách
  sửa hoặc xóa tiêu chí đã nêu.
- Nếu thiếu thông tin không cần thiết để gợi ý thì vẫn đưa ra kết quả kèm giả định rõ ràng;
  nếu thiếu dữ liệu an toàn/khả năng đáp ứng thì hỏi lại hoặc chuyển nhân viên.

**Đầu ra**

- Parser/validator tiêu chí độc lập với prompt Gemini.
- Bộ câu hỏi tiếp theo theo dữ liệu thiếu và loại yêu cầu.

**Điều kiện hoàn tất**

- Test cho tiếng Việt có dấu/không dấu, nhiều cách viết tiền, số người, câu có nhiều tiêu chí,
  câu mơ hồ và thông tin mâu thuẫn.
- Không để model tự sửa ngân sách/điều kiện cứng sau khi backend đã trích xuất.

### Giai đoạn 3 — Lọc và xếp hạng catalog ở backend

**Công việc**

- Truy vấn chỉ mẫu công khai, thuộc danh mục đang hoạt động và đang nhận đặt.
- Áp dụng trước các điều kiện cứng:
  - nếu có ngân sách tối đa, loại mẫu có giá tham khảo cao hơn;
  - nếu có điều kiện sản phẩm bắt buộc, chỉ giữ mẫu có dữ liệu xác nhận phù hợp;
  - nếu thông tin khẩu phần/dị ứng không có, không xem là đạt điều kiện đó.
- Xếp hạng các mẫu còn lại theo mức độ khớp sở thích/dịp/hương vị; có thể bắt đầu bằng
  trọng số đơn giản, có thể kiểm thử và giải thích được.
- Trả số lượng hữu hạn (đề xuất tối đa 3–5 mẫu), điểm/thành phần lý do nội bộ và dữ liệu
  snapshot cần cho lượt tư vấn.
- Khi không có kết quả, nói rõ điều kiện nào làm danh sách rỗng; hỏi khách nới điều kiện
  nếu phù hợp, không đưa mẫu vi phạm điều kiện cứng.

**Đầu ra**

- Service retrieval/ranking độc lập với provider.
- Lý do gợi ý gắn với thuộc tính catalog cụ thể, không phải lời giải thích bịa bởi model.

**Điều kiện hoàn tất**

- Test đảm bảo không có mẫu vượt ngân sách, mẫu ẩn/ngừng nhận đặt, mẫu không khớp điều
  kiện bắt buộc hoặc mẫu giả.
- Kết quả ổn định cho cùng catalog và cùng tiêu chí; tối đa 5 mẫu trong mỗi lượt.

### Giai đoạn 4 — Tạo phản hồi Gemini có cấu trúc

**Công việc**

- Chỉ gọi Gemini sau khi backend đã lọc/xếp hạng và tạo ngữ cảnh tối thiểu.
- Quy định output có cấu trúc: câu trả lời, ID mẫu được chọn từ danh sách cho phép, lý do
  theo dữ liệu, câu hỏi làm rõ và cờ cần nhân viên.
- Yêu cầu văn phong thân thiện, tiếng Việt tự nhiên; nêu rõ giá là tham khảo; tránh trả
  lời lặp nguyên danh sách hoặc mở đầu bằng thông báo lỗi nếu Gemini đã thành công.
- Backend xác thực schema, các ID, giá và nguồn. Nếu không hợp lệ thì fallback có căn cứ.
- Không để Gemini tự quyết định điều kiện cứng, tự thêm chính sách hoặc khẳng định giao dịch.

**Đầu ra**

- Prompt hệ thống có phiên bản và được kiểm thử.
- Adapter Gemini trả output có cấu trúc đã được backend xác thực.

**Điều kiện hoàn tất**

- Mock tests bao phủ JSON sai, ID không hợp lệ, câu khẳng định vượt quyền, timeout và
  response rỗng.
- Một bộ câu hỏi demo thật trả lời tự nhiên sau khi provider được xác nhận hoạt động.

### Giai đoạn 5 — Hội thoại, thẻ kết quả và fallback

**Công việc**

- Hiển thị tối đa 3–5 thẻ với tên, ảnh nếu có, giá tham khảo, thuộc tính liên quan, lý do
  gợi ý và liên kết trang chi tiết.
- Hiển thị câu hỏi bổ sung khi tiêu chí quan trọng còn thiếu; cho khách sửa ngân sách/sở thích.
- Fallback:
  - lời chào → chào lại, không lôi chủ đề cũ vào;
  - hỏi mẫu/giá → chỉ tra cứu và đưa các mẫu đủ điều kiện;
  - hỏi chính sách → chỉ trích chính sách đã công bố;
  - không có dữ liệu → nói rõ và đề nghị nhân viên xác nhận;
  - không tạo draft khi provider không sẵn sàng nếu quy trình cần model để tổng hợp.
- UI phân biệt rõ phản hồi Gemini, tra cứu cục bộ và yêu cầu đang xử lý/lỗi.
- Giữ các liên kết tới form đặt bánh hiện tại; không thêm nút có vẻ xác nhận/đặt hàng trong chat.

**Điều kiện hoàn tất**

- Kiểm thử UI cho màn hình desktop/mobile, rỗng, lỗi, fallback, nhiều mẫu và policy citation.
- Nội dung không tạo cảm giác đã đặt hàng hoặc giữ chỗ thành công.

### Giai đoạn 6 — Đánh giá chất lượng và an toàn

**Công việc**

- Tạo bộ dữ liệu đánh giá thủ công với nhiều nhóm:
  - ngân sách chính xác/không có mẫu;
  - sinh nhật, kỷ niệm, cupcake;
  - số người nhưng thiếu dữ liệu khẩu phần;
  - dị ứng/thành phần;
  - thời gian giao gấp;
  - thay đổi điều kiện qua nhiều lượt;
  - hỏi chính sách, báo đã thanh toán, giảm giá, xem đơn người khác;
  - lời chào, câu ngoài chủ đề và prompt injection.
- Chấm riêng:
  - tính đúng của tập mẫu backend chọn;
  - tuân thủ điều kiện cứng;
  - độ chính xác của giá/nguồn;
  - mức hữu ích của lý do và câu hỏi tiếp theo;
  - tỷ lệ cần nhân viên;
  - tỷ lệ provider/fallback và độ trễ.
- Lưu bộ test không chứa key, dữ liệu cá nhân thật hoặc hội thoại khách hàng chưa được phép
  sử dụng. Nếu dùng hội thoại thực tế, phải có quy trình đồng ý, ẩn danh và thời hạn lưu.
- Đặt ngưỡng chấp nhận trước demo; ví dụ 100% mẫu được gợi ý tuân thủ ngân sách và trạng
  thái công khai/nhận đặt trên bộ kiểm thử, không có tuyên bố thanh toán/đơn hàng giả.

**Điều kiện hoàn tất**

- Mỗi thay đổi prompt/retrieval đều chạy lại bộ đánh giá.
- Có danh sách lỗi đã biết, mức độ và hướng xử lý; không chỉ đánh giá bằng cảm nhận.

### Giai đoạn 7 — Vận hành, quyền riêng tư và phát hành demo

**Công việc**

- Theo dõi số lượt thành công/fallback, latency, nhóm mã lỗi, model và token nếu SDK cung cấp;
  không ghi prompt thô, response thô, key hoặc thông tin liên hệ.
- Giới hạn tốc độ theo khách và toàn hệ thống; đặt timeout, giới hạn độ dài input/output và
  số lần retry để kiểm soát chi phí.
- Quy định thời hạn lưu chat/draft/log; giới hạn quyền truy cập OWNER/STAFF/CUSTOMER đúng
  vai trò, chỉ khách xem hội thoại của mình.
- Tài liệu hóa cài đặt key bằng `.env`, bật/tắt AI, chọn model được tài khoản cho phép, xử lý
  quota và chuyển sang fallback.
- Kiểm tra backup/migration, `manage.py check`, `makemigrations --check --dry-run`, toàn bộ
  test và kịch bản demo trước khi trình bày.

**Điều kiện hoàn tất**

- Không lộ bí mật trong Git, browser, console, logs hoặc tài liệu.
- Có thể tắt Gemini và hệ thống vẫn cung cấp thông báo/fallback phù hợp.
- Demo xác nhận được giới hạn giữa tư vấn và đặt hàng thật.

## 6. Bộ tiêu chí gợi ý phiên bản đầu

| Tiêu chí | Vai trò | Xử lý |
|---|---|---|
| Ngân sách tối đa | Điều kiện cứng | Không gợi ý mẫu vượt giá tham khảo |
| Công khai/đang nhận đặt | Điều kiện cứng | Chỉ truy vấn mẫu đủ điều kiện |
| Dịp sử dụng | Xếp hạng | Dùng category/tags đã xác nhận |
| Hương vị | Xếp hạng hoặc điều kiện cứng theo lời khách | Chỉ dựa trên dữ liệu sản phẩm |
| Số người/khẩu phần | Cần dữ liệu xác nhận | Nếu thiếu thì hỏi hoặc báo chưa có thông tin |
| Dị ứng/chế độ ăn | An toàn, cần xác nhận người thật | Không bảo đảm an toàn bằng suy luận AI |
| Ngày/giờ mong muốn | Không xác nhận được từ catalog | Nhân viên kiểm tra khả năng đáp ứng |
| Phong cách/trang trí | Xếp hạng nếu catalog có dữ liệu | Yêu cầu riêng cần nhân viên xác nhận |

## 7. Bộ kiểm thử nghiệm thu tối thiểu

1. Câu “200k, bánh sinh nhật cho một người” không gợi ý bánh vượt 200.000đ; không khẳng
   định khẩu phần nếu catalog chưa có dữ liệu.
2. Câu yêu cầu chocolate dưới một ngân sách chỉ trả mẫu chocolate đủ điều kiện; nếu không có,
   nói rõ và có thể hỏi khách nới ngân sách.
3. Câu hỏi dị ứng không nhận được câu khẳng định an toàn khi thiếu dữ liệu xác nhận.
4. Câu cần giao gấp không bị bot xác nhận lịch giao; chuyển nhân viên kiểm tra.
5. Câu hỏi chính sách chỉ trích policy đang công bố, có link backend hợp lệ.
6. Câu hỏi thanh toán/đơn của người khác/giảm giá không được xác nhận hoặc tiết lộ dữ liệu.
7. Gemini timeout/5xx/429/401/response lỗi không làm mất hội thoại; fallback được gắn nhãn.
8. Lời chào sau câu hỏi về bánh không nhận nhầm ngữ cảnh cũ khi Gemini lỗi.
9. Gửi lặp request không tạo nhiều bản nháp/đơn; không có order nào được tạo trực tiếp từ chat.
10. Kiểm thử role CUSTOMER, STAFF, OWNER, CSRF và quyền riêng tư hội thoại.

## 8. Các quyết định cần cửa hàng xác nhận

- Khẩu phần tiêu chuẩn theo kích thước/mẫu và mức độ chính xác mong muốn.
- Danh sách thành phần/dị ứng nào cửa hàng có thể xác nhận; quy trình xử lý câu hỏi dị ứng.
- Tiêu chí “phù hợp ngân sách”: chỉ giá mẫu hay gồm trang trí/phí khác. Mặc định chatbot chỉ
  lọc theo giá tham khảo catalog và phải nói rõ chưa phải báo giá cuối.
- Có lưu tiêu chí tư vấn giữa các lượt không, lưu bao lâu và khách có thể xóa bằng cách nào.
- Ai chịu trách nhiệm cập nhật tags/giá/thành phần và tần suất rà soát.
- Tài khoản Gemini/model nào được dùng, hạn mức demo và giới hạn chi phí.
- Ngưỡng chất lượng chấp nhận được cho demo; ai duyệt nội dung trước khi đưa vào sử dụng.

## 9. Thứ tự triển khai đề xuất

1. Xác minh Gemini API hoạt động và phân loại lỗi provider chính xác.
2. Chốt dữ liệu tư vấn và quy trình OWNER cập nhật catalog.
3. Triển khai tiêu chí có cấu trúc, bộ lọc bắt buộc và ranking backend.
4. Cập nhật Gemini prompt/schema để giải thích kết quả đã chọn và hỏi làm rõ.
5. Hoàn thiện UI thẻ mẫu, câu hỏi tiếp theo, trạng thái fallback và chuyển nhân viên.
6. Xây bộ đánh giá, chạy test an toàn/phân quyền và sửa lỗi.
7. Cập nhật hướng dẫn, chuẩn bị kịch bản demo và trình bày giới hạn đã biết.

Không nên bắt đầu bằng fine-tuning. Chỉ cân nhắc sau khi retrieval/catalog đã ổn định, có
nhiều ví dụ hội thoại được cửa hàng rà soát và xác nhận, có bộ đo chất lượng rõ ràng và
provider thực sự hỗ trợ quy trình fine-tuning phù hợp. Giá, mẫu đang bán, chính sách và lịch
thay đổi nên tiếp tục lấy từ database tại thời điểm hỏi thay vì “nhồi” vào trọng số model.

## 10. Tài liệu liên quan

- [Bối cảnh dự án](./PROJECT_CONTEXT.md)
- [Phạm vi và cấu hình chatbot](./AI_ASSISTANT.md)
- [README chạy dự án](./README.md)
