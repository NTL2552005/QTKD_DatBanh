document.querySelectorAll("[data-intake-classification-form]").forEach((form) => {
    const classification = form.querySelector("#id_authority_classification");
    const availability = form.querySelector("[data-availability-fields]");
    const ownerReasons = form.querySelector("[data-owner-reason-fields]");
    const authorityReasons = form.querySelector("[data-authority-reasons]");
    const authorityDetails = form.querySelector("[data-authority-details]");
    const inScope = form.querySelector("[data-in-scope-fields]");
    const submit = form.querySelector("[data-intake-submit]");

    if (
        !classification
        || !availability
        || !ownerReasons
        || !authorityReasons
        || !authorityDetails
        || !inScope
        || !submit
    ) {
        return;
    }

    const updateFields = () => {
        const ownerRequired = classification.value === "OWNER_REQUIRED";
        const inStaffScope = classification.value === "IN_SCOPE";
        const needsInfo = classification.value === "NEEDS_INFO";

        availability.hidden = !inStaffScope;
        ownerReasons.hidden = !ownerRequired && !needsInfo;
        authorityReasons.hidden = !ownerRequired;
        inScope.hidden = !inStaffScope;
        submit.textContent = ownerRequired
            ? "Chuyển chủ xử lý"
            : needsInfo
                ? "Lưu kết quả phân loại"
                : "Lưu kết quả kiểm tra";

        availability.querySelectorAll('input[type="radio"]').forEach((input) => {
            input.required = inStaffScope;
        });
        availability.querySelectorAll("input, select, textarea").forEach((input) => {
            input.disabled = !inStaffScope;
        });
        const notes = availability.querySelector("#id_notes");
        if (notes) notes.required = inStaffScope || needsInfo;
        authorityReasons.querySelectorAll("input, select, textarea").forEach((input) => {
            input.disabled = !ownerRequired;
        });
        const details = authorityDetails.querySelector("#id_authority_details");
        if (details) {
            details.disabled = !ownerRequired && !needsInfo;
            details.required = ownerRequired || needsInfo;
        }
        inScope.querySelectorAll("input, select, textarea").forEach((input) => {
            input.disabled = !inStaffScope;
        });
        const basis = inScope.querySelector("#id_authority_basis");
        if (basis) basis.required = inStaffScope;
    };

    classification.addEventListener("change", updateFields);
    updateFields();
});
