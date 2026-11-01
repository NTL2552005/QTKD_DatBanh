import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import CakeTemplate, Category

from .models import Order, OrderHistory, Quote
from .services import (
    IdempotencyConflict,
    InvalidOrderTransition,
    accept_quote,
    create_order,
    record_staff_check,
    reject_order,
    request_customer_supplement,
    request_quote_change,
    send_quote,
    update_customer_request,
)

User = get_user_model()


class OrderWorkflowTestCase(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="order_customer",
            password="A-strong-demo-password-983!",
            role=User.Role.CUSTOMER,
        )
        self.other_customer = User.objects.create_user(
            username="other_customer",
            password="A-strong-demo-password-983!",
            role=User.Role.CUSTOMER,
        )
        self.staff = User.objects.create_user(
            username="order_staff",
            password="A-strong-demo-password-983!",
            role=User.Role.STAFF,
        )
        self.owner = User.objects.create_user(
            username="order_owner",
            password="A-strong-demo-password-983!",
            role=User.Role.OWNER,
        )
        self.category = Category.objects.create(name="Bánh sinh nhật")
        self.cake = CakeTemplate.objects.create(
            category=self.category,
            name="Bánh kem dâu",
            reference_price=Decimal("350000"),
        )

    def order_data(self, **overrides):
        data = {
            "size": "20 cm",
            "flavor": "Dâu tươi",
            "quantity": 2,
            "inscription": "Chúc mừng",
            "decoration_request": "Trang trí hoa kem",
            "recipient_name": "Nguyễn An",
            "recipient_phone": "0912345678",
            "delivery_address": "Quận 1, TP. Hồ Chí Minh",
            "desired_delivery_at": timezone.now() + timedelta(days=4),
            "note": "Gọi trước khi giao",
        }
        data.update(overrides)
        return data

    def create_order(self, customer=None, **overrides):
        order, created = create_order(
            customer=customer or self.customer,
            cake_id=self.cake.pk,
            request_key=uuid.uuid4(),
            data=self.order_data(**overrides),
        )
        self.assertTrue(created)
        return order

    def check_order(self, order):
        return record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=True,
            schedule_available=True,
            notes="Nguyên liệu và lịch làm đã kiểm tra.",
            authority_classification="IN_SCOPE",
            authority_basis="Đối chiếu mẫu đang nhận đặt và quy định hiện hành.",
        )

    def quote(self, order, **overrides):
        values = {
            "unit_price": Decimal("400000"),
            "delivery_fee": Decimal("25000"),
            "proposed_delivery_at": timezone.now() + timedelta(days=4),
            "expires_at": timezone.now() + timedelta(days=2),
            "notes": "Báo giá theo yêu cầu hiện tại.",
            "revision_reason": "",
        }
        values.update(overrides)
        return send_quote(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            **values,
        )


class OrderCreationAndPermissionTests(OrderWorkflowTestCase):
    def test_create_order_snapshots_catalog_price_and_ignores_client_price(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("orders:create_order", kwargs={"slug": self.cake.slug}),
            {
                "request_key": str(uuid.uuid4()),
                "desired_delivery_at": (
                    timezone.now() + timedelta(days=4)
                ).strftime("%Y-%m-%dT%H:%M"),
                "size": "20 cm",
                "flavor": "Dâu tươi",
                "quantity": "2",
                "inscription": "",
                "decoration_request": "",
                "recipient_name": "Nguyễn An",
                "recipient_phone": "0912345678",
                "delivery_address": "Quận 1, TP. Hồ Chí Minh",
                "note": "",
                "reference_price": "1",
                "customer": str(self.other_customer.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.reference_price_snapshot, Decimal("350000"))
        self.assertEqual(order.cake_name_snapshot, self.cake.name)
        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertEqual(order.history.count(), 1)

    def test_request_key_retry_is_idempotent_and_payload_conflict_is_rejected(self):
        request_key = uuid.uuid4()
        data = self.order_data()
        first, created = create_order(
            customer=self.customer,
            cake_id=self.cake.pk,
            request_key=request_key,
            data=data,
        )
        retry, retry_created = create_order(
            customer=self.customer,
            cake_id=self.cake.pk,
            request_key=request_key,
            data=data,
        )

        self.assertTrue(created)
        self.assertFalse(retry_created)
        self.assertEqual(first.pk, retry.pk)
        self.assertEqual(Order.objects.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            create_order(
                customer=self.customer,
                cake_id=self.cake.pk,
                request_key=request_key,
                data=self.order_data(quantity=3),
            )

    def test_order_views_require_customer_role_and_enforce_ownership(self):
        order = self.create_order()

        anonymous = self.client.get(reverse("orders:customer_order_list"))
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("accounts:login"), anonymous.url)

        self.client.force_login(self.other_customer)
        hidden = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(hidden.status_code, 404)

        self.client.force_login(self.staff)
        forbidden = self.client.get(reverse("orders:customer_order_list"))
        self.assertEqual(forbidden.status_code, 403)
        create_forbidden = self.client.get(
            reverse("orders:create_order", kwargs={"slug": self.cake.slug})
        )
        self.assertEqual(create_forbidden.status_code, 403)
        self.assertTemplateUsed(create_forbidden, "403.html")

    def test_staff_queue_is_forbidden_to_owner(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("orders:staff_order_list"))
        self.assertEqual(response.status_code, 403)

    def test_staff_production_check_uses_aligned_radio_options(self):
        order = self.create_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.DANG_LAM)
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "production-check-form")
        self.assertContains(response, 'id="id_passed"')
        self.assertContains(response, 'type="radio"', count=2)


class OrderWorkflowTests(OrderWorkflowTestCase):
    def test_customer_staff_quote_and_acceptance_views_complete_happy_path(self):
        order = self.create_order()
        self.client.force_login(self.staff)
        check_response = self.client.post(
            reverse("orders:staff_check_order", kwargs={"pk": order.pk}),
            {
                "expected_version": order.version,
                "materials_available": "yes",
                "schedule_available": "yes",
                "notes": "Nguyên liệu và lịch làm đã kiểm tra.",
                "authority_classification": "IN_SCOPE",
                "authority_basis": "Đối chiếu mẫu đang nhận đặt.",
            },
        )
        self.assertEqual(check_response.status_code, 302)
        order.refresh_from_db()
        quote_response = self.client.post(
            reverse("orders:staff_send_quote", kwargs={"pk": order.pk}),
            {
                "expected_version": order.version,
                "unit_price": "400000",
                "delivery_fee": "25000",
                "proposed_delivery_at": (
                    timezone.now() + timedelta(days=4)
                ).strftime("%Y-%m-%dT%H:%M"),
                "expires_at": (
                    timezone.now() + timedelta(days=2)
                ).strftime("%Y-%m-%dT%H:%M"),
                "notes": "Báo giá theo yêu cầu hiện tại.",
                "revision_reason": "",
            },
        )
        self.assertEqual(quote_response.status_code, 302)
        order.refresh_from_db()
        quote = order.current_quote
        self.assertEqual(order.status, Order.Status.CHO_KHACH_XAC_NHAN)

        self.client.force_login(self.customer)
        accept_response = self.client.post(
            reverse(
                "orders:customer_accept_quote",
                kwargs={"pk": order.pk, "quote_id": quote.pk},
            ),
            {
                "expected_order_version": order.version,
                "expected_quote_id": quote.pk,
                "expected_quote_revision": quote.revision,
            },
        )
        self.assertEqual(accept_response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.QUOTE_ACCEPTED,
            ).count(),
            1,
        )

    def test_staff_requests_supplement_and_customer_resubmits_updated_request(self):
        order = self.create_order()
        order = request_customer_supplement(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            message="Vui lòng xác nhận kích thước mong muốn.",
        )
        self.assertEqual(order.status, Order.Status.CAN_BO_SUNG)
        self.assertTrue(order.needs_supplement)

        data = self.order_data(size="22 cm")
        order = update_customer_request(
            order_id=order.pk,
            customer=self.customer,
            expected_version=order.version,
            data=data,
        )
        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertFalse(order.needs_supplement)
        self.assertEqual(order.size, "22 cm")
        self.assertEqual(order.history.count(), 3)

    def test_quote_total_is_calculated_and_acceptance_is_current_and_once_only(self):
        order = self.check_order(self.create_order())
        order, quote = self.quote(order)

        self.assertEqual(order.status, Order.Status.CHO_KHACH_XAC_NHAN)
        self.assertEqual(quote.cake_total, Decimal("800000"))
        self.assertEqual(quote.total, Decimal("825000"))
        self.assertEqual(quote.quantity_snapshot, 2)

        accepted = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
        )
        self.assertEqual(accepted.status, Order.Status.DA_XAC_NHAN)
        order.refresh_from_db()
        with self.assertRaises(InvalidOrderTransition):
            accept_quote(
                order_id=order.pk,
                customer=self.customer,
                quote_id=quote.pk,
                quote_revision=quote.revision,
                expected_order_version=order.version,
            )
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.QUOTE_ACCEPTED,
            ).count(),
            1,
        )

    def test_quote_change_request_and_revision_replace_previous_quote(self):
        order = self.check_order(self.create_order())
        order, first_quote = self.quote(order)
        order = request_quote_change(
            order_id=order.pk,
            customer=self.customer,
            quote_id=first_quote.pk,
            quote_revision=first_quote.revision,
            expected_order_version=order.version,
            message="Xin đổi hương vị sang chocolate.",
        )
        first_quote.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_TRAO_DOI)
        self.assertEqual(first_quote.status, Quote.Status.CHANGE_REQUESTED)

        order, second_quote = self.quote(
            order,
            unit_price=Decimal("420000"),
            revision_reason="Cập nhật giá theo hương vị chocolate mới.",
        )
        first_quote.refresh_from_db()
        self.assertEqual(second_quote.version_number, 2)
        self.assertEqual(first_quote.status, Quote.Status.REPLACED)
        self.assertEqual(order.current_quote_id, second_quote.pk)
        self.assertEqual(order.status, Order.Status.CHO_KHACH_XAC_NHAN)

    def test_expired_quote_cannot_be_accepted_and_can_be_replaced(self):
        order = self.check_order(self.create_order())
        order, expired_quote = self.quote(order)
        Quote.objects.filter(pk=expired_quote.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with self.assertRaises(InvalidOrderTransition):
            accept_quote(
                order_id=order.pk,
                customer=self.customer,
                quote_id=expired_quote.pk,
                quote_revision=expired_quote.revision,
                expected_order_version=order.version,
            )

        order, renewed_quote = self.quote(
            order,
            revision_reason="Báo giá cũ hết hạn, cập nhật thời hạn.",
        )
        expired_quote.refresh_from_db()
        self.assertEqual(expired_quote.status, Quote.Status.REPLACED)
        self.assertEqual(renewed_quote.version_number, 2)

    def test_rejecting_after_quote_change_closes_quote_and_keeps_history_link(self):
        order = self.check_order(self.create_order())
        order, quote = self.quote(order)
        order = request_quote_change(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
            message="Xin trao đổi lại.",
        )

        order = reject_order(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            reason="Cửa hàng không thể đáp ứng yêu cầu.",
        )
        quote.refresh_from_db()
        history = OrderHistory.objects.get(
            order=order,
            action=OrderHistory.Action.ORDER_REJECTED,
        )
        self.assertEqual(order.status, Order.Status.TU_CHOI)
        self.assertEqual(quote.status, Quote.Status.REPLACED)
        self.assertEqual(history.quote, quote)

    def test_staff_rejection_ends_order_and_redirects_without_server_error(self):
        order = self.create_order()
        self.client.force_login(self.staff)
        url = reverse("orders:staff_reject_order", kwargs={"pk": order.pk})
        payload = {
            "expected_version": order.version,
            "message": "Cửa hàng không thể tiếp nhận yêu cầu này.",
        }

        response = self.client.post(url, payload)

        self.assertRedirects(
            response,
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk}),
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.TU_CHOI)
        self.assertEqual(
            order.rejection_reason,
            "Cửa hàng không thể tiếp nhận yêu cầu này.",
        )
        history = OrderHistory.objects.filter(
            order=order,
            action=OrderHistory.Action.ORDER_REJECTED,
        )
        self.assertEqual(history.count(), 1)

        repeated = self.client.post(url, payload)
        self.assertRedirects(
            repeated,
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk}),
        )
        self.assertEqual(history.count(), 1)

        self.client.force_login(self.customer)
        customer_detail = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )
        self.assertContains(customer_detail, "Cửa hàng không tiếp nhận đơn")
        self.assertContains(
            customer_detail,
            "Cửa hàng không thể tiếp nhận yêu cầu này.",
        )

    def test_customer_detail_renders_snapshot_and_quote_actions(self):
        order = self.check_order(self.create_order())
        order, quote = self.quote(order)
        self.client.force_login(self.customer)

        response = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.code)
        self.assertContains(response, "Đồng ý báo giá")
        self.assertContains(response, "Yêu cầu trao đổi lại")
        self.assertContains(response, quote.delivery_address_snapshot)

    def test_staff_quote_form_requires_post_and_csrf(self):
        order = self.create_order()
        self.client.force_login(self.staff)
        detail_url = reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
        quote_url = reverse("orders:staff_send_quote", kwargs={"pk": order.pk})
        self.assertEqual(self.client.get(quote_url).status_code, 405)

        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        denied = csrf_client.post(
            reverse("orders:staff_check_order", kwargs={"pk": order.pk}),
            {},
        )
        self.assertEqual(denied.status_code, 403)
        detail = csrf_client.get(detail_url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "availability-choice-field")
        self.assertContains(detail, "availability-choice-options")
        self.assertContains(detail, "id_materials_available_0")

    def test_stale_staff_action_returns_to_staff_detail_not_customer_route(self):
        order = self.create_order()
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("orders:staff_check_order", kwargs={"pk": order.pk}),
            {
                "expected_version": 0,
                "materials_available": "yes",
                "schedule_available": "yes",
                "notes": "Đã kiểm tra.",
                "authority_classification": "IN_SCOPE",
                "authority_basis": "Theo mẫu và quy định cửa hàng.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain[-1][0],
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk}),
        )
        self.assertContains(response, "Đơn hàng đã thay đổi")
