document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-assistant-prompt]").forEach((prompt) => {
        prompt.addEventListener("click", () => {
            const content = document.querySelector("#assistant-form textarea[name='content']");
            if (!content) return;
            content.value = prompt.dataset.assistantPrompt;
            content.focus();
        });
    });

    const form = document.querySelector("#assistant-form");
    if (!form) return;

    const button = form.querySelector('button[type="submit"]');
    const status = document.querySelector("#assistant-status");
    const content = form.querySelector("textarea[name='content']");
    const requestId = form.querySelector("input[name='request_id']");
    const retryButton = document.querySelector("#assistant-retry");

    const submit = async (event) => {
        event.preventDefault();
        if (button.disabled) return;
        button.disabled = true;
        retryButton.hidden = true;
        status.textContent = "Đang xử lý yêu cầu...";
        status.classList.remove("assistant-error-text");

        try {
            const response = await fetch(form.action || window.location.href, {
                method: "POST",
                headers: { Accept: "application/json" },
                body: new FormData(form),
                credentials: "same-origin",
            });
            const payload = await response.json();
            if (!response.ok) {
                const error = new Error(payload.error || "Chưa gửi được tin nhắn.");
                error.retryable = Boolean(payload.retryable);
                throw error;
            }
            if (payload.redirect_url) {
                window.location.assign(payload.redirect_url);
                return;
            }
            throw new Error("Chưa nhận được trạng thái xử lý hợp lệ.");
        } catch (error) {
            status.textContent =
                error instanceof TypeError
                    ? "Mất kết nối khi gửi. Có thể thử lại; mã yêu cầu được giữ nguyên để tránh gửi trùng."
                    : error.message;
            status.classList.add("assistant-error-text");
            retryButton.hidden = !(error instanceof TypeError || error.retryable);
            if (error.retryable) requestId.value = crypto.randomUUID();
        } finally {
            button.disabled = false;
            content.focus();
        }
    };

    form.addEventListener("submit", submit);
    retryButton.addEventListener("click", submit);
});
