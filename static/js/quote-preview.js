const quoteForm = document.querySelector("[data-quote-form]");

if (quoteForm) {
    const quantity = Number(quoteForm.dataset.quantity || 0);
    const unitPrice = quoteForm.querySelector("#id_unit_price");
    const deliveryFee = quoteForm.querySelector("#id_delivery_fee");
    const cakeTotal = quoteForm.querySelector("[data-cake-total]");
    const quoteTotal = quoteForm.querySelector("[data-quote-total]");
    const formatVnd = (amount) => `${new Intl.NumberFormat("vi-VN").format(amount)} đ`;

    const refreshTotals = () => {
        const price = Number(unitPrice.value) || 0;
        const fee = Number(deliveryFee.value) || 0;
        cakeTotal.textContent = formatVnd(price * quantity);
        quoteTotal.textContent = formatVnd(price * quantity + fee);
    };

    unitPrice.addEventListener("input", refreshTotals);
    deliveryFee.addEventListener("input", refreshTotals);
    refreshTotals();
}
