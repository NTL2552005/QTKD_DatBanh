from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assistant.models import StorePolicy


DEMO_POLICIES = (
    (
        "CS01",
        "Tiếp nhận và xác nhận đơn",
        """Khách hàng có thể gửi yêu cầu đặt bánh qua website BiBi Cake. Thông tin cần cung cấp gồm mẫu hoặc loại bánh mong muốn, số lượng, yêu cầu trang trí, thời gian nhận bánh và thông tin liên hệ, giao nhận theo biểu mẫu.

Việc gửi yêu cầu chưa đồng nghĩa cửa hàng đã nhận thực hiện. Cửa hàng sẽ kiểm tra yêu cầu, khả năng thực hiện và phản hồi báo giá.

Đơn chỉ được xác nhận sau khi khách đồng ý báo giá còn hiệu lực và hệ thống ghi nhận xác nhận thành công.""",
    ),
    (
        "CS02",
        "Yêu cầu đặc biệt và đơn gấp",
        """Yêu cầu thiết kế đặc biệt, thời gian thực hiện gấp hoặc điều kiện ngoài phạm vi xử lý của nhân viên được chuyển cho chủ cửa hàng xem xét.

Nếu chấp nhận, chủ gửi báo giá và điều kiện thực hiện trực tiếp cho khách. Nếu không thể tiếp nhận, cửa hàng thông báo từ chối yêu cầu.

Cửa hàng không cam kết nhận đơn trước khi hoàn tất xem xét. Khách cần đồng ý báo giá trước khi đơn được xác nhận.""",
    ),
    (
        "CS03",
        "Khả năng đáp ứng và phương án thay thế",
        """Khả năng thực hiện phụ thuộc vào nguyên liệu, lịch làm bánh và yêu cầu cụ thể tại thời điểm cửa hàng kiểm tra.

Nếu không đủ nguyên liệu hoặc không đáp ứng được thời gian, cửa hàng có thể đề xuất đổi hương vị, mẫu bánh, nguyên liệu hoặc thời gian giao nhận.

Phương án thay thế chỉ áp dụng khi khách đồng ý. Cửa hàng cập nhật yêu cầu và kiểm tra lại khả năng đáp ứng trước khi báo giá.

Nếu không có phương án phù hợp hoặc khách không đồng ý, cửa hàng thông báo không tiếp nhận đơn.""",
    ),
    (
        "CS04",
        "Giá niêm yết và báo giá chính thức",
        """Giá trên danh mục áp dụng cho mẫu bánh và thông số được mô tả tương ứng. Thay đổi kích thước, số lượng, nguyên liệu hoặc trang trí có thể làm thay đổi giá.

Báo giá chính thức thể hiện nội dung bánh, số lượng, giá, phí giao hàng nếu có, thời gian giao nhận và điều kiện liên quan. Khách cần kiểm tra trước khi đồng ý.

Báo giá có hiệu lực theo thời hạn ghi trên báo giá. Khi hết hạn hoặc yêu cầu thay đổi, cửa hàng cần kiểm tra và phản hồi lại.

Mọi mức giảm giá, phụ phí hoặc giá bánh tùy chỉnh cần được cửa hàng xác nhận trong báo giá.""",
    ),
    (
        "CS05",
        "Điều chỉnh nội dung đơn hàng",
        """Khách có thể gửi đề nghị thay đổi thông tin đơn. Việc gửi đề nghị chưa đồng nghĩa thay đổi đã được chấp nhận.

Cửa hàng xem xét khả năng điều chỉnh theo tiến độ. Khi bánh đã bắt đầu làm, một số nội dung có thể không đổi được hoặc cần phương án xử lý riêng.

Nếu thay đổi ảnh hưởng đến giá, thời gian hoặc điều kiện, cửa hàng thông báo để khách xem xét và đồng ý trước khi áp dụng.

Việc gửi đề nghị thay đổi chưa đồng nghĩa cửa hàng đã phê duyệt.""",
    ),
    (
        "CS06",
        "Kiểm tra ảnh trước khi giao",
        """Sau khi hoàn thiện và kiểm tra chất lượng, cửa hàng gửi ảnh bánh để khách xem xét trước khi giao.

Khách cần kiểm tra nội dung chữ, màu sắc, trang trí và yêu cầu đã thống nhất. Nếu chưa đồng ý, khách phản hồi rõ nội dung cần điều chỉnh.

Nếu sau 30 phút khách chưa phản hồi, cửa hàng gửi nhắc. Chưa phản hồi không được coi là đồng ý và không tự động làm đơn bị hủy.

Yêu cầu chỉnh sửa được xem xét theo phạm vi có thể thực hiện. Nếu không thể sửa theo yêu cầu, cửa hàng trao đổi phương án xử lý với khách.""",
    ),
    (
        "CS07",
        "Thông tin và thời gian giao hàng",
        """Khách cần cung cấp chính xác địa chỉ, số điện thoại và thời gian nhận bánh mong muốn.

Thời gian nhập ban đầu là thời gian đề nghị. Thời gian cam kết thực hiện được thể hiện trong báo giá hoặc nội dung xác nhận hai bên đã thống nhất.

Phí giao hàng, nếu có, được thông báo trong báo giá; khách không nên tự suy đoán khoản phí khi chưa được xác nhận.

Nếu phát sinh sự cố giao hàng, cửa hàng liên hệ khách để thống nhất phương án xử lý.""",
    ),
    (
        "CS08",
        "Thanh toán và xác nhận nhận tiền",
        """Khách thanh toán theo phương thức và hướng dẫn trên đơn hoặc thông báo chính thức của cửa hàng.

Khách thông báo đã chuyển khoản chưa đồng nghĩa giao dịch đã được cửa hàng xác nhận. Trạng thái thanh toán chỉ cập nhật sau khi có kiểm tra hoặc xác nhận qua chức năng nghiệp vụ.

Nếu đã thanh toán trước, cửa hàng ghi nhận khoản thanh toán để tránh thu trùng khi giao.

Đơn được hoàn tất khi giao nhận thành công và nghĩa vụ thanh toán đã được ghi nhận đầy đủ. Thanh toán trước không đồng nghĩa đã hoàn tất giao hàng.""",
    ),
    (
        "CS09",
        "Xét duyệt yêu cầu hủy và hoàn tiền",
        """Khách có thể gửi yêu cầu hủy qua chức năng của hệ thống.

Nếu bánh chưa bắt đầu làm, nhân viên kiểm tra và xử lý yêu cầu hủy. Nếu bánh đã bắt đầu làm, yêu cầu được chuyển cho chủ cửa hàng xem xét.

Gửi yêu cầu hủy chưa làm đơn tự động bị hủy. Cửa hàng thông báo kết quả sau khi hoàn tất xử lý.

Nếu hủy được chấp nhận và có tiền đã thanh toán, cửa hàng kiểm tra để thông báo phương án hoàn tiền cụ thể.""",
    ),
    (
        "CS10",
        "Tiếp nhận phản ánh và xử lý sự cố",
        """Nếu bánh có vấn đề hoặc nhận hàng không đúng nội dung đã thống nhất, khách nên phản hồi sớm và cung cấp mã đơn, mô tả cùng hình ảnh liên quan nếu có.

Cửa hàng kiểm tra và trao đổi phương án phù hợp: sửa bánh, đổi bánh, giao lại hoặc xem xét hủy và hoàn tiền.

Phương án cụ thể phụ thuộc kết quả kiểm tra và trao đổi với khách.""",
    ),
    (
        "CS11",
        "Góp ý sau khi nhận bánh",
        """Sau khi nhận bánh, khách có thể đánh giá đơn qua chức năng được cung cấp trên hệ thống.

Đánh giá nên phản ánh trải nghiệm thực tế, sử dụng ngôn ngữ phù hợp và không công khai thông tin cá nhân của người khác.

Nếu cần xử lý sự cố, khách nên gửi phản ánh qua chức năng hỗ trợ của đơn hàng để cửa hàng kiểm tra.""",
    ),
    (
        "CS12",
        "Thông tin và xác nhận của cửa hàng",
        """Thông tin trên website có thể mang tính tham khảo. Cửa hàng kiểm tra yêu cầu cụ thể và xác nhận điều kiện thực hiện qua báo giá hoặc trao đổi trên đơn.

Gửi yêu cầu đặt bánh chưa đồng nghĩa đơn đã được cửa hàng nhận thực hiện. Khách chỉ xác nhận đơn sau khi đồng ý báo giá chính thức.

Các thay đổi về giá, thời gian, thanh toán, giao nhận hoặc xử lý sự cố cần được xác nhận qua chức năng nghiệp vụ hoặc trao đổi với nhân viên.""",
    ),
)


class Command(BaseCommand):
    help = "Tạo chính sách demo ở trạng thái nháp, không ghi đè nội dung có sẵn."

    def handle(self, *args, **options):
        user_model = get_user_model()
        owner = user_model.objects.filter(role=user_model.Role.OWNER).order_by("pk").first()
        if owner is None:
            raise CommandError(
                "Chưa có tài khoản OWNER. Hãy tạo OWNER trước khi nạp chính sách demo."
            )

        created_codes = []
        skipped_codes = []
        skipped_statuses = {}
        with transaction.atomic():
            for code, title, content in DEMO_POLICIES:
                policy, created = StorePolicy.objects.get_or_create(
                    code__iexact=code,
                    defaults={
                        "code": code,
                        "title": title,
                        "content": content,
                        "is_published": False,
                        "created_by": owner,
                    },
                )
                if created:
                    created_codes.append(code)
                else:
                    skipped_codes.append(code)
                    skipped_statuses[code] = policy.is_published

        self.stdout.write(
            f"Created ({len(created_codes)}): "
            f"{', '.join(f'{code} (draft)' for code in created_codes) or 'none'}"
        )
        self.stdout.write(
            f"Skipped ({len(skipped_codes)}): "
            + (
                ", ".join(
                    f"{code} ({'published' if skipped_statuses[code] else 'draft'})"
                    for code in skipped_codes
                )
                or "none"
            )
        )
        self.stdout.write("New policies are created as drafts.")
