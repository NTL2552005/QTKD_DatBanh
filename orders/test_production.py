import io
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from catalog.models import CakeTemplate, Category

from .models import (
    ApprovalRequest,
    CakeReviewVersion,
    ChangeAgreement,
    Order,
    OrderHistory,
    ProductionCheck,
    Quote,
    RevisionRequest,
)
from .production import (
    accept_review,
    classify_revision_request,
    continue_revision_discussion,
    decide_revision_approval,
    keep_current_review,
    record_production_check,
    request_review_revision,
    respond_change_agreement,
    send_review_photo,
    start_production,
)
from .services import (
    InvalidOrderTransition,
    StaleOrderVersion,
    accept_quote,
    create_order,
    record_staff_check,
    send_quote,
)

User = get_user_model()


def image_upload(name="cake.png", image_format="PNG"):
    image = Image.new("RGB", (32, 24), (230, 150, 170))
    data = io.BytesIO()
    image.save(data, format=image_format)
    content_type = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }[image_format]
    return SimpleUploadedFile(name, data.getvalue(), content_type=content_type)


class ProductionWorkflowTestCase(TestCase):
    def setUp(self):
        self.private_media = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_media.cleanup)
        self.settings(PRIVATE_MEDIA_ROOT=self.private_media.name)
        self.customer = User.objects.create_user(
            username="production_customer",
            password="A-strong-demo-password-983!",
            role=User.Role.CUSTOMER,
        )
        self.other_customer = User.objects.create_user(
            username="production_other",
            password="A-strong-demo-password-983!",
            role=User.Role.CUSTOMER,
        )
        self.staff = User.objects.create_user(
            username="production_staff",
            password="A-strong-demo-password-983!",
            role=User.Role.STAFF,
        )
        self.owner = User.objects.create_user(
            username="production_owner",
            password="A-strong-demo-password-983!",
            role=User.Role.OWNER,
        )
        self.category = Category.objects.create(name="Bánh sinh nhật")
        self.cake = CakeTemplate.objects.create(
            category=self.category,
            name="Bánh kem dâu",
            reference_price=Decimal("350000"),
        )

    def new_order(self):
        order, created = create_order(
            customer=self.customer,
            cake_id=self.cake.pk,
            request_key=uuid.uuid4(),
            data={
                "size": "20 cm",
                "flavor": "Dâu",
                "quantity": 2,
                "inscription": "",
                "decoration_request": "Hoa kem theo mẫu",
                "recipient_name": "Nguyễn An",
                "recipient_phone": "0912345678",
                "delivery_address": "Quận 1, TP. Hồ Chí Minh",
                "desired_delivery_at": timezone.now() + timedelta(days=5),
                "note": "",
            },
        )
        self.assertTrue(created)
        return order

    def accepted_order(self):
        order = self.new_order()
        order = record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=True,
            schedule_available=True,
            notes="Có nguyên liệu và lịch làm.",
            authority_classification="IN_SCOPE",
            authority_basis="Đối chiếu mẫu và quy định hiện hành.",
        )
        order, quote = send_quote(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            unit_price=Decimal("400000"),
            delivery_fee=Decimal("25000"),
            proposed_delivery_at=timezone.now() + timedelta(days=5),
            expires_at=timezone.now() + timedelta(days=2),
            notes="Báo giá đã thống nhất.",
            revision_reason="",
        )
        order = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
        )
        return order

    def start_order(self):
        order = self.accepted_order()
        return start_production(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
        )

    def check_quality(self, order, passed=True, notes="Bánh đạt yêu cầu."):
        return record_production_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            passed=passed,
            notes=notes,
        )

    def make_review(self, order, *, reason=""):
        return send_review_photo(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            photo=image_upload(),
            description="Ảnh bánh hoàn thiện.",
            replacement_reason=reason,
        )

    def request_revision(self, order, review, message="Vui lòng chỉnh hoa kem."):
        return request_review_revision(
            order_id=order.pk,
            customer=self.customer,
            review_id=review.pk,
            expected_order_version=order.version,
            expected_review_revision=review.revision,
            message=message,
        )

    def classify_outside(self, order, revision):
        return classify_revision_request(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            scope=RevisionRequest.Scope.OUTSIDE,
            reason="Khách yêu cầu đổi kích thước và thiết kế đã chốt.",
            approval_summary="Thay đổi thiết kế đã thống nhất.",
            proposed_plan="Cập nhật phương án trang trí và kiểm tra lại lịch.",
        )

    def prepare_outside_request(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, review = self.make_review(order)
        order, revision = self.request_revision(order, review)
        order, revision, approval = self.classify_outside(order, revision)
        return order, revision, approval


class ProductionLifecycleTests(ProductionWorkflowTestCase):
    def test_only_confirmed_order_with_accepted_quote_can_start(self):
        order = self.new_order()
        with self.assertRaises(InvalidOrderTransition):
            start_production(
                order_id=order.pk,
                staff=self.staff,
                expected_version=order.version,
            )
        confirmed = self.accepted_order()
        started = start_production(
            order_id=confirmed.pk,
            staff=self.staff,
            expected_version=confirmed.version,
        )
        self.assertEqual(started.status, Order.Status.DANG_LAM)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=confirmed,
                action=OrderHistory.Action.PRODUCTION_STARTED,
            ).count(),
            1,
        )

    def test_failed_quality_check_requires_reason_and_blocks_photo(self):
        order = self.start_order()
        with self.assertRaises(ValidationError):
            self.check_quality(order, passed=False, notes="   ")
        order, check = self.check_quality(
            order,
            passed=False,
            notes="Kem chưa đều, cần làm lại.",
        )
        self.assertFalse(check.passed)
        with self.assertRaises(InvalidOrderTransition):
            self.make_review(order)

    def test_qa_is_bound_to_production_round_and_must_be_repeated_after_edit(self):
        order = self.start_order()
        order, _check = self.check_quality(order)
        order, review = self.make_review(order)
        order, revision = self.request_revision(order, review)
        order, revision, _approval = classify_revision_request(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            scope=RevisionRequest.Scope.WITHIN,
            reason="Yêu cầu đổi hoa kem nhỏ hơn, nằm trong thỏa thuận trang trí.",
        )
        self.assertEqual(order.production_round, 2)
        with self.assertRaises(InvalidOrderTransition):
            self.make_review(order)
        order, check = self.check_quality(
            order,
            notes="Đã kiểm tra lại sau chỉnh sửa.",
        )
        order, second_review = self.make_review(order)
        revision.refresh_from_db()
        self.assertEqual(check.production_round, 2)
        self.assertEqual(second_review.production_round, 2)
        self.assertEqual(revision.status, RevisionRequest.Status.COMPLETED)
        self.assertEqual(revision.replacement_review_version, second_review)
        self.assertEqual(order.status, Order.Status.CHO_DUYET_ANH)

    def test_customer_accepts_only_current_review_and_old_photo_is_retained(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, first_review = self.make_review(order)
        order, second_review = self.make_review(
            order,
            reason="Ảnh đầu bị mờ, gửi lại ảnh rõ hơn.",
        )
        first_review.refresh_from_db()
        self.assertEqual(first_review.status, CakeReviewVersion.Status.REPLACED)
        self.assertTrue(first_review.photo.storage.exists(first_review.photo.name))
        with self.assertRaises(InvalidOrderTransition):
            accept_review(
                order_id=order.pk,
                customer=self.customer,
                review_id=first_review.pk,
                expected_order_version=order.version,
                expected_review_revision=first_review.revision - 1,
            )

        accepted = accept_review(
            order_id=order.pk,
            customer=self.customer,
            review_id=second_review.pk,
            expected_order_version=order.version,
            expected_review_revision=second_review.revision,
        )
        self.assertEqual(accepted.status, Order.Status.SAN_SANG_GIAO)
        self.assertEqual(Order.objects.get(pk=order.pk).current_review, second_review)
        with self.assertRaises(InvalidOrderTransition):
            accept_review(
                order_id=order.pk,
                customer=self.customer,
                review_id=second_review.pk,
                expected_order_version=accepted.version,
                expected_review_revision=second_review.revision,
            )

    def test_photo_replacement_requires_reason_and_preserves_old_version(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, first = self.make_review(order)
        with self.assertRaises(ValidationError):
            self.make_review(order)
        order, second = self.make_review(
            order,
            reason="Đã gửi nhầm ảnh chụp, thay bằng ảnh đúng.",
        )
        first.refresh_from_db()
        self.assertEqual(first.status, CakeReviewVersion.Status.REPLACED)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(second.replacement_reason, "Đã gửi nhầm ảnh chụp, thay bằng ảnh đúng.")
        self.assertTrue(first.photo.storage.exists(first.photo.name))

    def test_failed_photo_transaction_removes_private_file(self):
        order = self.start_order()
        order, _check = self.check_quality(order)
        with patch(
            "orders.production._change_order",
            side_effect=StaleOrderVersion("Đơn vừa thay đổi."),
        ):
            with self.assertRaises(StaleOrderVersion):
                self.make_review(order)

        self.assertFalse(CakeReviewVersion.objects.exists())
        self.assertEqual(list(Path(self.private_media.name).rglob("*.*")), [])

    def test_review_request_requires_text_and_links_to_exact_review(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, review = self.make_review(order)
        with self.assertRaises(ValidationError):
            self.request_revision(order, review, "  ")
        order, revision = self.request_revision(order, review)
        review.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_XU_LY_CHINH_SUA)
        self.assertEqual(revision.review_version, review)
        self.assertEqual(revision.status, RevisionRequest.Status.OPEN)
        self.assertEqual(review.status, CakeReviewVersion.Status.REVISION_REQUESTED)

    def test_revision_in_scope_restarts_production_and_requires_new_qa(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, review = self.make_review(order)
        order, _ = self.request_revision(order, review)
        order, revision, approval = classify_revision_request(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            scope=RevisionRequest.Scope.WITHIN,
            reason="Điều chỉnh hoa kem, không đổi kích thước hay giá đã chốt.",
        )
        self.assertIsNone(approval)
        self.assertEqual(order.status, Order.Status.DANG_LAM)
        self.assertEqual(order.production_round, 2)
        self.assertEqual(revision.status, RevisionRequest.Status.IN_PROGRESS)
        self.assertIsNone(order.production_checks.filter(production_round=2).first())


class PrivateReviewAccessTests(ProductionWorkflowTestCase):
    def test_private_image_requires_role_and_customer_ownership(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, review = self.make_review(order)
        url = reverse("orders:private_review_image", kwargs={"review_id": review.pk})

        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.client.force_login(self.other_customer)
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.customer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Cache-Control"].startswith("private"))
        self.assertEqual(b"".join(response.streaming_content)[:8], b"\x89PNG\r\n\x1a\n")

    def test_private_review_photo_is_not_served_from_public_media_url(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, review = self.make_review(order)
        self.settings(DEBUG=True)
        response = self.client.get(f"/media/{review.photo.name}")
        self.assertEqual(response.status_code, 404)

    def test_staff_and_related_owner_can_access_but_unrelated_owner_cannot(self):
        order = self.start_order()
        order, _ = self.check_quality(order)
        order, review = self.make_review(order)
        url = reverse("orders:private_review_image", kwargs={"review_id": review.pk})
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.classify_outside(
            *self.request_revision(order, review),
        )
        self.assertEqual(self.client.get(url).status_code, 200)


class RevisionApprovalAndAgreementTests(ProductionWorkflowTestCase):
    def test_out_of_scope_revision_requires_owner_and_owner_rejection_needs_customer_choice(self):
        order, revision, approval = self.prepare_outside_request()
        self.assertEqual(order.status, Order.Status.CHO_CHU_DUYET)
        self.assertEqual(approval.status, ApprovalRequest.Status.PENDING)
        with self.assertRaises(InvalidOrderTransition):
            decide_revision_approval(
                approval_id=approval.pk,
                owner=self.staff,
                expected_order_version=order.version,
                expected_approval_id=approval.pk,
                approve=True,
                reason="Should not be allowed.",
            )
        order, _agreement = decide_revision_approval(
            approval_id=approval.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_approval_id=approval.pk,
            approve=False,
            reason="Phạm vi chỉnh sửa chưa phù hợp với lịch cửa hàng.",
        )
        revision.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_XU_LY_CHINH_SUA)
        self.assertEqual(revision.status, RevisionRequest.Status.OWNER_REJECTED)
        order = keep_current_review(
            order_id=order.pk,
            customer=self.customer,
            expected_order_version=order.version,
            review_id=order.current_review_id,
        )
        revision.refresh_from_db()
        self.assertEqual(order.status, Order.Status.SAN_SANG_GIAO)
        self.assertEqual(revision.status, RevisionRequest.Status.CUSTOMER_KEPT)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.CUSTOMER_KEPT_REVIEW,
            ).count(),
            1,
        )

    def test_customer_can_continue_discussion_after_owner_rejects(self):
        order, revision, approval = self.prepare_outside_request()
        order, _ = decide_revision_approval(
            approval_id=approval.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_approval_id=approval.pk,
            approve=False,
            reason="Cần khách điều chỉnh mong muốn.",
        )
        order, revision = continue_revision_discussion(
            order_id=order.pk,
            customer=self.customer,
            expected_order_version=order.version,
            message="Tôi muốn giữ kích thước, chỉ đổi màu kem.",
        )
        self.assertEqual(order.status, Order.Status.CHO_TRAO_DOI_CHINH_SUA)
        self.assertEqual(revision.status, RevisionRequest.Status.OPEN)
        history = OrderHistory.objects.get(
            order=order,
            action=OrderHistory.Action.CUSTOMER_CONTINUED_DISCUSSION,
        )
        self.assertIn("chỉ đổi màu kem", history.message)

    def test_owner_approval_within_existing_terms_allows_edit_without_customer_consent(self):
        order, revision, approval = self.prepare_outside_request()
        order, agreement = decide_revision_approval(
            approval_id=approval.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_approval_id=approval.pk,
            approve=True,
            reason="Chấp thuận trong giá và lịch đã chốt.",
        )
        revision.refresh_from_db()
        self.assertIsNone(agreement)
        self.assertEqual(order.status, Order.Status.DANG_LAM)
        self.assertEqual(order.production_round, 2)
        self.assertEqual(revision.status, RevisionRequest.Status.IN_PROGRESS)

    def test_commercial_change_requires_customer_acceptance_and_rejection_preserves_old_terms(self):
        order, revision, approval = self.prepare_outside_request()
        original_quote = order.current_quote
        order, agreement = decide_revision_approval(
            approval_id=approval.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_approval_id=approval.pk,
            approve=True,
            reason="Khách cần duyệt thay đổi giá và lịch.",
            requires_change_agreement=True,
            unit_price=Decimal("450000"),
            delivery_fee=Decimal("30000"),
            proposed_delivery_at=timezone.now() + timedelta(days=7),
        )
        original_quote.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_KHACH_DUYET_THAY_DOI)
        self.assertEqual(agreement.previous_total, Decimal("825000"))
        self.assertEqual(agreement.total, Decimal("930000"))
        self.assertEqual(agreement.price_delta, Decimal("105000"))
        self.assertEqual(original_quote.total, Decimal("825000"))
        self.assertEqual(original_quote.status, Quote.Status.ACCEPTED)
        self.assertIsNone(order.active_change_agreement)

        order, agreement = respond_change_agreement(
            agreement_id=agreement.pk,
            customer=self.customer,
            expected_order_version=order.version,
            expected_agreement_id=agreement.pk,
            expected_agreement_revision=agreement.revision,
            accept=False,
            message="Tôi chưa đồng ý thay đổi giá này.",
        )
        revision.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_TRAO_DOI_CHINH_SUA)
        self.assertEqual(revision.status, RevisionRequest.Status.CUSTOMER_DECLINED)
        self.assertEqual(original_quote.total, Decimal("825000"))
        self.assertIsNone(order.active_change_agreement)
        self.assertEqual(order.production_round, 1)

    def test_agreement_only_applies_after_customer_accepts_and_stale_reply_fails(self):
        order, revision, approval = self.prepare_outside_request()
        order, agreement = decide_revision_approval(
            approval_id=approval.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_approval_id=approval.pk,
            approve=True,
            reason="Cập nhật lịch và giá theo yêu cầu mới.",
            requires_change_agreement=True,
            unit_price=Decimal("425000"),
            delivery_fee=Decimal("25000"),
            proposed_delivery_at=timezone.now() + timedelta(days=7),
        )
        accepted_order, agreement = respond_change_agreement(
            agreement_id=agreement.pk,
            customer=self.customer,
            expected_order_version=order.version,
            expected_agreement_id=agreement.pk,
            expected_agreement_revision=agreement.revision,
            accept=True,
            message="Tôi đồng ý phụ lục.",
        )
        revision.refresh_from_db()
        accepted_order.refresh_from_db()
        agreement.refresh_from_db()
        self.assertEqual(accepted_order.status, Order.Status.DANG_LAM)
        self.assertEqual(accepted_order.production_round, 2)
        self.assertEqual(accepted_order.active_change_agreement, agreement)
        self.assertEqual(revision.status, RevisionRequest.Status.IN_PROGRESS)
        with self.assertRaises(InvalidOrderTransition):
            respond_change_agreement(
                agreement_id=agreement.pk,
                customer=self.customer,
                expected_order_version=accepted_order.version,
                expected_agreement_id=agreement.pk,
                expected_agreement_revision=agreement.revision,
                accept=True,
            )


class ProductionViewPermissionTests(ProductionWorkflowTestCase):
    def test_staff_only_production_actions_and_get_does_not_change_state(self):
        order = self.accepted_order()
        start_url = reverse("orders:staff_start_production", kwargs={"pk": order.pk})
        self.client.force_login(self.customer)
        denied = self.client.post(start_url, {"expected_version": order.version})
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.staff)
        get_response = self.client.get(start_url)
        self.assertEqual(get_response.status_code, 405)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)

    def test_csrf_required_for_staff_production_mutations(self):
        order = self.accepted_order()
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        denied = csrf_client.post(
            reverse("orders:staff_start_production", kwargs={"pk": order.pk}),
            {"expected_version": order.version},
        )
        self.assertEqual(denied.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)

    def test_customer_and_staff_cannot_access_owner_approval_queue(self):
        order, _revision, approval = self.prepare_outside_request()
        self.client.force_login(self.customer)
        customer_response = self.client.get(reverse("orders:owner_approval_list"))
        self.assertEqual(customer_response.status_code, 403)
        self.client.force_login(self.staff)
        staff_response = self.client.get(reverse("orders:owner_approval_detail", kwargs={"approval_id": approval.pk}))
        self.assertEqual(staff_response.status_code, 403)


class ProductionDashboardTests(ProductionWorkflowTestCase):
    def test_staff_and_owner_dashboards_show_live_work_queues(self):
        order = self.start_order()
        order, _check = self.check_quality(order)
        order, _review = self.make_review(order)
        self.client.force_login(self.staff)
        staff_response = self.client.get(reverse("accounts:staff_dashboard"))
        self.assertContains(staff_response, "Chờ khách duyệt ảnh: 1")
        self.client.force_login(self.customer)
        customer_response = self.client.get(reverse("accounts:customer_dashboard"))
        self.assertContains(customer_response, "Chờ duyệt ảnh: 1")

        order, revision = self.request_revision(order, order.current_review)
        order, revision, _approval = self.classify_outside(order, revision)
        self.client.force_login(self.owner)
        owner_response = self.client.get(reverse("accounts:owner_dashboard"))
        self.assertContains(owner_response, "Yêu cầu chỉnh sửa vượt phạm vi chờ duyệt:")
        self.assertEqual(owner_response.context["pending_approvals"], 1)
