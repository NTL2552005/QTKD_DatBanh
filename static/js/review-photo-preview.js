const reviewUploadForm = document.querySelector("[data-review-upload]");

if (reviewUploadForm) {
    const photoInput = reviewUploadForm.querySelector('input[type="file"]');
    const preview = reviewUploadForm.querySelector("[data-review-preview]");

    photoInput.addEventListener("change", () => {
        const [file] = photoInput.files;
        if (!file) {
            preview.removeAttribute("src");
            preview.hidden = true;
            return;
        }

        preview.src = URL.createObjectURL(file);
        preview.hidden = false;
    });
}
